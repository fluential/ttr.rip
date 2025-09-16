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
    for status in ["up", "down", "new"]:
        CHECKS_TOTAL.labels(status=status).set(0) # Initialize to 0

    # Initialize notification metrics
    for status in ["success", "error"]:
        for ntype in ["telegram"]:
            NOTIFICATIONS_SENT.labels(status=status, type=ntype)

def record_check_update(status: str, previous_status: Optional[str] = None):
    """Record a check status update"""
    CHECKS_TOTAL.labels(status=status).inc()
    if previous_status and previous_status != status:
        CHECKS_TOTAL.labels(status=previous_status).dec()

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
    """Initialize check counts from the database at startup."""
    from sqlalchemy import text
    result = await session.execute(text("""
        SELECT status, COUNT(*) as count 
        FROM checks 
        GROUP BY status
    """))
    counts = {row[0]: row[1] for row in result}
    for status in ["up", "down", "new"]:
        CHECKS_TOTAL.labels(status=status).set(counts.get(status, 0))
