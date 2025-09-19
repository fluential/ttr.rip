import httpx
import logging
import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Check
from app.core.config import settings
from app.core import encryption
from app import metrics
from app.core.redis_pool import get_redis_connection

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

async def _execute_telegram_send(check: Check, message: str):
    """
    Core logic to send a Telegram notification.
    This function handles token decryption, API request, and updates the check object in-memory.
    The caller is responsible for database session management (commit).
    """
    if not all([check.telegram_enabled, check.telegram_bot_token, check.telegram_chat_id]):
        return

    if not check.owner or not check.owner.auth_key:
        logger.error(f"Cannot send notification for check {check.id}: owner or owner auth_key not loaded.")
        return

    try:
        decrypted_token = encryption.decrypt_token(check.telegram_bot_token, check.owner.auth_key)
    except Exception:
        error_message = "Failed to decrypt bot token. Please re-save your settings."
        logger.error(f"Error sending Telegram notification for check '{check.name}' (ID: {check.id}): {error_message}")
        check.telegram_last_notification_status = "error"
        check.telegram_last_notification_message = error_message
        metrics.record_notification_sent("telegram", "error")
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
            metrics.record_notification_sent("telegram", "success")
        except httpx.HTTPStatusError as e:
            error_message = f"Error: {e.response.status_code} {e.response.text}"
            logger.error(f"Error sending Telegram notification for check '{check.name}' (ID: {check.id}): {error_message}")
            check.telegram_last_notification_status = "error"
            check.telegram_last_notification_message = error_message
            metrics.record_notification_sent("telegram", "error")
        except Exception as e:
            error_message = f"An unexpected error occurred: {e}"
            logger.error(f"An unexpected error occurred while sending Telegram notification for check '{check.name}' (ID: {check.id}): {e}", exc_info=True)
            check.telegram_last_notification_status = "error"
            check.telegram_last_notification_message = error_message
            metrics.record_notification_sent("telegram", "error")
        finally:
            check.telegram_last_notification_timestamp = datetime.now(timezone.utc)


async def _execute_slack_send(check: Check, message: str):
    if not all([check.slack_enabled, check.slack_webhook_url]): return
    if not check.owner or not check.owner.auth_key: return
    try:
        decrypted_url = encryption.decrypt_token(check.slack_webhook_url, check.owner.auth_key)
        payload = {"text": message}
        async with httpx.AsyncClient() as client:
            response = await client.post(decrypted_url, json=payload)
            response.raise_for_status()
        check.slack_last_notification_status = "ok"
        check.slack_last_notification_message = "Successfully sent."
        metrics.record_notification_sent("slack", "success")
    except Exception as e:
        error_message = f"An unexpected error occurred: {e}"
        if isinstance(e, httpx.HTTPStatusError):
            error_message = f"Error: {e.response.status_code} {e.response.text}"
        check.slack_last_notification_status = "error"
        check.slack_last_notification_message = error_message
        metrics.record_notification_sent("slack", "error")
    finally:
        check.slack_last_notification_timestamp = datetime.now(timezone.utc)


async def _execute_discord_send(check: Check, message: str):
    if not all([check.discord_enabled, check.discord_webhook_url]): return
    if not check.owner or not check.owner.auth_key: return
    try:
        decrypted_url = encryption.decrypt_token(check.discord_webhook_url, check.owner.auth_key)
        payload = {"content": message}
        async with httpx.AsyncClient() as client:
            response = await client.post(decrypted_url, json=payload)
            response.raise_for_status()
        check.discord_last_notification_status = "ok"
        check.discord_last_notification_message = "Successfully sent."
        metrics.record_notification_sent("discord", "success")
    except Exception as e:
        error_message = f"An unexpected error occurred: {e}"
        if isinstance(e, httpx.HTTPStatusError):
            error_message = f"Error: {e.response.status_code} {e.response.text}"
        check.discord_last_notification_status = "error"
        check.discord_last_notification_message = error_message
        metrics.record_notification_sent("discord", "error")
    finally:
        check.discord_last_notification_timestamp = datetime.now(timezone.utc)


