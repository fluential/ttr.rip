#!/usr/bin/env python
import asyncio
import sys
import os
import logging

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.base import AsyncSessionLocal
from app.schemas import UserCreate
from app.crud import create_user, get_user_by_username
from app.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

async def main():
    logger.info("Creating admin user...")
    async with AsyncSessionLocal() as session:
        user = await get_user_by_username(session, username="admin")
        if user:
            logger.info("Admin user already exists.")
        else:
            user_in = UserCreate(username="admin", password="password", is_admin=True)
            await create_user(session, user_in)
            logger.info("Admin user created successfully.")
            logger.info("Username: admin")
            logger.info("Password: password")

if __name__ == "__main__":
    asyncio.run(main())
