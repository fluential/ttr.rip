import asyncio
import logging
from celery import Celery
from sqlalchemy.future import select
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
from app.services.notifications import _execute_telegram_send

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


async def _send_telegram_notification(check_id: int, message: str):
    """The core async logic for sending a notification and updating the DB."""
    owner_identifier_for_stats = None
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Eagerly load the owner to get access to the auth_key for decryption
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

            # Call the centralized sending logic
            await _execute_telegram_send(check, message)
            
            await session.commit()


@celery_app.task(name="send_telegram_notification_task")
def send_telegram_notification_task(check_id: int, message: str):
    """Celery task wrapper to run the async notification logic."""
    # This task is executed by a Celery worker in a synchronous context.
    # It runs the async notification function in a new event loop.
    asyncio.run(_send_telegram_notification(check_id, message))


async def _check_overdue_jobs():
    """The core async logic for finding and processing overdue checks."""
    from app.services import notifications
    from app.crud import _invalidate_user_stats_cache

    now_utc = datetime.now(timezone.utc)
    logger.info("Scheduler task running check cycle...")
    
    async with AsyncSessionLocal() as session:
        async with session.begin():
            query = select(Check).where(
                Check.status.in_(["up", "new"]),
                Check.deadline < now_utc
            )
            
            result = await session.execute(query)
            overdue_checks = result.scalars().all()

            if overdue_checks:
                logger.info(f"Scheduler found {len(overdue_checks)} overdue checks.")
                owner_ids_to_invalidate = set()
                for check in overdue_checks:
                    if check.status != "down":
                        logger.info(f"Check '{check.name}' (ID: {check.id}) is DOWN.")
                        check.status = "down"
                        owner_ids_to_invalidate.add(check.owner_id)
                        message = f"🔴 Check Down: [{check.name}] is overdue."
                        notifications.schedule_telegram_notification(check, message)
                
                await session.commit()

                # Invalidate caches after commit
                for owner_id in owner_ids_to_invalidate:
                    await _invalidate_user_stats_cache(owner_id)
            else:
                logger.info("Scheduler found no overdue checks.")


@celery_app.task(name="app.worker.check_overdue_jobs_task")
def check_overdue_jobs_task():
    """Celery task wrapper to run the async scheduler logic."""
    asyncio.run(_check_overdue_jobs())
