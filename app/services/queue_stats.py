import logging
import time
from app.worker import celery_app
from app.core.config import settings
from app.core.redis_pool import get_redis_connection

logger = logging.getLogger(__name__)

_CACHE = None
_CACHE_TS = 0.0
_CACHE_TTL = 5.0

def get_queue_stats():
    """
    Retrieves statistics about the Celery queue.
    Handles debug mode (eager execution) and production mode (Redis).
    """
    if settings.DEBUG_MODE:
        return {
            "broker_status": "DEBUG (Eager)",
            "workers_online": "N/A (Inline)",
            "total_queued": 0,
            "total_active": 0,
            "total_reserved": 0,
        }

    now = time.time()
    global _CACHE, _CACHE_TS
    if _CACHE and (now - _CACHE_TS) < _CACHE_TTL:
        return _CACHE

    try:
        # Use redis pool to check connection and queue length
        r = get_redis_connection()
        if not r:
            return {
                "broker_status": "Redis (Connection failed)",
                "workers_online": "N/A",
                "total_queued": "N/A",
                "total_active": "N/A",
                "total_reserved": "N/A",
            }
            
        r.ping()  # Check connection
        queue_name = celery_app.conf.get('task_default_queue', 'celery')
        total_queued = r.llen(queue_name)

        inspector = celery_app.control.inspect(timeout=1)
        stats = inspector.stats()
        if not stats:
            _CACHE = {
                "broker_status": "Redis (Connected, no workers)",
                "workers_online": 0,
                "total_queued": total_queued,
                "total_active": "N/A",
                "total_reserved": "N/A",
            }
            _CACHE_TS = time.time()
            return _CACHE

        active = inspector.active()
        reserved = inspector.reserved()

        workers_online = len(stats)
        total_active = sum(len(tasks) for tasks in active.values()) if active else 0
        total_reserved = sum(len(tasks) for tasks in reserved.values()) if reserved else 0

        _CACHE = {
            "broker_status": "Redis (Connected)",
            "workers_online": workers_online,
            "total_queued": total_queued,
            "total_active": total_active,
            "total_reserved": total_reserved,
        }
        _CACHE_TS = time.time()
        return _CACHE
    except Exception as e:
        logger.error(f"Could not get queue stats: {e}", exc_info=False)
        return {
            "broker_status": "Redis (Connection failed)",
            "workers_online": "N/A",
            "total_queued": "N/A",
            "total_active": "N/A",
            "total_reserved": "N/A",
        }
