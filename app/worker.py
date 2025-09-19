import asyncio
import logging
import threading
import atexit
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

# Use a single, long-lived asyncio event loop in this process to avoid
# cross-loop connection reuse issues with asyncpg/SQLAlchemy.
_event_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_event_loop.run_forever, daemon=True)
_loop_thread.start()

def run_coro(coro):
    """Run an async coroutine in the worker's dedicated event loop."""
    return asyncio.run_coroutine_threadsafe(coro, _event_loop).result()

@atexit.register
def _shutdown_event_loop():
    try:
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

@celery_app.task(name="send_telegram_notification_task")
def send_telegram_notification_task(check_id: int, message: str):
    run_coro(_send_telegram_notification(check_id, message))

@celery_app.task(name="send_slack_notification_task")
def send_slack_notification_task(check_id: int, message: str):
    run_coro(_send_slack_notification(check_id, message))

@celery_app.task(name="send_discord_notification_task")
def send_discord_notification_task(check_id: int, message: str):
    run_coro(_send_discord_notification(check_id, message))

@celery_app.task(name="send_webhook_notification_task")
def send_webhook_notification_task(check_id: int, message: str):
    run_coro(_send_webhook_notification(check_id, message))


async def _check_overdue_jobs():
    """The core async logic for finding and processing overdue checks."""
    from app.services import notifications
    from app.crud import get_check_runtime_redis_key

    now_utc = datetime.now(timezone.utc)
    logger.info("Scheduler task running check cycle...")
    
    async with AsyncSessionLocal() as session:
        async with session.begin():
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
                    if not r:
                        logger.warning(f"Skipping overdue processing for check {check.id} due to Redis unavailability.")
                        continue
                    # Atomically set status to 'down' and update counters
                    lua = """
local runtime_key = KEYS[1]
local user_counters = KEYS[2]
local global_counters = KEYS[3]
local is_paused = ARGV[1]
local prev = redis.call('HGET', runtime_key, 'status')
if not prev then prev = 'new' end
if prev ~= 'down' then
  redis.call('HSET', runtime_key, 'status', 'down', 'last_start', '')
  if is_paused ~= '1' then
    redis.call('HINCRBY', user_counters, prev, -1)
    redis.call('HINCRBY', global_counters, prev, -1)
    redis.call('HINCRBY', user_counters, 'down', 1)
    redis.call('HINCRBY', global_counters, 'down', 1)
  end
end
return prev
"""
                    runtime_key = get_check_runtime_redis_key(check.id)
                    user_key = f"user_stats:counters:{check.owner_id}"
                    global_key = "metrics:checks_status_counts"
                    try:
                        prev = await r.eval(lua, 3, runtime_key, user_key, global_key, "1" if check.paused else "0")
                        previous_status = prev.decode() if isinstance(prev, bytes) else prev
                    except Exception as e:
                        logger.error(f"Failed to update Redis status for overdue check {check.id}: {e}")
                        continue

                    if previous_status != "down":
                        logger.info(f"Check '{check.name}' (ID: {check.id}) is DOWN (overdue).")
                        message = f"🔴 Check Down: [{check.name}] is overdue."
                        notifications.schedule_all_notifications(check, message)
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
                try:
                    r = get_redis_connection()
                except Exception as e:
                    r = None
                    logger.error(f"Redis connection error while processing long-running checks: {e}")

                if r:
                    pipe = r.pipeline()
                    keys = []
                    for check in runtime_candidates:
                        key = get_check_runtime_redis_key(check.id)
                        keys.append((check, key))
                        pipe.hmget(key, "last_start", "status")
                    redis_results = await pipe.execute()
                    for (check, _), (last_start_str, status_val) in zip(keys, redis_results):
                        if isinstance(last_start_str, bytes):
                            last_start_str = last_start_str.decode()
                        if isinstance(status_val, bytes):
                            status_val = status_val.decode()
                        if not last_start_str:
                            continue
                        try:
                            last_start = datetime.fromisoformat(last_start_str)
                        except Exception:
                            continue
                        if (now_utc - last_start).total_seconds() > (check.max_runtime_seconds or 0):
                            if status_val != "down":
                                long_running_checks.append(check)

            if long_running_checks:
                logger.info(f"Scheduler found {len(long_running_checks)} long-running checks.")
                # Lua to set down and clear last_start, update counters
                lua = """
local runtime_key = KEYS[1]
local user_counters = KEYS[2]
local global_counters = KEYS[3]
local is_paused = ARGV[1]
local prev = redis.call('HGET', runtime_key, 'status')
if not prev then prev = 'new' end
if prev ~= 'down' then
  redis.call('HSET', runtime_key, 'status', 'down', 'last_start', '')
  if is_paused ~= '1' then
    redis.call('HINCRBY', user_counters, prev, -1)
    redis.call('HINCRBY', global_counters, prev, -1)
    redis.call('HINCRBY', user_counters, 'down', 1)
    redis.call('HINCRBY', global_counters, 'down', 1)
  end
end
return prev
"""
                try:
                    r = get_redis_connection()
                except Exception as e:
                    r = None
                    logger.error(f"Redis connection error while updating long-running checks: {e}")

                for check in long_running_checks:
                    if not r:
                        logger.warning(f"Skipping long-running processing for check {check.id} due to Redis unavailability.")
                        continue
                    runtime_key = get_check_runtime_redis_key(check.id)
                    user_key = f"user_stats:counters:{check.owner_id}"
                    global_key = "metrics:checks_status_counts"
                    try:
                        prev = await r.eval(lua, 3, runtime_key, user_key, global_key, "1" if check.paused else "0")
                        previous_status = prev.decode() if isinstance(prev, bytes) else prev
                    except Exception as e:
                        logger.error(f"Failed to update Redis status for long-running check {check.id}: {e}")
                        continue

                    if previous_status != "down":
                        logger.info(f"Check '{check.name}' (ID: {check.id}) is DOWN (exceeded max runtime).")
                        message = f"🔴 Check Down: [{check.name}] exceeded its max runtime of {check.max_runtime_seconds}s."
                        notifications.schedule_all_notifications(check, message)
            else:
                logger.info("Scheduler found no long-running checks.")

            


@celery_app.task(name="app.worker.check_overdue_jobs_task")
def check_overdue_jobs_task():
    """Celery task wrapper to run the async scheduler logic."""
    run_coro(_check_overdue_jobs())
