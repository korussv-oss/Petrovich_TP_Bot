"""
Единый реестр привязок заявок: (channel_id, channel_user_id, issue_key, project_key, ticket_type_id).
Используется для «Мои заявки» и доставки уведомлений (п. 6.11 плана).
Пока хранилище — JSON; при росте нагрузки можно перейти на SQLite.
"""
import json
import logging
import os
import re
import threading
import time as _time_module
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

REGISTRY_FILE = Path(__file__).resolve().parents[2] / "data" / "issue_binding_registry.json"

# SQLite storage (optional)
from core.storage import use_sqlite_storage
from core.storage import sqlite_backend as _sqlite

# In-memory кэш реестра: каждая функция чтения (_load) возвращает кэшированный список,
# а каждая запись (_save) немедленно инвалидирует и обновляет кэш.
_registry_lock = threading.Lock()
_registry_cache: Optional[List[Dict[str, Any]]] = None
_registry_cache_loaded_at: float = 0.0
_REGISTRY_CACHE_TTL = 15.0  # секунд; при записи обновляется немедленно
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def sanitize_registry(*, save: bool = True) -> Dict[str, int]:
    """
    Санитаризация JSON-реестра привязок (не SQLite):
    - удаляет записи без обязательных полей
    - нормализует issue_key (strip/upper)
    - удаляет дубликаты по (issue_key, channel_id, channel_user_id)
    - заполняет created_at, если его нет

    Возвращает статистику изменений: {"before": N, "after": M, "removed": R, "deduped": D, "fixed": F}
    """
    if use_sqlite_storage():
        return {"before": 0, "after": 0, "removed": 0, "deduped": 0, "fixed": 0}

    records = _load()
    before = len(records)
    out: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    removed = 0
    deduped = 0
    fixed = 0

    import time as _time

    for r in records:
        if not isinstance(r, dict):
            removed += 1
            continue

        issue_key_raw = r.get("issue_key")
        channel_id_raw = r.get("channel_id")
        user_id_raw = r.get("channel_user_id")

        issue_key = (str(issue_key_raw).strip().upper() if issue_key_raw is not None else "")
        channel_id = (str(channel_id_raw).strip() if channel_id_raw is not None else "")

        try:
            channel_user_id = int(user_id_raw)
        except Exception:
            channel_user_id = -1

        if not issue_key or not channel_id or channel_user_id < 0:
            removed += 1
            continue
        if not _ISSUE_KEY_RE.match(issue_key):
            removed += 1
            continue

        key = (issue_key, channel_id, channel_user_id)
        if key in seen:
            deduped += 1
            continue
        seen.add(key)

        # Нормализация + исправления
        new_r = dict(r)
        if new_r.get("issue_key") != issue_key:
            new_r["issue_key"] = issue_key
            fixed += 1
        if new_r.get("channel_id") != channel_id:
            new_r["channel_id"] = channel_id
            fixed += 1
        if new_r.get("channel_user_id") != channel_user_id:
            new_r["channel_user_id"] = channel_user_id
            fixed += 1

        if not new_r.get("created_at"):
            new_r["created_at"] = round(_time.time(), 2)
            fixed += 1

        out.append(new_r)

    after = len(out)
    changed = (after != before) or removed or deduped or fixed
    if changed and save:
        _save(out)
        logger.info(
            "Реестр: санитаризация выполнена (before=%s, after=%s, removed=%s, deduped=%s, fixed=%s)",
            before,
            after,
            removed,
            deduped,
            fixed,
        )
    return {"before": before, "after": after, "removed": removed, "deduped": deduped, "fixed": fixed}


def _load() -> List[Dict[str, Any]]:
    global _registry_cache, _registry_cache_loaded_at
    now = _time_module.monotonic()
    with _registry_lock:
        if _registry_cache is not None and (now - _registry_cache_loaded_at) < _REGISTRY_CACHE_TTL:
            return list(_registry_cache)
        if not REGISTRY_FILE.exists():
            _registry_cache = []
            _registry_cache_loaded_at = now
            return []
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Ошибка загрузки реестра привязок: %s", e)
            return list(_registry_cache) if _registry_cache is not None else []
        result = data if isinstance(data, list) else []
        _registry_cache = result
        _registry_cache_loaded_at = now
        return list(result)


def _save(records: List[Dict[str, Any]]) -> None:
    """Атомарная запись: пишем во временный файл, затем переименовываем.
    Это гарантирует, что при сбое процесса старый файл остаётся целым.
    """
    global _registry_cache, _registry_cache_loaded_at
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(REGISTRY_FILE) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, str(REGISTRY_FILE))
    with _registry_lock:
        _registry_cache = list(records)
        _registry_cache_loaded_at = _time_module.monotonic()


