"""
Единый реестр привязок заявок: (channel_id, channel_user_id, issue_key, project_key, ticket_type_id).
Используется для «Мои заявки» и доставки уведомлений (п. 6.11 плана).
Пока хранилище — JSON; при росте нагрузки можно перейти на SQLite.
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

REGISTRY_FILE = Path(__file__).resolve().parents[2] / "data" / "issue_binding_registry.json"


def _load() -> List[Dict[str, Any]]:
    if not REGISTRY_FILE.exists():
        return []
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Ошибка загрузки реестра привязок: %s", e)
        return []
    return data if isinstance(data, list) else []


def _save(records: List[Dict[str, Any]]) -> None:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def add_binding(
    channel_id: str,
    channel_user_id: int,
    issue_key: str,
    project_key: str,
    ticket_type_id: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Добавить привязку заявки к пользователю в канале."""
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
    records = _load()
    return [r for r in records if r.get("channel_id") == channel_id and r.get("channel_user_id") == channel_user_id]


def get_bindings_by_issue(issue_key: str) -> List[Dict[str, Any]]:
    """Все привязки по issue_key."""
    key = (issue_key or "").strip().upper()
    if not key:
        return []
    records = _load()
    return [r for r in records if (r.get("issue_key") or "").strip().upper() == key]


def get_user_ids_by_issue(issue_key: str) -> List[tuple]:
    """По issue_key вернуть список (channel_id, channel_user_id) для доставки уведомлений."""
    key = (issue_key or "").strip().upper()
    if not key:
        return []
    records = _load()
    return [(r["channel_id"], r["channel_user_id"]) for r in records if r.get("issue_key") == key]


def get_all_issue_keys() -> List[str]:
    """Все уникальные issue_key из реестра (для циклов уведомлений)."""
    records = _load()
    keys = set()
    for r in records:
        k = (r.get("issue_key") or "").strip().upper()
        if k:
            keys.add(k)
    return sorted(keys)


def get_all_bindings() -> List[Dict[str, Any]]:
    """Все записи реестра привязок."""
    return _load()


def remove_binding(issue_key: str, channel_id: str, channel_user_id: int) -> bool:
    """Удалить привязку (например после Resolved/Rejected)."""
    key = (issue_key or "").strip().upper()
    records = _load()
    new_records = [r for r in records if not (r.get("issue_key") == key and r.get("channel_id") == channel_id and r.get("channel_user_id") == channel_user_id)]
    if len(new_records) == len(records):
        return False
    _save(new_records)
    return True


def remove_bindings_by_issue(issue_key: str) -> int:
    """Удалить все привязки по issue_key (например заявка удалена в Jira). Возвращает количество удалённых записей."""
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
