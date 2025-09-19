import asyncio
import logging
import threading
import atexit
from celery import Celery, signals
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
import httpx
from datetime import datetime, timezone
import time
import math
from app.services.rate_control import RateLimitedError, TransientSendError

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.db.models import Check
from app.core.logging_config import setup_logging
from app.core import encryption
from app import metrics
from app.core.redis_pool import get_redis_connection, ephemeral_redis
from app.services.notifications import (
    _execute_telegram_send, _execute_slack_send, 
    _execute_discord_send, _execute_webhook_send
)
from app.services import alerting

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

# Use a single, long-lived asyncio event loop in this process to avoid
# cross-loop connection reuse issues with asyncpg/SQLAlchemy.
_event_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_event_loop.run_forever, daemon=True)
_loop_thread.start()

# Redis-based worker heartbeat for cross-worker "workers online" metric
import os, socket, uuid

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
_HEARTBEAT_TTL = 30  # seconds
_HEARTBEAT_INTERVAL = 10  # seconds

async def _worker_register():
    try:
        async with ephemeral_redis() as r:
            if r:
                await r.sadd("metrics:workers_online:set", WORKER_ID)
                await r.setex(f"metrics:worker:{WORKER_ID}:hb", _HEARTBEAT_TTL, "1")
    except Exception as e:
        logger.error(f"Worker register heartbeat failed: {e}")

async def _worker_heartbeat():
    while True:
        try:
            async with ephemeral_redis() as r:
                if r:
                    await r.setex(f"metrics:worker:{WORKER_ID}:hb", _HEARTBEAT_TTL, "1")
        except Exception:
            # Never raise from heartbeat loop
            pass
        await asyncio.sleep(_HEARTBEAT_INTERVAL)

async def _worker_unregister():
    try:
        async with ephemeral_redis() as r:
            if r:
                await r.delete(f"metrics:worker:{WORKER_ID}:hb")
                await r.srem("metrics:workers_online:set", WORKER_ID)
    except Exception as e:
        logger.error(f"Worker unregister heartbeat failed: {e}")

@signals.worker_ready.connect
def _on_worker_ready(sender, **kwargs):
    try:
        _event_loop.call_soon_threadsafe(lambda: _event_loop.create_task(_worker_register()))
        _event_loop.call_soon_threadsafe(lambda: _event_loop.create_task(_worker_heartbeat()))
        logger.info(f"Worker heartbeat started for {WORKER_ID}")
    except Exception as e:
        logger.error(f"Failed to start worker heartbeat: {e}")

@signals.worker_shutdown.connect
def _on_worker_shutdown(sender, **kwargs):
    try:
        _event_loop.call_soon_threadsafe(lambda: _event_loop.create_task(_worker_unregister()))
        logger.info(f"Worker heartbeat stopped for {WORKER_ID}")
    except Exception as e:
        logger.error(f"Failed to stop worker heartbeat: {e}")

def run_coro(coro):
    """Run an async coroutine in the worker's dedicated event loop."""
    return asyncio.run_coroutine_threadsafe(coro, _event_loop).result()

def _should_drop(enqueued_at: float | None, owner_id: int | None) -> bool:
    """
    Drop retry if older than 30 minutes or if user's queued notifications exceed 30.
    """
    try:
        if enqueued_at and (time.time() - enqueued_at) > 1800:
            return True
    except Exception:
        pass
    try:
        if owner_id:
            r = get_redis_connection()
            if r:
                owner_identifier = f"user_id_{owner_id}"
                val = run_coro(r.get(f"user_stats:queued_notifications:{owner_identifier}"))
                if val is not None:
                    try:
                        if int(val) > 30:
                            return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False

@atexit.register
def _shutdown_event_loop():
    try:
        # Attempt a final flush of buffered Redis counters before stopping the loop
        try:
            from app import crud
            run_coro(crud._flush_incr_buffer_async())
        except Exception:
            pass
        _event_loop.call_soon_threadsafe(_event_loop.stop)
    except Exception:
        pass

if settings.DEBUG_MODE:
    logger.info("DEBUG_MODE is on. Celery will run tasks eagerly without a broker.")
else:
    try:
        r = get_redis_connection()
        if r:
            run_coro(r.ping())
            logger.info("Celery worker successfully connected to Redis.")
    except Exception as e:
        logger.error(f"Celery worker failed to connect to Redis: {e}. Tasks may not be processed.")


async def _send_notification(check_id: int, message: str, send_function):
    """Generic async logic for sending a notification and updating the DB."""
    owner_identifier_for_stats = None
    async with AsyncSessionLocal() as session:
        if True:
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
                        await r.decr(f"user_stats:queued_notifications:{owner_identifier_for_stats}")
                except Exception as e:
                    logger.error(f"Could not decrement queued notification count for check {check_id}: {e}")

            await send_function(check, message)

async def _send_telegram_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_telegram_send)

async def _send_slack_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_slack_send)

async def _send_discord_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_discord_send)

async def _send_webhook_notification(check_id: int, message: str):
    await _send_notification(check_id, message, _execute_webhook_send)

