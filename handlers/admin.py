"""
Админ: панель для ADMIN_IDS, удаление пользователя — по логину/ID, список с пагинацией или поиск по ФИО.
Контент админ-панели (текст и кнопки) задаётся в Core API для единого отображения в Telegram и MAX.
"""
import logging
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from config import is_admin, is_lupa_report_allowed
from states import AdminStates
from keyboards import (
    get_main_menu_keyboard,
    get_admin_delete_keyboard,
    get_admin_back_to_choice_only_keyboard,
    get_admin_delete_choice_keyboard,
    get_admin_user_list_keyboard,
    get_admin_user_matches_keyboard,
    get_admin_confirm_delete_keyboard,
)
from user_storage import get_user_profile, delete_user, find_by_login, get_all_users_sorted, search_users_by_fio
from core.support.api import support_api
from core.support.models import Menu, Error
from adapters.telegram.render import render_menu_to_kwargs

logger = logging.getLogger(__name__)
router = Router()
CHANNEL_ID = "telegram"

ADMIN_DELETE_INTRO = (
    "👤 <b>Удаление пользователя</b>\n\n"
    "Выберите способ: список всех пользователей (по 10 на страницу), поиск по части ФИО или ввод логина/ID."
)


def _can_access_admin_panel(user_id: int) -> bool:
    """Доступ к админ-панели: полные админы (ADMIN_IDS) или админы Лупа (ADMIN_LUPA_IDS)."""
    return is_admin(user_id) or is_lupa_report_allowed(CHANNEL_ID, user_id)