async def _execute_webhook_send(check: Check, message: str):
    if not all([check.webhook_enabled, check.webhook_url]): return
    if not check.owner or not check.owner.auth_key: return
    try:
        decrypted_url = encryption.decrypt_token(check.webhook_url, check.owner.auth_key)
        payload = {
            "check_id": check.id,
            "check_name": check.name,
            "status": check.status,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(decrypted_url, json=payload)
            response.raise_for_status()
        check.webhook_last_notification_status = "ok"
        check.webhook_last_notification_message = "Successfully sent."
        metrics.record_notification_sent("webhook", "success")
    except Exception as e:
        error_message = f"An unexpected error occurred: {e}"
        if isinstance(e, httpx.HTTPStatusError):
            error_message = f"Error: {e.response.status_code} {e.response.text}"
        check.webhook_last_notification_status = "error"
        check.webhook_last_notification_message = error_message
        metrics.record_notification_sent("webhook", "error")
    finally:
        check.webhook_last_notification_timestamp = datetime.now(timezone.utc)


async def send_telegram_notification(db: AsyncSession, check: Check, message: str):
    """Sends a notification to the configured Telegram chat and updates the status."""
    await _execute_telegram_send(check, message)

async def send_slack_notification(db: AsyncSession, check: Check, message: str):
    await _execute_slack_send(check, message)

async def send_discord_notification(db: AsyncSession, check: Check, message: str):
    await _execute_discord_send(check, message)

async def send_webhook_notification(db: AsyncSession, check: Check, message: str):
    await _execute_webhook_send(check, message)


async def _schedule_notification(check: Check, message: str, task_func, async_func):
    """Generic helper to enqueue a notification task."""
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                owner_identifier = f"user_id_{check.owner_id}"
                await r.incr(f"user_stats:queued_notifications:{owner_identifier}")
        except Exception as e:
            logger.error(f"Could not increment queued notification count for check {check.id}: {e}")

    if settings.DEBUG_MODE:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(async_func(check.id, message))
        except RuntimeError:
            logger.error(f"Failed to schedule direct notification for {async_func.__name__}: no running event loop.")
    else:
        task_func.delay(check.id, message)


async def schedule_all_notifications(check: Check, message: str):
    """Schedules all enabled notifications for a check."""
    from app.worker import (
        _send_telegram_notification, send_telegram_notification_task,
        _send_slack_notification, send_slack_notification_task,
        _send_discord_notification, send_discord_notification_task,
        _send_webhook_notification, send_webhook_notification_task
    )
    if check.telegram_enabled:
        await _schedule_notification(check, message, send_telegram_notification_task, _send_telegram_notification)
    if check.slack_enabled:
        await _schedule_notification(check, message, send_slack_notification_task, _send_slack_notification)
    if check.discord_enabled:
        await _schedule_notification(check, message, send_discord_notification_task, _send_discord_notification)
    if check.webhook_enabled:
        await _schedule_notification(check, message, send_webhook_notification_task, _send_webhook_notification)


async def schedule_telegram_notification(check: Check, message: str):
    from app.worker import _send_telegram_notification, send_telegram_notification_task
    await _schedule_notification(check, message, send_telegram_notification_task, _send_telegram_notification)

async def schedule_slack_notification(check: Check, message: str):
    from app.worker import _send_slack_notification, send_slack_notification_task
    await _schedule_notification(check, message, send_slack_notification_task, _send_slack_notification)

async def schedule_discord_notification(check: Check, message: str):
    from app.worker import _send_discord_notification, send_discord_notification_task
    await _schedule_notification(check, message, send_discord_notification_task, _send_discord_notification)

async def schedule_webhook_notification(check: Check, message: str):
    from app.worker import _send_webhook_notification, send_webhook_notification_task
    await _schedule_notification(check, message, send_webhook_notification_task, _send_webhook_notification)
