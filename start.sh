#!/bin/bash

# Start the web server
echo "Starting Uvicorn server..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 &

# Start the Celery worker
echo "Starting Celery worker..."
celery -A app.worker.celery_app worker --loglevel=info &

# Start the scheduler in the foreground to keep the container running
echo "Starting scheduler..."
python scheduler_runner.py
