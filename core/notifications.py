"""
Уведомления о статусе и комментариях по единому реестру привязок (issue_binding_registry).
Доставка в оба канала (Telegram и MAX). При уведомлении о комментарии — кнопка «Написать комментарий».

Покрываются все типы заявок, попадающие в реестр при создании:
  wms_issue, wms_settings, wms_psi_user (PW), lupa_search (WHD), rubik_password_change (AA).
Фильтрации по ticket_type_id нет — проверяются все issue_key из реестра.
"""
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.support import delivery as delivery_module
from core.support.issue_binding_registry import get_all_issue_keys, get_user_ids_by_issue, get_bindings_by_issue

logger = logging.getLogger(__name__)

STATUS_RESOLVED = frozenset({"resolved", "готово", "исправлено", "done", "closed", "закрыто", "выполнена", "выполнено"})
STATUS_REJECTED = frozenset({"отклонено", "rejected", "declined", "отклонена"})
# Статусы, при смене на которые уведомление «Новый статус» не отправляется (ни в ТГ, ни в MAX)
STATUS_SILENT = frozenset({"waiting for customer", "ожидание ответа клиента", "ожидание клиента"})

STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "issue_notification_state.json"


def _load_state() -> Dict[str, Dict[str, Any]]:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Ошибка загрузки issue_notification_state: %s", e)
        return {}


def _save_state(data: Dict[str, Dict[str, Any]]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_last_comment_count(issue_key: str) -> Optional[int]:
    key = (issue_key or "").strip().upper()
    if not key:
        return None
    data = _load_state()
    return data.get(key, {}).get("last_comment_count")


def _set_last_comment_count(issue_key: str, count: int) -> None:
    key = (issue_key or "").strip().upper()
    if not key:
        return
    data = _load_state()
    if key not in data:
        data[key] = {}
    data[key]["last_comment_count"] = count
    _save_state(data)


def _get_last_status(issue_key: str) -> Optional[str]:
    key = (issue_key or "").strip().upper()
    if not key:
        return None
    data = _load_state()
    return data.get(key, {}).get("last_status")


def _get_last_assignee(issue_key: str) -> Optional[str]:
    key = (issue_key or "").strip().upper()
    if not key:
        return None
    data = _load_state()
    return data.get(key, {}).get("last_assignee")


def _set_last_status(issue_key: str, status: str) -> None:
    key = (issue_key or "").strip().upper()
    if not key:
        return
    data = _load_state()
    if key not in data:
        data[key] = {}
    data[key]["last_status"] = (status or "").strip()
    _save_state(data)


def _set_last_assignee(issue_key: str, assignee_username: str) -> None:
    key = (issue_key or "").strip().upper()
    if not key:
        return
    data = _load_state()
    if key not in data:
        data[key] = {}
    data[key]["last_assignee"] = (assignee_username or "").strip().lower()
    _save_state(data)


def set_issue_last_assignee_baseline(issue_key: str, assignee_username: str) -> None:
    """
    Установить baseline assignee без отправки уведомлений.
    Используется после технической переустановки исполнителя (например, при transition).
    """
    _set_last_assignee(issue_key, assignee_username)


def _comment_body_plain(comment: Dict[str, Any], max_len: int = 500) -> str:
    """Текст комментария (строка или ADF)."""
    body = comment.get("body")
    if body is None:
        return ""
    if isinstance(body, str):
        text = body
    elif isinstance(body, dict):
        parts = []

        def extract(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and "text" in node:
                    parts.append(node["text"])
                for c in node.get("content") or []:
                    extract(c)
            elif isinstance(node, list):
                for item in node:
                    extract(item)

        extract(body)
        text = " ".join(parts)
    else:
        text = str(body)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:max_len] + "…") if len(text) > max_len else text


def _expand_recipients_to_linked_channels(recipients: List[tuple]) -> List[tuple]:
    """
    Расширяет список (channel_id, user_id) привязанными каналами (Telegram↔MAX).
    Чтобы пользователь получал уведомления и в TG, и в MAX, если аккаунты привязаны.
    """
    from user_storage import get_linked_channel_user_pairs
    seen: set = set()
    out: List[tuple] = []
    for ch, uid in recipients:
        for c, u in get_linked_channel_user_pairs(ch, uid):
            key = (c, u)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _is_recent_issue_binding(issue_key: str, window_seconds: int = 900) -> bool:
    """True, если заявка из реестра создана недавно (по created_at любой привязки)."""
    now = time.time()
    for b in get_bindings_by_issue(issue_key):
        created = b.get("created_at")
        try:
            ts = float(created)
        except Exception:
            continue
        if 0 <= now - ts <= max(1, int(window_seconds)):
            return True
    return False


