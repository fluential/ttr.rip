from contextlib import asynccontextmanager
import asyncio
import logging
import time
import subprocess
import sys
import os
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base
from app.db import models as db_models
from app.db.base import engine, get_db
from app.api.v1.routes import api_router
from app.web.routes import router as web_router, admin_router
from app.services import scheduler
from app import crud, security
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.tasks import cleanup  # Add this import
from app import metrics  # Add metrics import
from app.core.redis_pool import get_redis_connection

setup_logging()
logger = logging.getLogger(__name__)

# Celery worker process
celery_worker_process = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    app.state.redis_connected = False
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Initialize metrics with app version
    metrics.initialize_metrics(app_version="1.0.0")
    
    # Check Redis connection and potentially start worker
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                r.ping()
                logger.info("Successfully connected to Redis for Celery broker.")
                app.state.redis_connected = True
                
                # Start Celery worker if enabled
                if settings.AUTO_START_WORKER:
                    start_celery_worker()
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
    
    # Start the cleanup task if enabled
    cleanup_task = None
    if settings.CLEANUP_ENABLED:
        logger.info(f"Starting periodic cleanup task. Will run every {settings.CLEANUP_INTERVAL_HOURS} hours")
        cleanup_task = asyncio.create_task(cleanup.start_periodic_cleanup())

    # Start metrics collection task
    metrics_task = asyncio.create_task(collect_metrics_periodically())

    yield
    
    # Stop metrics collection task
    metrics_task.cancel()
    try:
        await metrics_task
    except asyncio.CancelledError:
        logger.info("Metrics collection task cancelled")
    
    # Stop Celery worker if it was started
    if celery_worker_process:
        stop_celery_worker()
    
    # Cancel the cleanup task if it was started
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            logger.info("Cleanup task cancelled")
    
    logger.info("Shutting down...")


async def collect_metrics_periodically():
    """Collect metrics on a regular interval"""
    while True:
        try:
            # Update queue metrics if Redis is available
            if not settings.DEBUG_MODE:
                try:
                    r = get_redis_connection()
                    if r:
                        queue_name = 'rtt_celery_queue'
                        queue_size = r.llen(queue_name)
                        metrics.record_queue_size(queue_name, queue_size)
                        
                        # Update worker count
                        from app.services.queue_stats import get_queue_stats
                        stats = get_queue_stats()
                        if isinstance(stats["workers_online"], int):
                            metrics.record_workers_count(stats["workers_online"])
                except Exception as e:
                    logger.error(f"Error collecting queue metrics: {e}")
            
            # Update check status counts
            async with AsyncSession(engine) as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT status, COUNT(*) as count 
                    FROM checks 
                    GROUP BY status
                """))
                for row in result:
                    metrics.CHECKS_TOTAL.labels(status=row[0]).set(row[1])
                
        except Exception as e:
            logger.error(f"Error in metrics collection: {e}")
        
        # Sleep for 30 seconds before next collection
        await asyncio.sleep(30)


def start_celery_worker():
    """Start a Celery worker subprocess."""
    global celery_worker_process
    
    if celery_worker_process:
        logger.info("Celery worker is already running")
        return
    
    try:
        logger.info(f"Starting Celery worker with concurrency {settings.WORKER_CONCURRENCY}")
        
        # Get the directory of the current script
        base_dir = os.path.dirname(os.path.abspath(__file__))
        # Go up one level to the project root
        project_dir = os.path.dirname(base_dir)
        
        # Set up environment variables
        env = os.environ.copy()
        env["PYTHONPATH"] = project_dir
        
        # Start the worker
        cmd = [
            sys.executable, "-m", "celery", 
            "-A", "app.worker.celery_app", "worker", 
            "--loglevel=info", 
            f"--concurrency={settings.WORKER_CONCURRENCY}"
        ]
        
        celery_worker_process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        logger.info(f"Started Celery worker process with PID: {celery_worker_process.pid}")
    except Exception as e:
        logger.error(f"Failed to start Celery worker: {e}", exc_info=True)


def stop_celery_worker():
    """Stop the Celery worker subprocess."""
    global celery_worker_process
    
    if not celery_worker_process:
        return
    
    logger.info(f"Stopping Celery worker process (PID: {celery_worker_process.pid})")
    try:
        celery_worker_process.terminate()
        # Wait for a short time for graceful shutdown
        try:
            celery_worker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill if it doesn't shut down
            celery_worker_process.kill()
            celery_worker_process.wait()
        
        # Read any output from the worker
        stdout, stderr = celery_worker_process.communicate()
        if stdout:
            logger.debug(f"Celery worker stdout: {stdout}")
        if stderr:
            logger.warning(f"Celery worker stderr: {stderr}")
            
        logger.info("Celery worker stopped successfully")
    except Exception as e:
        logger.error(f"Error stopping Celery worker: {e}", exc_info=True)
    finally:
        celery_worker_process = None


app = FastAPI(lifespan=lifespan, title="ttl.rip")

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    
    # Record API request metrics
    path = request.url.path
    method = request.method
    
    # Extract user ID for active user tracking if authenticated
    user_id = None
    auth_key = request.cookies.get("auth_key")
    if auth_key:
        # This is a simplification - in a real implementation,
        # you would look up the user ID without a DB call
        if hasattr(request.state, "user_id"):
            user_id = request.state.user_id
            metrics.track_user_request_start(user_id)
    
    response = await call_next(request)
    
    # Record response time and status
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    request.state.process_time = process_time
    
    # Clean endpoint path to avoid high cardinality in metrics
    clean_path = path
    for segment in path.split('/'):
        if segment.isdigit() or (len(segment) > 20 and not segment.startswith('api')):
            clean_path = clean_path.replace(segment, '{id}')
    
    # Record API metrics
    metrics.API_REQUESTS.labels(
        method=method,
        endpoint=clean_path,
        status=response.status_code
    ).inc()
    metrics.API_REQUEST_DURATION.observe(process_time)
    
    # End tracking of user request
    if user_id:
        metrics.track_user_request_end(user_id)
    
    return response

app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/ping/{uuid}", status_code=status.HTTP_200_OK)
async def ping_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    previous_status = db_check.status
    updated_check = await crud.update_check_ping(db, check=db_check)
    
    # Record check metrics
    metrics.record_check_update(updated_check.status, previous_status)
    if updated_check.last_duration_seconds:
        metrics.record_check_duration(updated_check.last_duration_seconds)
    
    return {"message": "OK"}


@app.get("/ping/{uuid}/start", status_code=status.HTTP_200_OK)
async def start_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    await crud.update_check_start(db, check=db_check)
    return {"message": "OK"}


@app.get("/ping/{uuid}/fail", status_code=status.HTTP_200_OK)
async def fail_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    previous_status = db_check.status
    updated_check = await crud.update_check_fail(db, check=db_check)
    
    # Record check metrics
    metrics.record_check_update(updated_check.status, previous_status)
    
    return {"message": "OK"}

@app.get("/metrics", status_code=status.HTTP_200_OK)
async def get_metrics(
    response: Response,
    current_user: db_models.User = Depends(security.get_current_admin_user)
):
    """Expose Prometheus metrics endpoint"""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin")
    
    response.headers["Content-Type"] = "text/plain"
    return Response(content=metrics.get_metrics(), media_type="text/plain")


app.include_router(api_router, prefix="/api/v1")
app.include_router(web_router)
app.include_router(admin_router)
