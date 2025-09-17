import asyncio
import logging
import sys
import os

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.redis_pool import get_redis_connection
from app.core.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

async def main():
    """
    Connects to Redis and clears all keys used by the application.
    """
    logger.info("Connecting to Redis...")
    try:
        r = get_redis_connection()
        if not r:
            logger.error("Could not get Redis connection. Aborting.")
            return
        r.ping()
        logger.info("Successfully connected to Redis.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return

    key_patterns = [
        "check_runtime:*",
        "ping_logs:*",
        "check_content:*",
        "user_stats:counters:*",
        "user_stats:queued_notifications:*",
        "celery-task-meta-*", # Celery result backend keys
    ]
    
    # These are specific keys, not patterns
    specific_keys = [
        "rtt_celery_queue"
    ]

    total_deleted_count = 0

    for pattern in key_patterns:
        logger.info(f"Scanning for keys matching pattern: {pattern}")
        # scan_iter returns bytes, so we decode for logging
        keys_to_delete = [key for key in r.scan_iter(match=pattern)]
        
        if keys_to_delete:
            logger.info(f"Found {len(keys_to_delete)} keys to delete for pattern '{pattern}'. Deleting...")
            deleted_count = r.delete(*keys_to_delete)
            total_deleted_count += deleted_count
            logger.info(f"Deleted {deleted_count} keys.")
        else:
            logger.info(f"No keys found for pattern '{pattern}'.")

    if specific_keys:
        logger.info(f"Deleting specific keys: {specific_keys}")
        # Ensure keys are bytes if they exist
        existing_specific_keys = [k for k in specific_keys if r.exists(k)]
        if existing_specific_keys:
            deleted_count = r.delete(*existing_specific_keys)
            total_deleted_count += deleted_count
            logger.info(f"Deleted {deleted_count} keys.")
        else:
            logger.info("No specific keys found to delete.")


    logger.info(f"\nTotal keys deleted: {total_deleted_count}")
    logger.info("Redis cleanup complete.")

if __name__ == "__main__":
    asyncio.run(main())
