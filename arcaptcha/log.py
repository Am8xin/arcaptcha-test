"""Shared loguru configuration for the ARCcaptcha scripts."""

import sys

from loguru import logger

LOG_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{file.name}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def setup_logging(level="DEBUG"):
    """Configure colored loguru output and return the shared logger."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=LOG_FORMAT,
        backtrace=True,
    )
    return logger


__all__ = ["logger", "setup_logging"]