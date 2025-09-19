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
import time
import hashlib
from urllib.parse import urlparse
from app.services import rate_control

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
    Core logic to send a Telegram notification with adaptive rate control.
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

    # Derive per-bot identity (sha256 token truncated)
    identity = hashlib.sha256(decrypted_token.encode("utf-8")).hexdigest()[:10]

    # Global adaptive reserve
    ok, retry_after, _ = await rate_control.reserve("telegram", identity)
    if not ok:
        await rate_control.report("telegram", identity, "rate_limited", retry_after=retry_after)
        raise rate_control.RateLimitedError(retry_after)

    url = f"https://api.telegram.org/bot{decrypted_token}/sendMessage"
    payload = {
        "chat_id": check.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)) as client:
        try:
            response = await client.post(url, json=payload)
            response_text = response.text
            response.raise_for_status()
            await rate_control.report("telegram", identity, "success")
            logger.info(f"Successfully sent Telegram notification for check '{check.name}' (ID: {check.id}). Response: {response_text}")
            check.telegram_last_notification_status = "ok"
            check.telegram_last_notification_message = "Successfully sent."
            metrics.record_notification_sent("telegram", "success")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            retry_after = None
            if status == 429:
                try:
                    data = e.response.json()
                    retry_after = (
                        (data.get("parameters") or {}).get("retry_after")
                        or data.get("retry_after")
                    )
                    if retry_after is not None:
                        retry_after = float(retry_after)
                except Exception:
                    retry_after = None
                await rate_control.report("telegram", identity, "rate_limited", retry_after=retry_after)
                check.telegram_last_notification_status = "error"
                check.telegram_last_notification_message = f"Rate limited by Telegram (429)."
                metrics.record_notification_sent("telegram", "error")
                raise rate_control.RateLimitedError(retry_after)
            elif 500 <= status < 600:
                await rate_control.report("telegram", identity, "transient_error")
                check.telegram_last_notification_status = "error"
                check.telegram_last_notification_message = f"Telegram server error: {status}"
                metrics.record_notification_sent("telegram", "error")
                raise rate_control.TransientSendError()
            else:
                await rate_control.report("telegram", identity, "permanent_error")
                error_message = f"Error: {status} {e.response.text}"
                logger.error(f"Error sending Telegram notification for check '{check.name}' (ID: {check.id}): {error_message}")
                check.telegram_last_notification_status = "error"
                check.telegram_last_notification_message = error_message
                metrics.record_notification_sent("telegram", "error")
        except httpx.RequestError as e:
            await rate_control.report("telegram", identity, "transient_error")
            error_message = f"Network error: {e}"
            logger.error(f"Network error while sending Telegram notification for check '{check.name}' (ID: {check.id}): {e}", exc_info=True)
            check.telegram_last_notification_status = "error"
            check.telegram_last_notification_message = error_message
            metrics.record_notification_sent("telegram", "error")
            raise rate_control.TransientSendError()
        finally:
            check.telegram_last_notification_timestamp = datetime.now(timezone.utc)


async def _execute_slack_send(check: Check, message: str):
    if not all([check.slack_enabled, check.slack_webhook_url]): return
    if not check.owner or not check.owner.auth_key: return
    try:
        decrypted_url = encryption.decrypt_token(check.slack_webhook_url, check.owner.auth_key)
    except Exception:
        return

    identity = hashlib.sha256(decrypted_url.encode("utf-8")).hexdigest()[:10]
    ok, retry_after, _ = await rate_control.reserve("slack", identity)
    if not ok:
        await rate_control.report("slack", identity, "rate_limited", retry_after=retry_after)
        raise rate_control.RateLimitedError(retry_after)

    payload = {"text": message}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)) as client:
        try:
            response = await client.post(decrypted_url, json=payload)
            response.raise_for_status()
            await rate_control.report("slack", identity, "success")
            check.slack_last_notification_status = "ok"
            check.slack_last_notification_message = "Successfully sent."
            metrics.record_notification_sent("slack", "success")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                ra = None
                try:
                    ra_hdr = e.response.headers.get("Retry-After")
                    if ra_hdr:
                        ra = float(ra_hdr)
                except Exception:
                    ra = None
                await rate_control.report("slack", identity, "rate_limited", retry_after=ra)
                check.slack_last_notification_status = "error"
                check.slack_last_notification_message = "Rate limited by Slack (429)."
                metrics.record_notification_sent("slack", "error")
                raise rate_control.RateLimitedError(ra)
            elif 500 <= status < 600:
                await rate_control.report("slack", identity, "transient_error")
                check.slack_last_notification_status = "error"
                check.slack_last_notification_message = f"Slack server error: {status}"
                metrics.record_notification_sent("slack", "error")
                raise rate_control.TransientSendError()
            else:
                await rate_control.report("slack", identity, "permanent_error")
                check.slack_last_notification_status = "error"
                check.slack_last_notification_message = f"Error: {status} {e.response.text}"
                metrics.record_notification_sent("slack", "error")
        except httpx.RequestError as e:
            await rate_control.report("slack", identity, "transient_error")
            check.slack_last_notification_status = "error"
            check.slack_last_notification_message = f"Network error: {e}"
            metrics.record_notification_sent("slack", "error")
            raise rate_control.TransientSendError()
        finally:
            check.slack_last_notification_timestamp = datetime.now(timezone.utc)


