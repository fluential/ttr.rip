from contextlib import asynccontextmanager
import asyncio
import logging
import time
import subprocess
import sys
import os
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base
from app.db.base import engine, get_db
from app.api.v1.routes import api_router
from app.web.routes import router as web_router, admin_router
from app.services import scheduler
from app import crud
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.tasks import cleanup  # Add this import

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
    
    # Check Redis connection and potentially start worker
    if not settings.DEBUG_MODE:
        try:
            import redis
            r = redis.from_url(str(settings.REDIS_URL))
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

    yield
    
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
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    request.state.process_time = process_time
    return response

app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/ping/{uuid}", status_code=status.HTTP_200_OK)
async def ping_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    await crud.update_check_ping(db, check=db_check)
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
    await crud.update_check_fail(db, check=db_check)
    return {"message": "OK"}


app.include_router(api_router, prefix="/api/v1")
app.include_router(web_router)
app.include_router(admin_router)
