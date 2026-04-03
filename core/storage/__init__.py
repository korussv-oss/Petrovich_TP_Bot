from __future__ import annotations

import os


def use_sqlite_storage() -> bool:
    return (os.getenv("USE_SQLITE_STORAGE") or "").strip().lower() in ("1", "true", "yes", "on")

