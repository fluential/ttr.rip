import httpx
import logging
import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Check
from app.worker import send_telegram_notification_task
from app.core.config import settings
from app.core import encryption

logger = logging.getLogger(__name__)

def format_duration(seconds: int) -> str:
    """Formats seconds into a human-readable string like '1m 30s'."""
    if seconds < 0:
        return "N/A"
    if seconds < 60:
        return f"{seconds}s"
    
    minutes = seconds // 60
    secs = seconds % 60
    
    return f"{minutes}m {secs}s"

async def send_telegram_notification(db: AsyncSession, check: Check, message: str):
    """Sends a notification to the configured Telegram chat and updates the status."""
    if not all([check.telegram_enabled, check.telegram_bot_token, check.telegram_chat_id]):
        return

    try:
        decrypted_token = encryption.decrypt_token(check.telegram_bot_token)
    except Exception:
        error_message = "Failed to decrypt bot token. Please re-save your settings."
        logger.error(f"Error sending Telegram notification for check '{check.name}' (ID: {check.id}): {error_message}")
        check.telegram_last_notification_status = "error"
        check.telegram_last_notification_message = error_message
        check.telegram_last_notification_timestamp = datetime.now(timezone.utc)
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
    # The calling function is responsible for the commit


def schedule_telegram_notification(check: Check, message: str):
    """Enqueues a task to send a Telegram notification."""
    if not settings.DEBUG_MODE:
        try:
            import redis
            r = redis.from_url(str(settings.REDIS_URL))
            if check.owner_key:
                owner_identifier = check.owner_key
            elif check.owner_id:
                owner_identifier = f"user_id_{check.owner_id}"
            else:
                owner_identifier = None
            
            if owner_identifier:
                r.incr(f"user_stats:queued_notifications:{owner_identifier}")
        except Exception as e:
            logger.error(f"Could not increment queued notification count for check {check.id}: {e}")

    if settings.DEBUG_MODE:
        from app.worker import _send_telegram_notification
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send_telegram_notification(check.id, message))
        except RuntimeError:
            logger.error("Failed to schedule direct telegram notification: no running event loop.")
    else:
        send_telegram_notification_task.delay(check.id, message)
