import logging
import time
from app.core.config import settings
from app import metrics

logger = logging.getLogger(__name__)

# Async Redis
try:
    import redis.asyncio as aioredis
except Exception as e:
    logger.critical(f"Failed to import redis.asyncio: {e}")
    aioredis = None

# Create a global async Redis connection pool
try:
    redis_pool = aioredis.ConnectionPool.from_url(  # type: ignore[attr-defined]
        str(settings.REDIS_URL),
        decode_responses=True,
        max_connections=50,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        health_check_interval=30,
    ) if aioredis else None
    if redis_pool:
        logger.info("Async Redis connection pool initialized successfully")
except Exception as e:
    logger.error(f"Error initializing async Redis connection pool: {e}")
    redis_pool = None

class TimedAsyncRedis(aioredis.Redis):  # type: ignore[misc]
    """An async Redis client that records command execution times."""
    async def execute_command(self, *args, **kwargs):
        start_time = time.perf_counter()
        try:
            return await super().execute_command(*args, **kwargs)
        finally:
            duration = time.perf_counter() - start_time
            metrics.REDIS_COMMAND_DURATION.observe(duration)

def get_redis_connection():
    """
    Returns an async Redis client bound to the global pool.
    Operations on the returned client must be awaited.
    """
    if redis_pool is None or aioredis is None:
        logger.error("Async Redis connection pool is not initialized")
        return None
    return TimedAsyncRedis(connection_pool=redis_pool)
