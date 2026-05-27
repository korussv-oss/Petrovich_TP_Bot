"""Защита от повторного создания заявки в MAX (двойной клик / повтор апдейта)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional

_guard = None


def _ttl_seconds() -> float:
    try:
        return float(os.getenv("MAX_TICKET_CREATE_DEDUP_TTL_SECONDS", "120"))
    except ValueError:
        return 120.0


def ticket_create_fingerprint(
    user_id: int,
    ticket_type_id: str,
    form_data: Optional[Dict[str, Any]] = None,
) -> str:
    payload = {
        "user_id": int(user_id),
        "ticket_type_id": (ticket_type_id or "").strip(),
        "form_data": form_data or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MaxTicketCreateGuard:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight_by_user: Dict[int, str] = {}
        self._recent: Dict[str, tuple[float, dict]] = {}

    def _purge_recent(self, now: float) -> None:
        ttl = _ttl_seconds()
        stale = [fp for fp, (ts, _) in self._recent.items() if now - ts > ttl]
        for fp in stale:
            self._recent.pop(fp, None)

    async def run(
        self,
        user_id: int,
        ticket_type_id: str,
        form_data: Optional[Dict[str, Any]],
        factory: Callable[[], Awaitable[dict]],
    ) -> dict:
        uid = int(user_id)
        fp = ticket_create_fingerprint(uid, ticket_type_id, form_data)
        now = time.monotonic()

        async with self._lock:
            self._purge_recent(now)
            cached = self._recent.get(fp)
            if cached and (now - cached[0]) < _ttl_seconds():
                return dict(cached[1])
            if self._inflight_by_user.get(uid) == fp:
                return {
                    "text": "⏳ Заявка уже создаётся, подождите несколько секунд…",
                    "parse_mode": "HTML",
                    "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                }
            if uid in self._inflight_by_user:
                return {
                    "text": "⏳ Дождитесь завершения предыдущего создания заявки.",
                    "parse_mode": "HTML",
                    "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                }
            self._inflight_by_user[uid] = fp

        try:
            response = await factory()
        finally:
            async with self._lock:
                if self._inflight_by_user.get(uid) == fp:
                    self._inflight_by_user.pop(uid, None)

        async with self._lock:
            if isinstance(response, dict) and response.get("text"):
                self._recent[fp] = (time.monotonic(), dict(response))
        return response


def get_max_ticket_create_guard() -> MaxTicketCreateGuard:
    global _guard
    if _guard is None:
        _guard = MaxTicketCreateGuard()
    return _guard
