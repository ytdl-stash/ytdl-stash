"""Centralized logging configuration with console, file, and in-memory ring buffer handlers."""

import logging
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path


class MemoryLogHandler(logging.Handler):
    """Thread-safe in-memory ring buffer log handler for the web UI.

    Stores the most recent ``max_records`` formatted log entries so the
    log-viewer page can display them without touching the filesystem.
    """

    def __init__(self, max_records: int = 2000) -> None:
        super().__init__()
        self._records: deque[dict] = deque(maxlen=max_records)
        self._lock_rw = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            # Timestamp is the first two space-separated tokens (date + time)
            parts = formatted.split(" ", 2)
            timestamp = f"{parts[0]} {parts[1]}" if len(parts) >= 2 else ""
            # Include exception traceback in message when present
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                if not record.exc_text:
                    record.exc_text = self.formatException(record.exc_info)
                message = message + "\n" + record.exc_text
            entry = {
                "timestamp": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                "formatted": formatted,
            }
            with self._lock_rw:
                self._records.append(entry)
        except Exception:
            self.handleError(record)

    def get_records(
        self,
        limit: int = 200,
        level: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        """Return recent log entries, newest first, optionally filtered."""
        with self._lock_rw:
            entries = list(self._records)

        if level:
            level_upper = level.upper()
            level_num = getattr(logging, level_upper, None)
            if level_num is not None:
                entries = [e for e in entries if getattr(logging, e["level"], 0) >= level_num]

        if search:
            search_lower = search.lower()
            entries = [e for e in entries if search_lower in e["formatted"].lower()]

        # Newest first, limited
        return list(reversed(entries[-limit:]))

    def clear(self) -> None:
        """Clear all stored records."""
        with self._lock_rw:
            self._records.clear()


# Module-level singleton so routes can access it
_memory_handler: MemoryLogHandler | None = None


def get_memory_handler() -> MemoryLogHandler:
    """Return the singleton memory handler. Raises if setup_logging() hasn't been called."""
    if _memory_handler is None:
        raise RuntimeError("setup_logging() has not been called yet")
    return _memory_handler


def setup_logging(log_level: str = "INFO", data_dir: str = "/app/data") -> None:
    """Configure the root logger with console, rotating file, and memory handlers.

    Call once during app startup (lifespan), replacing the previous
    ``logging.basicConfig()`` call.
    """
    global _memory_handler

    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (e.g. from basicConfig or prior calls)
    for h in root.handlers[:]:
        root.removeHandler(h)

    # --- Console handler (stdout) ---
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # --- Rotating file handler ---
    log_dir = Path(data_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ytdl-stash.log"
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,  # 5 MB per file
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # --- In-memory ring buffer handler (for web UI) ---
    _memory_handler = MemoryLogHandler(max_records=2000)
    _memory_handler.setLevel(level)
    _memory_handler.setFormatter(formatter)
    root.addHandler(_memory_handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
