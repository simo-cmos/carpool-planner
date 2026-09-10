"""
Drivers Manager Project — Structured Performance & Debug Logging
================================================================
Provides LOG_INFO and LOG_DEBUG helpers that automatically capture
timestamp, source file, and calling function name.

Usage in any module:
    from logging_config import LOG_INFO, LOG_DEBUG

    LOG_INFO("server started on port 8000")
    LOG_DEBUG("distance matrix has 12 nodes")

Log files are written to a ``logs/`` folder (one file per execution,
named with the startup timestamp).

To enable DEBUG-level (verbose) logging, start the app with the
``--debug`` flag or set the environment variable ``DMPROJECT_DEBUG=1``:

    python -m uvicorn app.main:app --reload -- --debug
    DMPROJECT_DEBUG=1 python -m uvicorn app.main:app --reload
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_logger: logging.Logger | None = None
_debug_enabled: bool = False
_initialized: bool = False


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_logging(*, force_debug: bool = False) -> None:
    """
    Set up the per-execution log file and console handler.

    Call this once at application startup (e.g. in ``app.main.on_startup``).
    Repeated calls are safe — only the first one takes effect.

    Parameters
    ----------
    force_debug : bool
        When *True*, the log level is set to DEBUG regardless of CLI
        flags or environment variables.
    """
    global _logger, _debug_enabled, _initialized
    if _initialized:
        return
    _initialized = True

    # Detect debug mode from CLI flags or environment variable
    _debug_enabled = (
        force_debug
        or os.environ.get("DMPROJECT_DEBUG", "").strip().lower() in ("1", "true", "yes")
        or "--debug" in sys.argv
    )

    # Ensure the logs/ directory exists
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Build the log filename from the startup timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = _LOG_DIR / f"{timestamp}.log"

    # Create the logger
    _logger = logging.getLogger("dmproject")
    _logger.setLevel(logging.DEBUG if _debug_enabled else logging.INFO)
    _logger.propagate = False

    # File handler — always receives every message that passes the level
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG if _debug_enabled else logging.INFO)
    file_formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d  %(levelname)-5s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    _logger.addHandler(file_handler)

    # Console handler — only INFO+ to keep the terminal clean
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if _debug_enabled else logging.INFO)
    console_formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    _logger.addHandler(console_handler)

    # First message
    _logger.info("=== Drivers Manager Project - log session started ===")
    _logger.info("Log file : %s", log_file)
    _logger.info("Debug    : %s", "ON" if _debug_enabled else "OFF")


def is_debug_enabled() -> bool:
    """Return whether DEBUG-level logging is active."""
    return _debug_enabled


# ---------------------------------------------------------------------------
# Public logging helpers
# ---------------------------------------------------------------------------

def _caller_context(stack_level: int = 2) -> tuple[str, str]:
    """
    Walk the call stack to extract the **source filename** (without
    full path) and the **function name** of the actual caller.
    """
    frame = inspect.stack()[stack_level]
    filename = Path(frame.filename).name
    function = frame.function
    return filename, function


def LOG_INFO(message: str) -> None:          # noqa: N802 — intentional uppercase
    """
    Log an **INFO**-level message.

    The log line automatically includes timestamp, source file, and
    function name.  Example output::

        2026-03-28 14:05:12.345  INFO   app/main.py  on_startup — server started
    """
    if _logger is None:
        init_logging()
    filename, function = _caller_context()
    _logger.info("%-30s %-28s | %s", filename, function, message)  # type: ignore[union-attr]


def LOG_DEBUG(message: str) -> None:         # noqa: N802 — intentional uppercase
    """
    Log a **DEBUG**-level message (only written when debug mode is on).

    Same automatic context as :func:`LOG_INFO`.
    """
    if _logger is None:
        init_logging()
    if not _debug_enabled:
        return
    filename, function = _caller_context()
    _logger.debug("%-30s %-28s | %s", filename, function, message)  # type: ignore[union-attr]
