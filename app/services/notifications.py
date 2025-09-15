import httpx
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Check
from app.worker import send_telegram_notification_task

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

    url = f"https://api.telegram.org/bot{check.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": check.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info(f"Successfully sent Telegram notification for check '{check.name}' (ID: {check.id})")
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


def schedule_telegram_notification(check_id: int, message: str):
    """Enqueues a task to send a Telegram notification."""
    send_telegram_notification_task.delay(check_id, message)