def add_binding(
    channel_id: str,
    channel_user_id: int,
    issue_key: str,
    project_key: str,
    ticket_type_id: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Добавить привязку заявки к пользователю в канале."""
    if use_sqlite_storage():
        _sqlite.add_issue_binding(
            channel_id=channel_id,
            channel_user_id=channel_user_id,
            issue_key=issue_key,
            project_key=project_key,
            ticket_type_id=ticket_type_id,
            created_at=(extra or {}).get("created_at") if isinstance(extra, dict) else None,
            extra=extra,
        )
        return
    key = (issue_key or "").strip().upper()
    if not key:
        return
    records = _load()
    # Не дублируем ту же пару (issue_key, channel_user_id)
    for r in records:
        if r.get("issue_key") == key and r.get("channel_id") == channel_id and r.get("channel_user_id") == channel_user_id:
            return
    import time
    records.append({
        "channel_id": channel_id,
        "channel_user_id": channel_user_id,
        "issue_key": key,
        "project_key": (project_key or "").strip(),
        "ticket_type_id": (ticket_type_id or "").strip(),
        "created_at": extra.get("created_at") if isinstance(extra, dict) else None,
        **(extra or {}),
    })
    # created_at может прийти как ключ со значением null/None — это ломает определение «свежести».
    if not records[-1].get("created_at"):
        records[-1]["created_at"] = round(time.time(), 2)
    _save(records)
    logger.debug("Реестр: добавлена привязка %s -> %s/%s", key, channel_id, channel_user_id)


def get_bindings_by_user(channel_id: str, channel_user_id: int) -> List[Dict[str, Any]]:
    """Все заявки пользователя в канале (для «Мои заявки»)."""
    if use_sqlite_storage():
        return _sqlite.get_issue_bindings_by_user(channel_id, channel_user_id)
    records = _load()
    return [r for r in records if r.get("channel_id") == channel_id and r.get("channel_user_id") == channel_user_id]


def get_bindings_by_issue(issue_key: str) -> List[Dict[str, Any]]:
    """Все привязки по issue_key."""
    if use_sqlite_storage():
        return _sqlite.get_issue_bindings_by_issue(issue_key)
    key = (issue_key or "").strip().upper()
    if not key:
        return []
    records = _load()
    return [r for r in records if (r.get("issue_key") or "").strip().upper() == key]


def get_user_ids_by_issue(issue_key: str) -> List[tuple]:
    """По issue_key вернуть список (channel_id, channel_user_id) для доставки уведомлений."""
    if use_sqlite_storage():
        return _sqlite.get_issue_user_ids_by_issue(issue_key)
    key = (issue_key or "").strip().upper()
    if not key:
        return []
    records = _load()
    return [(r["channel_id"], r["channel_user_id"]) for r in records if r.get("issue_key") == key]


def get_all_issue_keys() -> List[str]:
    """Все уникальные issue_key из реестра (для циклов уведомлений)."""
    if use_sqlite_storage():
        return _sqlite.list_all_issue_keys()
    records = _load()
    keys = set()
    for r in records:
        k = (r.get("issue_key") or "").strip().upper()
        if k:
            keys.add(k)
    return sorted(keys)


def get_all_bindings() -> List[Dict[str, Any]]:
    """Все записи реестра привязок."""
    if use_sqlite_storage():
        return _sqlite.list_all_issue_bindings()
    return _load()


def remove_binding(issue_key: str, channel_id: str, channel_user_id: int) -> bool:
    """Удалить привязку (например после Resolved/Rejected)."""
    if use_sqlite_storage():
        return _sqlite.remove_issue_binding(issue_key, channel_id, channel_user_id)
    key = (issue_key or "").strip().upper()
    records = _load()
    new_records = [r for r in records if not (r.get("issue_key") == key and r.get("channel_id") == channel_id and r.get("channel_user_id") == channel_user_id)]
    if len(new_records) == len(records):
        return False
    _save(new_records)
    return True


def remove_bindings_by_issue(issue_key: str) -> int:
    """Удалить все привязки по issue_key (например заявка удалена в Jira). Возвращает количество удалённых записей."""
    if use_sqlite_storage():
        removed = _sqlite.remove_issue_bindings_by_issue(issue_key)
        if removed:
            logger.info("Реестр: удалены привязки для заявки %s (записей: %s)", (issue_key or "").strip().upper(), removed)
        return removed
    key = (issue_key or "").strip().upper()
    if not key:
        return 0
    records = _load()
    new_records = [r for r in records if r.get("issue_key") != key]
    removed = len(records) - len(new_records)
    if removed:
        _save(new_records)
        logger.info("Реестр: удалены привязки для заявки %s (записей: %s)", key, removed)
    return removed
