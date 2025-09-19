import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import models, base
from app.core.config import settings
from app.core.redis_pool import get_redis_connection
from app.crud import get_check_runtime_redis_key

logger = logging.getLogger(__name__)

async def cleanup_inactive_data():
    """
    Cleanup task that removes:
    1. Checks that haven't been pinged in X days
    2. User accounts that have no active checks and haven't been used in X days
       (unless linked to Telegram)
    """
    if not settings.CLEANUP_ENABLED:
        logger.debug("Cleanup is disabled in settings")
        return

    inactive_threshold = datetime.utcnow() - timedelta(days=settings.CLEANUP_INACTIVE_DAYS)
    logger.info(f"Starting cleanup of data inactive since {inactive_threshold}")
    
    async with base.AsyncSessionLocal() as db:
        # Step 1: Delete inactive checks
        await cleanup_inactive_checks(db, inactive_threshold)
        
        # Step 2: Find and delete inactive users without Telegram and without active checks
        await cleanup_inactive_users(db, inactive_threshold)

async def cleanup_inactive_checks(db: AsyncSession, inactive_threshold: datetime):
    """Delete checks that haven't been pinged since the inactive threshold."""
    # Candidate checks: created before the threshold
    candidate_query = select(models.Check).where(models.Check.created_at < inactive_threshold)
    result = await db.execute(candidate_query)
    candidates = result.scalars().all()

    if not candidates:
        logger.info("No candidate checks found to clean up")
        return

    to_delete = []
    r = None
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
        except Exception as e:
            logger.error(f"Redis connection error during cleanup_inactive_checks: {e}")

    for check in candidates:
        if r:
            try:
                key = get_check_runtime_redis_key(check.id)
                last_ping_str = await r.hget(key, "last_ping")
                if last_ping_str:
                    # If we have a recent last_ping, skip deletion
                    try:
                        last_ping = datetime.fromisoformat(last_ping_str)
                        if last_ping >= inactive_threshold:
                            continue
                    except Exception:
                        # Malformed timestamp; treat as no recent activity
                        pass
                # No last_ping or older than threshold -> mark for deletion
                to_delete.append(check)
            except Exception as e:
                logger.warning(f"Failed to read runtime for check {check.id}: {e}. Marking candidate for deletion.")
                to_delete.append(check)
        else:
            # No Redis available; fall back to created_at-only heuristic
            to_delete.append(check)

    if not to_delete:
        logger.info("No inactive checks found to clean up")
        return

    check_ids = [c.id for c in to_delete]
    check_uuids = [c.uuid for c in to_delete]
    logger.info(f"Deleting {len(check_ids)} inactive checks: {check_uuids}")

    for check in to_delete:
        await db.delete(check)
    await db.commit()
    logger.info(f"Successfully deleted {len(check_ids)} inactive checks")

    # Clean up Redis entries for these checks
    if not settings.DEBUG_MODE and r:
        try:
            pipe = r.pipeline()
            for check_id in check_ids:
                pipe.unlink(get_check_runtime_redis_key(check_id))
                pipe.unlink(f"ping_logs:{check_id}")
                pipe.unlink(f"check_content:{check_id}")
            await pipe.execute()
            logger.info(f"Cleaned up Redis entries for {len(check_ids)} deleted checks")
        except Exception as e:
            logger.error(f"Error cleaning up Redis entries for deleted checks: {e}")

