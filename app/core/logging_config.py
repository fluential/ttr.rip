import logging
import sys
import os

def setup_logging():
    """
    Set up logging configuration for the application.
    """
    log_format = "%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format=log_format,
        stream=sys.stdout,
    )

    # Align common third-party loggers
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("celery").setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # avoid leaking secrets in URLs
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
