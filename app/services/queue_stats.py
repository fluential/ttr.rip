import logging
import time
import json
import threading
from app.worker import celery_app
from app.core.config import settings
from app.core.redis_pool import get_redis_connection

logger = logging.getLogger(__name__)

_CACHE = None
_CACHE_TS = 0.0
_CACHE_TTL = 60.0

# Redis cache key/TTL
_REDIS_KEY = "cache:queue_stats:latest"
_REDIS_TTL = 65  # seconds

def get_queue_stats():
    """
    Fast path: read cached snapshot (Redis first, in-memory fallback).
    No Celery inspector calls on the request path.
    """
    if settings.DEBUG_MODE:
        return {
            "broker_status": "DEBUG (Eager)",
            "workers_online": "N/A (Inline)",
            "total_queued": 0,
            "total_active": 0,
            "total_reserved": 0,
        }

    # Try Redis cache first
    try:
        r = get_redis_connection()
        if r:
            raw = r.get(_REDIS_KEY)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                return json.loads(raw)
    except Exception as e:
        logger.debug(f"Redis queue stats cache read failed: {e}", exc_info=False)

    # Fallback to in-process cache if fresh
    now = time.time()
    if _CACHE and (now - _CACHE_TS) < _CACHE_TTL:
        return _CACHE

    # Last resort: degraded response (non-blocking)
    return {
        "broker_status": "Redis (Unknown)",
        "workers_online": "N/A",
        "total_queued": "N/A",
        "total_active": "N/A",
        "total_reserved": "N/A",
    }

def _store_cache(data: dict):
    global _CACHE, _CACHE_TS
    _CACHE = data
    _CACHE_TS = time.time()
    try:
        r = get_redis_connection()
        if r:
            r.setex(_REDIS_KEY, _REDIS_TTL, json.dumps(data))
    except Exception as e:
        logger.warning(f"Could not write queue stats to Redis: {e}", exc_info=False)

def refresh_queue_stats_cache():
    """
    Runs the slow/expensive inspector calls and stores a snapshot in Redis for quick reads.
    Intended to be called from a background thread/task, not the request path.
    """
    if settings.DEBUG_MODE:
        snapshot = {
            "broker_status": "DEBUG (Eager)",
            "workers_online": "N/A (Inline)",
            "total_queued": 0,
            "total_active": 0,
            "total_reserved": 0,
        }
        _store_cache(snapshot)
        return snapshot

    try:
        r = get_redis_connection()
        if not r:
            snapshot = {
                "broker_status": "Redis (Connection failed)",
                "workers_online": "N/A",
                "total_queued": "N/A",
                "total_active": "N/A",
                "total_reserved": "N/A",
            }
            _store_cache(snapshot)
            return snapshot

        # Broker connectivity and queue length
        r.ping()
        queue_name = celery_app.conf.get('task_default_queue', 'celery')
        total_queued = r.llen(queue_name)

        inspector = celery_app.control.inspect(timeout=1)
        stats = inspector.stats()
        if not stats:
            snapshot = {
                "broker_status": "Redis (Connected, no workers)",
                "workers_online": 0,
                "total_queued": total_queued,
                "total_active": "N/A",
                "total_reserved": "N/A",
            }
            _store_cache(snapshot)
            return snapshot

        active = inspector.active()
        reserved = inspector.reserved()
        workers_online = len(stats)
        total_active = sum(len(tasks) for tasks in active.values()) if active else 0
        total_reserved = sum(len(tasks) for tasks in reserved.values()) if reserved else 0

        snapshot = {
            "broker_status": "Redis (Connected)",
            "workers_online": workers_online,
            "total_queued": total_queued,
            "total_active": total_active,
            "total_reserved": total_reserved,
        }
        _store_cache(snapshot)
        return snapshot
    except Exception as e:
        logger.error(f"refresh_queue_stats_cache failed: {e}", exc_info=False)
        snapshot = {
            "broker_status": "Redis (Connection failed)",
            "workers_online": "N/A",
            "total_queued": "N/A",
            "total_active": "N/A",
            "total_reserved": "N/A",
        }
        _store_cache(snapshot)
        return snapshot

def _bg_loop():
    # Refresh once per minute
    interval = 60.0
    while True:
        try:
            refresh_queue_stats_cache()
        except Exception as e:
            logger.error(f"Queue stats background refresh failed: {e}", exc_info=False)
        time.sleep(interval)

# Start the background refresher in the web process (no-op in DEBUG)
if not settings.DEBUG_MODE:
    try:
        _t = threading.Thread(target=_bg_loop, name="queue-stats-refresher", daemon=True)
        _t.start()
    except Exception as e:
        logger.error(f"Failed to start queue stats background thread: {e}", exc_info=False)
