"""
Комментарии к заявке на смену пароля (Jira AA): просмотр и добавление.
Как в боте Лупа: пользователь видит комментарии и может добавить свой.
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from states import CommentStates
from keyboards import get_main_menu_keyboard, get_cancel_keyboard
from user_storage import is_user_registered, get_user_profile
from core.password_requests import get_pending_issue_key_by_user
from core.support.api import support_api
from core.jira_aa import get_issue_comments, add_comment
from core.jira_wms import add_attachments_to_issue

CHANNEL_ID = "telegram"

logger = logging.getLogger(__name__)
router = Router()

MAX_COMMENT_LEN = 300
MAX_COMMENTS_SHOW = 10


def _format_comments(comments: list, max_len: int = 200) -> list:
    """Форматирует комментарии для отображения (новые первыми)."""
    out = []
    for c in reversed(comments[-MAX_COMMENTS_SHOW:]):
        author = (c.get("author") or {}).get("displayName", "—")
        body = (c.get("body") or "").strip()
        if len(body) > max_len:
            body = body[:max_len] + "..."
        out.append(f"👤 {author}: {body}")
    return out


@router.callback_query(lambda c: c.data == "request_comments")
async def request_comments_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    issue_key = get_pending_issue_key_by_user(callback.from_user.id)
    if not issue_key:
        await callback.answer("У вас нет активной заявки на смену пароля.", show_alert=True)
        return
    await state.clear()
    comments = await get_issue_comments(issue_key)
    lines = _format_comments(comments)
    text = (
        f"💬 <b>Комментарии к заявке {issue_key}</b>\n\n"
        + ("\n\n".join(lines) if lines else "Пока нет комментариев.")
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Добавить комментарий", callback_data=f"add_comment:{issue_key}")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


async def _user_can_comment_issue(user_id: int, issue_key: str) -> bool:
    """Доступ: заявка в pending или в реестре привязок."""
    if get_pending_issue_key_by_user(user_id) == issue_key:
        return True
    if support_api.user_owns_issue(CHANNEL_ID, user_id, issue_key):
        return True
    # Роль СА СТЦ: может комментировать задачи, где он текущий assignee.
    try:
        from core.stc_tasks import can_stc_user_access_issue
        return await can_stc_user_access_issue(CHANNEL_ID, user_id, issue_key)
    except Exception:
        return False


@router.callback_query(lambda c: c.data and c.data.startswith("add_comment:"))
async def add_comment_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    issue_key = (callback.data or "").split(":", 1)[-1].strip()
    if not issue_key or not await _user_can_comment_issue(callback.from_user.id, issue_key):
        await callback.answer("Заявка не найдена или доступ запрещён.", show_alert=True)
        return
    await state.set_state(CommentStates.WAITING_FOR_COMMENT)
    await state.update_data(issue_key=issue_key)
    await callback.message.edit_text(
        f"✍️ Введите комментарий к заявке <b>{issue_key}</b> (или /cancel для отмены).\n\n"
        "Можно в одном сообщении приложить файл (документ/фото) — он будет добавлен как вложение к заявке.",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(CommentStates.WAITING_FOR_COMMENT)
async def process_comment(message: Message, state: FSMContext):
    # Отмена
    text_raw = (message.text or message.caption or "").strip()
    if text_raw.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    # Текст комментария: используем текст сообщения или подпись к файлу
    if not text_raw:
        await message.reply(
            "Введите текст комментария или добавьте подпись к файлу (caption). Либо /cancel.",
            reply_markup=get_cancel_keyboard(),
        )
        return
    if len(text_raw) > MAX_COMMENT_LEN:
        await message.reply(f"Комментарий не длиннее {MAX_COMMENT_LEN} символов.", reply_markup=get_cancel_keyboard())
        return
    data = await state.get_data()
    issue_key = data.get("issue_key")
    if not issue_key:
        await state.clear()
        await message.reply("Сессия истекла. Вернитесь в меню.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    profile = get_user_profile(message.from_user.id) or {}
    full_name = (profile.get("full_name") or "").strip() or "Пользователь"
    comment_body = f"[{full_name}] {text_raw}"

    # Скачиваем вложения (если есть) во временные файлы
    attachment_paths = []
    bot = message.bot
    try:
        file_ids = []
        if message.document:
            file_ids.append(message.document.file_id)
        if message.photo:
            # Берём фото максимального размера
            file_ids.append(message.photo[-1].file_id)
        if message.video:
            file_ids.append(message.video.file_id)

        if file_ids:
            import tempfile
            import os

            for fid in file_ids[:10]:
                try:
                    f = await bot.get_file(fid)
                    safe_name = (f.file_path or fid).replace("/", "_").replace("\\", "_")
                    path = os.path.join(tempfile.gettempdir(), f"comment_{safe_name}")
                    await bot.download_file(f.file_path, path)
                    if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                        attachment_paths.append(path)
                    else:
                        # слишком большой или не скачался
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning("Скачивание вложения TG comment %s: %s", fid, e)

        ok_comment = await add_comment(issue_key, comment_body)
        added_files = 0
        if ok_comment and attachment_paths:
            added_files, _ = await add_attachments_to_issue(issue_key, attachment_paths)
        await state.clear()

        if ok_comment:
            suffix = f" Прикреплено файлов: {added_files}." if added_files else ""
            await message.reply(
                f"✅ Комментарий добавлен к заявке {issue_key}.{suffix}",
                reply_markup=get_main_menu_keyboard(message.from_user.id),
            )
            logger.info(
                "Пользователь %s добавил комментарий к %s (вложений: %s)",
                message.from_user.id,
                issue_key,
                added_files,
            )
        else:
            await message.reply(
                "❌ Не удалось добавить комментарий. Попробуйте позже.",
                reply_markup=get_main_menu_keyboard(message.from_user.id),
            )
    finally:
        for path in attachment_paths:
            try:
                import os
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass
