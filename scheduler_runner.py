import asyncio
import sys
import os
import logging
import signal

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
    loop = asyncio.get_event_loop()
    main_task = loop.create_task(main())

    def shutdown_handler(sig):
        logger.info(f"Received signal {sig.name}, initiating shutdown...")
        if not main_task.done():
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler, sig)

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        logger.info("Scheduler main task was cancelled.")
    finally:
        logger.info("Cleaning up tasks and shutting down scheduler runner.")
        tasks = [t for t in asyncio.all_tasks(loop=loop) if t is not main_task and not t.done()]
        for task in tasks:
            task.cancel()
        
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

        loop.close()
        logger.info("Scheduler runner shut down.")
