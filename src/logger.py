import json
import logging
import sys
from datetime import datetime
from src.config import config


class StructuredFormatter(logging.Formatter):
    """Custom formatter providing clean console output with event tags and metadata."""

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        level_name = record.levelname
        color = self.COLORS.get(level_name, '')
        reset = self.RESET if color else ''

        event = getattr(record, 'event', None)
        event_tag = f" [{event}]" if event else ""

        message = record.getMessage()

        # Extract extra fields that aren't standard LogRecord attributes
        standard_attrs = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
            'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
            'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
            'processName', 'process', 'event'
        }
        extra_keys = [k for k in record.__dict__ if k not in standard_attrs]
        meta_str = ""
        if extra_keys:
            extra_dict = {k: record.__dict__[k] for k in extra_keys}
            try:
                formatted_json = json.dumps(extra_dict, indent=2, default=str)
                indented = "\n  " + formatted_json.replace("\n", "\n  ")
                meta_str = indented
            except Exception:
                meta_str = f"\n  {extra_dict}"

        formatted = f"{timestamp} {color}{level_name.lower()}{reset}{event_tag}: {message}{meta_str}"

        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


def setup_logger(name: str = 'teams-mvp') -> logging.Logger:
    logger = logging.getLogger(name)
    level = getattr(logging, config.log_level, logging.INFO)
    logger.setLevel(level)

    # Avoid duplicate handlers if re-called
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

    logger.propagate = False
    return logger


logger = setup_logger()
