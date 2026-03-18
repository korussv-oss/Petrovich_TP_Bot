"""
Мои заявки: список по реестру привязок из Core, переход к просмотру/комментариям.
"""
import logging
from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from user_storage import is_user_registered
from core.support.api import support_api
from config import is_stc_sa

logger = logging.getLogger(__name__)
router = Router()
CHANNEL_ID = "telegram"


@router.callback_query(lambda c: c.data == "my_tickets")
async def my_tickets_list(callback: CallbackQuery):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    tickets = await support_api.get_my_tickets_filtered(CHANNEL_ID, callback.from_user.id)
    if not tickets:
        await callback.message.edit_text(
            "📋 <b>Мои заявки</b>\n\nУ вас пока нет заявок.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
            ]),
        )
        await callback.answer()
        return
    lines = []
    for t in tickets:
        issue_key = t.get("issue_key") or "—"
        req_label = (t.get("request_type_label") or "").strip()
        tail = f" {req_label}" if req_label else ""
        url = t.get("customer_request_url") or ""
        if url and issue_key != "—":
            lines.append(f'• <a href="{url}">{issue_key}</a>{tail}')
        else:
            lines.append(f"• {issue_key}{tail}")
    text = "📋 <b>Мои заявки</b>\n\n" + "\n".join(lines) + "\n\nВыберите заявку (или откройте по ссылке):"
    buttons = []
    for t in tickets:
        issue_key = t.get("issue_key")
        if issue_key:
            buttons.append([InlineKeyboardButton(text=issue_key, callback_data=f"open_issue:{issue_key}")])
    buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("open_issue:"))
async def open_issue_view(callback: CallbackQuery):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    issue_key = (callback.data or "").split(":", 1)[-1].strip()
    if not support_api.user_owns_issue(CHANNEL_ID, callback.from_user.id, issue_key):
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    from core.jira_aa import get_issue_info, get_issue_comments

    info = await get_issue_info(issue_key)
    comments = await get_issue_comments(issue_key)
    summary = (info or {}).get("summary") or "—"
    status = (info or {}).get("status") or "—"
    def _fmt(comments, max_len=200):
        out = []
        for c in reversed(comments[-10:]):
            author = (c.get("author") or {}).get("displayName", "—")
            body = (c.get("body") or "").strip()
            if len(body) > max_len:
                body = body[:max_len] + "..."
            out.append(f"👤 {author}: {body}")
        return out
    lines = _fmt(comments)
    jira_url = support_api.get_jira_customer_request_url(issue_key)
    text = (
        f"💬 <b>Заявка {issue_key}</b>\n"
        f"Тема: {summary}\nСтатус: {status}\n\n"
        + ("\n\n".join(lines) if lines else "Пока нет комментариев.")
    )
    keyboard_rows = []
    if jira_url:
        keyboard_rows.append([InlineKeyboardButton(text="🔗 Открыть в Jira", url=jira_url)])
    keyboard_rows.extend([
        [InlineKeyboardButton(text="✏️ Добавить комментарий", callback_data=f"add_comment:{issue_key}")],
        [InlineKeyboardButton(text="🔙 К списку заявок", callback_data="my_tickets")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data == "sa_stc_menu")
async def sa_stc_menu(callback: CallbackQuery):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    if not is_stc_sa(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.message.edit_text(
        "🛠️ <b>СА СТЦ</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои задачи", callback_data="sa_stc_my_tasks")],
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
        ]),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "sa_stc_my_tasks")
