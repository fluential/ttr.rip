import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.future import select
from sqlalchemy import text
from app.db.base import AsyncSessionLocal
from app.db.models import Check
from app.services import notifications
from app.core.config import settings

logger = logging.getLogger(__name__)

async def check_jobs():
    logger.info(f"Scheduler started. Checking for overdue jobs every {settings.SCHEDULER_INTERVAL_SECONDS} seconds.")
    while True:
        await asyncio.sleep(settings.SCHEDULER_INTERVAL_SECONDS)
        now_utc = datetime.now(timezone.utc)
        logger.info("Scheduler running check cycle...")
        
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # This query is much more efficient. It lets the database do the work of finding
                # overdue checks, instead of loading all checks into memory.
                # It constructs a deadline for each check based on its own interval and grace period.
                # The `text()` clause is used for database-agnostic interval arithmetic.
                query = select(Check).where(
                    Check.status.in_(["up", "new"]),
                    Check.last_ping < now_utc - text("interval_seconds * '1 second'::interval") - text("grace_seconds * '1 second'::interval")
                )
                # SQLite version for compatibility
                if "sqlite" in settings.DATABASE_URL:
                     query = select(Check).where(
                        Check.status.in_(["up", "new"])
                    ).where(
                        # julianday is specific to SQLite
                        text("julianday(:now) - julianday(last_ping) > (interval_seconds + grace_seconds) / 86400.0")
                    )

                result = await session.execute(query, {"now": now_utc})
                overdue_checks = result.scalars().all()

                if overdue_checks:
                    logger.info(f"Scheduler found {len(overdue_checks)} overdue checks.")
                    for check in overdue_checks:
                        if check.status != "down":
                            logger.info(f"Check '{check.name}' (ID: {check.id}) is DOWN.")
                            check.status = "down"
                            message = f"🔴 Check Down: [{check.name}] is overdue."
                            notifications.schedule_telegram_notification(check, message)
                else:
                    logger.info("Scheduler found no overdue checks.")

                await session.commit()
