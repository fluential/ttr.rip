import asyncio
import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from app.services import scheduler

async def main():
    await scheduler.check_jobs()

if __name__ == "__main__":
    print("Starting scheduler runner...")
    asyncio.run(main())
