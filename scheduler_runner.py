import asyncio
import sys
import os
import logging

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from app.services import scheduler
from app.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

async def main():
    await scheduler.check_jobs()

if __name__ == "__main__":
    logger.info("Starting scheduler runner...")
    asyncio.run(main())