async def check_registry_statuses_and_notify() -> None:
    """
    Проверяет статусы всех заявок из реестра привязок.
    При переходе в Resolved/Rejected/Done/Closed — уведомление в TG и MAX (все привязанные каналы).
    """
    from core.jira_aa import get_issue_status
    from core.password_requests import remove_pending

    issue_keys = get_all_issue_keys()
    if not issue_keys:
        return
    for issue_key in issue_keys:
        try:
            recipients = get_user_ids_by_issue(issue_key)
            recipients = _expand_recipients_to_linked_channels(recipients)
            if not recipients:
                continue
            status = await get_issue_status(issue_key)
            if not status:
                continue
            status_lower = status.lower().strip()
            last = _get_last_status(issue_key)
            text = None
            if last is None:
                _set_last_status(issue_key, status)
                # Первый опрос: уведомляем только если статус уже финальный (заявку успели закрыть до первого опроса)
                if status_lower in STATUS_RESOLVED:
                    remove_pending(issue_key)
                    text = f"✅ <b>Заявка {issue_key}</b> выполнена.\n\nСтатус: {status}"
                elif status_lower in STATUS_REJECTED:
                    remove_pending(issue_key)
                    text = f"❌ <b>Заявка {issue_key}</b> отклонена.\n\nСтатус: {status}"
            else:
                last_lower = last.lower().strip()
                if status_lower in STATUS_RESOLVED:
                    remove_pending(issue_key)
                    if last_lower in STATUS_RESOLVED:
                        _set_last_status(issue_key, status)
                        continue
                    text = f"✅ <b>Заявка {issue_key}</b> выполнена.\n\nСтатус: {status}"
                elif status_lower in STATUS_REJECTED:
                    remove_pending(issue_key)
                    if last_lower in STATUS_REJECTED:
                        _set_last_status(issue_key, status)
                        continue
                    text = f"❌ <b>Заявка {issue_key}</b> отклонена.\n\nСтатус: {status}"
                else:
                    if last and last_lower == status_lower:
                        continue
                    if status_lower in STATUS_SILENT:
                        _set_last_status(issue_key, status)
                        continue
                    text = f"📋 <b>Заявка {issue_key}</b>\n\nНовый статус: {status}"
                _set_last_status(issue_key, status)

            if text:
                for channel_id, user_id in recipients:
                    try:
                        await delivery_module.deliver(channel_id, user_id, text, reply_markup=None)
                    except Exception as e:
                        logger.warning("Не удалось отправить уведомление о статусе %s -> %s/%s: %s", issue_key, channel_id, user_id, e)
        except Exception as e:
            logger.warning("Ошибка проверки статуса %s: %s", issue_key, e)
        await asyncio.sleep(0.3)


async def check_registry_comments_and_notify() -> None:
    """
    Проверяет новые комментарии по заявкам из реестра. Доставка в TG и MAX.
    Кнопка «Написать комментарий» (add_comment:{issue_key}).
    """
    from core.jira_aa import get_issue_comments

    issue_keys = get_all_issue_keys()
    if not issue_keys:
        return
    for issue_key in issue_keys:
        try:
            recipients = get_user_ids_by_issue(issue_key)
            recipients = _expand_recipients_to_linked_channels(recipients)
            if not recipients:
                continue
            comments = await get_issue_comments(issue_key)
            current_count = len(comments)
            last_count = _get_last_comment_count(issue_key)
            if last_count is None:
                _set_last_comment_count(issue_key, current_count)
                continue
            # Если бот был выключен/перезапускался, а baseline по комментариям "пустой" (0),
            # то при старте может полететь "волна" уведомлений по старым тикетам.
            # Для несвежих заявок просто выставляем baseline без уведомления.
            if last_count == 0 and current_count > 0 and not _is_recent_issue_binding(issue_key):
                _set_last_comment_count(issue_key, current_count)
                continue
            # После включения фильтра internal-комментариев счётчик может уменьшиться.
            # Сбрасываем baseline, чтобы цикл уведомлений не "залипал".
            if current_count < last_count:
                _set_last_comment_count(issue_key, current_count)
                continue
            if current_count <= last_count:
                continue
            new_count = current_count - last_count
            new_comments = comments[-new_count:]
            # Префиксы комментариев, написанных получателями через бота (TG/MAX): "[ФИО] текст"
            from user_storage import get_user_profile
            bot_comment_prefixes: set = set()
            for ch, uid in recipients:
                profile = get_user_profile(uid, ch) or {}
                full_name = (profile.get("full_name") or "").strip()
                if full_name:
                    bot_comment_prefixes.add(f"[{full_name}]")
            # Не уведомляем о комментариях, которые получатели сами написали через бота
            lines = []
            for c in new_comments:
                plain = _comment_body_plain(c)
                plain_stripped = (plain or "").strip()
                if bot_comment_prefixes and plain_stripped:
                    if any(plain_stripped.startswith(prefix) for prefix in bot_comment_prefixes):
                        continue
                author = (c.get("author") or {}).get("displayName", "—")
                if plain:
                    lines.append(f"👤 {author}:\n{plain}")
                else:
                    lines.append(f"👤 {author}: (без текста)")
            if not lines:
                _set_last_comment_count(issue_key, current_count)
                continue
            comment_block = "\n\n".join(lines)
            title = (
                f"💬 Новый комментарий в заявке {issue_key}:"
                if len(lines) == 1
                else f"💬 Новые комментарии в заявке {issue_key}:"
            )
            text = f"{title}\n\n{comment_block}"
            reply_markup = [
                [{"text": "✏️ Написать комментарий", "callback_data": f"add_comment:{issue_key}"}],
            ]
            for channel_id, user_id in recipients:
                try:
                    await delivery_module.deliver(channel_id, user_id, text, reply_markup=reply_markup)
                except Exception as e:
                    logger.warning("Не удалось отправить уведомление о комментарии %s -> %s/%s: %s", issue_key, channel_id, user_id, e)
            _set_last_comment_count(issue_key, current_count)
        except Exception as e:
            logger.warning("Ошибка проверки комментариев %s: %s", issue_key, e)
        await asyncio.sleep(0.3)


