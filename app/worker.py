import asyncio
import logging
from celery import Celery
from sqlalchemy.future import select
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
            result = await session.execute(select(Check).filter(Check.id == check_id))
            check = result.scalars().first()

            if not check:
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
