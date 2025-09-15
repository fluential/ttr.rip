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
        import redis
        r = redis.from_url(str(settings.REDIS_URL))
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

            if check.owner_key:
                owner_identifier_for_stats = check.owner_key
            elif check.owner_id:
                owner_identifier_for_stats = f"user_id_{check.owner_id}"

            if not all([check.telegram_enabled, check.telegram_bot_token, check.telegram_chat_id]):
                return

            try:
                decrypted_token = encryption.decrypt_token(check.telegram_bot_token)
            except Exception:
                error_message = "Failed to decrypt bot token. Please re-save your settings."
                logger.error(f"Error sending Telegram notification for check '{check.name}' (ID: {check.id}) in worker: {error_message}")
                check.telegram_last_notification_status = "error"
                check.telegram_last_notification_message = error_message
                check.telegram_last_notification_timestamp = datetime.now(timezone.utc)
                await session.commit()
                return

            url = f"https://api.telegram.org/bot{decrypted_token}/sendMessage"
            payload = {
                "chat_id": check.telegram_chat_id,
                "text": message,
                "disable_web_page_preview": True,
            }

            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(url, json=payload)
                    response_text = response.text
                    response.raise_for_status()
                    logger.info(f"Successfully sent Telegram notification for check '{check.name}' (ID: {check.id}). Response: {response_text}")
                    check.telegram_last_notification_status = "ok"
                    check.telegram_last_notification_message = "Successfully sent."
                except httpx.HTTPStatusError as e:
                    error_message = f"Error: {e.response.status_code} {e.response.text}"
                    logger.error(f"Error sending Telegram notification for check '{check.name}' (ID: {check.id}): {error_message}")
                    check.telegram_last_notification_status = "error"
                    check.telegram_last_notification_message = error_message
                except Exception as e:
                    error_message = f"An unexpected error occurred: {e}"
                    logger.error(f"An unexpected error occurred while sending Telegram notification for check '{check.name}' (ID: {check.id}): {e}", exc_info=True)
                    check.telegram_last_notification_status = "error"
                    check.telegram_last_notification_message = error_message
            
            check.telegram_last_notification_timestamp = datetime.now(timezone.utc)
            await session.commit()

    if owner_identifier_for_stats and not settings.DEBUG_MODE:
        try:
            import redis
            r = redis.from_url(str(settings.REDIS_URL))
            r.incr(f"user_stats:processed_notifications:{owner_identifier_for_stats}")
        except Exception as e:
            logger.error(f"Could not increment processed notification count for check {check_id}: {e}")

@celery_app.task(name="send_telegram_notification_task")
def send_telegram_notification_task(check_id: int, message: str):
    """Celery task wrapper to run the async notification logic."""
    # This task is executed by a Celery worker in a synchronous context.
    # It runs the async notification function in a new event loop.
    asyncio.run(_send_telegram_notification(check_id, message))
