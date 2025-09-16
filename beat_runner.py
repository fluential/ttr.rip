#!/usr/bin/env python
import os
import sys
from app.worker import celery_app

if __name__ == "__main__":
    # This runner is a simple wrapper to start the Celery Beat scheduler.
    # It's intended to be run as a separate service in production.
    # Example: python beat_runner.py
    
    # Set up environment for Celery if needed (e.g., PYTHONPATH)
    # In a containerized setup, this is often handled by the environment itself.
    
    # The '-S django' part is for django-celery-beat, which we are not using.
    # We use the default scheduler.
    # The --pidfile argument is removed to be more container-friendly.
    args = ["beat", "--loglevel=info"]
    celery_app.worker_main(argv=args)
