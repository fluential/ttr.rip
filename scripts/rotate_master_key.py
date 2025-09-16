#!/usr/bin/env python
import asyncio
import logging
import sys
import os
import getpass
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging_config import setup_logging
from app.db.base import AsyncSessionLocal
from app.db import models
from app.core import encryption as old_encryption_module
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

setup_logging()
logger = logging.getLogger(__name__)

async def rotate_master_key(new_master_key_str: str):
    """
    Rotates the master ENCRYPTION_KEY.
    This involves decrypting all secrets with the old key and re-encrypting with the new one.
    """
    try:
        # Validate the new key
        Fernet(new_master_key_str.encode())
    except (ValueError, TypeError):
        logger.critical("The new master key is invalid. It must be 32 url-safe base64-encoded bytes.")
        return

    logger.info("Starting master key rotation. This may take a while for large databases.")
    logger.warning("DO NOT interrupt this process. Ensure you have a database backup.")

    async with AsyncSessionLocal() as db:
        # Get all checks that have an encrypted telegram bot token
        query = (
            select(models.Check)
            .options(selectinload(models.Check.owner))
            .filter(models.Check.telegram_bot_token.isnot(None))
        )
        result = await db.execute(query)
        checks_to_migrate = result.scalars().all()

        if not checks_to_migrate:
            logger.info("No encrypted tokens found to migrate. Process complete.")
            logger.info("You can now update your .env file with the new ENCRYPTION_KEY.")
            return

        total_checks = len(checks_to_migrate)
        logger.info(f"Found {total_checks} checks with tokens to migrate.")
        
        migrated_count = 0
        failed_count = 0

        for i, check in enumerate(checks_to_migrate):
            if not check.owner or not check.owner.auth_key:
                logger.error(f"Skipping check ID {check.id}: Owner or owner's auth_key is missing.")
                failed_count += 1
                continue

            try:
                # 1. Decrypt with OLD master key and user's auth key
                decrypted_token = old_encryption_module.decrypt_token(
                    encrypted_token=check.telegram_bot_token,
                    user_auth_key=check.owner.auth_key
                )

                # 2. Encrypt with NEW master key and user's auth key
                # We create a temporary encryption function for this.
                new_derived_key = old_encryption_module.get_derived_key(new_master_key_str, check.owner.auth_key)
                fernet_instance = Fernet(new_derived_key)
                new_encrypted_token = fernet_instance.encrypt(decrypted_token.encode()).decode()

                check.telegram_bot_token = new_encrypted_token
                migrated_count += 1
                
                # Log progress
                if (i + 1) % 25 == 0 or (i + 1) == total_checks:
                    logger.info(f"Progress: {i + 1}/{total_checks} checks processed.")

            except Exception as e:
                logger.error(f"Failed to migrate token for check ID {check.id} (Owner ID: {check.owner_id}): {e}")
                failed_count += 1

        if failed_count > 0:
            logger.error(f"Migration completed with {failed_count} failures. Please review the logs.")
            logger.error("Rolling back all changes due to failures. The database has NOT been modified.")
            await db.rollback()
        else:
            logger.info("All tokens successfully prepared for migration. Committing changes to the database.")
            await db.commit()
            logger.info("Database migration successful!")
            logger.info("You can now update your .env file with the new ENCRYPTION_KEY.")
            print("\n---")
            print(f"New ENCRYPTION_KEY: {new_master_key_str}")
            print("---\n")

async def main():
    print("--- Master Key Rotation ---")
    print("This script will re-encrypt all secrets in the database with a new master key.")
    print("\nWARNING: This is a destructive operation. Ensure you have a database backup before proceeding.")
    print("The application should be OFFLINE while this script is running.\n")

    confirm = input("Type 'yes' to continue: ")
    if confirm.lower() != 'yes':
        print("Aborting.")
        return

    new_key = getpass.getpass("Enter the new 32-byte url-safe base64-encoded master key: ")
    if not new_key:
        print("No key entered. Aborting.")
        return
        
    await rotate_master_key(new_key)

if __name__ == "__main__":
    asyncio.run(main())
