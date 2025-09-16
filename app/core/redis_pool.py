import redis
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# Create a global Redis connection pool
try:
    redis_pool = redis.ConnectionPool.from_url(
        str(settings.REDIS_URL),
        decode_responses=True,
        max_connections=50,  # Adjust this based on your application needs
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        health_check_interval=30
    )
    logger.info("Redis connection pool initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Redis connection pool: {e}")
    redis_pool = None

def get_redis_connection():
    """
    Returns a Redis connection from the pool.
    This doesn't actually establish a connection until it's used.
    """
    if redis_pool is None:
        logger.error("Redis connection pool is not initialized")
        return None
    
    return redis.Redis(connection_pool=redis_pool)
