#!/usr/bin/env python
import asyncio
import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.base import AsyncSessionLocal
from app.schemas import UserCreate
from app.crud import create_user, get_user_by_username

async def main():
    print("Creating admin user...")
    async with AsyncSessionLocal() as session:
        user = await get_user_by_username(session, username="admin")
        if user:
            print("Admin user already exists.")
        else:
            user_in = UserCreate(username="admin", password="password")
            await create_user(session, user_in)
            print("Admin user created successfully.")
            print("Username: admin")
            print("Password: password")

if __name__ == "__main__":
    asyncio.run(main())
