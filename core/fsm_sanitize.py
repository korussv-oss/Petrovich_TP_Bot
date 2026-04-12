"""
Sanitizers for file-backed FSM storage.

We keep it conservative: only remove malformed entries, never "guess" user intent.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict


logger = logging.getLogger(__name__)


def sanitize_fsm_file(path: str | Path = "data/fsm_state.json", *, save: bool = True) -> Dict[str, int]:
    """
    Sanitize aiogram JsonFsmStorage file:
    - keep only dict entries
    - keep only entries that have at least one of: "state", "data"
    - drop keys that don't look like "<bot_id>:<chat_id>:<user_id>" with ints

    Returns stats: {"before": N, "after": M, "removed": R}.
    """
    p = Path(path)
    if not p.exists():
        return {"before": 0, "after": 0, "removed": 0}

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("FSM sanitize: cannot read %s: %s", p, e)
        return {"before": 0, "after": 0, "removed": 0}

    if not isinstance(raw, dict):
        return {"before": 0, "after": 0, "removed": 0}

    before = len(raw)
    out: dict[str, Any] = {}
    removed = 0

    for k, v in raw.items():
        if not isinstance(k, str):
            removed += 1
            continue
        parts = k.split(":")
        if len(parts) != 3:
            removed += 1
            continue
        try:
            int(parts[0]); int(parts[1]); int(parts[2])
        except Exception:
            removed += 1
            continue
        if not isinstance(v, dict):
            removed += 1
            continue
        if not (v.get("state") or v.get("data")):
            removed += 1
            continue
        out[k] = v

    after = len(out)
    if save and (after != before):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(p) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(p))
            logger.info("FSM sanitize: %s -> %s (removed=%s)", before, after, removed)
        except Exception as e:
            logger.warning("FSM sanitize: cannot write %s: %s", p, e)

    return {"before": before, "after": after, "removed": removed}

