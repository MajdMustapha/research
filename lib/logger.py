"""
Structured logging to stdout and SQLite.
All agents import get_logger(__name__) for consistent logging.
"""

import logging
import json
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "agent": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def get_logger(name: str, level: str = None) -> logging.Logger:
    """
    Get a named logger with JSON formatting to stdout.
    Level defaults to INFO or LOG_LEVEL env var.
    """
    import os

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = level or os.getenv("LOG_LEVEL", "INFO")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)

    logger.propagate = False
    return logger
