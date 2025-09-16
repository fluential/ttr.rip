#!/usr/bin/env python
import asyncio
import logging
import sys
import os

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.tasks.cleanup import run_manual_cleanup
from app.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Running manual cleanup")
    asyncio.run(run_manual_cleanup())