async def cleanup_inactive_users(db: AsyncSession, inactive_threshold: datetime):
    """
    Delete user accounts that:
    1. Have no checks OR all their checks are older than the threshold
    2. Were created before the threshold
    3. Are NOT linked to Telegram
    """
    # First find users with no checks
    users_with_no_checks_query = (
        select(models.User)
        .outerjoin(models.Check, models.User.id == models.Check.owner_id)
        .where(
            and_(
                models.User.created_at < inactive_threshold,
                models.User.telegram_user_id.is_(None),
                models.Check.id.is_(None)
            )
        )
        .group_by(models.User.id)
    )
    
    # Execute query for users with no checks
    result = await db.execute(users_with_no_checks_query)
    inactive_users = result.scalars().all()
    
    # Find users where all checks are inactive
    # First get users with at least one check created after the threshold (treated as active)
    users_with_active_checks_query = (
        select(models.User.id)
        .join(models.Check, models.User.id == models.Check.owner_id)
        .where(models.Check.created_at >= inactive_threshold)
        .distinct()
    )
    result = await db.execute(users_with_active_checks_query)
    users_with_active_checks = set(result.scalars().all())

    # Now find users with checks, but potentially all inactive (by created_at)
    users_with_only_inactive_checks_query = (
        select(models.User.id)
        .join(models.Check, models.User.id == models.Check.owner_id)
        .where(
            and_(
                models.User.created_at < inactive_threshold,
                models.User.telegram_user_id.is_(None),
            )
        )
        .distinct()
    )
    result = await db.execute(users_with_only_inactive_checks_query)
    candidate_user_ids = [uid for uid in result.scalars().all() if uid not in users_with_active_checks]

    users_with_only_inactive_checks = []
    if candidate_user_ids:
        # Refine with Redis last_ping: if any check has recent last_ping, treat user as active
        try:
            r = get_redis_connection()
        except Exception:
            r = None

        for uid in candidate_user_ids:
            # Load this user's checks
            checks_result = await db.execute(select(models.Check).where(models.Check.owner_id == uid))
            user_checks = checks_result.scalars().all()
            if not user_checks:
                continue
            if r:
                had_recent = False
                for c in user_checks:
                    try:
                        last_ping_str = await r.hget(f"check_runtime:{c.id}", "last_ping")
                        if last_ping_str:
                            try:
                                last_ping = datetime.fromisoformat(last_ping_str)
                                if last_ping >= inactive_threshold:
                                    had_recent = True
                                    break
                            except Exception:
                                continue
                    except Exception:
                        continue
                if had_recent:
                    continue
            users_with_only_inactive_checks.append(
                next(u for u in inactive_users if u.id == uid) if any(u.id == uid for u in inactive_users) else (await db.execute(select(models.User).where(models.User.id == uid))).scalars().first()  # type: ignore
            )

    # Combine both sets of users
    inactive_users.extend([u for u in users_with_only_inactive_checks if u is not None])
    
    if not inactive_users:
        logger.info("No inactive users found to clean up")
        return
    
    # Log the users to be deleted
    user_ids = [user.id for user in inactive_users]
    user_auth_keys = [f"...{user.auth_key[-4:]}" for user in inactive_users]
    logger.info(f"Deleting {len(user_ids)} inactive users: {list(zip(user_ids, user_auth_keys))}")
    
    # Clean up Redis entries for these users
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                pipe = r.pipeline()
                for user_id in user_ids:
                    # Clean up user-related Redis entries
                    owner_identifier = f"user_id_{user_id}"
                    
                    # Clean up notification counters
                    pipe.delete(f"user_stats:queued_notifications:{owner_identifier}")
                    pipe.delete(f"user_stats:processed_notifications:{owner_identifier}")
                    
                    # Clean up user stats cache
                    pipe.delete(f"user_stats:dashboard:{user_id}")
                    
                    # Find and delete any other user-related keys
                    user_key_pattern = f"*{owner_identifier}*"
                    keys = await r.keys(user_key_pattern)
                    if keys:
                        pipe.delete(*keys)
                
                await pipe.execute()
                logger.info(f"Cleaned up Redis entries for {len(user_ids)} users to be deleted")
        except Exception as e:
            logger.error(f"Error cleaning up Redis entries for users: {e}")
    
    # Delete the inactive users
    for user in inactive_users:
        await db.delete(user)
    
    await db.commit()
    logger.info(f"Successfully deleted {len(user_ids)} inactive users")

async def start_periodic_cleanup():
    """Start the periodic cleanup task."""
    while True:
        try:
            await cleanup_inactive_data()
        except Exception as e:
            logger.error(f"Error during cleanup task: {e}", exc_info=True)
        
        # Sleep until next cleanup
        logger.info(f"Next cleanup scheduled in {settings.CLEANUP_INTERVAL_HOURS} hours")
        await asyncio.sleep(settings.CLEANUP_INTERVAL_HOURS * 3600)

# Create a simple command for manual cleanup
async def run_manual_cleanup():
    """Run the cleanup task once and exit."""
    logger.info("Starting manual cleanup")
    
    # Force enable cleanup for manual run
    original_setting = settings.CLEANUP_ENABLED
    settings.CLEANUP_ENABLED = True
    
    try:
        await cleanup_inactive_data()
        logger.info("Cleanup completed successfully")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
    finally:
        # Restore original setting
        settings.CLEANUP_ENABLED = original_setting