async def check_assignee_tasks_and_notify() -> None:
    """
    Уведомления для роли «СА СТЦ»: если у заявки из реестра сменился assignee на СА,
    отправляем уведомление новому исполнителю.
    """
    from core.jira_aa import get_issue_admin_details
    from core.stc_tasks import get_stc_recipients_by_jira_username
    from core.support.api import get_jira_browse_url

    issue_keys = get_all_issue_keys()
    if not issue_keys:
        return
    for issue_key in issue_keys:
        try:
            info = await get_issue_admin_details(issue_key)
            if not info:
                continue
            assignee_username = (info.get("assignee_username") or "").strip().lower()
            if not assignee_username:
                continue
            recipients = get_stc_recipients_by_jira_username(assignee_username)
            last_assignee = (_get_last_assignee(issue_key) or "").strip().lower()
            if not last_assignee:
                # Для новых заявок шлём уведомление сразу; для исторических — только baseline.
                _set_last_assignee(issue_key, assignee_username)
                if not recipients or not _is_recent_issue_binding(issue_key):
                    continue
            if last_assignee == assignee_username:
                continue
            if last_assignee:
                _set_last_assignee(issue_key, assignee_username)
            if not recipients:
                continue
            summary = (info.get("summary") or "—").strip()
            status = (info.get("status") or "—").strip()
            assignee_display = (info.get("assignee_display") or assignee_username).strip()
            browse_url = get_jira_browse_url(issue_key)
            text = (
                f"🛠️ <b>Новая задача для СА СТЦ</b>\n\n"
                f"Заявка: {issue_key}\n"
                f"Тема: {summary}\n"
                f"Статус: {status}\n"
                f"Assignee: {assignee_display}\n"
            )
            if browse_url:
                text += f'\n🔗 <a href="{browse_url}">Открыть в Jira</a>'
            reply_markup = [
                [{"text": "📋 Мои задачи", "callback_data": "sa_stc_my_tasks"}],
                [{"text": "🔎 Открыть задачу", "callback_data": f"stc_open_issue:{issue_key}"}],
            ]
            for channel_id, user_id in recipients:
                try:
                    await delivery_module.deliver(channel_id, user_id, text, reply_markup=reply_markup)
                except Exception as e:
                    logger.warning(
                        "Не удалось отправить уведомление assignee %s -> %s/%s: %s",
                        issue_key,
                        channel_id,
                        user_id,
                        e,
                    )
        except Exception as e:
            logger.warning("Ошибка уведомления assignee %s: %s", issue_key, e)
        await asyncio.sleep(0.3)


async def run_registry_status_loop(interval_seconds: int = 90) -> None:
    """Цикл проверки статусов по реестру."""
    logger.info("Запущен проверщик статусов по реестру (интервал %s с)", interval_seconds)
    while True:
        try:
            await check_registry_statuses_and_notify()
            await check_assignee_tasks_and_notify()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Ошибка в проверщике статусов по реестру: %s", e)
        await asyncio.sleep(interval_seconds)


async def run_registry_comments_loop(interval_seconds: int = 30) -> None:
    """Цикл проверки комментариев по реестру."""
    logger.info("Запущен проверщик комментариев по реестру (интервал %s с)", interval_seconds)
    while True:
        try:
            await check_registry_comments_and_notify()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Ошибка в проверщике комментариев по реестру: %s", e)
        await asyncio.sleep(interval_seconds)
