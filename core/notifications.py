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
import os
import re
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from core.support import delivery as delivery_module
from core.support.issue_binding_registry import get_all_issue_keys, get_user_ids_by_issue, get_bindings_by_issue
from core.jira_status_ru import jira_status_display_ru

logger = logging.getLogger(__name__)

# SQLite storage (optional)
from core.storage import use_sqlite_storage
from core.storage import sqlite_backend as _sqlite

STATUS_RESOLVED = frozenset({"resolved", "готово", "исправлено", "done", "closed", "закрыто", "выполнена", "выполнено"})
STATUS_REJECTED = frozenset({"отклонено", "rejected", "declined", "отклонена"})
# Статусы, при смене на которые уведомление «Новый статус» не отправляется (ни в ТГ, ни в MAX)
STATUS_SILENT = frozenset({"waiting for customer", "ожидание ответа клиента", "ожидание клиента"})

STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "issue_notification_state.json"
STC_ASSIGN_NOTIFY_QUEUE_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "stc_assign_notify_queue.json"
)


# Настройки рабочих часов СТЦ читаются один раз при загрузке модуля.
# Это устраняет повторные вызовы os.getenv() при каждом тике фонового цикла.
_STC_TZ: ZoneInfo = ZoneInfo(os.getenv("STC_NOTIFY_TZ", "Europe/Moscow"))
_STC_HOUR_START: dtime = dtime(int(os.getenv("STC_NOTIFY_HOUR_START", "8")), 0, 0)
_STC_HOUR_END: dtime = dtime(int(os.getenv("STC_NOTIFY_HOUR_END", "17")), 0, 0)
_STC_BUSINESS_HOURS_ENABLED: bool = os.getenv("STC_NOTIFY_BUSINESS_HOURS", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def _now_moscow() -> datetime:
    return datetime.now(_STC_TZ)


def is_stc_moscow_business_hours(now: Optional[datetime] = None) -> bool:
    """Будни, интервал [08:00, 17:00) по Москве (настраивается через STC_NOTIFY_HOUR_*)."""
    if not _STC_BUSINESS_HOURS_ENABLED:
        return True
    now = _now_moscow() if now is None else now.astimezone(_STC_TZ)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return _STC_HOUR_START <= t < _STC_HOUR_END


def next_stc_moscow_workday_delivery_time(from_dt: Optional[datetime] = None) -> datetime:
    """
    Ближайший момент «08:00 в ближайший рабочий день» (МСК): сегодня в 8:00, если ещё не наступило,
    иначе следующий рабочий день в 8:00 (выходные пропускаются).
    """
    tz = _STC_TZ
    now = _now_moscow() if from_dt is None else from_dt.astimezone(tz)
    work_start, work_end = _STC_HOUR_START, _STC_HOUR_END
    d = now.date()
    t = now.time()

    if now.weekday() < 5 and t < work_start:
        return datetime.combine(d, work_start, tzinfo=tz)

    if now.weekday() >= 5:
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return datetime.combine(nxt, work_start, tzinfo=tz)

    if now.weekday() < 5 and t >= work_end:
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return datetime.combine(nxt, work_start, tzinfo=tz)

    # Внутри рабочего окна — для очереди не должны вызывать; на всякий случай — следующий день 8:00.
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return datetime.combine(nxt, work_start, tzinfo=tz)


def _load_stc_assign_queue() -> Dict[str, Dict[str, str]]:
    if not STC_ASSIGN_NOTIFY_QUEUE_FILE.exists():
        return {}
    try:
        with open(STC_ASSIGN_NOTIFY_QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for k, v in data.items():
            if isinstance(v, dict) and v.get("deliver_after") and v.get("assignee_username"):
                out[str(k).strip().upper()] = {
                    "assignee_username": str(v["assignee_username"]).strip().lower(),
                    "deliver_after": str(v["deliver_after"]),
                }
        return out
    except Exception as e:
        logger.warning("Ошибка загрузки очереди уведомлений СА СТЦ: %s", e)
        return {}


def _save_stc_assign_queue(data: Dict[str, Dict[str, str]]) -> None:
    STC_ASSIGN_NOTIFY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STC_ASSIGN_NOTIFY_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _enqueue_stc_assign_notification(issue_key: str, assignee_username: str, deliver_after: datetime) -> None:
    key = (issue_key or "").strip().upper()
    if not key or not assignee_username:
        return
    if deliver_after.tzinfo is None:
        deliver_after = deliver_after.replace(tzinfo=_STC_TZ)
    else:
        deliver_after = deliver_after.astimezone(_STC_TZ)
    q = _load_stc_assign_queue()
    q[key] = {
        "assignee_username": assignee_username.strip().lower(),
        "deliver_after": deliver_after.isoformat(),
    }
    _save_stc_assign_queue(q)
    logger.info(
        "СА СТЦ: уведомление о %s отложено до %s (МСК, вне рабочего времени)",
        key,
        deliver_after.isoformat(timespec="minutes"),
    )


def _stc_new_task_reply_markup(issue_key: str) -> List[List[Dict[str, str]]]:
    return [
        [{"text": "📋 Мои задачи", "callback_data": "sa_stc_my_tasks"}],
        [{"text": "🔎 Открыть задачу", "callback_data": f"stc_open_issue:{issue_key}"}],
    ]


def _format_stc_new_task_message(
    issue_key: str, info: Dict[str, Any], browse_url: Optional[str]
) -> str:
    assignee_username = (info.get("assignee_username") or "").strip().lower()
    summary = (info.get("summary") or "—").strip()
    status_ru = jira_status_display_ru(info.get("status"))
    assignee_display = (info.get("assignee_display") or assignee_username).strip()
    text = (
        f"🛠️ <b>Новая задача для СА СТЦ</b>\n\n"
        f"Заявка: {issue_key}\n"
        f"Тема: {summary}\n"
        f"Статус: {status_ru}\n"
        f"Assignee: {assignee_display}\n"
    )
    if browse_url:
        text += f'\n🔗 <a href="{browse_url}">Открыть в Jira</a>'
    return text


async def flush_stc_assign_notification_queue() -> None:
    """Отправить отложенные уведомления СА СТЦ, у которых наступило время доставки (МСК)."""
    if not _STC_BUSINESS_HOURS_ENABLED:
        return
    q = _load_stc_assign_queue()
    if not q:
        return
    now = _now_moscow()
    from core.jira_aa import get_issue_admin_details
    from core.stc_tasks import get_stc_recipients_by_jira_username
    from core.support.api import get_jira_browse_url

    changed = False
    for issue_key in list(q.keys()):
        entry = q[issue_key]
        try:
            deliver_after = datetime.fromisoformat(entry["deliver_after"])
        except Exception:
            del q[issue_key]
            changed = True
            continue
        if deliver_after.tzinfo is None:
            deliver_after = deliver_after.replace(tzinfo=_STC_TZ)
        else:
            deliver_after = deliver_after.astimezone(_STC_TZ)
        if now < deliver_after:
            continue
        queued_assignee = (entry.get("assignee_username") or "").strip().lower()
        try:
            info = await get_issue_admin_details(issue_key)
        except Exception as e:
            logger.warning("Очередь СА СТЦ: не удалось получить %s: %s", issue_key, e)
            continue
        if not info:
            del q[issue_key]
            changed = True
            continue
        current = (info.get("assignee_username") or "").strip().lower()
        if current != queued_assignee:
            logger.info(
                "Очередь СА СТЦ: %s пропуск (assignee изменился с %s на %s)",
                issue_key,
                queued_assignee,
                current or "—",
            )
            del q[issue_key]
            changed = True
            continue
        recipients = get_stc_recipients_by_jira_username(queued_assignee)
        recipients = _expand_recipients_to_linked_channels(recipients)
        if not recipients:
            del q[issue_key]
            changed = True
            continue
        browse_url = get_jira_browse_url(issue_key)
        text = _format_stc_new_task_message(issue_key, info, browse_url)
        reply_markup = _stc_new_task_reply_markup(issue_key)
        for channel_id, user_id in recipients:
            try:
                await delivery_module.deliver(channel_id, user_id, text, reply_markup=reply_markup)
            except Exception as e:
                logger.warning(
                    "Не удалось отправить отложенное уведомление СА СТЦ %s -> %s/%s: %s",
                    issue_key,
                    channel_id,
                    user_id,
                    e,
                )
        del q[issue_key]
        changed = True
    if changed:
        _save_stc_assign_queue(q)


def _load_state() -> Dict[str, Dict[str, Any]]:
    if use_sqlite_storage():
        # SQLite: читаем одним запросом (иначе N запросов на каждый тик фонового цикла).
        return _sqlite.list_notification_states()
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
    if use_sqlite_storage():
        # SQLite: bulk-upsert за один проход (меньше блокирует event-loop)
        t0 = time.monotonic()
        _sqlite.upsert_notification_states_bulk(data or {})
        dt = time.monotonic() - t0
        if dt > 0.2:
            logger.warning("SQLite: upsert_notification_states_bulk занял %.3fs (keys=%s)", dt, len(data or {}))
        return
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


_NOTIFIED_COMMENT_IDS_CAP = 400


def _get_notified_comment_ids(issue_key: str) -> set:
    key = (issue_key or "").strip().upper()
    if not key:
        return set()
    data = _load_state()
    raw = (data.get(key) or {}).get("notified_comment_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if x is not None and str(x).strip()}


def _add_notified_comment_ids(issue_key: str, ids: List[str]) -> None:
    key = (issue_key or "").strip().upper()
    if not key or not ids:
        return
    data = _load_state()
    if key not in data:
        data[key] = {}
    seen = data[key].get("notified_comment_ids")
    if not isinstance(seen, list):
        seen = []
    for i in ids:
        si = str(i).strip()
        if si and si not in seen:
            seen.append(si)
    cap = max(50, int(os.getenv("COMMENTS_NOTIFIED_IDS_CAP", str(_NOTIFIED_COMMENT_IDS_CAP))))
    while len(seen) > cap:
        seen.pop(0)
    data[key]["notified_comment_ids"] = seen
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
    # Глобальный приоритет MAX: если есть MAX-получатель — отрезаем Telegram,
    # чтобы не было дублей при включении Telegram в будущем.
    only_max = (os.getenv("ONLY_MAX_NOTIFICATIONS", "1") or "").strip().lower() not in ("0", "false", "no", "off")
    if only_max:
        max_only = [(c, u) for (c, u) in out if (c or "").strip().lower() == "max"]
        return max_only if max_only else out
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

    Статусы запрашиваются параллельно через asyncio.gather + семафор (не более 5 запросов к Jira
    одновременно), что устраняет накопленный asyncio.sleep(0.3) на каждый тикет.
    """
    from core.jira_aa import get_issue_status
    from core.password_requests import remove_pending

    issue_keys = get_all_issue_keys()
    if not issue_keys:
        return

    _semaphore = asyncio.Semaphore(5)

    async def _fetch_status(key: str):
        async with _semaphore:
            return key, await get_issue_status(key)

    raw_results = await asyncio.gather(*[_fetch_status(k) for k in issue_keys], return_exceptions=True)

    for result in raw_results:
        if isinstance(result, Exception):
            logger.warning("Ошибка проверки статуса: %s", result)
            continue
        issue_key, status = result
        try:
            recipients = get_user_ids_by_issue(issue_key)
            recipients = _expand_recipients_to_linked_channels(recipients)
            if not recipients:
                continue
            if not status:
                continue
            status_lower = status.lower().strip()
            status_ru = jira_status_display_ru(status)
            last = _get_last_status(issue_key)
            text = None
            if last is None:
                _set_last_status(issue_key, status)
                # Первый опрос: уведомляем только если статус уже финальный (заявку успели закрыть до первого опроса)
                if status_lower in STATUS_RESOLVED:
                    remove_pending(issue_key)
                    text = f"✅ <b>Заявка {issue_key}</b> выполнена.\n\nСтатус: {status_ru}"
                elif status_lower in STATUS_REJECTED:
                    remove_pending(issue_key)
                    text = f"❌ <b>Заявка {issue_key}</b> отклонена.\n\nСтатус: {status_ru}"
            else:
                last_lower = last.lower().strip()
                if status_lower in STATUS_RESOLVED:
                    remove_pending(issue_key)
                    if last_lower in STATUS_RESOLVED:
                        _set_last_status(issue_key, status)
                        continue
                    text = f"✅ <b>Заявка {issue_key}</b> выполнена.\n\nСтатус: {status_ru}"
                elif status_lower in STATUS_REJECTED:
                    remove_pending(issue_key)
                    if last_lower in STATUS_REJECTED:
                        _set_last_status(issue_key, status)
                        continue
                    text = f"❌ <b>Заявка {issue_key}</b> отклонена.\n\nСтатус: {status_ru}"
                else:
                    if last and last_lower == status_lower:
                        continue
                    if status_lower in STATUS_SILENT:
                        _set_last_status(issue_key, status)
                        continue
                    text = f"📋 <b>Заявка {issue_key}</b>\n\nНовый статус: {status_ru}"
                _set_last_status(issue_key, status)

            if text:
                for channel_id, user_id in recipients:
                    try:
                        await delivery_module.deliver(channel_id, user_id, text, reply_markup=None)
                    except Exception as e:
                        logger.warning("Не удалось отправить уведомление о статусе %s -> %s/%s: %s", issue_key, channel_id, user_id, e)
        except Exception as e:
            logger.warning("Ошибка обработки статуса %s: %s", issue_key, e)


async def check_registry_comments_and_notify() -> None:
    """
    Проверяет новые комментарии по заявкам из реестра. Доставка в TG и MAX.
    Кнопка «Написать комментарий» (add_comment:{issue_key}).
    """
    from core.jira_aa import get_issue_comments

    # Защита от “волны” уведомлений при резком росте числа комментариев
    # (часто бывает после рестарта/изменений способа выборки комментариев).
    comments_wave_delta_threshold = int(os.getenv("COMMENTS_WAVE_DELTA_THRESHOLD", "20"))
    # При last_comment_count==0 и несвежей привязке молчаливая синхронизация нужна против «волны»
    # после сброшенного state; но при малых current_count это отрезает законный первый комментарий
    # (пользователь создал заявку, подождал > окна «свежести», появился первый публичный комментарий).
    zero_baseline_skip_min = max(2, int(os.getenv("COMMENTS_ZERO_BASELINE_SKIP_MIN", "8")))

    # Автоочистка реестра для issue_key, которые стабильно не читаются Jira (404/401).
    comments_cleanup_threshold = int(os.getenv("COMMENTS_CLEANUP_ERROR_STREAK_THRESHOLD", "5"))
    comments_cleanup_window_hours = float(os.getenv("COMMENTS_CLEANUP_ERROR_STREAK_WINDOW_HOURS", "12"))
    comments_cleanup_statuses = {s.strip() for s in os.getenv("COMMENTS_CLEANUP_ERROR_STATUSES", "404,401").split(",") if s.strip()}

    issue_keys = get_all_issue_keys()
    if not issue_keys:
        return
    # Чтобы бот не "зависал" на большом реестре, проверяем ограниченное количество заявок за тик
    # и ходим по ним по кругу (round-robin).
    per_tick = max(5, int(os.getenv("COMMENTS_ISSUES_PER_TICK", "15")))
    global _comments_rr_cursor
    try:
        _comments_rr_cursor
    except NameError:
        _comments_rr_cursor = 0
    start = int(_comments_rr_cursor) % max(1, len(issue_keys))
    window = issue_keys[start : start + per_tick]
    if len(window) < min(per_tick, len(issue_keys)):
        window = window + issue_keys[: max(0, per_tick - len(window))]
    _comments_rr_cursor = (start + len(window)) % max(1, len(issue_keys))

    include_internal = (os.getenv("COMMENTS_INCLUDE_INTERNAL") or "").strip().lower() in ("1", "true", "yes", "on")

    for issue_key in window:
        try:
            recipients = get_user_ids_by_issue(issue_key)
            recipients = _expand_recipients_to_linked_channels(recipients)
            if not recipients:
                continue
            comments_resp = await get_issue_comments(issue_key, return_http_status=True, include_internal=include_internal)
            # comments_resp: (comments_or_none, http_status_or_none)
            comments, http_status = comments_resp if isinstance(comments_resp, tuple) and len(comments_resp) == 2 else (None, None)
            if comments is None:
                # Автоочистка реестра при стабильно повторяющихся HTTP ошибках.
                if http_status is not None and str(http_status) in comments_cleanup_statuses:
                    now_ts = time.time()
                    key = (issue_key or "").strip().upper()
                    if key:
                        state = _load_state()
                        rec = state.get(key, {})
                        last_err = rec.get("jira_comment_error_streak") or {}
                        last_status = last_err.get("status")
                        last_count = int(last_err.get("count") or 0)
                        last_ts = last_err.get("last_ts")
                        try:
                            last_ts_f = float(last_ts) if last_ts is not None else None
                        except Exception:
                            last_ts_f = None

                        # Сбрасываем streak, если статус другой или прошло слишком много времени.
                        if last_status != str(http_status) or last_ts_f is None or (now_ts - last_ts_f) > comments_cleanup_window_hours * 3600:
                            new_count = 1
                        else:
                            new_count = last_count + 1

                        rec["jira_comment_error_streak"] = {"status": str(http_status), "count": new_count, "last_ts": now_ts}
                        state[key] = rec
                        _save_state(state)

                        if (
                            new_count >= comments_cleanup_threshold
                            and not _is_recent_issue_binding(issue_key)
                        ):
                            from core.support.issue_binding_registry import remove_bindings_by_issue

                            removed = remove_bindings_by_issue(issue_key)
                            logger.warning(
                                "Комментарии %s: Jira стабильно отвечает HTTP %s (streak=%s). Удалены привязки: %s",
                                issue_key,
                                http_status,
                                new_count,
                                removed,
                            )
                            # Чтобы не продолжать попытки уведомлений по этому issue_key в цикле состояния.
                            # Привязки удалятся из реестра, но базу счётчика на всякий случай обновим.
                            _set_last_comment_count(issue_key, 0)
                            # Сбросим streak.
                            state2 = _load_state()
                            k2 = (issue_key or "").strip().upper()
                            if k2 and k2 in state2:
                                state2[k2].pop("jira_comment_error_streak", None)
                                _save_state(state2)
                continue
            current_count = len(comments)
            last_count = _get_last_comment_count(issue_key)
            if last_count is None:
                # Первый опрос по заявке. Чтобы не терять «первый комментарий» на свежих заявках,
                # отправляем уведомление, если привязка свежая и комментариев немного.
                if current_count > 0 and _is_recent_issue_binding(issue_key) and current_count <= 5:
                    last_count = 0
                else:
                    _set_last_comment_count(issue_key, current_count)
                    continue
            # Если бот был выключен/перезапускался, а baseline по комментариям "пустой" (0),
            # то при старте может полететь "волна" уведомлений по старым тикетам.
            # Для несвежих заявок с уже большим числом комментариев — только baseline; при
            # малых current_count идём в обычную ветку и шлём уведомления (см. zero_baseline_skip_min).
            if (
                last_count == 0
                and current_count > 0
                and not _is_recent_issue_binding(issue_key)
                and current_count >= zero_baseline_skip_min
            ):
                _set_last_comment_count(issue_key, current_count)
                continue
            # Пустой список при last_count>0 часто означает сбой Jira (раньше ошибка маскировалась под []).
            # Не сбрасываем baseline — иначе после восстановления связи прилетит «волна» старых уведомлений.
            if current_count == 0 and last_count > 0:
                logger.warning(
                    "Комментарии %s: получено 0 комментариев при сохранённом baseline=%s — не обновляем счётчик (вероятен сбой API)",
                    issue_key,
                    last_count,
                )
                continue
            # После включения фильтра internal-комментариев счётчик может уменьшиться (но не до нуля «из N» выше).
            # Сбрасываем baseline, чтобы цикл уведомлений не "залипал".
            if current_count < last_count:
                _set_last_comment_count(issue_key, current_count)
                continue
            new_count = current_count - last_count
            # Если “новых” комментариев слишком много за один цикл, это почти наверняка не
            # реальные новые комментарии пользователя, а расхождение baseline (например из-за
            # пагинации/сортировки/фильтра internal). В этом случае просто обновим baseline.
            if new_count > comments_wave_delta_threshold and not _is_recent_issue_binding(issue_key):
                logger.warning(
                    "Комментарии: подозрительный всплеск для %s (last=%s current=%s delta=%s). "
                    "Уведомления не отправляем, baseline обновляем.",
                    issue_key,
                    last_count,
                    current_count,
                    new_count,
                )
                _set_last_comment_count(issue_key, current_count)
                continue
            if current_count <= last_count:
                continue
            new_comments = comments[-new_count:]
            already_notified = _get_notified_comment_ids(issue_key)
            # Премикас: комментарии, отправленные через бота, имеют тело формата "[ФИО] текст".
            # Мы не должны уведомлять только автора (того получателя, чьё ФИО совпадает), но
            # должны уведомлять остальных получателей.
            from user_storage import get_user_profile

            recipient_prefixes: List[tuple[str | None, str, int]] = []
            for ch, uid in recipients:
                profile = get_user_profile(uid, ch) or {}
                full_name = (profile.get("full_name") or "").strip()
                prefix = f"[{full_name}]" if full_name else None
                recipient_prefixes.append((ch, prefix, int(uid)))

            comment_entries: List[Dict[str, Any]] = []
            for c in new_comments:
                cid = c.get("id")
                cid_str = str(cid).strip() if cid is not None else ""
                if cid is not None and cid_str and cid_str in already_notified:
                    continue

                plain = _comment_body_plain(c)
                plain_stripped = (plain or "").strip()

                # Выделяем авторский префикс из "[ФИО] ...", чтобы понять, кто является автором по формату бота.
                prefix = None
                if plain_stripped:
                    # Без regex, чтобы не ловить ошибки экранирования/формата от Jira.
                    if plain_stripped.startswith("["):
                        end = plain_stripped.find("]")
                        if end > 1:
                            prefix = f"[{plain_stripped[1:end]}]"

                author = (c.get("author") or {}).get("displayName", "—")
                if plain:
                    line = f"👤 {author}:\n{plain}"
                else:
                    line = f"👤 {author}: (без текста)"

                comment_entries.append(
                    {
                        "cid": cid_str if cid_str else None,
                        "prefix": prefix,
                        "line": line,
                    }
                )

            if not comment_entries:
                _set_last_comment_count(issue_key, current_count)
                continue

            reply_markup = [
                [{"text": "✏️ Написать комментарий", "callback_data": f"add_comment:{issue_key}"}],
            ]

            delivered_any = False
            delivered_ids: set[str] = set()

            # Отправляем один и тот же набор комментариев, но отфильтровываем автора на уровне получателя.
            for channel_id, prefix, user_id in recipient_prefixes:
                lines_to_send = [e["line"] for e in comment_entries if not (prefix and e.get("prefix") == prefix)]
                if not lines_to_send:
                    continue

                comment_block = "\n\n".join(lines_to_send)
                title = (
                    f"💬 Новый комментарий в заявке {issue_key}:"
                    if len(lines_to_send) == 1
                    else f"💬 Новые комментарии в заявке {issue_key}:"
                )
                text = f"{title}\n\n{comment_block}"

                try:
                    await delivery_module.deliver(channel_id, user_id, text, reply_markup=reply_markup)
                    delivered_any = True
                    for e in comment_entries:
                        if not (prefix and e.get("prefix") == prefix):
                            if e.get("cid"):
                                delivered_ids.add(str(e["cid"]))
                except Exception as e:
                    logger.warning(
                        "Не удалось отправить уведомление о комментарии %s -> %s/%s: %s",
                        issue_key,
                        channel_id,
                        user_id,
                        e,
                    )

            if delivered_any and delivered_ids:
                _add_notified_comment_ids(issue_key, list(delivered_ids))
            _set_last_comment_count(issue_key, current_count)
        except Exception as e:
            logger.warning("Ошибка проверки комментариев %s: %s", issue_key, e)
        await asyncio.sleep(0)


async def check_assignee_tasks_and_notify() -> None:
    """
    Уведомления для роли «СА СТЦ»: если у заявки из реестра сменился assignee на СА,
    отправляем уведомление новому исполнителю.
    Вне будней 08:00–17:00 (МСК) уведомления ставятся в очередь и уходят в 08:00 ближайшего рабочего дня.
    """
    from core.jira_aa import get_issues_admin_details_batch
    from core.stc_tasks import get_stc_recipients_by_jira_username
    from core.support.api import get_jira_browse_url

    await flush_stc_assign_notification_queue()

    issue_keys = get_all_issue_keys()
    if not issue_keys:
        return
    # Batch-запрос: все заявки за один HTTP-вызов вместо N
    details_batch = await get_issues_admin_details_batch(issue_keys)
    for issue_key in issue_keys:
        try:
            info = details_batch.get(issue_key)
            if not info:
                continue
            assignee_username = (info.get("assignee_username") or "").strip().lower()
            if not assignee_username:
                continue
            recipients = get_stc_recipients_by_jira_username(assignee_username)
            last_assignee = (_get_last_assignee(issue_key) or "").strip().lower()
            if not recipients:
                continue
            # should_send управляет именно рассылкой «Новая задача…».
            # Привязку для СА СТЦ мы добавляем всегда (INSERT OR IGNORE), чтобы
            # комментарии/статусы дальше доходили до них из issue_bindings.
            should_send = False
            if not last_assignee:
                _set_last_assignee(issue_key, assignee_username)
                # Для новых заявок шлём уведомление сразу; для исторических — только baseline.
                should_send = _is_recent_issue_binding(issue_key)
            elif last_assignee != assignee_username:
                _set_last_assignee(issue_key, assignee_username)
                should_send = True

            recipients = _expand_recipients_to_linked_channels(recipients)
            if not recipients:
                continue
            # Важно: уведомления о комментариях/статусах берутся из реестра привязок (issue_bindings).
            # Роль «СА СТЦ» сначала узнаёт задачу через уведомление об assignee, но сама по себе
            # не попадает в issue_bindings. Поэтому добавляем привязку для получателей СА СТЦ,
            # чтобы следующие события (комментарии/статусы) доходили до них.
            try:
                from core.support.issue_binding_registry import add_binding

                existing_bindings = get_bindings_by_issue(issue_key) or []
                project_key = (
                    (existing_bindings[0].get("project_key") or "").strip()
                    or (info.get("project_key") or "").strip()
                )
                ticket_type_id = (existing_bindings[0].get("ticket_type_id") or "").strip()

                async def _ensure_binding(channel_id: str, user_id: int) -> None:
                    if use_sqlite_storage():
                        await asyncio.to_thread(
                            add_binding,
                            channel_id,
                            int(user_id),
                            issue_key,
                            project_key,
                            ticket_type_id,
                        )
                    else:
                        add_binding(channel_id, int(user_id), issue_key, project_key, ticket_type_id)

                await asyncio.gather(*[_ensure_binding(ch, uid) for ch, uid in recipients])
            except Exception as e:
                logger.warning("Не удалось добавить привязки для %s: %s", issue_key, e)

            if not should_send:
                continue

            browse_url = get_jira_browse_url(issue_key)
            text = _format_stc_new_task_message(issue_key, info, browse_url)
            reply_markup = _stc_new_task_reply_markup(issue_key)
            if _STC_BUSINESS_HOURS_ENABLED and not is_stc_moscow_business_hours():
                _enqueue_stc_assign_notification(
                    issue_key,
                    assignee_username,
                    next_stc_moscow_workday_delivery_time(),
                )
                continue
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
        await asyncio.sleep(0)


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