async def sa_stc_my_tasks(callback: CallbackQuery):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    if not is_stc_sa(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    from core.stc_tasks import get_stc_assignee_tasks

    tasks = await get_stc_assignee_tasks(CHANNEL_ID, callback.from_user.id)
    if not tasks:
        await callback.message.edit_text(
            "📋 <b>Мои задачи (СА СТЦ)</b>\n\nУ вас нет заявок, где вы текущий исполнитель.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="sa_stc_menu")],
                [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
            ]),
        )
        await callback.answer()
        return
    lines = [f"• {t['issue_key']} — {(t.get('request_type_label') or '—')}" for t in tasks]
    buttons = [[InlineKeyboardButton(text=t["issue_key"], callback_data=f"stc_open_issue:{t['issue_key']}")] for t in tasks]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sa_stc_menu")])
    buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    await callback.message.edit_text(
        "📋 <b>Мои задачи (СА СТЦ)</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


async def _render_stc_issue_view(callback: CallbackQuery, issue_key: str):
    from core.stc_tasks import can_stc_user_access_issue, get_stc_assignee_tasks
    from core.jira_aa import get_issue_admin_details, get_issue_comments

    if not await can_stc_user_access_issue(CHANNEL_ID, callback.from_user.id, issue_key):
        await callback.message.edit_text(
            "❌ Заявка недоступна (возможно, вы больше не исполнитель).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К моим задачам", callback_data="sa_stc_my_tasks")],
                [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
            ]),
        )
        return
    info = await get_issue_admin_details(issue_key) or {}
    comments = await get_issue_comments(issue_key)
    # creator и type из списка задач (чтобы не делать лишний проход по реестру)
    creator = "—"
    req_label = "—"
    for t in await get_stc_assignee_tasks(CHANNEL_ID, callback.from_user.id):
        if (t.get("issue_key") or "").upper() == issue_key.upper():
            creator = t.get("creator") or "—"
            req_label = t.get("request_type_label") or "—"
            break
    summary = info.get("summary") or "—"
    status = info.get("status") or "—"
    desc = info.get("description") or "—"
    reporter = info.get("reporter_display") or "—"
    assignee = info.get("assignee_display") or "—"
    comm_lines = []
    for c in reversed((comments or [])[-5:]):
        author = (c.get("author") or {}).get("displayName", "—")
        body = (c.get("body") or "").strip()
        if len(body) > 180:
            body = body[:180] + "..."
        comm_lines.append(f"👤 {author}: {body}")
    jira_url = support_api.get_jira_browse_url(issue_key)
    text = (
        f"🛠️ <b>Задача {issue_key}</b>\n\n"
        f"Тип: {req_label}\n"
        f"Автор: {creator}\n"
        f"Reporter: {reporter}\n"
        f"Assignee: {assignee}\n"
        f"Статус: {status}\n"
        f"Тема: {summary}\n\n"
        f"Описание:\n{desc}\n\n"
        + ("Последние комментарии:\n" + "\n\n".join(comm_lines) if comm_lines else "Комментариев пока нет.")
    )
    rows = []
    if jira_url:
        rows.append([InlineKeyboardButton(text="🔗 Открыть в JIRA", url=jira_url)])
    rows.extend([
        [InlineKeyboardButton(text="🔄 Установить статус", callback_data=f"stc_set_status:{issue_key}")],
        [InlineKeyboardButton(text="✏️ Добавить комментарий", callback_data=f"add_comment:{issue_key}")],
        [InlineKeyboardButton(text="🔙 К моим задачам", callback_data="sa_stc_my_tasks")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(lambda c: c.data and c.data.startswith("stc_open_issue:"))
async def stc_open_issue(callback: CallbackQuery):
    if not is_stc_sa(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    issue_key = (callback.data or "").split(":", 1)[-1].strip()
    await _render_stc_issue_view(callback, issue_key)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("stc_set_status:"))
async def stc_set_status(callback: CallbackQuery):
    if not is_stc_sa(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    issue_key = (callback.data or "").split(":", 1)[-1].strip()
    from core.stc_tasks import can_stc_user_access_issue
    from core.jira_aa import get_issue_transitions
    if not await can_stc_user_access_issue(CHANNEL_ID, callback.from_user.id, issue_key):
        await callback.answer("Заявка недоступна.", show_alert=True)
        return
    transitions = await get_issue_transitions(issue_key)
    if not transitions:
        await callback.answer("Нет доступных переходов.", show_alert=True)
        return
    buttons = []
    def _needs_timespent(t: dict) -> bool:
        name = ((t.get("name") or "") + " " + (t.get("to_name") or "")).strip().lower()
        markers = ("resolve", "resolved", "done", "close", "закры", "выполн", "реш")
        return any(m in name for m in markers)
    def _pick_transition(kind: str) -> dict | None:
        """
        Выбираем один переход для нужного статуса.
        kind: in_progress | pause | done
        """
        def norm(s: str) -> str:
            return (s or "").strip().lower()
        for t in transitions:
            to_name = norm(t.get("to_name") or "")
            name = norm(t.get("name") or "")
            blob = f"{to_name} {name}".strip()
            if kind == "in_progress":
                if ("in progress" in blob) or ("в работе" in blob) or ("работ" in to_name and "в" in to_name):
                    return t
            if kind == "pause":
                if any(x in blob for x in ("pause", "paused", "on hold", "hold", "пауза", "приост", "ожид")):
                    return t
            if kind == "done":
                if any(x in blob for x in ("resolved", "resolve", "done", "close", "closed", "готов", "выполн", "закры", "решен", "решён")):
                    return t
        return None

    ordered = [
        ("in_progress", "🟢 В работе"),
        ("pause", "⏸ Пауза"),
        ("done", "✅ Готово"),
    ]
    for kind, label in ordered:
        t = _pick_transition(kind)
        if not t:
            continue
        tid = (t.get("id") or "").strip()
        if not tid:
            continue
        cb = f"stc_ask_timespent:{issue_key}:{tid}" if _needs_timespent(t) else f"stc_apply_status:{issue_key}:{tid}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"stc_open_issue:{issue_key}")])
    await callback.message.edit_text(
        f"🔄 <b>Установить статус</b>\n\nЗаявка: {issue_key}\nВыберите новый статус:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("stc_ask_timespent:"))
async def stc_ask_timespent(callback: CallbackQuery):
    if not is_stc_sa(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer("Некорректный переход.", show_alert=True)
        return
    issue_key = parts[1].strip()
    transition_id = parts[2].strip()
    buttons = [
        [InlineKeyboardButton(text="5m", callback_data=f"stc_apply_status_ts:{issue_key}:{transition_id}:5m")],
        [InlineKeyboardButton(text="15m", callback_data=f"stc_apply_status_ts:{issue_key}:{transition_id}:15m")],
        [InlineKeyboardButton(text="30m", callback_data=f"stc_apply_status_ts:{issue_key}:{transition_id}:30m")],
        [InlineKeyboardButton(text="1h", callback_data=f"stc_apply_status_ts:{issue_key}:{transition_id}:1h")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"stc_set_status:{issue_key}")],
    ]
    await callback.message.edit_text(
        f"⏱ <b>Time Spent</b>\n\nЗаявка: {issue_key}\nВыберите затраченное время:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("stc_apply_status_ts:"))
async def stc_apply_status_with_timespent(callback: CallbackQuery):
    if not is_stc_sa(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer("Некорректный переход.", show_alert=True)
        return
    issue_key = parts[1].strip()
    transition_id = parts[2].strip()
    time_spent = parts[3].strip()
    from core.stc_tasks import can_stc_user_access_issue
    from core.jira_aa import transition_issue
    from user_storage import get_user_profile
    if not await can_stc_user_access_issue(CHANNEL_ID, callback.from_user.id, issue_key):
        await callback.answer("Заявка недоступна.", show_alert=True)
        return
    profile = get_user_profile(callback.from_user.id, CHANNEL_ID) or {}
    preserve_assignee = (profile.get("jira_username") or "").strip() or None
    ok, msg = await transition_issue(
        issue_key,
        transition_id,
        preserve_assignee_username=preserve_assignee,
        default_time_spent=time_spent or "5m",
    )
    await callback.answer("✅ Статус обновлён" if ok else f"❌ {msg}", show_alert=not ok)
    await _render_stc_issue_view(callback, issue_key)


@router.callback_query(lambda c: c.data and c.data.startswith("stc_apply_status:"))
async def stc_apply_status(callback: CallbackQuery):
    if not is_stc_sa(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer("Некорректный переход.", show_alert=True)
        return
    issue_key = parts[1].strip()
    transition_id = parts[2].strip()
    from core.stc_tasks import can_stc_user_access_issue
    from core.jira_aa import transition_issue
    from user_storage import get_user_profile
    if not await can_stc_user_access_issue(CHANNEL_ID, callback.from_user.id, issue_key):
        await callback.answer("Заявка недоступна.", show_alert=True)
        return
    profile = get_user_profile(callback.from_user.id, CHANNEL_ID) or {}
    preserve_assignee = (profile.get("jira_username") or "").strip() or None
    ok, msg = await transition_issue(issue_key, transition_id, preserve_assignee_username=preserve_assignee)
    await callback.answer("✅ Статус обновлён" if ok else f"❌ {msg}", show_alert=not ok)
    await _render_stc_issue_view(callback, issue_key)
