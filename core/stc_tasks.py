"""Логика роли «Системный администратор СТЦ»."""

from typing import Any, Dict, List, Optional, Tuple

from config import is_stc_sa
from user_storage import (
    get_user_profile,
    find_users_by_jira_username,
    get_linked_max_user_ids,
)
from core.support.issue_binding_registry import get_all_issue_keys, get_bindings_by_issue
from core.jira_aa import get_issue_admin_details
from core.support.api import MY_TICKETS_EXCLUDED_STATUSES
from core.jira_status_ru import jira_status_display_ru


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _creator_label(binding: Dict[str, Any]) -> str:
    ch = (binding.get("channel_id") or "telegram").strip().lower()
    uid = int(binding.get("channel_user_id") or 0)
    profile = get_user_profile(uid, ch) or {}
    full_name = (profile.get("full_name") or "").strip()
    login = (profile.get("login") or "").strip()
    if full_name and login:
        return f"{full_name} ({login})"
    return full_name or login or f"{ch}:{uid}"


def _creator_pairs(issue_key: str) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    for b in get_bindings_by_issue(issue_key):
        ch = (b.get("channel_id") or "telegram").strip().lower()
        try:
            uid = int(b.get("channel_user_id"))
        except Exception:
            continue
        out.append((ch, uid))
    return out


async def can_stc_user_access_issue(channel_id: str, user_id: int, issue_key: str) -> bool:
    """Доступ СА СТЦ к заявке: роль + заявка из реестра + assignee == jira_username пользователя."""
    if not is_stc_sa(channel_id, user_id):
        return False
    profile = get_user_profile(user_id, channel_id) or {}
    me = _norm(profile.get("jira_username") or "")
    if not me:
        return False
    bindings = get_bindings_by_issue(issue_key)
    if not bindings:
        return False
    info = await get_issue_admin_details(issue_key)
    if not info:
        return False
    assignee = _norm(info.get("assignee_username") or "")
    if assignee != me:
        return False
    return True


async def get_stc_assignee_tasks(channel_id: str, user_id: int) -> List[Dict[str, Any]]:
    """
    Список заявок из реестра (созданы через бота), где assignee == jira_username текущего СА СТЦ.
    Включаются все заявки из реестра, где пользователь текущий assignee.
    """
    if not is_stc_sa(channel_id, user_id):
        return []
    profile = get_user_profile(user_id, channel_id) or {}
    my_jira = _norm(profile.get("jira_username") or "")
    if not my_jira:
        return []
    tasks: List[Dict[str, Any]] = []
    for issue_key in get_all_issue_keys():
        bindings = get_bindings_by_issue(issue_key)
        if not bindings:
            continue
        info = await get_issue_admin_details(issue_key)
        if not info:
            continue
        assignee = _norm(info.get("assignee_username") or "")
        if assignee != my_jira:
            continue
        status = _norm(info.get("status") or "")
        # Поведение как в «Мои заявки»: скрываем финальные/закрытые статусы.
        if status in MY_TICKETS_EXCLUDED_STATUSES:
            continue
        first = bindings[0]
        from core.support.api import get_ticket_type_label
        tasks.append(
            {
                "issue_key": issue_key,
                "project_key": first.get("project_key"),
                "ticket_type_id": first.get("ticket_type_id"),
                "request_type_label": get_ticket_type_label(first.get("ticket_type_id"), first.get("project_key")),
                "creator": _creator_label(first),
                "summary": info.get("summary") or "—",
                "status": jira_status_display_ru(info.get("status")),
                "description": info.get("description") or "",
                "assignee_display": info.get("assignee_display") or "",
                "reporter_display": info.get("reporter_display") or "",
            }
        )
    tasks.sort(key=lambda x: x.get("issue_key", ""), reverse=True)
    return tasks


def get_stc_recipients_by_jira_username(jira_username: str) -> List[Tuple[str, int]]:
    """
    Получатели (канал, user_id) с ролью СА СТЦ для заданного jira_username.
    Раскрывает привязки Telegram↔MAX.
    """
    target = _norm(jira_username)
    if not target:
        return []
    recipients: List[Tuple[str, int]] = []
    seen = set()
    for tg_uid in find_users_by_jira_username(target):
        # 1) Если профиль реально хранится как telegram_id для роли СА СТЦ — добавляем как telegram.
        if is_stc_sa("telegram", tg_uid):
            key = ("telegram", tg_uid)
            if key not in seen:
                seen.add(key)
                recipients.append(key)

        # 2) Если профиль реально является MAX пользователем (в аккаунте USE_TELEGRAMM=0 иногда нет привязки
        # Telegram↔MAX в index_by_max_user.json), добавляем MAX получателя напрямую по STC_SA_MAX_IDS.
        if is_stc_sa("max", tg_uid):
            key = ("max", tg_uid)
            if key not in seen:
                seen.add(key)
                recipients.append(key)

        # 3) Дополнительно раскрываем привязки Telegram↔MAX, если они настроены.
        for max_uid in get_linked_max_user_ids(tg_uid):
            if is_stc_sa("max", max_uid):
                key = ("max", max_uid)
                if key not in seen:
                    seen.add(key)
                    recipients.append(key)
    return recipients