@celery_app.task(
    bind=True,
    name="send_telegram_notification_task",
    autoretry_for=(TransientSendError,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=12,
)
def send_telegram_notification_task(self, check_id: int, message: str, enqueued_at: float = None, owner_id: int | None = None):
    try:
        run_coro(_send_telegram_notification(check_id, message))
    except RateLimitedError as e:
        if _should_drop(enqueued_at, owner_id):
            return
        if e.retry_after:
            raise self.retry(countdown=int(math.ceil(e.retry_after)))
        raise self.retry()
    except TransientSendError:
        if _should_drop(enqueued_at, owner_id):
            return
        raise self.retry()

@celery_app.task(
    bind=True,
    name="send_slack_notification_task",
    autoretry_for=(TransientSendError,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=12,
)
def send_slack_notification_task(self, check_id: int, message: str, enqueued_at: float = None, owner_id: int | None = None):
    try:
        run_coro(_send_slack_notification(check_id, message))
    except RateLimitedError as e:
        if _should_drop(enqueued_at, owner_id):
            return
        if e.retry_after:
            raise self.retry(countdown=int(math.ceil(e.retry_after)))
        raise self.retry()
    except TransientSendError:
        if _should_drop(enqueued_at, owner_id):
            return
        raise self.retry()

@celery_app.task(
    bind=True,
    name="send_discord_notification_task",
    autoretry_for=(TransientSendError,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=12,
)
def send_discord_notification_task(self, check_id: int, message: str, enqueued_at: float = None, owner_id: int | None = None):
    try:
        run_coro(_send_discord_notification(check_id, message))
    except RateLimitedError as e:
        if _should_drop(enqueued_at, owner_id):
            return
        if e.retry_after:
            raise self.retry(countdown=int(math.ceil(e.retry_after)))
        raise self.retry()
    except TransientSendError:
        if _should_drop(enqueued_at, owner_id):
            return
        raise self.retry()

@celery_app.task(
    bind=True,
    name="send_webhook_notification_task",
    autoretry_for=(TransientSendError,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=12,
)
def send_webhook_notification_task(self, check_id: int, message: str, enqueued_at: float = None, owner_id: int | None = None):
    try:
        run_coro(_send_webhook_notification(check_id, message))
    except RateLimitedError as e:
        if _should_drop(enqueued_at, owner_id):
            return
        if e.retry_after:
            raise self.retry(countdown=int(math.ceil(e.retry_after)))
        raise self.retry()
    except TransientSendError:
        if _should_drop(enqueued_at, owner_id):
            return
        raise self.retry()


async def _check_overdue_jobs():
    """The core async logic for finding and processing overdue checks."""
    from app.services import notifications
    from app import crud

    now_utc = datetime.now(timezone.utc)
    logger.info("Scheduler task running check cycle...")
    
    async with AsyncSessionLocal() as session:
        if True:
            # --- Find overdue checks ---
            overdue_query = select(Check).where(
                Check.paused == False,
                Check.deadline.isnot(None),
                Check.deadline < now_utc
            )
            result = await session.execute(overdue_query)
            overdue_checks = result.scalars().all()

            if overdue_checks:
                logger.info(f"Scheduler found {len(overdue_checks)} overdue checks.")
                try:
                    r = get_redis_connection()
                except Exception as e:
                    r = None
                    logger.error(f"Redis connection error while processing overdue checks: {e}")
                for check in overdue_checks:
                    check_id = getattr(check, "id", None)
                    try:
                        await crud.update_check_fail(session, check, reason="overdue")
                    except Exception as e:
                        logger.error(f"Failed to mark overdue check {check_id} as down: {e}")
            else:
                logger.info("Scheduler found no overdue checks.")

            # --- Find checks that exceeded max runtime ---
            # First, get candidates from DB (those with a configured max runtime and not paused)
            runtime_candidates_query = select(Check).where(
                Check.max_runtime_seconds.isnot(None),
                Check.paused == False
            )
            result = await session.execute(runtime_candidates_query)
            runtime_candidates = result.scalars().all()

            long_running_checks = []
            if runtime_candidates:
                for check in runtime_candidates:
                    if not check.last_start:
                        continue
                    try:
                        if (now_utc - check.last_start).total_seconds() > (check.max_runtime_seconds or 0):
                            if getattr(check, "status", "new") != "down":
                                long_running_checks.append(check)
                    except Exception:
                        continue

            if long_running_checks:
                logger.info(f"Scheduler found {len(long_running_checks)} long-running checks.")
                for check in long_running_checks:
                    check_id = getattr(check, "id", None)
                    try:
                        await crud.update_check_fail(session, check, reason=f"exceeded max runtime of {check.max_runtime_seconds}s")
                    except Exception as e:
                        logger.error(f"Failed to mark long-running check {check_id} as down: {e}")
            else:
                logger.info("Scheduler found no long-running checks.")

            


@celery_app.task(name="app.worker.check_overdue_jobs_task")
def check_overdue_jobs_task():
    """Celery task wrapper to run the async scheduler logic."""
    run_coro(_check_overdue_jobs())
