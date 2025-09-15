import asyncio
import sys
import os
import logging

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from app.services import scheduler
from app.core.logging_config import setup_logging
from app.core.config import settings

setup_logging()
logger = logging.getLogger(__name__)

async def main():
    if not settings.DEBUG_MODE:
        try:
            import redis
            r = redis.from_url(str(settings.REDIS_URL))
            r.ping()
            logger.info("Scheduler successfully connected to Redis.")
        except Exception as e:
            logger.error(f"Scheduler failed to connect to Redis: {e}. Notifications may not be sent.")

    await scheduler.check_jobs()

if __name__ == "__main__":
    logger.info("Starting scheduler runner...")
    asyncio.run(main())
