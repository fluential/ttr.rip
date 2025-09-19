import logging
import time
import asyncio
from contextlib import asynccontextmanager
from app.core.config import settings

logger = logging.getLogger(__name__)

# Async Redis
try:
    import redis.asyncio as aioredis
except Exception as e:
    logger.critical(f"Failed to import redis.asyncio: {e}")
    aioredis = None

# Single, process-wide async Redis pool/client initialized at app startup
_redis_pool: "aioredis.ConnectionPool | None" = None if aioredis else None
_redis_client: "TimedAsyncRedis | None" = None

if aioredis:
    class TimedAsyncRedis(aioredis.Redis):  # type: ignore[misc]
        """An async Redis client that records command execution times."""
        async def execute_command(self, *args, **kwargs):
            start_time = time.perf_counter()
            try:
                return await super().execute_command(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start_time
                try:
                    from app import metrics as _metrics
                    _metrics.REDIS_COMMAND_DURATION.observe(duration)
                except Exception:
                    # Avoid import-time cycles; metrics is optional here
                    pass
else:
    class TimedAsyncRedis:  # Fallback stub to keep typing happy if Redis is unavailable
        pass

@asynccontextmanager
async def ephemeral_redis():
    """
    Async context manager that yields a short-lived Redis client bound to the current event loop.
    Useful during application startup or one-off tasks to avoid cross-loop reuse.
    """
    if aioredis is None:
        yield None
        return
    pool = None
    client = None
    try:
        pool = aioredis.ConnectionPool.from_url(
            str(settings.REDIS_URL),
            decode_responses=True,
            max_connections=50,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            health_check_interval=30,
        )
        client = TimedAsyncRedis(connection_pool=pool)
        yield client
    finally:
        try:
            if client is not None:
                res = client.close()
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass
        try:
            if pool is not None:
                res = pool.disconnect()
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

def _init_redis_client_if_needed() -> bool:
    """
    Lazily initialize the Redis pool/client for non-FastAPI contexts
    (e.g., Celery worker) where init_redis_for_app() is not called.
    """
    global _redis_pool, _redis_client
    if aioredis is None:
        return False
    if _redis_client is not None:
        return True
    try:
        _redis_pool = aioredis.ConnectionPool.from_url(
            str(settings.REDIS_URL),
            decode_responses=True,
            max_connections=50,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            health_check_interval=30,
        )
        _redis_client = TimedAsyncRedis(connection_pool=_redis_pool)
        logger.info("Async Redis connection pool initialized (lazy)")
        return True
    except Exception as e:
        logger.error(f"Error initializing async Redis connection pool: {e}")
        _redis_client = None
        _redis_pool = None
        return False

def get_redis_connection():
    """
    Returns the process-wide async Redis client. If not yet initialized,
    attempts a lazy initialization (for non-FastAPI contexts like Celery).
    Operations on the returned client must be awaited.
    """
    if aioredis is None:
        logger.error("Async Redis library (redis.asyncio) is not available")
        return None
    if _redis_client is None:
        _init_redis_client_if_needed()
    if _redis_client is None:
        logger.error("Async Redis client is not initialized.")
        return None
    return _redis_client

async def init_redis_for_app(app):
    """
    Create and attach a single Redis pool/client to the FastAPI app state.
    Should be called during application startup on the serving event loop.
    """
    if aioredis is None:
        logger.error("Async Redis library (redis.asyncio) is not available")
        return False

    ok = _init_redis_client_if_needed()
    if not ok:
        return False

    try:
        app.state.redis = _redis_client
        app.state.redis_pool = _redis_pool
    except Exception:
        pass
    logger.info("Async Redis connection pool initialized")
    return True

async def close_redis_for_app(app):
    """
    Close the Redis client and disconnect the pool, and clear from app.state.
    Safe to call multiple times.
    """
    global _redis_pool, _redis_client
    try:
        if _redis_client is not None:
            res = _redis_client.close()
            if asyncio.iscoroutine(res):
                await res
    except Exception as e:
        logger.debug(f"Error closing Redis client: {e}")
    try:
        if _redis_pool is not None:
            res = _redis_pool.disconnect()
            if asyncio.iscoroutine(res):
                await res
    except Exception as e:
        logger.debug(f"Error disconnecting Redis pool: {e}")
    try:
        if hasattr(app.state, "redis"):
            delattr(app.state, "redis")
        if hasattr(app.state, "redis_pool"):
            delattr(app.state, "redis_pool")
    except Exception:
        pass
    _redis_client = None
    _redis_pool = None
