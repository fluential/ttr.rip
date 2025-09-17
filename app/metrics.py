import time
from typing import Dict, Optional, Set
from prometheus_client import Counter, Gauge, Histogram, Info
import prometheus_client

# Define metrics
CHECKS_TOTAL = Gauge("ttl_checks_total", "Total number of checks", ["status"])
CHECKS_DURATION = Histogram("ttl_check_duration_seconds", "Duration of check executions in seconds", buckets=(1, 5, 10, 30, 60, 300, 600, 1800, 3600))
NOTIFICATIONS_SENT = Counter("ttl_notifications_sent_total", "Total number of notifications sent", ["status", "type"])
API_REQUESTS = Counter("ttl_api_requests_total", "Total number of API requests", ["method", "endpoint", "status"])
API_REQUEST_DURATION = Histogram("ttl_api_request_duration_seconds", "Duration of API requests in seconds", buckets=(0.01, 0.05, 0.1, 0.5, 1, 5))
DB_QUERY_DURATION = Histogram("ttl_db_query_duration_seconds", "Duration of database queries in seconds", buckets=(0.01, 0.05, 0.1, 0.5, 1, 5))
REDIS_COMMAND_DURATION = Histogram("ttl_redis_command_duration_seconds", "Duration of Redis commands in seconds", buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5))
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

def get_metrics():
    """Get metrics in Prometheus format"""
    return prometheus_client.generate_latest()

async def initialize_check_counts(session):
    """Initialize check counts from Redis and the database at startup."""
    from app.core.redis_pool import get_redis_connection
    from sqlalchemy import text
    import logging

    # Initialize all to 0 first
    for status in ["up", "down", "new", "paused"]:
        CHECKS_TOTAL.labels(status=status).set(0)

    # Count paused checks from the database
    try:
        result_paused = await session.execute(text("SELECT COUNT(*) as count FROM checks WHERE paused = true"))
        paused_count = result_paused.scalar_one_or_none() or 0
        CHECKS_TOTAL.labels(status='paused').set(paused_count)
    except Exception as e:
        logging.error(f"Failed to initialize paused check count from DB: {e}")

    # Count active checks from Redis
    try:
        r = get_redis_connection()
        if r:
            logging.info("Initializing active check counts from Redis...")
            status_counts = {"up": 0, "down": 0, "new": 0}
            # Note: SCAN can be slow on large databases. This is a startup-only task.
            for key in r.scan_iter(match='check_runtime:*', count=1000):
                status = r.hget(key, "status")
                if status in status_counts:
                    status_counts[status] += 1
            
            for status, count in status_counts.items():
                if count > 0:
                    CHECKS_TOTAL.labels(status=status).set(count)
            logging.info("Finished initializing active check counts from Redis.")
    except Exception as e:
        logging.error(f"Failed to initialize check counts from Redis: {e}")
