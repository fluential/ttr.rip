import time
import logging
from typing import Dict, Optional, Set
from prometheus_client import Counter, Gauge, Histogram, Info
import prometheus_client
from app.core.redis_pool import get_redis_connection, ephemeral_redis

logger = logging.getLogger(__name__)

# Define metrics
CHECKS_TOTAL = Gauge("ttl_checks_total", "Total number of checks", ["status"])
CHECKS_DURATION = Histogram("ttl_check_duration_seconds", "Duration of check executions in seconds", buckets=(1, 5, 10, 30, 60, 300, 600, 1800, 3600))
NOTIFICATIONS_SENT = Counter("ttl_notifications_sent_total", "Total number of notifications sent", ["status", "type"])
API_REQUESTS = Counter("ttl_api_requests_total", "Total number of API requests", ["method", "endpoint", "status"])
API_REQUEST_DURATION = Histogram("ttl_api_request_duration_seconds", "Duration of API requests in seconds", buckets=(0.01, 0.05, 0.1, 0.5, 1, 5))
DB_QUERY_DURATION = Histogram("ttl_db_query_duration_seconds", "Duration of database queries in seconds", buckets=(0.01, 0.05, 0.1, 0.5, 1, 5))
REDIS_COMMAND_DURATION = Histogram("ttl_redis_command_duration_seconds", "Duration of Redis commands in seconds", buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5))
PING_PROCESS_TIME = Histogram("ttl_ping_process_time_seconds", "Processing time for ping endpoint in seconds", buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2))
ACTIVE_USERS = Gauge("ttl_active_users", "Number of active users")
QUEUE_SIZE = Gauge("ttl_queue_size", "Size of the message queue", ["queue_name"])
WORKERS_ONLINE = Gauge("ttl_workers_online", "Number of workers online")
APP_INFO = Info("ttl_app_info", "Application information")

# Track in-flight requests for active user counting
active_users: Set[int] = set()

def initialize_metrics(app_version: str):
    """Initialize metrics with static values"""
    APP_INFO.info({"version": app_version})
    
    # Initialize status-based metrics
    for status in ["up", "down", "new", "paused"]:
        CHECKS_TOTAL.labels(status=status).set(0) # Initialize to 0

    # Initialize notification metrics
    for status in ["success", "error"]:
        for ntype in ["telegram"]:
            NOTIFICATIONS_SENT.labels(status=status, type=ntype)

def record_check_update(status: str, previous_status: Optional[str] = None, is_paused: bool = False):
    """Record a check status update. Does nothing if the check is paused."""
    if is_paused:
        return
    CHECKS_TOTAL.labels(status=status).inc()
    if previous_status and previous_status != status:
        CHECKS_TOTAL.labels(status=previous_status).dec()

def record_check_creation():
    """Record the creation of a new check."""
    CHECKS_TOTAL.labels(status='new').inc()

def record_check_deletion(status: str):
    """Record the deletion of a check."""
    CHECKS_TOTAL.labels(status=status).dec()

def record_check_pause_toggle(is_pausing: bool, status: str):
    """Record a check being paused or resumed."""
    if is_pausing:
        CHECKS_TOTAL.labels(status=status).dec()
        CHECKS_TOTAL.labels(status='paused').inc()
    else: # Resuming
        CHECKS_TOTAL.labels(status='paused').dec()
        CHECKS_TOTAL.labels(status=status).inc()

def record_check_duration(duration_seconds: float):
    """Record a check execution duration"""
    CHECKS_DURATION.observe(duration_seconds)

def record_notification_sent(notification_type: str, status: str):
    """Record a notification being sent"""
    NOTIFICATIONS_SENT.labels(status=status, type=notification_type).inc()

def record_queue_size(queue_name: str, size: int):
    """Record the size of a queue"""
    QUEUE_SIZE.labels(queue_name=queue_name).set(size)

def record_workers_count(count: int):
    """Record the number of online workers"""
    WORKERS_ONLINE.set(count)

def track_user_request_start(user_id: int):
    """Track the start of a user request"""
    active_users.add(user_id)
    ACTIVE_USERS.set(len(active_users))

def track_user_request_end(user_id: int):
    """Track the end of a user request"""
    if user_id in active_users:
        active_users.remove(user_id)
    ACTIVE_USERS.set(len(active_users))

class DBQueryTimer:
    """Context manager for timing database queries"""
    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        DB_QUERY_DURATION.observe(duration)

class PingProcessTimer:
    """Context manager for timing ping endpoint processing."""
    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.perf_counter() - self._start
        PING_PROCESS_TIME.observe(duration)

def record_ping_process_time(seconds: float):
    """Manually record ping processing time in seconds."""
    try:
        PING_PROCESS_TIME.observe(float(seconds))
    except Exception:
        pass

def get_metrics():
    """Get metrics in Prometheus format"""
    return prometheus_client.generate_latest()

async def initialize_check_counts(session):
    """Initialize check counts at startup from cached Redis counters if present, without scanning."""
    # Initialize all to 0 first
    for s in ["up", "down", "new", "paused"]:
        CHECKS_TOTAL.labels(status=s).set(0)

    try:
        async with ephemeral_redis() as r:
            if r:
                counts = await r.hgetall("metrics:checks_status_counts") or {}
                # Normalize values (handle bytes or str)
                def _get_int(key: str) -> int:
                    v = counts.get(key)
                    if v is None and isinstance(counts, dict):
                        v = counts.get(key.encode())  # type: ignore
                    if v is None:
                        return 0
                    try:
                        if isinstance(v, bytes):
                            v = v.decode()
                        return int(v)
                    except Exception:
                        return 0

                up = _get_int("up")
                down = _get_int("down")
                new = _get_int("new")
                paused = _get_int("paused")

                CHECKS_TOTAL.labels(status="up").set(up)
                CHECKS_TOTAL.labels(status="down").set(down)
                CHECKS_TOTAL.labels(status="new").set(new)
                CHECKS_TOTAL.labels(status="paused").set(paused)

                logger.info(f"Initialized check counts from Redis cache: up={up}, down={down}, new={new}, paused={paused}")
                return
    except Exception as e:
        logger.error(f"Failed reading cached global status counters from Redis: {e}")

    # Fallback: if Redis cache is missing/unavailable, set paused from DB (others remain 0)
    try:
        from sqlalchemy import text
        result_paused = await session.execute(text("SELECT COUNT(*) as count FROM checks WHERE paused = true"))
        paused_count = result_paused.scalar_one_or_none() or 0
        CHECKS_TOTAL.labels(status='paused').set(paused_count)
        # Optionally seed Redis with paused count so next startup has it
        try:
            async with ephemeral_redis() as r:
                if r:
                    await r.hset("metrics:checks_status_counts", mapping={"paused": paused_count})
        except Exception:
            pass
        logger.info(f"Initialized paused check count from DB: paused={paused_count}; other counts default to 0.")
    except Exception as e:
        logger.error(f"Failed to initialize paused check count from DB: {e}")
