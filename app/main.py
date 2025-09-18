from contextlib import asynccontextmanager
import asyncio
import logging
import time
import subprocess
import sys
import os
import re
import json
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base
from app.db import models as db_models
from app.db.base import engine, get_db
from app.api.v1.routes import api_router
from app.web.routes import router as web_router, admin_router
from app.services import scheduler, geoip
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

    # Initialize check counts from DB
    from sqlalchemy.ext.asyncio import AsyncSession
    async with AsyncSession(engine) as session:
        await metrics.initialize_check_counts(session)
    
    # Initialize GeoIP service
    if settings.SAVE_CHECK_LAST_LOGS:
        geoip.initialize_geoip()
    
    # Check Redis connection and potentially start worker
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                r.ping()
                logger.info("Successfully connected to Redis for Celery broker.")
                app.state.redis_connected = True

                # Clear stale user stats counters on startup without KEYS fan-out
                logger.info("Clearing stale user stats counters from Redis...")
                deleted = 0
                cursor = 0
                while True:
                    cursor, keys = r.scan(cursor=cursor, match="user_stats:counters:*", count=1000)
                    if keys:
                        try:
                            r.unlink(*keys)
                        except Exception:
                            # Fallback if UNLINK not supported
                            r.delete(*keys)
                        deleted += len(keys)
                    if cursor == 0:
                        break
                logger.info(f"Deleted {deleted} stale user stats counters.")
                
                # Start Celery worker if enabled for testing/dev
                if settings.AUTO_START_EMBEDDED_WORKER:
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
        logger.info(f"Starting Celery worker with thread pool, concurrency {settings.WORKER_CONCURRENCY}")
        
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
            "--pool=threads",
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


app = FastAPI(lifespan=lifespan, title="ttr.rip")

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
    request.state.start_time = start_time
    
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
    
    # Add Vary header to prevent cache poisoning
    response.headers["Vary"] = "Accept-Encoding, Accept-Language"

    # Security headers and CSP (strong CSP for user dashboard)
    if settings.SECURITY_HEADERS_ENABLED:
        # Common
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # Strong CSP only on user-facing pages (not API/static/admin)
        if not path.startswith("/api") and not path.startswith("/static") and not path.startswith("/admin") and not path.startswith("/p/") and not path.startswith("/metrics"):
            # Apply CSP to home, dashboard, and public integrations/status pages
            if path == "/" or path.startswith("/dashboard") or path.startswith("/check/") or path.startswith("/s/"):
                response.headers["Content-Security-Policy"] = settings.CSP_USER_DASHBOARD
    
    # Record response time and status
    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    
    # Use the route template for the endpoint path to avoid high cardinality
    route = request.scope.get('route')
    if route:
        clean_path = route.path
    else:
        # For 404s and other unhandled paths
        clean_path = "not_found"
    
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

