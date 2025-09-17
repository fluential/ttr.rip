import asyncio
import logging
from celery import Celery
from sqlalchemy.future import select
from sqlalchemy import text
from sqlalchemy.orm import selectinload
import httpx
from datetime import datetime, timezone

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.db.models import Check
from app.core.logging_config import setup_logging
from app.core import encryption
from app import metrics
from app.core.redis_pool import get_redis_connection
from app.services.notifications import (
    _execute_telegram_send, _execute_slack_send, 
    _execute_discord_send, _execute_webhook_send
)

setup_logging()
logger = logging.getLogger(__name__)

celery_app = Celery(
    "worker",
    broker=str(settings.REDIS_URL),
    backend=str(settings.REDIS_URL)
)
celery_app.conf.update(
    task_track_started=True,
    task_always_eager=settings.DEBUG_MODE,
    task_default_queue='rtt_celery_queue',
    beat_schedule={
        'check-overdue-jobs-every-5-seconds': {
            'task': 'app.worker.check_overdue_jobs_task',
            'schedule': 5.0,
        },
    },
)

if settings.DEBUG_MODE:
    logger.info("DEBUG_MODE is on. Celery will run tasks eagerly without a broker.")
else:
    try:
        r = get_redis_connection()
        if r:
            r.ping()
            logger.info("Celery worker successfully connected to Redis.")
    except Exception as e:
        logger.error(f"Celery worker failed to connect to Redis: {e}. Tasks may not be processed.")


async def _send_notification(check_id: int, message: str, send_function):
    """Generic async logic for sending a notification and updating the DB."""
    owner_identifier_for_stats = None
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                select(Check)
                .options(selectinload(Check.owner))
                .filter(Check.id == check_id)
            )
            check = result.scalars().first()

            if not check:
                logger.warning(f"Could not find check ID {check_id} for sending notification.")
                return

            owner_identifier_for_stats = f"user_id_{check.owner_id}"

            if owner_identifier_for_stats and not settings.DEBUG_MODE:
                try:
                    r = get_redis_connection()
                    if r:
                        r.decr(f"user_stats:queued_notifications:{owner_identifier_for_stats}")
                except Exception as e:
                    logger.error(f"Could not decrement queued notification count for check {check_id}: {e}")

            await send_function(check, message)
            await session.commit()

async def _send_telegram_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_telegram_send)

async def _send_slack_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_slack_send)

async def _send_discord_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_discord_send)

async def _send_webhook_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_webhook_send)

@celery_app.task(name="send_telegram_notification_task")
def send_telegram_notification_task(check_id: int, message: str):
    asyncio.run(_send_telegram_notification(check_id, message))

@celery_app.task(name="send_slack_notification_task")
def send_slack_notification_task(check_id: int, message: str):
    asyncio.run(_send_slack_notification(check_id, message))

@celery_app.task(name="send_discord_notification_task")
def send_discord_notification_task(check_id: int, message: str):
    asyncio.run(_send_discord_notification(check_id, message))

@celery_app.task(name="send_webhook_notification_task")
def send_webhook_notification_task(check_id: int, message: str):
    asyncio.run(_send_webhook_notification(check_id, message))


async def _check_overdue_jobs():
    """The core async logic for finding and processing overdue checks."""
    from app.services import notifications
    from app.crud import _update_redis_stats_counters

    now_utc = datetime.now(timezone.utc)
    logger.info("Scheduler task running check cycle...")
    
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # --- Find overdue checks ---
            overdue_query = select(Check).where(
                Check.status.in_(["up", "new"]),
                Check.paused == False,
                Check.deadline < now_utc
            )
            result = await session.execute(overdue_query)
            overdue_checks = result.scalars().all()

            if overdue_checks:
                logger.info(f"Scheduler found {len(overdue_checks)} overdue checks.")
                for check in overdue_checks:
                    if check.status != "down":
                        old_status = check.status
                        logger.info(f"Check '{check.name}' (ID: {check.id}) is DOWN (overdue).")
                        check.status = "down"
                        message = f"🔴 Check Down: [{check.name}] is overdue."
                        notifications.schedule_all_notifications(check, message)
                        _update_redis_stats_counters(check.owner_id, old_status, "down")
            else:
                logger.info("Scheduler found no overdue checks.")

            # --- Find checks that exceeded max runtime ---
            runtime_query = select(Check).where(
                Check.last_start.isnot(None),
                Check.max_runtime_seconds.isnot(None),
                Check.paused == False,
                Check.last_start < now_utc - text("max_runtime_seconds * '1 second'::interval")
            )
            # SQLite version for compatibility
            if "sqlite" in settings.DATABASE_URL:
                runtime_query = select(Check).where(
                    Check.last_start.isnot(None),
                    Check.max_runtime_seconds.isnot(None),
                    Check.paused == False
                ).where(
                    text("julianday(:now) - julianday(last_start) > max_runtime_seconds / 86400.0")
                )
            
            result = await session.execute(runtime_query, {"now": now_utc})
            long_running_checks = result.scalars().all()

            if long_running_checks:
                logger.info(f"Scheduler found {len(long_running_checks)} long-running checks.")
                for check in long_running_checks:
                    if check.status != "down":
                        old_status = check.status
                        logger.info(f"Check '{check.name}' (ID: {check.id}) is DOWN (exceeded max runtime).")
                        check.status = "down"
                        check.last_start = None # Clear start time to prevent re-triggering
                        message = f"🔴 Check Down: [{check.name}] exceeded its max runtime of {check.max_runtime_seconds}s."
                        notifications.schedule_all_notifications(check, message)
                        _update_redis_stats_counters(check.owner_id, old_status, "down")
            else:
                logger.info("Scheduler found no long-running checks.")

            await session.commit()


@celery_app.task(name="app.worker.check_overdue_jobs_task")
def check_overdue_jobs_task():
    """Celery task wrapper to run the async scheduler logic."""
    asyncio.run(_check_overdue_jobs())
