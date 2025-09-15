import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy.future import select
from app.db.base import AsyncSessionLocal
from app.db.models import Check

async def check_jobs():
    print("Scheduler started. Checking for overdue jobs every 60 seconds.")
    while True:
        await asyncio.sleep(60)
        now = datetime.now(timezone.utc)
        
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(Check).where(Check.status == "up")
                )
                checks_to_verify = result.scalars().all()

                for check in checks_to_verify:
                    if check.last_ping:
                        deadline = check.last_ping + timedelta(seconds=check.interval_seconds + check.grace_seconds)
                        if now > deadline:
                            print(f"Check '{check.name}' (ID: {check.id}) is DOWN.")
                            check.status = "down"
                
                await session.commit()