@app.api_route("/p/{user_slug}/{check_identifier}", methods=["GET", "POST"], status_code=status.HTTP_200_OK)
async def ping_check(user_slug: str, check_identifier: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user_by_slug(db, slug=user_slug)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db_check = await crud.get_check_by_identifier(db, user=user, check_identifier=check_identifier)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")

    # --- Log Ping if Enabled ---
    if settings.SAVE_CHECK_LAST_LOGS:
        try:
            r = get_redis_connection()
            if r:
                # Derive client IP: prefer X-Forwarded-For, then X-Real-IP, then socket
                xff = request.headers.get("x-forwarded-for")
                if xff:
                    ip_address = xff.split(",")[0].strip()
                else:
                    ip_address = request.headers.get("x-real-ip") or (request.client.host if request.client else None)
                if not ip_address:
                    ip_address = "0.0.0.0"

                user_agent = request.headers.get("user-agent", "Unknown")

                # Prefer GeoIP headers injected by Caddy; fallback to local DB lookup
                country_code = request.headers.get("x-geoip-country-code") or request.headers.get("x-geoip-country_code")
                country_name = request.headers.get("x-geoip-country-name") or request.headers.get("x-geoip-country_name")

                if country_code or country_name:
                    geoip_details = {
                        "country_code": (country_code or ""),
                        "country_name": country_name or "",
                        "connection_type": "Unknown",  # Not available from Caddy GeoIP
                    }
                else:
                    geoip_details = geoip.get_geoip_details(ip_address)

                log_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    **geoip_details,
                }
                
                key = crud.get_check_runtime_redis_key(db_check.id)
                # Update last_pings JSON array inside the runtime hash (keep last 3)
                try:
                    existing = r.hget(key, "last_pings")
                except Exception:
                    existing = None
                logs = []
                if existing:
                    try:
                        if isinstance(existing, bytes):
                            existing = existing.decode()
                        parsed = json.loads(existing)
                        if isinstance(parsed, list):
                            logs = parsed
                    except Exception:
                        logs = []
                logs.insert(0, log_entry)
                if len(logs) > 3:
                    logs = logs[:3]
                r.hset(key, "last_pings", json.dumps(logs))
        except Exception as e:
            logger.error(f"Failed to log ping details to Redis for check {db_check.id}: {e}")
    # --- End Log Ping ---

    # Capture content from GET query params or POST body
    content = ""
    if request.method == "GET":
        content = str(request.query_params)
    elif request.method == "POST":
        try:
            # Limit body size to prevent abuse (e.g., 1MB)
            body_bytes = await request.body()
            if len(body_bytes) > 1_048_576:
                 raise HTTPException(status_code=413, detail="Request body too large.")
            content = body_bytes.decode('utf-8', errors='replace')
        except Exception:
            content = "[Could not decode request body]"

    # Store content in runtime Redis hash
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                key = crud.get_check_runtime_redis_key(db_check.id)
                r.hset(key, "last_content", content)
        except Exception as e:
            logger.error(f"Failed to store ping content in Redis for check {db_check.id}: {e}")

    # Get previous status from Redis for metrics
    previous_status = "new"
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                previous_status = r.hget(crud.get_check_runtime_redis_key(db_check.id), "status") or "new"
        except Exception as e:
            logger.error(f"Could not get previous status from Redis for check {db_check.id}: {e}")

    updated_check, reason = await crud.update_check_ping(db, check=db_check, content=content)

    # Record check metrics
    metrics.record_check_update(updated_check.status, previous_status, is_paused=updated_check.paused)
    if updated_check.last_duration_seconds:
        metrics.record_check_duration(updated_check.last_duration_seconds)
    
    start_time = getattr(request.state, "start_time", time.perf_counter())
    process_time = time.perf_counter() - start_time
    
    response_message = "OK"
    if reason:
        response_message = f"OK, but content validation failed: {reason}"
        
    return {"message": response_message, "process_time_seconds": process_time}


