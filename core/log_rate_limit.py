"""
Helpers to reduce repetitive log noise.

Use when the same error can repeat frequently in background loops.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


_LOCK = threading.Lock()
_LAST_TS: dict[str, float] = {}


def should_log(key: str, *, interval_seconds: float = 60.0) -> bool:
    """
    Returns True if enough time passed since last log for the given key.
    Thread-safe, process-local (in-memory).
    """
    k = (key or "").strip()
    if not k:
        return True
    now = time.monotonic()
    with _LOCK:
        last = _LAST_TS.get(k)
        if last is not None and (now - last) < float(interval_seconds):
            return False
        _LAST_TS[k] = now
        return True


def log_rate_limited(
    log_fn: Callable[..., Any],
    *,
    key: str,
    interval_seconds: float = 60.0,
    msg: str,
    args: tuple[Any, ...] = (),
) -> None:
    """
    Calls log_fn(msg, *args) not more often than interval_seconds for the same key.
    """
    if should_log(key, interval_seconds=interval_seconds):
        try:
            log_fn(msg, *args)
        except Exception:
            # Never let logging crash production loops
            pass