@router.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel(callback: CallbackQuery, state: FSMContext):
    """Показать подменю админ-панели (контент из Core API — как в MAX)."""
    if not _can_access_admin_panel(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    await state.clear()
    result = support_api.get_admin_panel(CHANNEL_ID, callback.from_user.id)
    if isinstance(result, Error):
        await callback.answer(result.message, show_alert=True)
        return
    if isinstance(result, Menu):
        kwargs = render_menu_to_kwargs(result)
        await callback.message.edit_text(**kwargs)
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_lupa_excel_report")
async def admin_lupa_excel_report(callback: CallbackQuery, state: FSMContext):
    """Отправка Excel-отчёта по заявкам Лупа (поиск на сайте)."""
    if not is_lupa_report_allowed(CHANNEL_ID, callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    from aiogram.types import FSInputFile
    from core.lupa_report import get_report_path
    from core.support.api import support_api
    from core.support.models import Menu, Error
    from adapters.telegram.render import render_menu_to_kwargs

    report_path = get_report_path()
    if not report_path:
        await callback.message.edit_text(
            "❌ Файл отчёта не найден.\nВозможно, ещё не было создано ни одной заявки по поиску (Лупа).",
        )
        result = support_api.get_admin_panel(CHANNEL_ID, callback.from_user.id)
        if isinstance(result, Menu):
            await callback.message.answer(**render_menu_to_kwargs(result))
        await callback.answer()
        return
    try:
        doc = FSInputFile(str(report_path))
        await callback.message.answer_document(
            document=doc,
            caption="📊 <b>Excel-отчёт по заявкам Лупа (поиск на сайте)</b>",
            parse_mode="HTML",
        )
        await callback.message.edit_text("✅ Отчёт отправлен.")
    except Exception as e:
        logger.exception("Ошибка отправки отчёта Лупа: %s", e)
        await callback.message.edit_text(f"❌ Ошибка при отправке отчёта: {e!s}")
    result = support_api.get_admin_panel(CHANNEL_ID, callback.from_user.id)
    if isinstance(result, Menu):
        await callback.message.answer(**render_menu_to_kwargs(result))
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_delete_user")
async def admin_delete_user_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        ADMIN_DELETE_INTRO,
        parse_mode="HTML",
        reply_markup=get_admin_delete_choice_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_del_back_choice")
async def admin_del_back_to_choice(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        ADMIN_DELETE_INTRO,
        parse_mode="HTML",
        reply_markup=get_admin_delete_choice_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_del_choice_list")
async def admin_del_choice_list(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    all_users = get_all_users_sorted()
    per_page = 10
    total_pages = max(1, (len(all_users) + per_page - 1) // per_page)
    page = 0
    start = page * per_page
    users_page = all_users[start : start + per_page]
    if not users_page:
        await callback.message.edit_text(
            "Нет зарегистрированных пользователей.",
            parse_mode="HTML",
            reply_markup=get_admin_delete_choice_keyboard(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"📋 <b>Список пользователей</b> (страница 1 из {total_pages}):",
        parse_mode="HTML",
        reply_markup=get_admin_user_list_keyboard(users_page, page, total_pages, per_page),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin_del_page_"))
async def admin_del_page(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    match = re.match(r"admin_del_page_(\d+)", callback.data)
    if not match:
        await callback.answer()
        return
    page = int(match.group(1))
    all_users = get_all_users_sorted()
    per_page = 10
    total_pages = max(1, (len(all_users) + per_page - 1) // per_page)
    if page < 0 or page >= total_pages:
        await callback.answer("Страница недоступна.", show_alert=True)
        return
    start = page * per_page
    users_page = all_users[start : start + per_page]
    await callback.message.edit_text(
        f"📋 <b>Список пользователей</b> (страница {page + 1} из {total_pages}):",
        parse_mode="HTML",
        reply_markup=get_admin_user_list_keyboard(users_page, page, total_pages, per_page),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin_del_uid_"))
async def admin_del_uid_select(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    match = re.match(r"admin_del_uid_(\d+)", callback.data)
    if not match:
        await callback.answer()
        return
    uid = int(match.group(1))
    profile = get_user_profile(uid)
    if not profile:
        await callback.answer("Пользователь не найден в базе.", show_alert=True)
        return
    name = profile.get("full_name", "—")
    login = profile.get("login", "—")
    await callback.message.edit_text(
        f"Удалить пользователя?\n\n<b>{name}</b>\nЛогин: {login}\nID: {uid}",
        parse_mode="HTML",
        reply_markup=get_admin_confirm_delete_keyboard(uid),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("admin_del_confirm_"))
async def admin_del_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    match = re.match(r"admin_del_confirm_(\d+)", callback.data)
    if not match:
        await callback.answer()
        return
    uid = int(match.group(1))
    profile = get_user_profile(uid)
    await state.clear()
    deleted = delete_user(uid)
    if deleted:
        text = f"✅ Пользователь удалён: {profile.get('full_name', '—')} ({profile.get('login', '—')}, ID {uid})."
        logger.info("Админ %s удалил пользователя %s", callback.from_user.id, uid)
    else:
        text = "Не удалось удалить пользователя."
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_del_cancel")
async def admin_del_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        ADMIN_DELETE_INTRO,
        parse_mode="HTML",
        reply_markup=get_admin_delete_choice_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_del_choice_search")
async def admin_del_choice_search(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.WAITING_FOR_FIO_SEARCH)
    await callback.message.edit_text(
        "🔍 <b>Поиск по ФИО</b>\n\nВведите часть фамилии, имени или отчества:",
        parse_mode="HTML",
        reply_markup=get_admin_back_to_choice_only_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.WAITING_FOR_FIO_SEARCH, F.text)
async def admin_del_fio_search(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.reply("Введите часть ФИО для поиска.", reply_markup=get_admin_back_to_choice_only_keyboard())
        return
    matches = search_users_by_fio(text)
    if not matches:
        await message.reply(
            "По запросу никого не найдено. Введите другую часть ФИО или нажмите «К выбору способа».",
            reply_markup=get_admin_back_to_choice_only_keyboard(),
        )
        return
    intro = f"Найдено по «{text}»: {len(matches)}. Выберите пользователя для удаления:"
    await message.reply(
        intro,
        reply_markup=get_admin_user_matches_keyboard(matches),
    )


@router.callback_query(lambda c: c.data == "admin_del_choice_login")
async def admin_del_choice_login(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.WAITING_FOR_USER_ID_OR_LOGIN)
    await callback.message.edit_text(
        "✏️ <b>Ввод логина или ID</b>\n\nВведите Telegram ID (число) или рабочий логин (например i.ivanov):",
        parse_mode="HTML",
        reply_markup=get_admin_delete_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.WAITING_FOR_USER_ID_OR_LOGIN, F.text)
async def admin_delete_user_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    user_id_to_delete = None

    if text.isdigit():
        user_id_to_delete = int(text)
    else:
        user_id_to_delete = find_by_login(text)

    if user_id_to_delete is None:
        await message.reply(
            "Пользователь не найден. Введите Telegram ID (число) или рабочий логин.",
            reply_markup=get_admin_delete_keyboard(),
        )
        return

    profile = get_user_profile(user_id_to_delete)
    if not profile:
        await message.reply(
            "Пользователь не найден в базе.",
            reply_markup=get_main_menu_keyboard(message.from_user.id),
        )
        await state.clear()
        return

    deleted = delete_user(user_id_to_delete)
    await state.clear()
    if deleted:
        await message.reply(
            f"✅ Пользователь удалён: {profile.get('full_name', '—')} ({profile.get('login', '—')}, ID {user_id_to_delete}).",
            reply_markup=get_main_menu_keyboard(message.from_user.id),
        )
        logger.info("Админ %s удалил пользователя %s", message.from_user.id, user_id_to_delete)
    else:
        await message.reply("Не удалось удалить.", reply_markup=get_main_menu_keyboard(message.from_user.id))
