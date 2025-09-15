import logging
from app.worker import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

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

    try:
        # Use redis client to check connection and queue length
        import redis
        r = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
        r.ping()  # Check connection
        queue_name = celery_app.conf.get('task_default_queue', 'celery')
        total_queued = r.llen(queue_name)

        inspector = celery_app.control.inspect(timeout=1)
        stats = inspector.stats()
        if not stats:
            return {
                "broker_status": "Redis (Connected, no workers)",
                "workers_online": 0,
                "total_queued": total_queued,
                "total_active": "N/A",
                "total_reserved": "N/A",
            }

        active = inspector.active()
        reserved = inspector.reserved()

        workers_online = len(stats)
        total_active = sum(len(tasks) for tasks in active.values()) if active else 0
        total_reserved = sum(len(tasks) for tasks in reserved.values()) if reserved else 0

        return {
            "broker_status": "Redis (Connected)",
            "workers_online": workers_online,
            "total_queued": total_queued,
            "total_active": total_active,
            "total_reserved": total_reserved,
        }
    except Exception as e:
        logger.error(f"Could not get queue stats: {e}", exc_info=False)
        return {
            "broker_status": "Redis (Connection failed)",
            "workers_online": "N/A",
            "total_queued": "N/A",
            "total_active": "N/A",
            "total_reserved": "N/A",
        }