async def _execute_discord_send(check: Check, message: str):
    if not all([check.discord_enabled, check.discord_webhook_url]): return
    if not check.owner or not check.owner.auth_key: return
    try:
        decrypted_url = encryption.decrypt_token(check.discord_webhook_url, check.owner.auth_key)
    except Exception:
        return

    identity = hashlib.sha256(decrypted_url.encode("utf-8")).hexdigest()[:10]
    ok, retry_after, _ = await rate_control.reserve("discord", identity)
    if not ok:
        await rate_control.report("discord", identity, "rate_limited", retry_after=retry_after)
        raise rate_control.RateLimitedError(retry_after)

    payload = {"content": message}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)) as client:
        try:
            response = await client.post(decrypted_url, json=payload)
            response.raise_for_status()
            await rate_control.report("discord", identity, "success")
            check.discord_last_notification_status = "ok"
            check.discord_last_notification_message = "Successfully sent."
            metrics.record_notification_sent("discord", "success")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                ra = None
                try:
                    ra_hdr = e.response.headers.get("Retry-After")
                    if ra_hdr:
                        ra = float(ra_hdr)
                except Exception:
                    ra = None
                await rate_control.report("discord", identity, "rate_limited", retry_after=ra)
                check.discord_last_notification_status = "error"
                check.discord_last_notification_message = "Rate limited by Discord (429)."
                metrics.record_notification_sent("discord", "error")
                raise rate_control.RateLimitedError(ra)
            elif 500 <= status < 600:
                await rate_control.report("discord", identity, "transient_error")
                check.discord_last_notification_status = "error"
                check.discord_last_notification_message = f"Discord server error: {status}"
                metrics.record_notification_sent("discord", "error")
                raise rate_control.TransientSendError()
            else:
                await rate_control.report("discord", identity, "permanent_error")
                check.discord_last_notification_status = "error"
                check.discord_last_notification_message = f"Error: {status} {e.response.text}"
                metrics.record_notification_sent("discord", "error")
        except httpx.RequestError as e:
            await rate_control.report("discord", identity, "transient_error")
            check.discord_last_notification_status = "error"
            check.discord_last_notification_message = f"Network error: {e}"
            metrics.record_notification_sent("discord", "error")
            raise rate_control.TransientSendError()
        finally:
            check.discord_last_notification_timestamp = datetime.now(timezone.utc)


async def _execute_webhook_send(check: Check, message: str):
    if not all([check.webhook_enabled, check.webhook_url]): return
    if not check.owner or not check.owner.auth_key: return
    try:
        decrypted_url = encryption.decrypt_token(check.webhook_url, check.owner.auth_key)
    except Exception:
        return

    identity = hashlib.sha256(decrypted_url.encode("utf-8")).hexdigest()[:10]
    ok, retry_after, _ = await rate_control.reserve("webhook", identity)
    if not ok:
        await rate_control.report("webhook", identity, "rate_limited", retry_after=retry_after)
        raise rate_control.RateLimitedError(retry_after)

    payload = {
        "check_id": check.id,
        "check_name": check.name,
        "status": check.status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)) as client:
        try:
            response = await client.post(decrypted_url, json=payload)
            response.raise_for_status()
            await rate_control.report("webhook", identity, "success")
            check.webhook_last_notification_status = "ok"
            check.webhook_last_notification_message = "Successfully sent."
            metrics.record_notification_sent("webhook", "success")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                ra = None
                try:
                    ra_hdr = e.response.headers.get("Retry-After")
                    if ra_hdr:
                        ra = float(ra_hdr)
                except Exception:
                    ra = None
                await rate_control.report("webhook", identity, "rate_limited", retry_after=ra)
                check.webhook_last_notification_status = "error"
                check.webhook_last_notification_message = "Rate limited by webhook endpoint (429)."
                metrics.record_notification_sent("webhook", "error")
                raise rate_control.RateLimitedError(ra)
            elif 500 <= status < 600:
                await rate_control.report("webhook", identity, "transient_error")
                check.webhook_last_notification_status = "error"
                check.webhook_last_notification_message = f"Webhook server error: {status}"
                metrics.record_notification_sent("webhook", "error")
                raise rate_control.TransientSendError()
            else:
                await rate_control.report("webhook", identity, "permanent_error")
                check.webhook_last_notification_status = "error"
                check.webhook_last_notification_message = f"Error: {status} {e.response.text}"
                metrics.record_notification_sent("webhook", "error")
        except httpx.RequestError as e:
            await rate_control.report("webhook", identity, "transient_error")
            check.webhook_last_notification_status = "error"
            check.webhook_last_notification_message = f"Network error: {e}"
            metrics.record_notification_sent("webhook", "error")
            raise rate_control.TransientSendError()
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
        enqueued_at = time.time()
        task_func.delay(check.id, message, enqueued_at, check.owner_id)


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
