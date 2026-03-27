"""Centralised logging configuration for the FinOps Automation Hub.

Call ``configure_logging()`` once at application startup to set up
structured, levelled logging across all hub modules.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

__all__ = ["configure_logging"]

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(
    *,
    level: int | str = logging.INFO,
    fmt: str = _DEFAULT_FORMAT,
    date_fmt: str = _DEFAULT_DATE_FORMAT,
    stream: TextIO | None = None,
) -> None:
    """Configure logging for all hub modules.

    Args:
        level: Root log level (int or name like ``"DEBUG"``).
        fmt: Log format string.
        date_fmt: Date format for ``%(asctime)s``.
        stream: Output stream (defaults to ``sys.stderr``).
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers on repeated calls
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.StreamHandler)]
    root.addHandler(handler)

    # Quieten noisy third-party loggers
    for noisy in ("urllib3", "botocore", "google.auth", "google.cloud"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
