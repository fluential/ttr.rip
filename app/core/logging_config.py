import logging
import sys

def setup_logging():
    """
    Set up logging configuration for the application.
    """
    log_format = "%(asctime)s - %(levelname)s - [%(name)s:%(funcName)s:%(lineno)d] - %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        stream=sys.stdout,
    )
