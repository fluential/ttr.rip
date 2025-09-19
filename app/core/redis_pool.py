import logging
import time
import asyncio
from app.core.config import settings
from app import metrics

logger = logging.getLogger(__name__)

# Async Redis
try:
    import redis.asyncio as aioredis
except Exception as e:
    logger.critical(f"Failed to import redis.asyncio: {e}")
    aioredis = None

# Maintain a per-event-loop async Redis connection pool
_pools: dict[int, "aioredis.ConnectionPool"] = {} if aioredis else {}

def _get_or_create_pool():
    """
    Returns a ConnectionPool bound to the current running event loop.
    Prevents 'Future attached to a different loop' errors by avoiding
    cross-loop reuse of a single global pool.
    """
    if aioredis is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Fallback for contexts without a running loop
        loop = asyncio.get_event_loop()
    loop_id = id(loop)

    pool = _pools.get(loop_id)
    if pool is not None:
        return pool

    try:
        pool = aioredis.ConnectionPool.from_url(  # type: ignore[attr-defined]
            str(settings.REDIS_URL),
            decode_responses=True,
            max_connections=50,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            health_check_interval=30,
        )
        _pools[loop_id] = pool
        logger.info("Async Redis connection pool initialized for loop %s", loop)
        return pool
    except Exception as e:
        logger.error(f"Error initializing async Redis connection pool: {e}")
        return None

if aioredis:
    class TimedAsyncRedis(aioredis.Redis):  # type: ignore[misc]
        """An async Redis client that records command execution times."""
        async def execute_command(self, *args, **kwargs):
            start_time = time.perf_counter()
            try:
                return await super().execute_command(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start_time
                metrics.REDIS_COMMAND_DURATION.observe(duration)
else:
    class TimedAsyncRedis:  # Fallback stub to keep typing happy if Redis is unavailable
        pass

def get_redis_connection():
    """
    Returns an async Redis client bound to a pool tied to the current event loop.
    Operations on the returned client must be awaited.
    """
    if aioredis is None:
        logger.error("Async Redis library (redis.asyncio) is not available")
        return None

    pool = _get_or_create_pool()
    if pool is None:
        logger.error("Async Redis connection pool is not initialized for this event loop")
        return None

    return TimedAsyncRedis(connection_pool=pool)