@app.get("/p/{user_slug}/{check_identifier}/start", status_code=status.HTTP_200_OK)
async def start_check(user_slug: str, check_identifier: str, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user_by_slug(db, slug=user_slug)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db_check = await crud.get_check_by_identifier(db, user=user, check_identifier=check_identifier)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    await crud.update_check_start(db, check=db_check)
    return {"message": "OK"}


class SVGResponse(Response):
    media_type = "image/svg+xml"

BADGE_TEMPLATES = {
    "up": """<svg xmlns="http://www.w3.org/2000/svg" width="88" height="20"><linearGradient id="b" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><mask id="a"><rect width="88" height="20" rx="3" fill="#fff"/></mask><g mask="url(#a)"><path fill="#555" d="M0 0h37v20H0z"/><path fill="#4c1" d="M37 0h51v20H37z"/><path fill="url(#b)" d="M0 0h88v20H0z"/></g><g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11"><text x="18.5" y="15" fill="#010101" fill-opacity=".3">status</text><text x="18.5" y="14">status</text><text x="61.5" y="15" fill="#010101" fill-opacity=".3">passing</text><text x="61.5" y="14">passing</text></g></svg>""",
    "down": """<svg xmlns="http://www.w3.org/2000/svg" width="88" height="20"><linearGradient id="b" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><mask id="a"><rect width="88" height="20" rx="3" fill="#fff"/></mask><g mask="url(#a)"><path fill="#555" d="M0 0h37v20H0z"/><path fill="#e05d44" d="M37 0h51v20H37z"/><path fill="url(#b)" d="M0 0h88v20H0z"/></g><g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11"><text x="18.5" y="15" fill="#010101" fill-opacity=".3">status</text><text x="18.5" y="14">status</text><text x="61.5" y="15" fill="#010101" fill-opacity=".3">failing</text><text x="61.5" y="14">failing</text></g></svg>""",
    "new": """<svg xmlns="http://www.w3.org/2000/svg" width="88" height="20"><linearGradient id="b" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><mask id="a"><rect width="88" height="20" rx="3" fill="#fff"/></mask><g mask="url(#a)"><path fill="#555" d="M0 0h37v20H0z"/><path fill="#9f9f9f" d="M37 0h51v20H37z"/><path fill="url(#b)" d="M0 0h88v20H0z"/></g><g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11"><text x="18.5" y="15" fill="#010101" fill-opacity=".3">status</text><text x="18.5" y="14">status</text><text x="61.5" y="15" fill="#010101" fill-opacity=".3">new</text><text x="61.5" y="14">new</text></g></svg>""",
    "paused": """<svg xmlns="http://www.w3.org/2000/svg" width="88" height="20"><linearGradient id="b" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><mask id="a"><rect width="88" height="20" rx="3" fill="#fff"/></mask><g mask="url(#a)"><path fill="#555" d="M0 0h37v20H0z"/><path fill="#9f9f9f" d="M37 0h51v20H37z"/><path fill="url(#b)" d="M0 0h88v20H0z"/></g><g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11"><text x="18.5" y="15" fill="#010101" fill-opacity=".3">status</text><text x="18.5" y="14">status</text><text x="61.5" y="15" fill="#010101" fill-opacity=".3">paused</text><text x="61.5" y="14">paused</text></g></svg>""",
}

@app.get("/p/{user_slug}/{check_identifier}/badge.svg", response_class=SVGResponse)
async def get_status_badge(user_slug: str, check_identifier: str, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user_by_slug(db, slug=user_slug)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db_check = await crud.get_check_by_identifier(db, user=user, check_identifier=check_identifier)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")

    # Get current status from Redis and re-evaluate to ensure badge is up-to-date.
    current_status = "new"
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                key = crud.get_check_runtime_redis_key(db_check.id)
                runtime_data = r.hgetall(key)
                current_status = runtime_data.get("status", "new")
                last_ping_str = runtime_data.get("last_ping")

                if current_status != 'down':
                    now = datetime.now(timezone.utc)
                    reference_time = datetime.fromisoformat(last_ping_str) if last_ping_str else db_check.created_at
                    
                    deadline = reference_time + timedelta(seconds=db_check.interval_seconds + db_check.grace_seconds)
                    
                    if now > deadline:
                        current_status = "down"
        except Exception as e:
            logger.error(f"Failed to get runtime status for badge for check {db_check.id}: {e}")
            current_status = "unknown" # Should probably be a different badge
    
    status = "paused" if db_check.paused else current_status
    badge_svg = BADGE_TEMPLATES.get(status, BADGE_TEMPLATES["new"])
    
    # Add cache control headers. Allow caching for 60 seconds.
    headers = {
        "Cache-Control": "public, max-age=60",
    }
    return SVGResponse(content=badge_svg, headers=headers)


@app.get("/p/{user_slug}/{check_identifier}/fail", status_code=status.HTTP_200_OK)
async def fail_check(user_slug: str, check_identifier: str, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user_by_slug(db, slug=user_slug)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db_check = await crud.get_check_by_identifier(db, user=user, check_identifier=check_identifier)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    # Get previous status from Redis for metrics
    previous_status = "new"
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                previous_status = r.hget(crud.get_check_runtime_redis_key(db_check.id), "status") or "new"
        except Exception as e:
            logger.error(f"Could not get previous status from Redis for check {db_check.id}: {e}")

    updated_check, _ = await crud.update_check_fail(db, check=db_check, reason="Manual failure triggered")
    
    # Record check metrics
    metrics.record_check_update(updated_check.status, previous_status, is_paused=updated_check.paused)
    
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
