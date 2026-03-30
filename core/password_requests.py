"""
Ожидающие заявки на смену пароля: храним issue_key -> user_id для оповещения при смене статуса.
При статусе Resolved — «Пароль успешно изменён»; при Отклонено — «В смене пароля отказано...».
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from user_storage import get_user_profile
from core.support import delivery as delivery_module

logger = logging.getLogger(__name__)

# Статусы, при которых считаем пароль успешно изменённым
STATUS_RESOLVED = frozenset({"resolved", "готово", "исправлено", "done"})
# Статусы отказа в смене пароля
STATUS_REJECTED = frozenset({"отклонено", "rejected", "declined"})

PENDING_FILE = Path(__file__).resolve().parent.parent / "data" / "pending_password_requests.json"


def _load() -> Dict[str, Dict]:
    if not PENDING_FILE.exists():
        return {}
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Ошибка загрузки pending_password_requests: %s", e)
        return {}


def _save(data: Dict[str, Dict]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_pending(issue_key: str, user_id: int, channel_id: str = "telegram") -> None:
    """Регистрирует заявку для оповещения пользователя при смене статуса (в нужном канале)."""
    key = (issue_key or "").strip().upper()
    if not key:
        return
    data = _load()
    data[key] = {"user_id": user_id, "channel_id": (channel_id or "telegram").strip().lower()}
    _save(data)
    logger.debug("Добавлена ожидающая заявка %s для %s/user_id=%s", key, channel_id, user_id)


def get_all_pending() -> List[Tuple[str, int, str]]:
    """Возвращает список (issue_key, user_id, channel_id) по всем ожидающим заявкам."""
    data = _load()
    return [
        (k, v["user_id"], (v.get("channel_id") or "telegram").strip().lower())
        for k, v in data.items()
        if isinstance(v.get("user_id"), int)
    ]


def _get_pending_data() -> Dict[str, Dict]:
    """Возвращает полные данные по ожидающим заявкам (включая last_comment_count)."""
    return _load()


def _comment_body_plain(comment: Dict[str, Any], max_len: int = 500) -> str:
    """Извлекает простой текст из body комментария (строка или ADF)."""
    body = comment.get("body")
    if body is None:
        return ""
    if isinstance(body, str):
        text = body
    elif isinstance(body, dict):
        # Atlassian Document Format: рекурсивно собрать text из content
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


def _set_last_comment_count(issue_key: str, count: int) -> None:
    """Сохраняет количество комментариев для заявки (для уведомлений о новых)."""
    key = (issue_key or "").strip().upper()
    if not key:
        return
    data = _load()
    if key not in data:
        return
    data[key]["last_comment_count"] = count
    _save(data)


def get_pending_issue_key_by_user(user_id: int) -> Optional[str]:
    """Возвращает issue_key ожидающей заявки на смену пароля для пользователя или None."""
    for issue_key, uid, _ in get_all_pending():
        if uid == user_id:
            return issue_key
    return None


def remove_pending(issue_key: str) -> None:
    """Удаляет заявку из ожидающих (после оповещения)."""
    key = (issue_key or "").strip().upper()
    if not key:
        return
    data = _load()
    if key in data:
        del data[key]
        _save(data)


CHANNEL_ID_TELEGRAM = "telegram"


async def check_statuses_and_notify() -> None:
    """
    Проверяет статусы всех ожидающих заявок в Jira.
    При статусе Resolved — «Пароль успешно изменён»; при Отклонено — отказ.
    Доставка через core.support.delivery (без привязки к aiogram).
    """
    from core.jira_aa import get_issue_status

    pending = get_all_pending()
    if not pending:
        return
    for issue_key, user_id, channel_id in pending:
        try:
            status = await get_issue_status(issue_key)
            if not status:
                continue
            status_lower = status.lower().strip()
            if status_lower in STATUS_RESOLVED:
                try:
                    await delivery_module.deliver(
                        channel_id,
                        user_id,
                        "✅ <b>Пароль успешно изменён.</b>\n\n" f"Заявка {issue_key} выполнена.",
                        reply_markup=None,
                    )
                except Exception as e:
                    logger.warning("Не удалось отправить оповещение %s/user_id=%s (Resolved): %s", channel_id, user_id, e)
                remove_pending(issue_key)
            elif status_lower in STATUS_REJECTED:
                try:
                    await delivery_module.deliver(
                        channel_id,
                        user_id,
                        "❌ <b>В смене пароля отказано.</b>\n\n"
                        "Обратитесь к вашему системному администратору.",
                        reply_markup=None,
                    )
                except Exception as e:
                    logger.warning("Не удалось отправить оповещение %s/user_id=%s (Отклонено): %s", channel_id, user_id, e)
                remove_pending(issue_key)
        except Exception as e:
            logger.warning("Ошибка проверки заявки %s: %s", issue_key, e)
        await asyncio.sleep(0.5)


async def check_comments_and_notify() -> None:
    """Проверяет новые комментарии по ожидающим заявкам и отправляет уведомление пользователю."""
    from core.jira_aa import get_issue_comments

    pending = get_all_pending()
    if not pending:
        return
    data = _get_pending_data()
    for issue_key, user_id, channel_id in pending:
        try:
            comments = await get_issue_comments(issue_key)
            if comments is None:
                continue
            current_count = len(comments)
            last_count = data.get(issue_key, {}).get("last_comment_count")
            if last_count is None:
                _set_last_comment_count(issue_key, current_count)
            elif current_count > last_count:
                try:
                    new_count = current_count - last_count
                    new_comments = comments[-new_count:]
                    profile = get_user_profile(user_id, channel_id) or {}
                    user_full_name = (profile.get("full_name") or "").strip()
                    # Не уведомляем о комментариях, которые пользователь сам написал через бота ([ФИО] текст)
                    from_bot_prefix = f"[{user_full_name}]" if user_full_name else None
                    other_comments = []
                    for c in new_comments:
                        plain = _comment_body_plain(c)
                        if from_bot_prefix and plain.strip().startswith(from_bot_prefix):
                            continue
                        other_comments.append(c)
                    if not other_comments:
                        _set_last_comment_count(issue_key, current_count)
                        continue
                    lines = []
                    for c in other_comments:
                        author = (c.get("author") or {}).get("displayName", "—")
                        plain = _comment_body_plain(c)
                        if plain:
                            lines.append(f"👤 {author}:\n{plain}")
                        else:
                            lines.append(f"👤 {author}: (без текста)")
                    comment_block = "\n\n".join(lines)
                    title = (
                        f"💬 Новый комментарий в заявке {issue_key}:"
                        if len(other_comments) == 1
                        else f"💬 Новые комментарии в заявке {issue_key}:"
                    )
                    reply_markup = [
                        [{"text": "✏️ Написать комментарий", "callback_data": f"add_comment:{issue_key}"}],
                    ]
                    await delivery_module.deliver(
                        channel_id,
                        user_id,
                        f"{title}\n\n{comment_block}",
                        reply_markup=reply_markup,
                    )
                except Exception as e:
                    logger.warning("Не удалось отправить уведомление о комментарии %s/user_id=%s: %s", channel_id, user_id, e)
                _set_last_comment_count(issue_key, current_count)
        except Exception as e:
            logger.warning("Ошибка проверки комментариев заявки %s: %s", issue_key, e)
        await asyncio.sleep(0.3)


async def run_status_checker_loop(interval_seconds: int = 90) -> None:
    """Фоновая задача: раз в interval_seconds проверяет статусы заявок и отправляет оповещения через delivery."""
    logger.info("Запущен проверщик статусов заявок на смену пароля (интервал %s с)", interval_seconds)
    while True:
        try:
            await check_statuses_and_notify()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Ошибка в проверщике статусов: %s", e)
        await asyncio.sleep(interval_seconds)


async def run_comments_checker_loop(interval_seconds: int = 30) -> None:
    """Фоновая задача: раз в interval_seconds проверяет новые комментарии и отправляет уведомления через delivery."""
    logger.info("Запущен проверщик комментариев заявок (интервал %s с)", interval_seconds)
    while True:
        try:
            await check_comments_and_notify()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Ошибка в проверщике комментариев: %s", e)
        await asyncio.sleep(interval_seconds)
