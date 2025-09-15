import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.future import select
from app.db.base import AsyncSessionLocal
from app.db.models import Check
from app.services import notifications
from app.core.config import settings

logger = logging.getLogger(__name__)

async def check_jobs():
    logger.info(f"Scheduler started. Checking for overdue jobs every {settings.SCHEDULER_INTERVAL_SECONDS} seconds.")
    while True:
        await asyncio.sleep(settings.SCHEDULER_INTERVAL_SECONDS)
        now = datetime.now(timezone.utc)
        logger.info("Scheduler running check cycle...")
        
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(Check).where(Check.status.in_(["up", "new"]))
                )
                checks_to_verify = result.scalars().all()
                logger.info(f"Scheduler found {len(checks_to_verify)} active checks to verify.")

                for check in checks_to_verify:
                    # The reference time for the deadline is the last ping if it exists,
                    # otherwise it's the time the check was created.
                    reference_time = check.last_ping if check.last_ping else check.created_at
                    
                    if reference_time:
                        if reference_time.tzinfo is None:
                            reference_time = reference_time.replace(tzinfo=timezone.utc)
                        deadline = reference_time + timedelta(seconds=check.interval_seconds + check.grace_seconds)
                        if now > deadline:
                            if check.status != "down":
                                logger.info(f"Check '{check.name}' (ID: {check.id}) is DOWN.")
                                check.status = "down"
                                message = f"🔴 Check Down: [{check.name}] is overdue."
                                notifications.schedule_telegram_notification(check.id, message)
                
                await session.commit()
