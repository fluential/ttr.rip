import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import models, base
from app.core.config import settings
from app.core.redis_pool import get_redis_connection

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
    # Find checks that haven't been pinged since the threshold
    inactive_checks_query = (
        select(models.Check)
        .where(or_(
            models.Check.last_ping < inactive_threshold,
            and_(
                models.Check.last_ping.is_(None),
                models.Check.created_at < inactive_threshold
            )
        ))
    )
    
    result = await db.execute(inactive_checks_query)
    inactive_checks = result.scalars().all()
    
    if not inactive_checks:
        logger.info("No inactive checks found to clean up")
        return
    
    # Log the checks to be deleted
    check_ids = [check.id for check in inactive_checks]
    check_uuids = [check.uuid for check in inactive_checks]
    logger.info(f"Deleting {len(check_ids)} inactive checks: {check_uuids}")
    
    # Delete the inactive checks
    for check in inactive_checks:
        await db.delete(check)
    
    await db.commit()
    logger.info(f"Successfully deleted {len(check_ids)} inactive checks")

    # Clean up Redis entries for these checks
    if not settings.DEBUG_MODE:
        try:
            r = get_redis_connection()
            if r:
                # Delete any Redis entries associated with these checks
                # This is just an example - you might have other patterns to clean up
                pipe = r.pipeline()
                for check_id in check_ids:
                    key_pattern = f"check:{check_id}:*"
                    keys = r.keys(key_pattern)
                    if keys:
                        pipe.delete(*keys)
                pipe.execute()
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
    # First get users with at least one active check
    users_with_active_checks_query = (
        select(models.User.id)
        .join(models.Check, models.User.id == models.Check.owner_id)
        .where(
            or_(
                models.Check.last_ping >= inactive_threshold,
                and_(
                    models.Check.last_ping.is_(None),
                    models.Check.created_at >= inactive_threshold
                )
            )
        )
        .distinct()
    )
    
    result = await db.execute(users_with_active_checks_query)
    users_with_active_checks = [user_id for user_id in result.scalars().all()]
    
    # Now find users with checks, but all inactive
    users_with_only_inactive_checks_query = (
        select(models.User)
        .join(models.Check, models.User.id == models.Check.owner_id)
        .where(
            and_(
                models.User.created_at < inactive_threshold,
                models.User.telegram_user_id.is_(None),
                ~models.User.id.in_(users_with_active_checks) if users_with_active_checks else True
            )
        )
        .group_by(models.User.id)
    )
    
    result = await db.execute(users_with_only_inactive_checks_query)
    users_with_only_inactive_checks = result.scalars().all()
    
    # Combine both sets of users
    inactive_users.extend(users_with_only_inactive_checks)
    
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
                    keys = r.keys(user_key_pattern)
                    if keys:
                        pipe.delete(*keys)
                
                pipe.execute()
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
