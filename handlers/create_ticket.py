"""
Создание заявки: «Создать заявку в ТП» → выбор раздела (Сайт | WMS | Смена пароля),
проверки department_wms / employee_id, пошаговые формы WMS и Lupa.
Смена пароля перенаправляется в handlers.password.
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from user_storage import is_user_registered, get_user_profile, save_user_profile, check_employee_id_taken

from core.support.api import support_api
from core.support.models import Menu, Error
from core.support import ticket_wizard
from core.support.ticket_wizard import (
    save_wizard_session,
    load_wizard_session,
    screen_for_state,
    WizardEvent,
    WizardScreen,
    WizardSession,
    wizard_step,
)
from adapters.telegram.render import render_menu_to_kwargs
from states import (
    TicketWizardStates,
    WmsTicketStates,
    TpSectionStates,
)
from keyboards import (
    get_main_menu_keyboard,
    get_cancel_keyboard,
    get_wms_department_keyboard,
    get_wms_subtype_keyboard,
    get_wms_process_keyboard,
    get_wms_service_type_keyboard,
    get_lupa_service_keyboard,
    get_lupa_request_type_keyboard,
    get_lupa_city_keyboard,
    get_lupa_skip_comment_keyboard,
    get_pc_problem_kind_keyboard,
    get_orgtech_kind_keyboard,
    get_peripheral_kind_keyboard,
    LUPA_SERVICE_VALUES,
    LUPA_REQUEST_TYPE_VALUES,
)
from core.pc_problem import PC_PROBLEM_KIND_BY_ID
from core.email_owa import EMAIL_OWA_REQUEST_KINDS, EMAIL_OWA_KIND_BY_ID
from core.orgtech import ORGTECH_KIND_BY_ID
from core.peripheral_equipment import PERIPHERAL_KIND_BY_ID
from core.network_problem import (
    NETWORK_TYPES,
    NETWORK_TYPE_BY_ID,
    NETWORK_PROVIDERS,
    NETWORK_PROVIDER_BY_ID,
    NETWORK_WIFI_OWNERS,
    NETWORK_WIFI_OWNER_BY_ID,
    NETWORK_PC_TYPES,
    NETWORK_PC_TYPE_BY_ID,
)
from core.electronic_queue import (
    ELECTRONIC_QUEUE_SERVICE_TYPES,
    ELECTRONIC_QUEUE_SERVICE_TYPE_BY_ID,
)
from core.email_forwarding import EMAIL_FORWARDING_ON_OFF, EMAIL_FORWARDING_ON_OFF_BY_ID
from core.email_groups import EMAIL_GROUPS_WHAT_TO_DO, EMAIL_GROUPS_WHAT_TO_DO_BY_ID

import os
import tempfile

logger = logging.getLogger(__name__)
router = Router()
CHANNEL_ID = "telegram"

# ===========================================================================
# Thin-adapter helpers (Phase 16)
# ===========================================================================

def _make_inline_kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Строит InlineKeyboardMarkup из списка (callback_data, text) пар."""
    buttons = [[InlineKeyboardButton(text=label, callback_data=cd) for cd, label in row] for row in rows]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_wizard_keyboard(screen: WizardScreen, data: dict) -> InlineKeyboardMarkup:
    """
    По screen.kind возвращает правильную клавиатуру для TG-рендера.
    Вызывается из _apply_wizard_screen().
    """
    from keyboards import (
        get_wms_department_keyboard,
        get_wms_process_keyboard,
        get_wms_service_type_keyboard,
        get_lupa_service_keyboard,
        get_lupa_request_type_keyboard,
        get_lupa_city_keyboard,
        get_lupa_skip_comment_keyboard,
        get_pc_problem_kind_keyboard,
        get_orgtech_kind_keyboard,
        get_peripheral_kind_keyboard,
        get_cancel_keyboard,
    )

    kind = screen.kind
    depts: list = list(screen.departments or data.get("departments") or [])
    page: int = int(data.get("dept_page") or 0)

    # --- WMS ---
    if kind in ("department_wms", "wms_issue_department"):
        return get_wms_department_keyboard(depts, page)
    if kind == "process_wms":
        return get_wms_process_keyboard()
    if kind == "wms_settings_department":
        return get_wms_department_keyboard(depts, page)
    if kind == "wms_settings_service_type":
        return get_wms_service_type_keyboard()
    if kind == "wms_settings_attachments":
        return _make_inline_kb(
            [("finish_wms_settings", "✅ Завершить создание задачи")],
            [("cancel", "❌ Отмена")],
        )
    if kind == "wms_issue_attachments":
        return _make_inline_kb(
            [("wms_finish_ticket", "✅ Создать заявку")],
            [("cancel", "❌ Отмена")],
        )

    # --- PSI ---
    if kind == "psi_department":
        return get_wms_department_keyboard(depts, page)
    if kind == "psi_attachments":
        return _make_inline_kb(
            [("finish_psi_user", "✅ Завершить"), ("skip_psi_attachment", "⏭ Пропустить вложения")],
            [("cancel", "❌ Отмена")],
        )

    # --- Lupa ---
    if kind == "department_lupa":
        from keyboards import get_lupa_department_keyboard
        return get_lupa_department_keyboard(depts, page)
    if kind == "lupa_service":
        return get_lupa_service_keyboard()
    if kind == "lupa_request_type":
        return get_lupa_request_type_keyboard()
    if kind == "lupa_city":
        return get_lupa_city_keyboard()
    if kind in ("lupa_description", "lupa_city_manual"):
        return get_lupa_skip_comment_keyboard()

    # --- PC ---
    if kind == "pc_kind":
        return get_pc_problem_kind_keyboard()
    if kind == "pc_description":
        return _make_inline_kb(
            [("pc_skip_description", "⏭ Пропустить описание")],
            [("cancel", "❌ Отмена")],
        )
    if kind == "pc_attachments":
        return _make_inline_kb(
            [("pc_finish_ticket", "✅ Создать заявку"), ("pc_skip_attachments", "⏭ Пропустить вложения")],
            [("cancel", "❌ Отмена")],
        )

    # --- Orgtech ---
    if kind == "orgtech_kind":
        return get_orgtech_kind_keyboard()
    if kind == "orgtech_description":
        return _make_inline_kb(
            [("orgtech_skip_description", "⏭ Пропустить описание")],
            [("cancel", "❌ Отмена")],
        )
    if kind == "orgtech_attachments":
        return _make_inline_kb(
            [("orgtech_finish_ticket", "✅ Создать заявку"), ("orgtech_skip_attachments", "⏭ Пропустить вложения")],
            [("cancel", "❌ Отмена")],
        )

    # --- Peripheral ---
    if kind == "peripheral_kind":
        return get_peripheral_kind_keyboard()
    if kind == "peripheral_description":
        return _make_inline_kb(
            [("peripheral_skip_description", "⏭ Пропустить описание")],
            [("cancel", "❌ Отмена")],
        )
    if kind == "peripheral_attachments":
        return _make_inline_kb(
            [("peripheral_finish_ticket", "✅ Создать заявку"), ("peripheral_skip_attachments", "⏭ Пропустить вложения")],
            [("cancel", "❌ Отмена")],
        )

    # --- Network ---
    if kind == "network_type":
        return _network_select_keyboard(NETWORK_TYPES, "network_type_")
    if kind == "network_wifi_owner":
        return _network_select_keyboard(NETWORK_WIFI_OWNERS, "network_wifi_owner_")
    if kind == "network_pc_type":
        return _network_select_keyboard(NETWORK_PC_TYPES, "network_pc_type_")
    if kind == "network_provider":
        return _network_select_keyboard(NETWORK_PROVIDERS, "network_provider_")
    if kind in ("network_rms", "network_description"):
        skip_id = {"network_rms": "network_skip_rms", "network_description": "network_skip_description"}[kind]
        return _make_inline_kb([(skip_id, "⏭ Пропустить"), ("cancel", "❌ Отмена")])
    if kind == "network_provider_other":
        return get_cancel_keyboard()
    if kind == "network_attachments":
        return _make_inline_kb(
            [("network_finish_ticket", "✅ Создать заявку"), ("network_skip_attachments", "⏭ Пропустить вложения")],
            [("cancel", "❌ Отмена")],
        )

    # --- Email OWA ---
    if kind == "email_owa_request_kind":
        from core.email_owa import EMAIL_OWA_REQUEST_KINDS
        buttons = [[InlineKeyboardButton(text=label, callback_data=cd)] for cd, label in EMAIL_OWA_REQUEST_KINDS]
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    if kind == "email_owa_workplace":
        return _make_inline_kb(
            [("email_owa_skip_workplace", "⏭ Пропустить")],
            [("cancel", "❌ Отмена")],
        )
    if kind == "email_owa_attachments":
        return _make_inline_kb(
            [("email_owa_finish_ticket", "✅ Создать заявку"), ("email_owa_skip_attachments", "⏭ Пропустить вложения")],
            [("cancel", "❌ Отмена")],
        )

    # --- Electronic Queue ---
    if kind == "equeue_service":
        buttons = [[InlineKeyboardButton(text=v, callback_data=k)] for k, v in ELECTRONIC_QUEUE_SERVICE_TYPES]
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    return get_cancel_keyboard()


async def _download_tg_files(bot, file_ids: list) -> list[str]:
    """Скачивает файлы из Telegram во временную директорию. Возвращает список локальных путей."""
    paths: list[str] = []
    for fid in file_ids[:10]:
        try:
            f = await bot.get_file(fid)
            safe = (f.file_path or fid).replace("/", "_").replace("\\", "_")
            path = os.path.join(tempfile.gettempdir(), f"wiz_attach_{safe}")
            await bot.download_file(f.file_path, path)
            if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                paths.append(path)
        except Exception as exc:
            logger.warning("_download_tg_files: %s → %s", str(fid)[:30], exc)
    return paths


async def _apply_wizard_screen(
    update: "CallbackQuery | Message",
    state: FSMContext,
    new_session: WizardSession,
    screen: WizardScreen,
    user_id: int,
) -> None:
    """
    Применяет результат wizard_step() к TG-апдейту.

    Если screen.create_ticket_payload — создаёт тикет, загружает вложения,
    очищает FSM и рендерит подтверждение.
    Иначе — обновляет FSM-состояние + данные и рендерит screen.
    """
    if screen.create_ticket_payload is not None:
        payload = screen.create_ticket_payload
        ttype = payload.get("ticket_type_id", "")
        form_data = payload.get("form_data") or {}
        file_ids: list = list(payload.get("attachment_tokens") or [])
        bot = update.bot if hasattr(update, "bot") else update.message.bot

        attachment_paths: list[str] = []
        if file_ids:
            attachment_paths = await _download_tg_files(bot, file_ids)

        try:
            success, key_or_msg, full_msg = await support_api.create_ticket(
                CHANNEL_ID,
                user_id,
                ttype,
                form_data,
                attachment_paths=attachment_paths or None,
            )
            display = full_msg or key_or_msg or ("Заявка создана" if success else "Ошибка")

            if success and ttype == "wms_issue" and key_or_msg and attachment_paths:
                try:
                    from core.jira_wms import add_attachments_to_issue
                    added, _ = await add_attachments_to_issue(key_or_msg, attachment_paths)
                    if added:
                        display += f"\n\n📎 Приложено файлов: {added}."
                except Exception as exc:
                    logger.warning("_apply_wizard_screen: wms attachments: %s", exc)
        finally:
            for p in attachment_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass

        await state.clear()
        text = f"✅ {display}" if success else f"❌ {display}"
        if isinstance(update, CallbackQuery):
            await update.message.edit_text(text, parse_mode="HTML",
                                           reply_markup=get_main_menu_keyboard(user_id))
        else:
            await update.answer(text, parse_mode="HTML",
                                reply_markup=get_main_menu_keyboard(user_id))
        return

    if screen.kind == "error":
        if isinstance(update, CallbackQuery):
            await update.answer(screen.text, show_alert=True)
        else:
            await update.answer(screen.text, parse_mode="HTML", reply_markup=get_cancel_keyboard())
        return

    try:
        await state.set_state(TicketWizardStates[new_session.step])
    except (KeyError, ValueError):
        logger.warning("_apply_wizard_screen: unknown step=%s", new_session.step)
    await state.update_data(**save_wizard_session(new_session))

    fsm_data = await state.get_data()
    keyboard = _build_wizard_keyboard(screen, {**new_session.data, **fsm_data})

    if isinstance(update, CallbackQuery):
        await update.message.edit_text(screen.text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.answer(screen.text, parse_mode="HTML", reply_markup=keyboard)


# ===========================================================================
# Универсальные тонкие обёртки для всех TicketWizardStates (Phase 16)
# ===========================================================================
# Зарегистрированы ПЕРВЫМИ — перехватывают события до legacy-хандлеров.
# Если session отсутствует (legacy-flow без wizard_session), пропускаем.

async def _wizard_no_session(update: "CallbackQuery | Message", state: FSMContext) -> None:
    """Сессия wizard не найдена — сбрасываем FSM и просим начать заново."""
    await state.clear()
    txt = "⚠️ Сессия устарела. Пожалуйста, начните создание заявки заново."
    if isinstance(update, CallbackQuery):
        await update.message.edit_text(txt, reply_markup=get_main_menu_keyboard(update.from_user.id))
        await update.answer()
    else:
        await update.answer(txt, reply_markup=get_main_menu_keyboard(update.from_user.id))


@router.callback_query(TicketWizardStates.any())
async def wizard_callback_universal(callback: CallbackQuery, state: FSMContext):
    """Тонкая обёртка: любой callback в wizard-состоянии → wizard_step()."""
    data = await state.get_data()
    session = load_wizard_session(data)
    if session is None:
        await _wizard_no_session(callback, state)
        return

    cid = callback.data or ""
    if cid == "cancel":
        await state.clear()
        await callback.message.edit_text(
            "❌ Отменено.", reply_markup=get_main_menu_keyboard(callback.from_user.id)
        )
        await callback.answer()
        return

    profile = get_user_profile(callback.from_user.id, CHANNEL_ID) or {}
    event = WizardEvent(kind="callback", callback_id=cid)
    try:
        new_session, screen = await wizard_step(session, event, profile=profile)
    except Exception as exc:
        logger.error("wizard_callback_universal: wizard_step failed: %s", exc, exc_info=True)
        await callback.answer("Произошла ошибка. Попробуйте начать заново.", show_alert=True)
        return

    await _apply_wizard_screen(callback, state, new_session, screen, callback.from_user.id)
    await callback.answer()


@router.message(TicketWizardStates.any(), F.text)
async def wizard_message_universal(message: Message, state: FSMContext):
    """Тонкая обёртка: любое текстовое сообщение в wizard-состоянии → wizard_step()."""
    data = await state.get_data()
    session = load_wizard_session(data)
    if session is None:
        await _wizard_no_session(message, state)
        return

    txt = (message.text or "").strip()
    if txt.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return

    profile = get_user_profile(message.from_user.id, CHANNEL_ID) or {}
    event = WizardEvent(kind="text", text=txt)
    try:
        new_session, screen = await wizard_step(session, event, profile=profile)
    except Exception as exc:
        logger.error("wizard_message_universal: wizard_step failed: %s", exc, exc_info=True)
        await message.answer("Произошла ошибка. Попробуйте начать заново.")
        return

    await _apply_wizard_screen(message, state, new_session, screen, message.from_user.id)


@router.message(TicketWizardStates.any(), F.photo | F.document | F.video)
async def wizard_attachment_universal(message: Message, state: FSMContext):
    """Тонкая обёртка: вложения в wizard-состоянии → wizard_step(kind='attachment')."""
    data = await state.get_data()
    session = load_wizard_session(data)
    if session is None:
        await _wizard_no_session(message, state)
        return

    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id
    elif message.video:
        file_id = message.video.file_id
    else:
        return

    profile = get_user_profile(message.from_user.id, CHANNEL_ID) or {}
    event = WizardEvent(kind="attachment", attachments=[file_id])
    try:
        new_session, screen = await wizard_step(session, event, profile=profile)
    except Exception as exc:
        logger.error("wizard_attachment_universal: wizard_step failed: %s", exc, exc_info=True)
        await message.answer("Произошла ошибка при обработке вложения.")
        return

    await _apply_wizard_screen(message, state, new_session, screen, message.from_user.id)


# --- «Создать заявку в ТП»: выбор раздела ---

@router.callback_query(lambda c: c.data == "create_ticket_tp")
async def create_ticket_tp(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "📋 <b>Создать заявку в ТП</b>\n\nВ каком разделе создаём заявку?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💻 Программы и сайт", callback_data="tp_group_programs")],
            [InlineKeyboardButton(text="🛠️ Оборудование", callback_data="tp_group_equipment")],
            [InlineKeyboardButton(text="🧰 Услуги", callback_data="tp_group_services")],
            [InlineKeyboardButton(text="🔑 Смена пароля", callback_data="tp_section_password")],
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
        ]),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "tp_group_programs")
async def tp_group_programs(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "💻 <b>Программы и сайт</b>\n\nВыберите направление:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Поиск/Сайт", callback_data="tp_section_site")],
            [InlineKeyboardButton(text="📦 WMS", callback_data="tp_section_wms")],
            [InlineKeyboardButton(text="📧 Электронная почта", callback_data="tp_section_email")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="create_ticket_tp")],
        ]),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "tp_group_equipment")
async def tp_group_equipment(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠️ <b>Оборудование</b>\n\nВыберите тип заявки:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖥️ Проблема в работе ПК", callback_data="pc_issue_start")],
            [InlineKeyboardButton(text="🖨️ Оргтехника", callback_data="orgtech_issue_start")],
            [InlineKeyboardButton(text="🧩 Периферийное оборудование", callback_data="peripheral_issue_start")],
            [InlineKeyboardButton(text="📶 Проблемы в работе сети", callback_data="network_issue_start")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="create_ticket_tp")],
        ]),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "tp_group_services")
async def tp_group_services(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🧰 <b>Услуги</b>\n\nВыберите тип заявки:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎫 Электронная очередь", callback_data="electronic_queue_start")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="create_ticket_tp")],
        ]),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "tp_section_email")
async def tp_section_email(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "📧 <b>Электронная почта</b>\n\nВыберите направление:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📨 Электронная почта (Owa\\Outlook)", callback_data="tp_email_owa_outlook")],
            [InlineKeyboardButton(text="👥 Группы рассылки", callback_data="tp_email_groups")],
            [InlineKeyboardButton(text="↪️ Настройка переадресации", callback_data="tp_email_forwarding")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="tp_group_programs")],
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"tp_email_groups", "tp_email_forwarding"}))
async def tp_section_email_stub(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    if callback.data == "tp_email_groups":
        await _email_groups_start(callback, state)
        await callback.answer()
        return

    # tp_email_forwarding — полноценный сценарий ниже
    await _email_forwarding_start(callback, state)
    await callback.answer()


def _email_groups_what_to_do_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"email_groups_do:{oid}")] for oid, label in EMAIL_GROUPS_WHAT_TO_DO]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="email_groups_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _email_groups_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="email_groups_cancel")]])


async def _email_groups_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TicketWizardStates.EMAIL_GROUPS_WHAT_TO_DO)
    await callback.message.edit_text(
        "👥 <b>Группы рассылки</b>\n\nКакой тип работ вас интересует?",
        parse_mode="HTML",
        reply_markup=_email_groups_what_to_do_keyboard(),
    )


@router.callback_query(F.data == "email_groups_cancel")
async def email_groups_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await tp_section_email(callback, state)
    await callback.answer()


@router.callback_query(TicketWizardStates.EMAIL_GROUPS_WHAT_TO_DO, F.data.startswith("email_groups_do:"))
async def email_groups_select_what_to_do(callback: CallbackQuery, state: FSMContext):
    oid = (callback.data.split(":", 1)[1] if callback.data else "").strip()
    label = EMAIL_GROUPS_WHAT_TO_DO_BY_ID.get(oid)
    if not label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(email_groups_what_to_do_id=oid, email_groups_what_to_do_label=label)
    # Ветвление:
    # 13012 create group -> name -> owner -> membership -> description
    # 13013 delete group -> name -> description
    # 13014 add member -> group email -> AD login
    # 13015 remove member -> group email -> AD login
    if oid in ("13012", "13013"):
        await state.set_state(TicketWizardStates.EMAIL_GROUPS_GROUP_NAME)
        await callback.message.edit_text(
            f"👥 <b>Группы рассылки</b>\n\n✅ Тип работ: {label}\n\nВведите <b>Название группы рассылки</b>:",
            parse_mode="HTML",
            reply_markup=_email_groups_cancel_keyboard(),
        )
    else:
        await state.set_state(TicketWizardStates.EMAIL_GROUPS_GROUP_EMAIL)
        await callback.message.edit_text(
            f"👥 <b>Группы рассылки</b>\n\n✅ Тип работ: {label}\n\nВведите <b>Адрес группы рассылки</b> (email):",
            parse_mode="HTML",
            reply_markup=_email_groups_cancel_keyboard(),
        )
    await callback.answer()


@router.message(TicketWizardStates.EMAIL_GROUPS_GROUP_NAME, F.text)
async def email_groups_group_name(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not text:
        await message.answer("❌ Название не может быть пустым. Введите название группы.")
        return
    await state.update_data(email_groups_group_name=text)
    data = await state.get_data()
    what_id = (data.get("email_groups_what_to_do_id") or "").strip()
    if what_id == "13012":
        await state.set_state(TicketWizardStates.EMAIL_GROUPS_OWNER)
        await message.answer("Введите <b>Владельца группы рассылки</b> (email, например i.vanov@petrovich.ru):", parse_mode="HTML", reply_markup=_email_groups_cancel_keyboard())
        return
    # 13013
    await state.set_state(TicketWizardStates.EMAIL_GROUPS_DESCRIPTION)
    await message.answer("Введите <b>Причину изменения</b>:", parse_mode="HTML", reply_markup=_email_groups_cancel_keyboard())


@router.message(TicketWizardStates.EMAIL_GROUPS_OWNER, F.text)
async def email_groups_group_owner(message: Message, state: FSMContext):
    owner = (message.text or "").strip()
    if owner.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not _looks_like_email(owner):
        await message.answer("❌ Похоже, это не email. Введите адрес в формате name@domain.tld.")
        return
    await state.update_data(email_groups_group_owner=owner)
    await state.set_state(TicketWizardStates.EMAIL_GROUPS_MEMBERSHIP)
    await message.answer(
        "Введите <b>Кто будет входить в группу рассылки</b>.\n"
        "Можно перечислить несколько email через запятую/перенос строки:",
        parse_mode="HTML",
        reply_markup=_email_groups_cancel_keyboard(),
    )


@router.message(TicketWizardStates.EMAIL_GROUPS_MEMBERSHIP, F.text)
async def email_groups_group_membership(message: Message, state: FSMContext):
    members = (message.text or "").strip()
    if members.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not members:
        await message.answer("❌ Поле не может быть пустым. Укажите хотя бы одного участника (email).")
        return
    await state.update_data(email_groups_group_membership=members)
    await state.set_state(TicketWizardStates.EMAIL_GROUPS_DESCRIPTION)
    await message.answer("Введите <b>Причину изменения</b>:", parse_mode="HTML", reply_markup=_email_groups_cancel_keyboard())


@router.message(TicketWizardStates.EMAIL_GROUPS_GROUP_EMAIL, F.text)
async def email_groups_group_email(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    # В Jira часто поле принимает "Имя <email@...>", вытащим email если так.
    email = raw
    if "<" in raw and ">" in raw:
        inner = raw.split("<", 1)[1].split(">", 1)[0].strip()
        if inner:
            email = inner
    if not _looks_like_email(email):
        await message.answer("❌ Похоже, это не email. Введите адрес группы рассылки (например, group@petrovich.ru).")
        return
    await state.update_data(email_groups_group_email=email)
    await state.set_state(TicketWizardStates.EMAIL_GROUPS_AD_LOGIN)
    await message.answer("Введите <b>Имя учетной записи сотрудника</b> (AD Login) в формате <b>i.vanov</b>:", parse_mode="HTML", reply_markup=_email_groups_cancel_keyboard())


def _looks_like_ad_login(value: str) -> bool:
    v = (value or "").strip()
    if not v or " " in v or "@" in v:
        return False
    if "." not in v:
        return False
    left, _, right = v.partition(".")
    if not left or not right:
        return False
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789._-"
    vv = v.lower()
    return all(c in allowed for c in vv)


@router.message(TicketWizardStates.EMAIL_GROUPS_AD_LOGIN, F.text)
async def email_groups_ad_login(message: Message, state: FSMContext):
    login = (message.text or "").strip()
    if login.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not _looks_like_ad_login(login):
        await message.answer("❌ Нужен AD Login строго в формате <b>i.vanov</b> (без @ и домена).", parse_mode="HTML")
        return
    await state.update_data(email_groups_ad_login=login)
    await _email_groups_finish(message, state)


@router.message(TicketWizardStates.EMAIL_GROUPS_DESCRIPTION, F.text)
async def email_groups_description(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    if desc.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not desc:
        await message.answer("❌ Причина изменения не может быть пустой.")
        return
    await state.update_data(email_groups_description=desc)
    await _email_groups_finish(message, state)


async def _email_groups_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    what_id = (data.get("email_groups_what_to_do_id") or "").strip()
    what_label = (data.get("email_groups_what_to_do_label") or "").strip()

    form_data = {"what_to_do": what_id}
    # ветка 1
    if what_id == "13012":
        form_data.update({
            "group_name": (data.get("email_groups_group_name") or "").strip(),
            "group_owner": (data.get("email_groups_group_owner") or "").strip(),
            "group_membership": (data.get("email_groups_group_membership") or "").strip(),
            "description": (data.get("email_groups_description") or "").strip(),
        })
        missing = [k for k in ("group_name", "group_owner", "group_membership", "description") if not (form_data.get(k) or "").strip()]
        if missing:
            await message.answer("❌ Не все поля заполнены. Начните заново.", reply_markup=get_main_menu_keyboard(message.from_user.id))
            await state.clear()
            return
    # ветка 2
    elif what_id == "13013":
        form_data.update({
            "group_name": (data.get("email_groups_group_name") or "").strip(),
            "description": (data.get("email_groups_description") or "").strip(),
        })
        missing = [k for k in ("group_name", "description") if not (form_data.get(k) or "").strip()]
        if missing:
            await message.answer("❌ Не все поля заполнены. Начните заново.", reply_markup=get_main_menu_keyboard(message.from_user.id))
            await state.clear()
            return
    # ветки 3/4
    elif what_id in ("13014", "13015"):
        form_data.update({
            "group_email": (data.get("email_groups_group_email") or "").strip(),
            "ad_login": (data.get("email_groups_ad_login") or "").strip(),
        })
        missing = [k for k in ("group_email", "ad_login") if not (form_data.get(k) or "").strip()]
        if missing:
            await message.answer("❌ Не все поля заполнены. Начните заново.", reply_markup=get_main_menu_keyboard(message.from_user.id))
            await state.clear()
            return
    else:
        await message.answer("❌ Неизвестный тип работ. Начните заново.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        await state.clear()
        return

    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, message.from_user.id, "email_groups", form_data)
    await state.clear()
    if not success:
        await message.answer(f"❌ Ошибка Jira: {issue_key}")
        return
    # пользователю полезно видеть выбранный тип работ
    await message.answer(f"✅ {what_label}\n\n{msg}", disable_web_page_preview=True)


def _email_forwarding_on_off_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"email_fwd_onoff:{oid}")] for oid, label in EMAIL_FORWARDING_ON_OFF]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="email_fwd_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _email_forwarding_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="email_fwd_cancel")]])


async def _email_forwarding_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TicketWizardStates.EMAIL_FORWARDING_ON_OFF)
    await callback.message.edit_text(
        "↪️ <b>Настройка переадресации</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=_email_forwarding_on_off_keyboard(),
    )


@router.callback_query(F.data == "email_fwd_cancel")
async def email_forwarding_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await tp_section_email(callback, state)
    await callback.answer()


@router.callback_query(TicketWizardStates.EMAIL_FORWARDING_ON_OFF, F.data.startswith("email_fwd_onoff:"))
async def email_forwarding_select_on_off(callback: CallbackQuery, state: FSMContext):
    oid = (callback.data.split(":", 1)[1] if callback.data else "").strip()
    if oid not in EMAIL_FORWARDING_ON_OFF_BY_ID:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(email_fwd_on_off=oid)
    await state.set_state(TicketWizardStates.EMAIL_FORWARDING_FROM)
    await callback.message.edit_text(
        "Введите email, <b>с которого</b> нужно установить переадресацию (например, name@petrovich.ru):",
        parse_mode="HTML",
        reply_markup=_email_forwarding_cancel_keyboard(),
    )
    await callback.answer()


def _looks_like_email(value: str) -> bool:
    v = (value or "").strip()
    if len(v) < 5 or "@" not in v or " " in v:
        return False
    local, _, domain = v.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return True


@router.message(TicketWizardStates.EMAIL_FORWARDING_FROM, F.text)
async def email_forwarding_email_from(message: Message, state: FSMContext):
    email_from = (message.text or "").strip()
    if email_from.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not _looks_like_email(email_from):
        await message.answer("❌ Похоже, это не email. Введите адрес в формате name@domain.tld.")
        return
    await state.update_data(email_fwd_email_from=email_from)
    await state.set_state(TicketWizardStates.EMAIL_FORWARDING_TO)
    await message.answer("Введите email, <b>на который</b> нужно установить переадресацию:", parse_mode="HTML", reply_markup=_email_forwarding_cancel_keyboard())


@router.message(TicketWizardStates.EMAIL_FORWARDING_TO, F.text)
async def email_forwarding_email_to(message: Message, state: FSMContext):
    email_to = (message.text or "").strip()
    if email_to.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not _looks_like_email(email_to):
        await message.answer("❌ Похоже, это не email. Введите адрес в формате name@domain.tld.")
        return
    await state.update_data(email_fwd_email_to=email_to)
    await state.set_state(TicketWizardStates.EMAIL_FORWARDING_DATE)
    await message.answer(
        "Введите дату включения/выключения переадресации.\n\nФормат: <b>YYYY-MM-DD</b> (например, 2026-03-16) или <b>DD.MM.YYYY</b>.",
        parse_mode="HTML",
        reply_markup=_email_forwarding_cancel_keyboard(),
    )


def _parse_date_to_yyyy_mm_dd(value: str) -> str | None:
    import datetime as _dt

    v = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return _dt.datetime.strptime(v, fmt).date().isoformat()
        except Exception:
            pass
    return None


@router.message(TicketWizardStates.EMAIL_FORWARDING_DATE, F.text)
async def email_forwarding_date(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    d = _parse_date_to_yyyy_mm_dd(raw)
    if not d:
        await message.answer("❌ Не понял дату. Введите YYYY-MM-DD или DD.MM.YYYY.")
        return
    data = await state.get_data()
    on_off = (data.get("email_fwd_on_off") or "").strip()
    email_from = (data.get("email_fwd_email_from") or "").strip()
    email_to = (data.get("email_fwd_email_to") or "").strip()

    # Jira validValues для customfield_13688:
    # 13006 = Включить, 13007 = Выключить
    on_off_value = "13006" if on_off == "email_fwd_on" else "13007"

    form_data = {
        "on_off": on_off_value,
        "email_from": email_from,
        "email_to": email_to,
        "redirection_date": d,
    }
    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, message.from_user.id, "email_forwarding", form_data)
    await state.clear()
    if not success:
        await message.answer(f"❌ Ошибка Jira: {issue_key}")
        return
    await message.answer(msg, disable_web_page_preview=True)


def _email_owa_request_kind_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=key)] for key, label in EMAIL_OWA_REQUEST_KINDS]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _email_owa_workplace_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="email_owa_skip_workplace")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _email_owa_attachments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать заявку", callback_data="email_owa_finish_ticket")],
        [InlineKeyboardButton(text="⏭ Пропустить вложения", callback_data="email_owa_skip_attachments")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


@router.callback_query(lambda c: c.data == "tp_email_owa_outlook")
async def tp_email_owa_outlook_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await state.set_state(TicketWizardStates.EMAIL_OWA_REQUEST_KIND)
    await callback.message.edit_text(
        "📨 <b>Электронная почта (Owa\\Outlook)</b>\n\nВыберите ваш запрос:",
        parse_mode="HTML",
        reply_markup=_email_owa_request_kind_keyboard(),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.EMAIL_OWA_REQUEST_KIND, F.data.in_(set(EMAIL_OWA_KIND_BY_ID.keys())))
async def tp_email_owa_select_kind(callback: CallbackQuery, state: FSMContext):
    kind_key = (callback.data or "").strip()
    kind_label = EMAIL_OWA_KIND_BY_ID.get(kind_key)
    if not kind_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(
        request_kind=kind_label,
        **save_wizard_session(ticket_wizard.WizardSession("email_owa", "EMAIL_OWA_RMS_OR_IP")),
    )
    await state.set_state(TicketWizardStates.EMAIL_OWA_RMS_OR_IP)
    await callback.message.edit_text(
        ticket_wizard.email_owa_rms_or_ip_screen(request_kind=kind_label).text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.EMAIL_OWA_REQUEST_KIND, F.text)
async def tp_email_owa_kind_text(message: Message):
    await message.reply("Выберите тип запроса кнопкой ниже.", reply_markup=_email_owa_request_kind_keyboard())


@router.message(TicketWizardStates.EMAIL_OWA_RMS_OR_IP, F.text)
async def tp_email_owa_rms(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not text:
        await message.reply("Укажите RMS или IP.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(
        rms_or_ip=text,
        **save_wizard_session(ticket_wizard.WizardSession("email_owa", "EMAIL_OWA_WORKPLACE")),
    )
    await state.set_state(TicketWizardStates.EMAIL_OWA_WORKPLACE)
    await message.reply(
        ticket_wizard.email_owa_workplace_screen().text,
        reply_markup=_email_owa_workplace_keyboard(),
    )


@router.callback_query(TicketWizardStates.EMAIL_OWA_WORKPLACE, F.data == "email_owa_skip_workplace")
async def tp_email_owa_skip_workplace(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        workplace="",
        **save_wizard_session(ticket_wizard.WizardSession("email_owa", "EMAIL_OWA_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.EMAIL_OWA_DESCRIPTION)
    await callback.message.edit_text(
        ticket_wizard.email_owa_description_screen().text,
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.EMAIL_OWA_WORKPLACE, F.text)
async def tp_email_owa_workplace(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(
        workplace=text,
        **save_wizard_session(ticket_wizard.WizardSession("email_owa", "EMAIL_OWA_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.EMAIL_OWA_DESCRIPTION)
    await message.reply(ticket_wizard.email_owa_description_screen().text, reply_markup=get_cancel_keyboard())


@router.message(TicketWizardStates.EMAIL_OWA_DESCRIPTION, F.text)
async def tp_email_owa_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not text:
        await message.reply("Описание не может быть пустым.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(
        description=text,
        email_owa_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("email_owa", "EMAIL_OWA_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.EMAIL_OWA_ATTACHMENTS)
    await message.reply(
        ticket_wizard.email_owa_attachments_screen(added_count=0).text,
        reply_markup=_email_owa_attachments_keyboard(),
    )


@router.message(TicketWizardStates.EMAIL_OWA_ATTACHMENTS, F.photo | F.document | F.video)
async def tp_email_owa_attachment_add(message: Message, state: FSMContext):
    data = await state.get_data()
    file_ids = list(data.get("email_owa_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов.", reply_markup=_email_owa_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        photo = message.photo[-1]
        if getattr(photo, "file_size", 0) and photo.file_size > 10 * 1024 * 1024:
            await message.reply("Фото не должно превышать 10 МБ.", reply_markup=_email_owa_attachments_keyboard())
            return
        file_id = photo.file_id
    elif message.document:
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_email_owa_attachments_keyboard())
            return
        file_id = message.document.file_id
    elif message.video:
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_email_owa_attachments_keyboard())
            return
        file_id = message.video.file_id
    if file_id:
        file_ids.append(file_id)
        await state.update_data(email_owa_attachment_file_ids=file_ids)
        await message.reply(f"📎 Добавлено {len(file_ids)} из 10.", reply_markup=_email_owa_attachments_keyboard())


async def _finish_email_owa_common(callback: CallbackQuery, state: FSMContext, file_ids: list):
    data = await state.get_data()
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department") or "").strip()
    phone = (profile.get("phone") or "").strip()
    jira_username = (profile.get("jira_username") or "").strip()
    if not department:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указано подразделение. Сначала выберите его в заявке Lupa.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not phone:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан телефон. Перепройдите регистрацию или привяжите аккаунт.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not jira_username:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан Jira-пользователь (Reporter). Перепройдите регистрацию.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    form_data = {
        "request_kind": (data.get("request_kind") or "").strip(),
        "rms_or_ip": (data.get("rms_or_ip") or "").strip(),
        "workplace": (data.get("workplace") or "").strip(),
        "description": (data.get("description") or "").strip(),
    }

    attachment_paths = []
    if file_ids:
        import os
        import tempfile
        bot = callback.bot
        for fid in file_ids[:10]:
            try:
                f = await bot.get_file(fid)
                safe_name = f.file_path.replace("/", "_").replace("\\", "_") if f.file_path else str(fid)
                path = os.path.join(tempfile.gettempdir(), f"email_owa_attach_{safe_name}")
                await bot.download_file(f.file_path, path)
                if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                    attachment_paths.append(path)
            except Exception as e:
                logger.warning("Скачивание вложения TG email_owa %s: %s", fid[:20] if isinstance(fid, str) else fid, e)
    try:
        success, issue_key, msg = await support_api.create_ticket(
            CHANNEL_ID,
            callback.from_user.id,
            "email_owa_outlook",
            form_data,
            attachment_paths=attachment_paths,
        )
        display_text = msg or issue_key
        await state.clear()
        await callback.message.edit_text(
            f"✅ {display_text}" if success else f"❌ {display_text}",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
    finally:
        import os
        for p in attachment_paths:
            try:
                os.remove(p)
            except Exception:
                pass


@router.callback_query(TicketWizardStates.EMAIL_OWA_ATTACHMENTS, F.data == "email_owa_skip_attachments")
async def tp_email_owa_skip_attachments(callback: CallbackQuery, state: FSMContext):
    await _finish_email_owa_common(callback, state, [])


@router.callback_query(TicketWizardStates.EMAIL_OWA_ATTACHMENTS, F.data == "email_owa_finish_ticket")
async def tp_email_owa_finish(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("email_owa_attachment_file_ids") or []
    await _finish_email_owa_common(callback, state, file_ids)


@router.callback_query(lambda c: c.data == "pc_issue_start")
async def pc_issue_start(callback: CallbackQuery, state: FSMContext):
    """Старт сценария «Проблема в работе ПК»."""
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    session = WizardSession("pc_problem", "PC_KIND")
    await state.set_state(TicketWizardStates.PC_KIND)
    await state.update_data(**save_wizard_session(session))
    await callback.message.edit_text(
        ticket_wizard.pc_kind_screen().text,
        parse_mode="HTML",
        reply_markup=get_pc_problem_kind_keyboard(),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.PC_KIND, F.data.startswith("pc_kind_"))
async def pc_issue_select_kind(callback: CallbackQuery, state: FSMContext):
    kind_id = callback.data.replace("pc_kind_", "", 1).strip()
    kind_label = PC_PROBLEM_KIND_BY_ID.get(kind_id)
    if not kind_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(
        pc_problem_kind_id=kind_id,
        pc_problem_kind_label=kind_label,
        kind_label=kind_label,
        **save_wizard_session(ticket_wizard.WizardSession("pc_issue", "PC_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.PC_DESCRIPTION)
    await callback.message.edit_text(
        ticket_wizard.pc_description_screen(kind_label=kind_label).text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="pc_skip_description")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        ]),
    )
    await callback.answer()


@router.message(TicketWizardStates.PC_KIND, F.text)
async def pc_issue_kind_text(message: Message):
    await message.reply("Выберите категорию кнопкой ниже.", reply_markup=get_pc_problem_kind_keyboard())

@router.callback_query(lambda c: c.data == "tp_section_password")
async def tp_section_password(callback: CallbackQuery, state: FSMContext):
    """Смена пароля — переход в сценарий смены пароля."""
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    from states import ChangePasswordStates
    await state.set_state(ChangePasswordStates.WAITING_FOR_NEW_PASSWORD)
    await callback.message.edit_text(
        "🔑 <b>Смена пароля</b>\n\nРубик поможет! Введите новый пароль:",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"tp_section_wms", "ticket_wms_issue"}))
async def tp_section_wms(callback: CallbackQuery, state: FSMContext):
    """WMS: меню из 4 кнопок (проблема / настройки / пользователь PSIwms / назад).
    Срабатывает и на кнопку из раздела (tp_section_wms), и на кнопку из каталога типов (ticket_wms_issue)."""
    await callback.answer()
    if not is_user_registered(callback.from_user.id):
        await callback.message.answer("Сначала пройдите регистрацию.")
        return
    await state.clear()
    await state.set_state(WmsTicketStates.WAITING_WMS_SUBTYPE)
    await state.update_data(wms_entry_point="section" if callback.data == "tp_section_wms" else "catalog")
    await callback.message.edit_text(
        "📦 <b>WMS</b>\n\nГена на связи! Выберите тип заявки:",
        parse_mode="HTML",
        reply_markup=get_wms_subtype_keyboard(),
    )


@router.callback_query(TicketWizardStates.WMS_ISSUE_DEPARTMENT, F.data.startswith("wms_dept_page_"))
async def tp_wms_department_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int(callback.data.replace("wms_dept_page_", ""))
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    depts = data.get("tp_wms_departments_list") or []
    await callback.message.edit_reply_markup(reply_markup=get_wms_department_keyboard(depts, page=page))
    await callback.answer()


@router.callback_query(TicketWizardStates.WMS_ISSUE_DEPARTMENT, F.data.regexp(r"^wms_dept_\d+$"))
async def tp_wms_department_select(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    depts = data.get("tp_wms_departments_list") or []
    try:
        idx = int(callback.data.replace("wms_dept_", ""))
    except ValueError:
        await callback.answer()
        return
    if idx < 0 or idx >= len(depts):
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    value = depts[idx]
    profile = get_user_profile(callback.from_user.id) or {}
    profile["department_wms"] = value
    save_user_profile(callback.from_user.id, profile)
    await state.clear()
    await state.set_state(TicketWizardStates.WMS_ISSUE_PROCESS)
    await state.update_data(ticket_type_id="wms_issue", **save_wizard_session(
        ticket_wizard.WizardSession("wms_issue", "WMS_ISSUE_PROCESS")
    ))
    await callback.message.edit_text(
        ticket_wizard.wms_issue_process_screen().text,
        parse_mode="HTML",
        reply_markup=get_wms_process_keyboard(),
    )
    await callback.answer()


async def _lupa_start_or_ask_department(callback_or_message, state: FSMContext, is_callback: bool):
    """Если в профиле нет подразделения — показать выбор из Jira и сохранить; иначе — шаг 1 Lupa (сервис)."""
    user_id = callback_or_message.from_user.id
    profile = get_user_profile(user_id) or {}
    department = (profile.get("department") or "").strip()
    if department:
        await state.clear()
        await state.set_state(TicketWizardStates.LUPA_SERVICE)
        await state.update_data(
            ticket_type_id="lupa_search",
            **save_wizard_session(ticket_wizard.WizardSession("lupa_search", "LUPA_SERVICE")),
        )
        text = ticket_wizard.lupa_service_screen().text
        if is_callback:
            await callback_or_message.message.edit_text(text, parse_mode="HTML", reply_markup=get_lupa_service_keyboard())
            await callback_or_message.answer()
        else:
            await callback_or_message.reply(text, parse_mode="HTML", reply_markup=get_lupa_service_keyboard())
        return
    from core.jira_departments import get_departments_async
    from keyboards import get_department_keyboard
    depts = await get_departments_async()
    await state.clear()
    await state.set_state(TicketWizardStates.LUPA_DEPARTMENT)
    await state.update_data(ticket_type_id="lupa_search", tp_lupa_departments_list=depts)
    if not depts:
        if is_callback:
            await callback_or_message.message.edit_text(
                "Список подразделений недоступен. Попробуйте позже или укажите подразделение в Личном кабинете.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
                ]),
            )
            await callback_or_message.answer()
        else:
            await callback_or_message.reply(
                "Список подразделений недоступен. Попробуйте позже или укажите подразделение в Личном кабинете.",
                reply_markup=get_main_menu_keyboard(user_id),
            )
        return
    msg_text = "🔍 <b>Создание заявки о поиске (Lupa)</b>\n\nВыберите ваше подразделение (оно будет сохранено в профиль):"
    if is_callback:
        await callback_or_message.message.edit_text(msg_text, parse_mode="HTML", reply_markup=get_department_keyboard(departments=depts))
        await callback_or_message.answer()
    else:
        await callback_or_message.reply(msg_text, parse_mode="HTML", reply_markup=get_department_keyboard(departments=depts))


@router.callback_query(lambda c: c.data == "tp_section_site")
async def tp_section_site(callback: CallbackQuery, state: FSMContext):
    """Сайт (Lupa): если нет employee_id — запросить табельный; иначе подразделение (если нет) → выбор сервиса."""
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    profile = get_user_profile(callback.from_user.id) or {}
    employee_id = (profile.get("employee_id") or "").strip()
    if not employee_id:
        hint = (
            "💡 <i>Табельный номер можно найти в расчётном листке. "
            "Он нужен для идентификации в заявке.</i>"
        )
        await state.clear()
        await state.set_state(TpSectionStates.WAITING_EMPLOYEE_ID)
        await callback.message.edit_text(
            f"🌐 <b>Сайт (Lupa)</b>\n\nУкажите ваш <b>табельный номер</b> (например: 0000000311):\n\n{hint}",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(),
        )
        await callback.answer()
        return
    await _lupa_start_or_ask_department(callback, state, is_callback=True)


EMPLOYEE_ID_HINT = (
    "💡 Табельный номер можно найти в расчётном листке. Он нужен для идентификации в заявке."
)


# --- Lupa: выбор по кнопкам (как the_bot_lupa) ---

@router.callback_query(TicketWizardStates.LUPA_SERVICE, F.data.in_(list(LUPA_SERVICE_VALUES)))
async def lupa_select_service(callback: CallbackQuery, state: FSMContext):
    """Шаг 1 → 2: сохранение сервиса, показ типа запроса."""
    service = LUPA_SERVICE_VALUES.get(callback.data)
    await state.update_data(problematic_service=service)
    await state.set_state(TicketWizardStates.LUPA_REQUEST_TYPE)
    await callback.message.edit_text(
        f"🔍 <b>Создание заявки о поиске</b>\n\n✅ Сервис: {service}\n\nШаг 2/5: Выберите тип запроса:",
        parse_mode="HTML",
        reply_markup=get_lupa_request_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.LUPA_REQUEST_TYPE, F.data.in_(list(LUPA_REQUEST_TYPE_VALUES)))
async def lupa_select_request_type(callback: CallbackQuery, state: FSMContext):
    """Шаг 2 → 3: сохранение типа запроса, подразделение из профиля, показ городов."""
    request_type = LUPA_REQUEST_TYPE_VALUES.get(callback.data)
    await state.update_data(request_type=request_type)
    profile = get_user_profile(callback.from_user.id) or {}
    subdivision = (profile.get("department") or "").strip()
    await state.update_data(subdivision=subdivision)
    from config import CONFIG
    cities = CONFIG.get("JIRA_LUPA", {}).get("CITIES", [])[:4]
    await state.set_state(TicketWizardStates.LUPA_CITY)
    await callback.message.edit_text(
        "🔍 <b>Создание заявки о поиске</b>\n\n"
        f"✅ Тип запроса: {request_type}\n"
        f"✅ Подразделение: {subdivision or 'не указано'}\n\n"
        "Шаг 3/5: Укажите город:",
        parse_mode="HTML",
        reply_markup=get_lupa_city_keyboard(cities),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.LUPA_CITY, F.data.startswith("lupa_city_"))
async def lupa_city_callback(callback: CallbackQuery, state: FSMContext):
    """Шаг 3: выбор города кнопкой или «Ввести вручную»."""
    if callback.data == "lupa_city_manual":
        await state.set_state(TicketWizardStates.LUPA_CITY_MANUAL)
        await callback.message.edit_text(
            "🔍 <b>Создание заявки о поиске</b>\n\nШаг 3/5: Введите название города:",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(),
        )
        await callback.answer()
        return
    city = callback.data.replace("lupa_city_", "", 1).replace("_", " ")
    await state.update_data(city=city)
    await state.set_state(TicketWizardStates.LUPA_DESCRIPTION)
    await callback.message.edit_text(
        f"🔍 <b>Создание заявки о поиске</b>\n\n✅ Город: {city}\n\n"
        "Шаг 4/5: Введите комментарий (описание проблемы):\n\nМожно пропустить, нажав кнопку ниже.",
        parse_mode="HTML",
        reply_markup=get_lupa_skip_comment_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.LUPA_CITY_MANUAL, F.text)
async def lupa_city_manual(message: Message, state: FSMContext):
    """Ввод города вручную."""
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    city = (message.text or "").strip()
    if not city:
        await message.reply("Введите название города или /cancel.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(city=city)
    await state.set_state(TicketWizardStates.LUPA_DESCRIPTION)
    await message.reply(
        f"✅ Город: {city}\n\n"
        "Шаг 4/5: Введите комментарий (описание проблемы):\n\nМожно пропустить, нажав кнопку ниже.",
        parse_mode="HTML",
        reply_markup=get_lupa_skip_comment_keyboard(),
    )


@router.callback_query(TicketWizardStates.LUPA_DESCRIPTION, F.data == "lupa_skip_comment")
async def lupa_skip_comment(callback: CallbackQuery, state: FSMContext):
    """Пропуск комментария → создание заявки."""
    await state.update_data(description="")
    data = await state.get_data()
    await state.clear()
    profile = get_user_profile(callback.from_user.id) or {}
    subdivision = (data.get("subdivision") or profile.get("department") or "").strip()
    form_data = {
        "description": data.get("description", ""),
        "problematic_service": data.get("problematic_service", ""),
        "request_type": data.get("request_type", ""),
        "subdivision": subdivision,
        "city": data.get("city", ""),
    }
    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, callback.from_user.id, "lupa_search", form_data)
    display_text = msg or issue_key
    if success:
        await callback.message.edit_text(f"✅ {display_text}", parse_mode="HTML")
        await callback.message.answer("Выберите действие:", reply_markup=get_main_menu_keyboard(callback.from_user.id))
    else:
        await callback.message.edit_text(f"❌ {display_text}", parse_mode="HTML")
        await callback.message.answer("Выберите действие:", reply_markup=get_main_menu_keyboard(callback.from_user.id))
    await callback.answer()


@router.message(TpSectionStates.WAITING_EMPLOYEE_ID, F.text)
async def tp_employee_id_enter(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    from validators import validate_employee_id
    value = (message.text or "").strip()
    ok, err = validate_employee_id(value)
    if not ok:
        await message.reply(f"❗ {err}\n\n{EMPLOYEE_ID_HINT}", reply_markup=get_cancel_keyboard())
        return
    taken, _ = check_employee_id_taken(value, exclude_user_id=message.from_user.id)
    if taken:
        await message.reply(
            "❗ Этот табельный номер уже привязан к другому пользователю. Введите другой номер или /cancel.",
            reply_markup=get_cancel_keyboard(),
        )
        return
    from user_storage import save_user_profile
    profile = get_user_profile(message.from_user.id) or {}
    profile["employee_id"] = value
    save_user_profile(message.from_user.id, profile)
    await _lupa_start_or_ask_department(message, state, is_callback=False)


@router.callback_query(TicketWizardStates.LUPA_DEPARTMENT, F.data.startswith("department_page_"))
async def lupa_department_page(callback: CallbackQuery, state: FSMContext):
    """Пагинация списка подразделений для Lupa."""
    try:
        page = int(callback.data.replace("department_page_", ""))
    except ValueError:
        await callback.answer()
        return
    from keyboards import get_department_keyboard
    data = await state.get_data()
    depts = data.get("tp_lupa_departments_list") or []
    await callback.message.edit_reply_markup(reply_markup=get_department_keyboard(departments=depts, page=page))
    await callback.answer()


@router.callback_query(TicketWizardStates.LUPA_DEPARTMENT, F.data.startswith("department_"))
async def lupa_department_select(callback: CallbackQuery, state: FSMContext):
    """Выбор подразделения для Lupa: сохраняем в профиль и переходим к выбору сервиса."""
    if "department_page_" in callback.data:
        await callback.answer()
        return
    data = await state.get_data()
    depts = data.get("tp_lupa_departments_list") or []
    raw = callback.data.replace("department_", "")
    if not raw.isdigit():
        await callback.answer()
        return
    idx = int(raw)
    if idx < 0 or idx >= len(depts):
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    value = depts[idx]
    profile = get_user_profile(callback.from_user.id) or {}
    profile["department"] = value
    save_user_profile(callback.from_user.id, profile)
    await state.set_state(TicketWizardStates.LUPA_SERVICE)
    await state.update_data(ticket_type_id="lupa_search")
    await callback.message.edit_text(
        "🔍 <b>Создание заявки о поиске</b>\n\nШаг 1/5: Выберите проблемный сервис:",
        parse_mode="HTML",
        reply_markup=get_lupa_service_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "create_ticket")
async def show_ticket_types(callback: CallbackQuery, state: FSMContext):
    """Старое меню типов из каталога (если где-то осталась кнопка)."""
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    response = support_api.get_ticket_types_menu(CHANNEL_ID, callback.from_user.id)
    if isinstance(response, Error):
        await callback.message.edit_text(f"❌ {response.message}")
        await callback.answer()
        return
    kwargs = render_menu_to_kwargs(response)
    await callback.message.edit_text(**kwargs)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data == "ticket_rubik_password_change")
async def ticket_rubik_selected(callback: CallbackQuery, state: FSMContext):
    """Смена пароля — перенаправляем в сценарий «Поменять пароль»."""
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    from core.ad_ldap import is_password_expired
    import asyncio as _asyncio_tg

    profile = get_user_profile(callback.from_user.id) or {}
    login = (profile.get("login") or "").strip()
    if not login:
        await callback.message.edit_text(
            "В профиле не указан рабочий логин. Обратитесь в поддержку для смены пароля.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    try:
        expired = await _asyncio_tg.to_thread(is_password_expired, login)
    except Exception:
        expired = None
    if expired is False:
        await callback.message.edit_text(
            "Смена пароля через бота доступна только если срок действия вашего пароля истёк.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if expired is None:
        await callback.message.edit_text(
            "Не удалось проверить в AD, истёк ли ваш пароль. Обратитесь на первую линию поддержки.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    await state.clear()
    from states import ChangePasswordStates
    await state.set_state(ChangePasswordStates.WAITING_FOR_NEW_PASSWORD)
    await callback.message.edit_text(
        "🔑 <b>Смена пароля</b>\n\nРубик поможет! Введите новый пароль:",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


# ---------- WMS ----------
@router.callback_query(lambda c: c.data == "wms_type_back", WmsTicketStates.WAITING_WMS_SUBTYPE)
async def wms_type_back(callback: CallbackQuery, state: FSMContext):
    """Назад из меню WMS: в раздел (Сайт | WMS | Смена пароля) или в каталог типов заявок."""
    data = await state.get_data()
    entry = data.get("wms_entry_point") or "section"
    await state.clear()
    if entry == "catalog":
        response = support_api.get_ticket_types_menu(CHANNEL_ID, callback.from_user.id)
        if isinstance(response, Error):
            await callback.message.edit_text(f"❌ {response.message}")
        else:
            kwargs = render_menu_to_kwargs(response)
            await callback.message.edit_text(**kwargs)
    else:
        await callback.message.edit_text(
            "📋 <b>Создать заявку в ТП</b>\n\nВ каком разделе создаём заявку?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Поиск/Сайт", callback_data="tp_section_site")],
                [InlineKeyboardButton(text="📦 WMS", callback_data="tp_section_wms")],
                [InlineKeyboardButton(text="🔑 Смена пароля", callback_data="tp_section_password")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_main")],
            ]),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "wms_show_subtype_menu", WmsTicketStates.WAITING_WMS_SUBTYPE)
async def wms_show_subtype_menu(callback: CallbackQuery, state: FSMContext):
    """Вернуть меню выбора типа заявки WMS (из заглушки «настройки» / «пользователь»)."""
    await callback.message.edit_text(
        "Выберите тип заявки:",
        parse_mode="HTML",
        reply_markup=get_wms_subtype_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "wms_type_issue", WmsTicketStates.WAITING_WMS_SUBTYPE)
async def wms_type_issue(callback: CallbackQuery, state: FSMContext):
    """Проблема в работе WMS: подразделение → процесс → тема → описание → вложения."""
    profile = get_user_profile(callback.from_user.id) or {}
    dept_wms = (profile.get("department_wms") or "").strip()
    if dept_wms:
        session = WizardSession("wms_issue", "WMS_ISSUE_PROCESS", {"department": dept_wms, "department_wms": dept_wms})
        await state.set_state(TicketWizardStates.WMS_ISSUE_PROCESS)
        await state.update_data(**save_wizard_session(session))
        await callback.message.edit_text(
            ticket_wizard.wms_issue_process_screen().text,
            parse_mode="HTML",
            reply_markup=get_wms_process_keyboard(),
        )
    else:
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async() or []
        session = WizardSession("wms_issue", "WMS_ISSUE_DEPARTMENT", {"departments": depts, "dept_page": 0})
        await state.set_state(TicketWizardStates.WMS_ISSUE_DEPARTMENT)
        await state.update_data(**save_wizard_session(session), departments=depts, dept_page=0)
        if not depts:
            await callback.message.edit_text(
                "Список подразделений WMS недоступен. Попробуйте позже или обратитесь в поддержку.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
                ]),
            )
        else:
            await callback.message.edit_text(
                ticket_wizard.wms_issue_start_screen(has_department_wms=False, departments=depts).text,
                parse_mode="HTML",
                reply_markup=get_wms_department_keyboard(depts),
            )
    await callback.answer()


@router.callback_query(lambda c: c.data == "wms_type_settings", WmsTicketStates.WAITING_WMS_SUBTYPE)
async def wms_type_settings(callback: CallbackQuery, state: FSMContext):
    """Изменение настроек системы WMS: подразделение → тип услуги → описание → вложения → завершить."""
    await callback.answer()
    profile = get_user_profile(callback.from_user.id) or {}
    dept_wms = (profile.get("department_wms") or "").strip()
    if dept_wms:
        session = WizardSession("wms_settings", "WMS_SETTINGS_SERVICE_TYPE", {"department": dept_wms})
        await state.set_state(TicketWizardStates.WMS_SETTINGS_SERVICE_TYPE)
        await state.update_data(**save_wizard_session(session))
        await callback.message.edit_text(
            ticket_wizard.wms_settings_service_type_screen().text,
            parse_mode="HTML",
            reply_markup=get_wms_service_type_keyboard(),
        )
    else:
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async() or []
        session = WizardSession("wms_settings", "WMS_SETTINGS_DEPARTMENT", {"departments": depts, "dept_page": 0})
        await state.set_state(TicketWizardStates.WMS_SETTINGS_DEPARTMENT)
        await state.update_data(**save_wizard_session(session), departments=depts, dept_page=0)
        if not depts:
            await callback.message.edit_text(
                "Список подразделений WMS недоступен. Попробуйте позже или обратитесь в поддержку.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="wms_show_subtype_menu")],
                ]),
            )
        else:
            await callback.message.edit_text(
                ticket_wizard.wms_settings_department_screen(depts).text,
                parse_mode="HTML",
                reply_markup=get_wms_department_keyboard(depts),
            )


@router.callback_query(TicketWizardStates.WMS_SETTINGS_DEPARTMENT, F.data.startswith("wms_dept_page_"))
async def wms_settings_department_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int(callback.data.replace("wms_dept_page_", ""))
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    depts = data.get("tp_wms_departments_list") or []
    await callback.message.edit_reply_markup(reply_markup=get_wms_department_keyboard(depts, page=page))
    await callback.answer()


@router.callback_query(TicketWizardStates.WMS_SETTINGS_DEPARTMENT, F.data.regexp(r"^wms_dept_\d+$"))
async def wms_settings_department_select(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    depts = data.get("tp_wms_departments_list") or []
    try:
        idx = int(callback.data.replace("wms_dept_", ""))
    except ValueError:
        await callback.answer()
        return
    if idx < 0 or idx >= len(depts):
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    value = depts[idx]
    profile = get_user_profile(callback.from_user.id) or {}
    profile["department_wms"] = value
    save_user_profile(callback.from_user.id, profile)
    await state.update_data(
        department=value,
        **save_wizard_session(ticket_wizard.WizardSession("wms_settings", "WMS_SETTINGS_SERVICE_TYPE")),
    )
    await state.set_state(TicketWizardStates.WMS_SETTINGS_SERVICE_TYPE)
    await callback.message.edit_text(
        ticket_wizard.wms_settings_service_type_screen().text,
        parse_mode="HTML",
        reply_markup=get_wms_service_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.WMS_SETTINGS_SERVICE_TYPE, F.data.in_({"wms_service_topology", "wms_service_other"}))
async def wms_settings_service_type(callback: CallbackQuery, state: FSMContext):
    """Тип услуги: Изменение топологии / Другие настройки."""
    from core.wms_constants import WMS_SERVICE_TYPES
    key = callback.data
    service_type = WMS_SERVICE_TYPES.get(key)
    if not service_type:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(
        service_type=service_type,
        **save_wizard_session(ticket_wizard.WizardSession("wms_settings", "WMS_SETTINGS_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.WMS_SETTINGS_DESCRIPTION)
    await callback.message.edit_text(
        ticket_wizard.wms_settings_description_screen().text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.callback_query(TicketWizardStates.WMS_SETTINGS_SERVICE_TYPE, F.data == "wms_show_subtype_menu")
async def wms_settings_back_to_subtype(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    entry = data.get("wms_entry_point", "section")
    await state.clear()
    await state.set_state(WmsTicketStates.WAITING_WMS_SUBTYPE)
    await state.update_data(wms_entry_point=entry)
    await callback.message.edit_text(
        "📦 <b>WMS</b>\n\nГена на связи! Выберите тип заявки:",
        parse_mode="HTML",
        reply_markup=get_wms_subtype_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.WMS_SETTINGS_DESCRIPTION, F.text)
async def wms_settings_description(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    desc = (message.text or "").strip()
    if desc == "—":
        desc = ""
    await state.update_data(
        description=desc,
        wms_settings_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("wms_settings", "WMS_SETTINGS_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.WMS_SETTINGS_ATTACHMENTS)
    await message.reply(
        ticket_wizard.wms_settings_attachments_screen(added_count=0).text,
        parse_mode="HTML",
        reply_markup=_wms_settings_attachments_keyboard(),
    )


@router.message(TicketWizardStates.WMS_SETTINGS_ATTACHMENTS, F.photo | F.document | F.video)
async def wms_settings_attachment_add(message: Message, state: FSMContext):
    data = await state.get_data()
    file_ids = list(data.get("wms_settings_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов. Нажмите «✅ Завершить создание задачи».", reply_markup=_wms_settings_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        photo = message.photo[-1]
        if getattr(photo, "file_size", 0) and photo.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_wms_settings_attachments_keyboard())
            return
        file_id = photo.file_id
    elif message.document:
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_wms_settings_attachments_keyboard())
            return
        file_id = message.document.file_id
    elif message.video:
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_wms_settings_attachments_keyboard())
            return
        file_id = message.video.file_id
    if file_id:
        file_ids.append(file_id)
        await state.update_data(wms_settings_attachment_file_ids=file_ids)
        await message.reply(f"📎 Добавлено {len(file_ids)} из 10. Приложите файлы и нажмите «✅ Завершить создание задачи».", reply_markup=_wms_settings_attachments_keyboard())


@router.callback_query(TicketWizardStates.WMS_SETTINGS_ATTACHMENTS, F.data == "finish_wms_settings")
async def finish_wms_settings(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("wms_settings_attachment_file_ids") or []
    if not file_ids:
        await callback.answer("Вложения обязательны. Загрузите хотя бы один файл.", show_alert=True)
        return
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department_wms") or profile.get("department") or data.get("department") or "").strip()
    if not department:
        await callback.message.edit_text("Укажите подразделение.", reply_markup=get_main_menu_keyboard(callback.from_user.id))
        await state.clear()
        await callback.answer()
        return
    form_data = {
        "department": department,
        "service_type": (data.get("service_type") or "").strip(),
        "description": (data.get("description") or "").strip() or "-",
    }
    if not form_data["service_type"]:
        await callback.message.edit_text("Ошибка: не выбран тип услуги.", reply_markup=get_main_menu_keyboard(callback.from_user.id))
        await state.clear()
        await callback.answer()
        return
    import tempfile
    import os
    bot = callback.bot
    attachment_paths = []
    try:
        for fid in file_ids[:10]:
            try:
                f = await bot.get_file(fid)
                safe_name = (f.file_path or fid).replace("/", "_").replace("\\", "_")
                path = os.path.join(tempfile.gettempdir(), f"wms_settings_{safe_name}")
                await bot.download_file(f.file_path, path)
                if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                    attachment_paths.append(path)
            except Exception as e:
                logger.warning("Скачивание вложения TG wms_settings %s: %s", fid[:20] if isinstance(fid, str) else fid, e)
        success, issue_key, msg = await support_api.create_ticket(
            CHANNEL_ID, callback.from_user.id, "wms_settings", form_data, attachment_paths=attachment_paths
        )
        display_text = msg or issue_key
        if success and attachment_paths:
            display_text += f"\n\n📎 Приложено файлов: {len(attachment_paths)}."
    except Exception as e:
        logger.exception("TG wms_settings: %s", e)
        success, issue_key, msg = False, None, "Ошибка при создании заявки."
        display_text = msg
    finally:
        for p in attachment_paths:
            try:
                os.remove(p)
            except Exception:
                pass
    await state.clear()
    await callback.message.edit_text(
        f"✅ {display_text}" if success else f"❌ {display_text}",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


# --- Пользователь PSIwms ---
@router.callback_query(lambda c: c.data == "wms_type_psi_user", WmsTicketStates.WAITING_WMS_SUBTYPE)
async def wms_type_psi_user(callback: CallbackQuery, state: FSMContext):
    """Создать/изменить/удалить пользователя PSIwms: тема → ФИО+должность → подразделение → комментарий → вложения (опционально)."""
    await callback.answer()
    await state.update_data(
        ticket_type_id="wms_psi_user",
        **save_wizard_session(ticket_wizard.WizardSession("wms_psi_user", "PSI_TITLE")),
    )
    await state.set_state(TicketWizardStates.PSI_TITLE)
    await callback.message.edit_text(
        ticket_wizard.psi_title_screen().text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(TicketWizardStates.PSI_TITLE, F.text)
async def psi_user_title(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    title = (message.text or "").strip()
    if len(title) < 3:
        await message.reply("Тема должна быть не менее 3 символов. Введите тему задачи:", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(
        summary=title,
        **save_wizard_session(ticket_wizard.WizardSession("wms_psi_user", "PSI_FULL_NAME")),
    )
    await state.set_state(TicketWizardStates.PSI_FULL_NAME)
    await message.reply(
        ticket_wizard.psi_full_name_screen().text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(TicketWizardStates.PSI_FULL_NAME, F.text)
async def psi_user_full_name(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(full_name=(message.text or "").strip())
    profile = get_user_profile(message.from_user.id) or {}
    dept_wms = (profile.get("department_wms") or "").strip()
    if dept_wms:
        await state.update_data(
            department=dept_wms,
            **save_wizard_session(ticket_wizard.WizardSession("wms_psi_user", "PSI_COMMENT")),
        )
        await state.set_state(TicketWizardStates.PSI_COMMENT)
        await message.reply(
            ticket_wizard.psi_comment_screen().text,
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(),
        )
    else:
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async()
        await state.set_state(TicketWizardStates.PSI_DEPARTMENT)
        await state.update_data(psi_departments_list=depts)
        if not depts:
            await message.reply("Список подразделений недоступен. Введите подразделение текстом или /cancel.", reply_markup=get_cancel_keyboard())
        else:
            await message.reply(
                "👤 Выберите подразделение:",
                parse_mode="HTML",
                reply_markup=get_wms_department_keyboard(depts),
            )


@router.callback_query(TicketWizardStates.PSI_DEPARTMENT, F.data.startswith("wms_dept_page_"))
async def psi_user_department_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int(callback.data.replace("wms_dept_page_", ""))
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    depts = data.get("psi_departments_list") or []
    await callback.message.edit_reply_markup(reply_markup=get_wms_department_keyboard(depts, page=page))
    await callback.answer()


@router.callback_query(TicketWizardStates.PSI_DEPARTMENT, F.data.regexp(r"^wms_dept_\d+$"))
async def psi_user_department_select(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    depts = data.get("psi_departments_list") or []
    try:
        idx = int(callback.data.replace("wms_dept_", ""))
    except ValueError:
        await callback.answer()
        return
    if idx < 0 or idx >= len(depts):
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    value = depts[idx]
    profile = get_user_profile(callback.from_user.id) or {}
    profile["department_wms"] = value
    save_user_profile(callback.from_user.id, profile)
    await state.update_data(
        department=value,
        **save_wizard_session(ticket_wizard.WizardSession("wms_psi_user", "PSI_COMMENT")),
    )
    await state.set_state(TicketWizardStates.PSI_COMMENT)
    await callback.message.edit_text(
        ticket_wizard.psi_comment_screen().text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.PSI_COMMENT, F.text)
async def psi_user_comment(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    comment = (message.text or "").strip()
    if comment == "—":
        comment = ""
    await state.update_data(
        comment=comment,
        psi_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("wms_psi_user", "PSI_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.PSI_ATTACHMENTS)
    await message.reply(
        ticket_wizard.psi_attachments_screen(added_count=0).text,
        parse_mode="HTML",
        reply_markup=_psi_user_attachments_keyboard(),
    )


@router.message(TicketWizardStates.PSI_ATTACHMENTS, F.photo | F.document | F.video)
async def psi_user_attachment_add(message: Message, state: FSMContext):
    data = await state.get_data()
    file_ids = list(data.get("psi_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов. Нажмите «✅ Завершить создание задачи».", reply_markup=_psi_user_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
        if getattr(message.photo[-1], "file_size", 0) and message.photo[-1].file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_psi_user_attachments_keyboard())
            return
    elif message.document:
        file_id = message.document.file_id
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_psi_user_attachments_keyboard())
            return
    elif message.video:
        file_id = message.video.file_id
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_psi_user_attachments_keyboard())
            return
    if file_id:
        file_ids.append(file_id)
        await state.update_data(psi_attachment_file_ids=file_ids)
        await message.reply(f"📎 Добавлено {len(file_ids)} из 10. «✅ Завершить создание задачи» или «⏭ Пропустить вложения».", reply_markup=_psi_user_attachments_keyboard())


async def _finish_psi_user_common(callback: CallbackQuery, state: FSMContext, file_ids: list):
    """Общая логика завершения заявки PSI user: создание тикета и вложения."""
    data = await state.get_data()
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department_wms") or profile.get("department") or data.get("department") or "").strip()
    if not department:
        await callback.message.edit_text("Укажите подразделение.", reply_markup=get_main_menu_keyboard(callback.from_user.id))
        await state.clear()
        return
    form_data = {
        "summary": (data.get("summary") or "").strip(),
        "full_name": (data.get("full_name") or "").strip(),
        "department": department,
        "comment": (data.get("comment") or "").strip(),
    }
    if not form_data["full_name"]:
        await callback.message.edit_text("Ошибка: не указаны ФИО и должность.", reply_markup=get_main_menu_keyboard(callback.from_user.id))
        await state.clear()
        return
    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, callback.from_user.id, "wms_psi_user", form_data)
    display_text = msg or issue_key
    attachment_paths = []
    if success and issue_key and file_ids:
        import tempfile
        import os
        bot = callback.bot
        try:
            for fid in file_ids[:10]:
                try:
                    f = await bot.get_file(fid)
                    safe_name = (f.file_path or fid).replace("/", "_").replace("\\", "_")
                    path = os.path.join(tempfile.gettempdir(), f"psi_user_{safe_name}")
                    await bot.download_file(f.file_path, path)
                    if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                        attachment_paths.append(path)
                except Exception as e:
                    logger.warning("Скачивание вложения TG psi_user %s: %s", fid[:20] if isinstance(fid, str) else fid, e)
            if attachment_paths:
                from core.jira_wms import add_attachments_to_issue
                added, _ = await add_attachments_to_issue(issue_key, attachment_paths)
                if added:
                    display_text += f"\n\n📎 Приложено файлов: {added}."
            for p in attachment_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
        except Exception as e:
            logger.exception("TG psi_user attachments: %s", e)
    await state.clear()
    await callback.message.edit_text(
        f"✅ {display_text}" if success else f"❌ {display_text}",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(callback.from_user.id),
    )


@router.callback_query(TicketWizardStates.PSI_ATTACHMENTS, F.data == "finish_psi_user")
async def finish_psi_user(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    file_ids = data.get("psi_attachment_file_ids") or []
    await _finish_psi_user_common(callback, state, file_ids)


@router.callback_query(TicketWizardStates.PSI_ATTACHMENTS, F.data == "skip_psi_attachment")
async def skip_psi_attachment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _finish_psi_user_common(callback, state, [])


@router.callback_query(TicketWizardStates.WMS_ISSUE_PROCESS, F.data.startswith("wms_process_"))
async def wms_process_callback(callback: CallbackQuery, state: FSMContext):
    """Шаг 2: выбор сбойного процесса (как the_bot_wms)."""
    from core.wms_constants import WMS_PROCESSES
    key = (callback.data or "").replace("wms_process_", "", 1)
    process_value = WMS_PROCESSES.get(key)
    if not process_value:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(process=process_value, **save_wizard_session(
        ticket_wizard.WizardSession("wms_issue", "WMS_ISSUE_SUMMARY")
    ))
    await state.set_state(TicketWizardStates.WMS_ISSUE_SUMMARY)
    await callback.message.edit_text(
        ticket_wizard.wms_issue_summary_screen().text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(TicketWizardStates.WMS_ISSUE_PROCESS, F.text)
async def wms_process_message(message: Message, state: FSMContext):
    """Процесс выбирается только кнопкой."""
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await message.reply(
        "Выберите процесс кнопкой ниже:",
        parse_mode="HTML",
        reply_markup=get_wms_process_keyboard(),
    )


@router.message(TicketWizardStates.WMS_ISSUE_SUMMARY, F.text)
async def wms_summary(message: Message, state: FSMContext):
    """Шаг 3: тема заявки."""
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(
        summary=(message.text or "").strip(),
        **save_wizard_session(ticket_wizard.WizardSession("wms_issue", "WMS_ISSUE_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.WMS_ISSUE_DESCRIPTION)
    skip_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="wms_skip_description")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])
    await message.reply(
        ticket_wizard.wms_issue_description_screen().text,
        parse_mode="HTML",
        reply_markup=skip_btn,
    )


@router.callback_query(TicketWizardStates.WMS_ISSUE_DESCRIPTION, F.data == "wms_skip_description")
async def wms_skip_description(callback: CallbackQuery, state: FSMContext):
    """Пропуск описания → шаг вложений."""
    await callback.answer()
    await state.update_data(description="", wms_attachment_file_ids=[])
    await state.set_state(TicketWizardStates.WMS_ISSUE_DESCRIPTION)
    text = (
        "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
        "Добавлено: 0 из 10.\n\nИли нажмите «Завершить создание тикета»."
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_wms_attachments_keyboard())


def _wms_attachments_keyboard():
    """Вложения WMS (проблема): завершить или отмена. Текст кнопки как в the_bot_wms."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить создание задачи", callback_data="wms_finish_ticket")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _wms_settings_attachments_keyboard():
    """Настройки WMS: вложения обязательны — только завершить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить создание задачи", callback_data="finish_wms_settings")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _psi_user_attachments_keyboard():
    """Пользователь PSIwms: вложения опциональны — завершить или пропустить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить создание задачи", callback_data="finish_psi_user")],
        [InlineKeyboardButton(text="⏭ Пропустить вложения", callback_data="skip_psi_attachment")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _pc_issue_attachments_keyboard():
    """Вложения для заявки ПК: завершить или пропустить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать заявку", callback_data="pc_finish_ticket")],
        [InlineKeyboardButton(text="⏭ Пропустить вложения", callback_data="pc_skip_attachments")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


async def _pc_issue_enter_attachments(callback_or_message, state: FSMContext, is_callback: bool):
    await state.set_state(TicketWizardStates.PC_ATTACHMENTS)
    await state.update_data(pc_attachment_file_ids=[])
    text = (
        "🖥️ <b>Проблема в работе ПК</b>\n\n"
        "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый), "
        "или нажмите «Создать заявку» / «Пропустить вложения»."
    )
    if is_callback:
        await callback_or_message.message.edit_text(text, parse_mode="HTML", reply_markup=_pc_issue_attachments_keyboard())
        await callback_or_message.answer()
    else:
        await callback_or_message.reply(text, parse_mode="HTML", reply_markup=_pc_issue_attachments_keyboard())


@router.callback_query(TicketWizardStates.PC_DESCRIPTION, F.data == "pc_skip_description")
async def pc_skip_description(callback: CallbackQuery, state: FSMContext):
    await state.update_data(description="")
    await _pc_issue_enter_attachments(callback, state, is_callback=True)


@router.message(TicketWizardStates.PC_DESCRIPTION, F.text)
async def pc_description(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(description=(message.text or "").strip())
    await _pc_issue_enter_attachments(message, state, is_callback=False)


@router.message(TicketWizardStates.PC_ATTACHMENTS, F.photo | F.document | F.video)
async def pc_attachment_add(message: Message, state: FSMContext):
    data = await state.get_data()
    file_ids = list(data.get("pc_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов.", reply_markup=_pc_issue_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        photo = message.photo[-1]
        if getattr(photo, "file_size", 0) and photo.file_size > 10 * 1024 * 1024:
            await message.reply("Фото не должно превышать 10 МБ.", reply_markup=_pc_issue_attachments_keyboard())
            return
        file_id = photo.file_id
    elif message.document:
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_pc_issue_attachments_keyboard())
            return
        file_id = message.document.file_id
    elif message.video:
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_pc_issue_attachments_keyboard())
            return
        file_id = message.video.file_id
    if file_id:
        file_ids.append(file_id)
        await state.update_data(pc_attachment_file_ids=file_ids)
        await message.reply(
            f"📎 Добавлено {len(file_ids)} из 10. Можно добавить ещё или завершить создание заявки.",
            reply_markup=_pc_issue_attachments_keyboard(),
        )


async def _finish_pc_issue_common(callback: CallbackQuery, state: FSMContext, file_ids: list):
    data = await state.get_data()
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department") or "").strip()
    phone = (profile.get("phone") or "").strip()
    jira_username = (profile.get("jira_username") or "").strip()
    if not department:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указано подразделение. Сначала выберите его в заявке Lupa.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not phone:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан телефон. Перепройдите регистрацию или привяжите аккаунт.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not jira_username:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан Jira-пользователь (Reporter). Перепройдите регистрацию.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    form_data = {
        "pc_problem_kind_id": (data.get("pc_problem_kind_id") or "").strip(),
        "description": (data.get("description") or "").strip(),
    }

    attachment_paths = []
    if file_ids:
        import os
        import tempfile

        bot = callback.bot
        for fid in file_ids[:10]:
            try:
                f = await bot.get_file(fid)
                safe_name = f.file_path.replace("/", "_").replace("\\", "_") if f.file_path else str(fid)
                path = os.path.join(tempfile.gettempdir(), f"pc_attach_{safe_name}")
                await bot.download_file(f.file_path, path)
                if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                    attachment_paths.append(path)
            except Exception as e:
                logger.warning("Скачивание вложения TG pc_problem %s: %s", fid[:20] if isinstance(fid, str) else fid, e)

    try:
        success, issue_key, msg = await support_api.create_ticket(
            CHANNEL_ID, callback.from_user.id, "pc_problem", form_data, attachment_paths=attachment_paths
        )
        display_text = msg or issue_key
        await state.clear()
        await callback.message.edit_text(
            f"✅ {display_text}" if success else f"❌ {display_text}",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
    finally:
        import os
        for p in attachment_paths:
            try:
                os.remove(p)
            except Exception:
                pass


@router.callback_query(TicketWizardStates.PC_ATTACHMENTS, F.data == "pc_skip_attachments")
async def pc_skip_attachments(callback: CallbackQuery, state: FSMContext):
    await _finish_pc_issue_common(callback, state, [])


@router.callback_query(TicketWizardStates.PC_ATTACHMENTS, F.data == "pc_finish_ticket")
async def pc_finish_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("pc_attachment_file_ids") or []
    await _finish_pc_issue_common(callback, state, file_ids)


def _orgtech_desc_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="orgtech_skip_description")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _orgtech_attachments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать заявку", callback_data="orgtech_finish_ticket")],
        [InlineKeyboardButton(text="⏭ Пропустить вложения", callback_data="orgtech_skip_attachments")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


@router.callback_query(lambda c: c.data == "orgtech_issue_start")
async def orgtech_issue_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await state.set_state(TicketWizardStates.ORGTECH_KIND)
    await state.update_data(**save_wizard_session(ticket_wizard.WizardSession("orgtech_problem", "ORGTECH_KIND")))
    await callback.message.edit_text(
        ticket_wizard.orgtech_kind_screen().text,
        parse_mode="HTML",
        reply_markup=get_orgtech_kind_keyboard(),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.ORGTECH_KIND, F.data.startswith("orgtech_kind_"))
async def orgtech_select_kind(callback: CallbackQuery, state: FSMContext):
    kind_id = callback.data.replace("orgtech_kind_", "", 1).strip()
    kind_label = ORGTECH_KIND_BY_ID.get(kind_id)
    if not kind_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(
        orgtech_kind=kind_label,
        kind_label=kind_label,
        **save_wizard_session(ticket_wizard.WizardSession("orgtech_problem", "ORGTECH_LOCATION")),
    )
    await state.set_state(TicketWizardStates.ORGTECH_LOCATION)
    await callback.message.edit_text(
        ticket_wizard.orgtech_location_screen(kind_label=kind_label).text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.ORGTECH_KIND, F.text)
async def orgtech_kind_text(message: Message):
    await message.reply("Выберите тип оргтехники кнопкой ниже.", reply_markup=get_orgtech_kind_keyboard())


@router.message(TicketWizardStates.ORGTECH_LOCATION, F.text)
async def orgtech_location(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not text:
        await message.reply("Укажите местоположение.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(
        location=text,
        **save_wizard_session(ticket_wizard.WizardSession("orgtech_problem", "ORGTECH_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.ORGTECH_DESCRIPTION)
    await message.reply(
        ticket_wizard.orgtech_description_screen().text,
        reply_markup=_orgtech_desc_keyboard(),
    )


@router.callback_query(TicketWizardStates.ORGTECH_DESCRIPTION, F.data == "orgtech_skip_description")
async def orgtech_skip_description(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        description="",
        orgtech_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("orgtech_problem", "ORGTECH_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.ORGTECH_ATTACHMENTS)
    await callback.message.edit_text(
        ticket_wizard.orgtech_attachments_screen(added_count=0).text,
        reply_markup=_orgtech_attachments_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.ORGTECH_DESCRIPTION, F.text)
async def orgtech_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(
        description=text,
        orgtech_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("orgtech_problem", "ORGTECH_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.ORGTECH_ATTACHMENTS)
    await message.reply(
        ticket_wizard.orgtech_attachments_screen(added_count=0).text,
        reply_markup=_orgtech_attachments_keyboard(),
    )


@router.message(TicketWizardStates.ORGTECH_ATTACHMENTS, F.photo | F.document | F.video)
async def orgtech_attachment_add(message: Message, state: FSMContext):
    data = await state.get_data()
    file_ids = list(data.get("orgtech_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов.", reply_markup=_orgtech_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        photo = message.photo[-1]
        if getattr(photo, "file_size", 0) and photo.file_size > 10 * 1024 * 1024:
            await message.reply("Фото не должно превышать 10 МБ.", reply_markup=_orgtech_attachments_keyboard())
            return
        file_id = photo.file_id
    elif message.document:
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_orgtech_attachments_keyboard())
            return
        file_id = message.document.file_id
    elif message.video:
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_orgtech_attachments_keyboard())
            return
        file_id = message.video.file_id
    if file_id:
        file_ids.append(file_id)
        await state.update_data(orgtech_attachment_file_ids=file_ids)
        await message.reply(f"📎 Добавлено {len(file_ids)} из 10.", reply_markup=_orgtech_attachments_keyboard())


async def _finish_orgtech_common(callback: CallbackQuery, state: FSMContext, file_ids: list):
    data = await state.get_data()
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department") or "").strip()
    phone = (profile.get("phone") or "").strip()
    jira_username = (profile.get("jira_username") or "").strip()
    if not department:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указано подразделение. Сначала выберите его в заявке Lupa.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not phone:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан телефон. Перепройдите регистрацию или привяжите аккаунт.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not jira_username:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан Jira-пользователь (Reporter). Перепройдите регистрацию.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    form_data = {
        "orgtech_kind": (data.get("orgtech_kind") or "").strip(),
        "location": (data.get("location") or "").strip(),
        "description": (data.get("description") or "").strip(),
    }
    attachment_paths = []
    if file_ids:
        import os
        import tempfile
        bot = callback.bot
        for fid in file_ids[:10]:
            try:
                f = await bot.get_file(fid)
                safe_name = f.file_path.replace("/", "_").replace("\\", "_") if f.file_path else str(fid)
                path = os.path.join(tempfile.gettempdir(), f"orgtech_attach_{safe_name}")
                await bot.download_file(f.file_path, path)
                if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                    attachment_paths.append(path)
            except Exception as e:
                logger.warning("Скачивание вложения TG orgtech %s: %s", fid[:20] if isinstance(fid, str) else fid, e)

    try:
        success, issue_key, msg = await support_api.create_ticket(
            CHANNEL_ID, callback.from_user.id, "orgtech_problem", form_data, attachment_paths=attachment_paths
        )
        display_text = msg or issue_key
        await state.clear()
        await callback.message.edit_text(
            f"✅ {display_text}" if success else f"❌ {display_text}",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
    finally:
        import os
        for p in attachment_paths:
            try:
                os.remove(p)
            except Exception:
                pass


@router.callback_query(TicketWizardStates.ORGTECH_ATTACHMENTS, F.data == "orgtech_skip_attachments")
async def orgtech_skip_attachments(callback: CallbackQuery, state: FSMContext):
    await _finish_orgtech_common(callback, state, [])


@router.callback_query(TicketWizardStates.ORGTECH_ATTACHMENTS, F.data == "orgtech_finish_ticket")
async def orgtech_finish_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("orgtech_attachment_file_ids") or []
    await _finish_orgtech_common(callback, state, file_ids)


def _peripheral_desc_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="peripheral_skip_description")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _peripheral_attachments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать заявку", callback_data="peripheral_finish_ticket")],
        [InlineKeyboardButton(text="⏭ Пропустить вложения", callback_data="peripheral_skip_attachments")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


@router.callback_query(lambda c: c.data == "peripheral_issue_start")
async def peripheral_issue_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await state.set_state(TicketWizardStates.PERIPHERAL_KIND)
    await state.update_data(**save_wizard_session(ticket_wizard.WizardSession("peripheral_equipment", "PERIPHERAL_KIND")))
    await callback.message.edit_text(
        ticket_wizard.peripheral_kind_screen().text,
        parse_mode="HTML",
        reply_markup=get_peripheral_kind_keyboard(),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.PERIPHERAL_KIND, F.data.startswith("peripheral_kind_"))
async def peripheral_select_kind(callback: CallbackQuery, state: FSMContext):
    kind_id = callback.data.replace("peripheral_kind_", "", 1).strip()
    kind_label = PERIPHERAL_KIND_BY_ID.get(kind_id)
    if not kind_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(
        peripheral_kind=kind_label,
        kind_label=kind_label,
        **save_wizard_session(ticket_wizard.WizardSession("peripheral_equipment", "PERIPHERAL_IP")),
    )
    await state.set_state(TicketWizardStates.PERIPHERAL_IP)
    await callback.message.edit_text(
        ticket_wizard.peripheral_ip_screen(kind_label=kind_label).text,
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.PERIPHERAL_KIND, F.text)
async def peripheral_kind_text(message: Message):
    await message.reply("Выберите вид оборудования кнопкой ниже.", reply_markup=get_peripheral_kind_keyboard())


@router.message(TicketWizardStates.PERIPHERAL_IP, F.text)
async def peripheral_ip(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not text:
        await message.reply("Укажите IP адрес или «нет».", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(
        ip_address=text,
        **save_wizard_session(ticket_wizard.WizardSession("peripheral_equipment", "PERIPHERAL_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.PERIPHERAL_DESCRIPTION)
    await message.reply(ticket_wizard.peripheral_description_screen().text, reply_markup=_peripheral_desc_keyboard())


@router.callback_query(TicketWizardStates.PERIPHERAL_DESCRIPTION, F.data == "peripheral_skip_description")
async def peripheral_skip_description(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        description="",
        peripheral_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("peripheral_equipment", "PERIPHERAL_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.PERIPHERAL_ATTACHMENTS)
    await callback.message.edit_text(
        ticket_wizard.peripheral_attachments_screen(added_count=0).text,
        reply_markup=_peripheral_attachments_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.PERIPHERAL_DESCRIPTION, F.text)
async def peripheral_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(
        description=text,
        peripheral_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("peripheral_equipment", "PERIPHERAL_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.PERIPHERAL_ATTACHMENTS)
    await message.reply(
        ticket_wizard.peripheral_attachments_screen(added_count=0).text,
        reply_markup=_peripheral_attachments_keyboard(),
    )


@router.message(TicketWizardStates.PERIPHERAL_ATTACHMENTS, F.photo | F.document | F.video)
async def peripheral_attachment_add(message: Message, state: FSMContext):
    data = await state.get_data()
    file_ids = list(data.get("peripheral_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов.", reply_markup=_peripheral_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        photo = message.photo[-1]
        if getattr(photo, "file_size", 0) and photo.file_size > 10 * 1024 * 1024:
            await message.reply("Фото не должно превышать 10 МБ.", reply_markup=_peripheral_attachments_keyboard())
            return
        file_id = photo.file_id
    elif message.document:
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_peripheral_attachments_keyboard())
            return
        file_id = message.document.file_id
    elif message.video:
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_peripheral_attachments_keyboard())
            return
        file_id = message.video.file_id
    if file_id:
        file_ids.append(file_id)
        await state.update_data(peripheral_attachment_file_ids=file_ids)
        await message.reply(f"📎 Добавлено {len(file_ids)} из 10.", reply_markup=_peripheral_attachments_keyboard())


async def _finish_peripheral_common(callback: CallbackQuery, state: FSMContext, file_ids: list):
    data = await state.get_data()
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department") or "").strip()
    phone = (profile.get("phone") or "").strip()
    jira_username = (profile.get("jira_username") or "").strip()
    if not department:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указано подразделение. Сначала выберите его в заявке Lupa.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not phone:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан телефон. Перепройдите регистрацию или привяжите аккаунт.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not jira_username:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан Jira-пользователь (Reporter). Перепройдите регистрацию.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    form_data = {
        "peripheral_kind": (data.get("peripheral_kind") or "").strip(),
        "ip_address": (data.get("ip_address") or "").strip(),
        "description": (data.get("description") or "").strip(),
    }
    attachment_paths = []
    if file_ids:
        import os
        import tempfile
        bot = callback.bot
        for fid in file_ids[:10]:
            try:
                f = await bot.get_file(fid)
                safe_name = f.file_path.replace("/", "_").replace("\\", "_") if f.file_path else str(fid)
                path = os.path.join(tempfile.gettempdir(), f"peripheral_attach_{safe_name}")
                await bot.download_file(f.file_path, path)
                if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                    attachment_paths.append(path)
            except Exception as e:
                logger.warning("Скачивание вложения TG peripheral %s: %s", fid[:20] if isinstance(fid, str) else fid, e)

    try:
        success, issue_key, msg = await support_api.create_ticket(
            CHANNEL_ID, callback.from_user.id, "peripheral_equipment", form_data, attachment_paths=attachment_paths
        )
        display_text = msg or issue_key
        await state.clear()
        await callback.message.edit_text(
            f"✅ {display_text}" if success else f"❌ {display_text}",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
    finally:
        import os
        for p in attachment_paths:
            try:
                os.remove(p)
            except Exception:
                pass


@router.callback_query(TicketWizardStates.PERIPHERAL_ATTACHMENTS, F.data == "peripheral_skip_attachments")
async def peripheral_skip_attachments(callback: CallbackQuery, state: FSMContext):
    await _finish_peripheral_common(callback, state, [])


@router.callback_query(TicketWizardStates.PERIPHERAL_ATTACHMENTS, F.data == "peripheral_finish_ticket")
async def peripheral_finish_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("peripheral_attachment_file_ids") or []
    await _finish_peripheral_common(callback, state, file_ids)


def _network_select_keyboard(options: list[tuple[str, str]], prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"{prefix}{oid}")] for oid, label in options]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _network_description_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="network_skip_description")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _network_rms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="network_skip_rms")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def _network_attachments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать заявку", callback_data="network_finish_ticket")],
        [InlineKeyboardButton(text="⏭ Пропустить вложения", callback_data="network_skip_attachments")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


@router.callback_query(lambda c: c.data == "network_issue_start")
async def network_issue_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await state.set_state(TicketWizardStates.NETWORK_TYPE)
    await state.update_data(**save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_TYPE")))
    await callback.message.edit_text(
        ticket_wizard.network_type_screen().text,
        parse_mode="HTML",
        reply_markup=_network_select_keyboard(NETWORK_TYPES, "network_type_"),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.NETWORK_TYPE, F.data.startswith("network_type_"))
async def network_select_type(callback: CallbackQuery, state: FSMContext):
    type_id = callback.data.replace("network_type_", "", 1).strip()
    type_label = NETWORK_TYPE_BY_ID.get(type_id)
    if not type_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(network_type=type_label, provider="", provider_other="", wifi_problem_owner="", pc_type="")
    if type_label == "Wi-Fi (беспроводная)":
        await state.update_data(**save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_WIFI_OWNER")))
        await state.set_state(TicketWizardStates.NETWORK_WIFI_OWNER)
        await callback.message.edit_text(
            ticket_wizard.network_wifi_owner_screen(network_type=type_label).text,
            parse_mode="HTML",
            reply_markup=_network_select_keyboard(NETWORK_WIFI_OWNERS, "network_wifi_owner_"),
        )
    elif type_label == "VPN":
        await state.update_data(**save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_PC_TYPE")))
        await state.set_state(TicketWizardStates.NETWORK_PC_TYPE)
        await callback.message.edit_text(
            ticket_wizard.network_pc_type_screen(network_type=type_label).text,
            parse_mode="HTML",
            reply_markup=_network_select_keyboard(NETWORK_PC_TYPES, "network_pc_type_"),
        )
    else:
        await state.update_data(**save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_PROVIDER")))
        await state.set_state(TicketWizardStates.NETWORK_PROVIDER)
        await callback.message.edit_text(
            ticket_wizard.network_provider_screen(network_type=type_label).text,
            parse_mode="HTML",
            reply_markup=_network_select_keyboard(NETWORK_PROVIDERS, "network_provider_"),
        )
    await callback.answer()


@router.message(TicketWizardStates.NETWORK_TYPE, F.text)
async def network_type_text(message: Message):
    await message.reply("Выберите тип сети кнопкой ниже.", reply_markup=_network_select_keyboard(NETWORK_TYPES, "network_type_"))


@router.callback_query(TicketWizardStates.NETWORK_WIFI_OWNER, F.data.startswith("network_wifi_owner_"))
async def network_select_wifi_owner(callback: CallbackQuery, state: FSMContext):
    owner_id = callback.data.replace("network_wifi_owner_", "", 1).strip()
    owner_label = NETWORK_WIFI_OWNER_BY_ID.get(owner_id)
    if not owner_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(
        wifi_problem_owner=owner_label,
        **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_RMS")),
    )
    await state.set_state(TicketWizardStates.NETWORK_RMS)
    await callback.message.edit_text(
        ticket_wizard.network_rms_screen().text,
        reply_markup=_network_rms_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.NETWORK_WIFI_OWNER, F.text)
async def network_wifi_owner_text(message: Message):
    await message.reply(
        "Выберите вариант кнопкой ниже.",
        reply_markup=_network_select_keyboard(NETWORK_WIFI_OWNERS, "network_wifi_owner_"),
    )


@router.callback_query(TicketWizardStates.NETWORK_PC_TYPE, F.data.startswith("network_pc_type_"))
async def network_select_pc_type(callback: CallbackQuery, state: FSMContext):
    pc_id = callback.data.replace("network_pc_type_", "", 1).strip()
    pc_label = NETWORK_PC_TYPE_BY_ID.get(pc_id)
    if not pc_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    data = await state.get_data()
    await state.update_data(
        pc_type=pc_label,
        **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_PROVIDER")),
    )
    await state.set_state(TicketWizardStates.NETWORK_PROVIDER)
    await callback.message.edit_text(
        ticket_wizard.network_provider_screen(network_type=data.get("network_type", "")).text,
        reply_markup=_network_select_keyboard(NETWORK_PROVIDERS, "network_provider_"),
    )
    await callback.answer()


@router.message(TicketWizardStates.NETWORK_PC_TYPE, F.text)
async def network_pc_type_text(message: Message):
    await message.reply(
        "Выберите тип ПК кнопкой ниже.",
        reply_markup=_network_select_keyboard(NETWORK_PC_TYPES, "network_pc_type_"),
    )


@router.callback_query(TicketWizardStates.NETWORK_PROVIDER, F.data.startswith("network_provider_"))
async def network_select_provider(callback: CallbackQuery, state: FSMContext):
    provider_id = callback.data.replace("network_provider_", "", 1).strip()
    provider_label = NETWORK_PROVIDER_BY_ID.get(provider_id)
    if not provider_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(provider=provider_label)
    if provider_label == "Другой":
        await state.update_data(**save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_PROVIDER_OTHER")))
        await state.set_state(TicketWizardStates.NETWORK_PROVIDER_OTHER)
        await callback.message.edit_text(
            ticket_wizard.network_provider_other_screen().text,
            reply_markup=get_cancel_keyboard(),
        )
    else:
        await state.update_data(
            provider_other="",
            **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_RMS")),
        )
        await state.set_state(TicketWizardStates.NETWORK_RMS)
        await callback.message.edit_text(
            ticket_wizard.network_rms_screen().text,
            reply_markup=_network_rms_keyboard(),
        )
    await callback.answer()


@router.message(TicketWizardStates.NETWORK_PROVIDER, F.text)
async def network_provider_text(message: Message):
    await message.reply(
        "Выберите провайдера кнопкой ниже.",
        reply_markup=_network_select_keyboard(NETWORK_PROVIDERS, "network_provider_"),
    )


@router.message(TicketWizardStates.NETWORK_PROVIDER_OTHER, F.text)
async def network_provider_other(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not text:
        await message.reply("Укажите поставщика услуг.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(
        provider_other=text,
        **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_RMS")),
    )
    await state.set_state(TicketWizardStates.NETWORK_RMS)
    await message.reply(ticket_wizard.network_rms_screen().text, reply_markup=_network_rms_keyboard())


@router.callback_query(TicketWizardStates.NETWORK_RMS, F.data == "network_skip_rms")
async def network_skip_rms(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        rms_internet_id="нет",
        **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.NETWORK_DESCRIPTION)
    await callback.message.edit_text(
        ticket_wizard.network_description_screen().text,
        reply_markup=_network_description_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.NETWORK_RMS, F.text)
async def network_rms(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(
        rms_internet_id=text or "нет",
        **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_DESCRIPTION")),
    )
    await state.set_state(TicketWizardStates.NETWORK_DESCRIPTION)
    await message.reply(ticket_wizard.network_description_screen().text, reply_markup=_network_description_keyboard())


@router.callback_query(TicketWizardStates.NETWORK_DESCRIPTION, F.data == "network_skip_description")
async def network_skip_description(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        description="",
        network_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.NETWORK_ATTACHMENTS)
    await callback.message.edit_text(
        ticket_wizard.network_attachments_screen(added_count=0).text,
        reply_markup=_network_attachments_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.NETWORK_DESCRIPTION, F.text)
async def network_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(
        description=text,
        network_attachment_file_ids=[],
        **save_wizard_session(ticket_wizard.WizardSession("network_issue", "NETWORK_ATTACHMENTS")),
    )
    await state.set_state(TicketWizardStates.NETWORK_ATTACHMENTS)
    await message.reply(
        ticket_wizard.network_attachments_screen(added_count=0).text,
        reply_markup=_network_attachments_keyboard(),
    )


@router.message(TicketWizardStates.NETWORK_ATTACHMENTS, F.photo | F.document | F.video)
async def network_attachment_add(message: Message, state: FSMContext):
    data = await state.get_data()
    file_ids = list(data.get("network_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов.", reply_markup=_network_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        photo = message.photo[-1]
        if getattr(photo, "file_size", 0) and photo.file_size > 10 * 1024 * 1024:
            await message.reply("Фото не должно превышать 10 МБ.", reply_markup=_network_attachments_keyboard())
            return
        file_id = photo.file_id
    elif message.document:
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_network_attachments_keyboard())
            return
        file_id = message.document.file_id
    elif message.video:
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_network_attachments_keyboard())
            return
        file_id = message.video.file_id
    if file_id:
        file_ids.append(file_id)
        await state.update_data(network_attachment_file_ids=file_ids)
        await message.reply(f"📎 Добавлено {len(file_ids)} из 10.", reply_markup=_network_attachments_keyboard())


async def _finish_network_common(callback: CallbackQuery, state: FSMContext, file_ids: list):
    data = await state.get_data()
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department") or "").strip()
    phone = (profile.get("phone") or "").strip()
    jira_username = (profile.get("jira_username") or "").strip()
    if not department:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указано подразделение. Сначала выберите его в заявке Lupa.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not phone:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан телефон. Перепройдите регистрацию или привяжите аккаунт.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    if not jira_username:
        await state.clear()
        await callback.message.edit_text(
            "❌ В профиле не указан Jira-пользователь (Reporter). Перепройдите регистрацию.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return
    network_type = (data.get("network_type") or "").strip()
    provider = (data.get("provider") or "").strip()
    provider_other = (data.get("provider_other") or "").strip()
    wifi_owner = (data.get("wifi_problem_owner") or "").strip()
    pc_type = (data.get("pc_type") or "").strip()
    if network_type == "Локальная сеть (проводная)" and not provider:
        await callback.answer("Укажите провайдера.", show_alert=True)
        return
    if network_type == "Wi-Fi (беспроводная)" and not wifi_owner:
        await callback.answer("Укажите, у кого проблемы.", show_alert=True)
        return
    if network_type == "VPN" and (not pc_type or not provider):
        await callback.answer("Для VPN нужно указать тип ПК и провайдера.", show_alert=True)
        return
    if provider == "Другой" and not provider_other:
        await callback.answer("Укажите поставщика услуг (Other).", show_alert=True)
        return

    form_data = {
        "network_type": network_type,
        "provider": provider,
        "provider_other": provider_other,
        "wifi_problem_owner": wifi_owner,
        "pc_type": pc_type,
        "description": (data.get("description") or "").strip(),
        "rms_internet_id": (data.get("rms_internet_id") or "").strip() or "нет",
        "ip_address": "нет",
        "preferred_contact_time": "нет",
    }
    attachment_paths = []
    if file_ids:
        import os
        import tempfile
        bot = callback.bot
        for fid in file_ids[:10]:
            try:
                f = await bot.get_file(fid)
                safe_name = f.file_path.replace("/", "_").replace("\\", "_") if f.file_path else str(fid)
                path = os.path.join(tempfile.gettempdir(), f"network_attach_{safe_name}")
                await bot.download_file(f.file_path, path)
                if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                    attachment_paths.append(path)
            except Exception as e:
                logger.warning("Скачивание вложения TG network %s: %s", fid[:20] if isinstance(fid, str) else fid, e)

    try:
        success, issue_key, msg = await support_api.create_ticket(
            CHANNEL_ID, callback.from_user.id, "network_problem", form_data, attachment_paths=attachment_paths
        )
        display_text = msg or issue_key
        await state.clear()
        await callback.message.edit_text(
            f"✅ {display_text}" if success else f"❌ {display_text}",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
    finally:
        import os
        for p in attachment_paths:
            try:
                os.remove(p)
            except Exception:
                pass


@router.callback_query(TicketWizardStates.NETWORK_ATTACHMENTS, F.data == "network_skip_attachments")
async def network_skip_attachments(callback: CallbackQuery, state: FSMContext):
    await _finish_network_common(callback, state, [])


@router.callback_query(TicketWizardStates.NETWORK_ATTACHMENTS, F.data == "network_finish_ticket")
async def network_finish_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    file_ids = data.get("network_attachment_file_ids") or []
    await _finish_network_common(callback, state, file_ids)


def _electronic_queue_type_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"eq_type_{sid}")] for sid, label in ELECTRONIC_QUEUE_SERVICE_TYPES]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(lambda c: c.data == "electronic_queue_start")
async def electronic_queue_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    await state.set_state(TicketWizardStates.EQUEUE_SERVICE_TYPE)
    await callback.message.edit_text(
        "🎫 <b>Электронная очередь</b>\n\nВыберите тип услуги:",
        parse_mode="HTML",
        reply_markup=_electronic_queue_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(TicketWizardStates.EQUEUE_SERVICE_TYPE, F.data.startswith("eq_type_"))
async def electronic_queue_select_type(callback: CallbackQuery, state: FSMContext):
    type_id = callback.data.replace("eq_type_", "", 1).strip()
    type_label = ELECTRONIC_QUEUE_SERVICE_TYPE_BY_ID.get(type_id)
    if not type_label:
        await callback.answer("Неверный выбор.", show_alert=True)
        return
    await state.update_data(service_type=type_label)
    await state.set_state(TicketWizardStates.EQUEUE_DESCRIPTION)
    await callback.message.edit_text(
        "🎫 <b>Электронная очередь</b>\n\n"
        f"✅ Тип услуги: {type_label}\n\n"
        "Введите подробное описание:",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(TicketWizardStates.EQUEUE_SERVICE_TYPE, F.text)
async def electronic_queue_type_text(message: Message):
    await message.reply("Выберите тип услуги кнопкой ниже.", reply_markup=_electronic_queue_type_keyboard())


@router.message(TicketWizardStates.EQUEUE_DESCRIPTION, F.text)
async def electronic_queue_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not text:
        await message.reply("Описание не может быть пустым.", reply_markup=get_cancel_keyboard())
        return
    data = await state.get_data()
    profile = get_user_profile(message.from_user.id) or {}
    department = (profile.get("department") or "").strip()
    phone = (profile.get("phone") or "").strip()
    jira_username = (profile.get("jira_username") or "").strip()
    if not department:
        await state.clear()
        await message.reply("❌ В профиле не указано подразделение.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not phone:
        await state.clear()
        await message.reply("❌ В профиле не указан телефон.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    if not jira_username:
        await state.clear()
        await message.reply("❌ В профиле не указан Jira-пользователь (Reporter).", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    form_data = {
        "summary": "Электронная очередь",
        "service_type": (data.get("service_type") or "").strip(),
        "description": text,
    }
    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, message.from_user.id, "electronic_queue", form_data)
    await state.clear()
    display_text = msg or issue_key
    await message.reply(
        f"✅ {display_text}" if success else f"❌ {display_text}",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(message.from_user.id),
    )


@router.message(TicketWizardStates.WMS_ISSUE_DESCRIPTION, F.text)
async def wms_description(message: Message, state: FSMContext):
    """Шаг 4: описание (или пропустить)."""
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(TicketWizardStates.WMS_ISSUE_DESCRIPTION)
    await state.update_data(wms_attachment_file_ids=[])
    await message.reply(
        "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый). Или нажмите «Завершить создание тикета».",
        parse_mode="HTML",
        reply_markup=_wms_attachments_keyboard(),
    )


@router.message(TicketWizardStates.WMS_ISSUE_DESCRIPTION, F.photo | F.document | F.video)
async def wms_attachment_add(message: Message, state: FSMContext):
    """Добавление вложения (до 10, до 10 МБ)."""
    data = await state.get_data()
    file_ids = list(data.get("wms_attachment_file_ids") or [])
    if len(file_ids) >= 10:
        await message.reply("Достигнут лимит 10 файлов. Нажмите «Завершить создание тикета».", reply_markup=_wms_attachments_keyboard())
        return
    file_id = None
    if message.photo:
        photo = message.photo[-1]
        if getattr(photo, "file_size", 0) and photo.file_size > 10 * 1024 * 1024:
            await message.reply("Фото не должно превышать 10 МБ.", reply_markup=_wms_attachments_keyboard())
            return
        file_id = photo.file_id
    elif message.document:
        if message.document.file_size and message.document.file_size > 10 * 1024 * 1024:
            await message.reply("Файл не должен превышать 10 МБ.", reply_markup=_wms_attachments_keyboard())
            return
        file_id = message.document.file_id
    elif message.video:
        if message.video.file_size and message.video.file_size > 10 * 1024 * 1024:
            await message.reply("Видео не должно превышать 10 МБ.", reply_markup=_wms_attachments_keyboard())
            return
        file_id = message.video.file_id
    if file_id:
        file_ids.append(file_id)
        await state.update_data(wms_attachment_file_ids=file_ids)
        await message.reply(f"📎 Добавлено {len(file_ids)} из 10. Можно приложить ещё или нажмите «Завершить создание тикета».", reply_markup=_wms_attachments_keyboard())


@router.callback_query(TicketWizardStates.WMS_ISSUE_DESCRIPTION, F.data == "wms_finish_ticket")
async def wms_finish_ticket(callback: CallbackQuery, state: FSMContext):
    """Завершение: создание тикета и загрузка вложений в Jira."""
    await callback.answer()
    data = await state.get_data()
    profile = get_user_profile(callback.from_user.id) or {}
    department = (profile.get("department_wms") or profile.get("department") or "").strip()
    if not department:
        await callback.message.edit_text(
            "Укажите подразделение в профиле или начните заявку заново и выберите подразделение.",
            reply_markup=get_main_menu_keyboard(callback.from_user.id),
        )
        await state.clear()
        return
    form_data = {
        "summary": (data.get("summary") or "").strip() or "Заявка по настройке WMS",
        "description": (data.get("description") or "").strip(),
        "process": (data.get("process") or "").strip(),
        "department": department,
    }
    if not form_data["process"]:
        await callback.message.edit_text("Ошибка: не выбран процесс.", reply_markup=get_main_menu_keyboard(callback.from_user.id))
        await state.clear()
        return
    file_ids = data.get("wms_attachment_file_ids") or []
    # API возвращает (success, issue_key, msg); msg — текст с ссылкой на заявку
    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, callback.from_user.id, "wms_issue", form_data)
    display_text = msg or issue_key
    attachment_paths = []
    if success and issue_key and file_ids:
        import tempfile
        import os
        bot = callback.bot
        try:
            for fid in file_ids[:10]:
                try:
                    f = await bot.get_file(fid)
                    # destination: путь к файлу (aiogram 3: download_file(file_path, destination))
                    safe_name = f.file_path.replace("/", "_").replace("\\", "_") if f.file_path else fid
                    path = os.path.join(tempfile.gettempdir(), f"wms_attach_{safe_name}")
                    await bot.download_file(f.file_path, path)
                    if os.path.isfile(path) and os.path.getsize(path) <= 10 * 1024 * 1024:
                        attachment_paths.append(path)
                except Exception as e:
                    logger.warning("Скачивание вложения TG %s: %s", fid[:20] if isinstance(fid, str) else fid, e)
            if attachment_paths:
                from core.jira_wms import add_attachments_to_issue
                added, _ = await add_attachments_to_issue(issue_key, attachment_paths)
                if added:
                    display_text += f"\n\n📎 Приложено файлов: {added}."
                else:
                    logger.warning("TG WMS: add_attachments_to_issue не добавил файлы к %s", issue_key)
            elif file_ids:
                logger.warning("TG WMS: вложений было %s, скачано 0", len(file_ids))
        finally:
            for p in attachment_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
    await state.clear()
    await callback.message.edit_text(
        f"✅ {display_text}" if success else f"❌ {display_text}",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(callback.from_user.id),
    )


@router.message(TicketWizardStates.WMS_ISSUE_DEPARTMENT, F.text)
async def wms_department(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(department=(message.text or "").strip())
    data = await state.get_data()
    await state.clear()
    form_data = {
        "summary": data.get("summary", ""),
        "description": data.get("description", ""),
        "process": data.get("process", ""),
        "department": data.get("department", ""),
    }
    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, message.from_user.id, "wms_issue", form_data)
    display_text = msg or issue_key
    if success:
        await message.reply(f"✅ {display_text}", parse_mode="HTML", reply_markup=get_main_menu_keyboard(message.from_user.id))
    else:
        await message.reply(f"❌ {display_text}", parse_mode="HTML", reply_markup=get_main_menu_keyboard(message.from_user.id))


# ---------- Lupa ----------
@router.callback_query(lambda c: c.data == "ticket_lupa_search")
async def ticket_lupa_start(callback: CallbackQuery, state: FSMContext):
    if not is_user_registered(callback.from_user.id):
        await callback.answer("Сначала пройдите регистрацию.", show_alert=True)
        return
    await state.clear()
    profile = get_user_profile(callback.from_user.id, CHANNEL_ID) or {}
    dept = (profile.get("department") or "").strip()
    if dept:
        session = WizardSession("lupa_search", "LUPA_SERVICE", {"subdivision": dept})
        await state.set_state(TicketWizardStates.LUPA_SERVICE)
        await state.update_data(**save_wizard_session(session))
        await callback.message.edit_text(
            ticket_wizard.lupa_service_screen().text,
            parse_mode="HTML",
            reply_markup=get_lupa_service_keyboard(),
        )
    else:
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async() or []
        session = WizardSession("lupa_search", "LUPA_DEPARTMENT", {"departments": depts, "dept_page": 0})
        await state.set_state(TicketWizardStates.LUPA_DEPARTMENT)
        await state.update_data(**save_wizard_session(session), departments=depts, dept_page=0)
        screen = ticket_wizard.lupa_department_screen(depts)
        from keyboards import get_lupa_department_keyboard
        await callback.message.edit_text(
            screen.text, parse_mode="HTML",
            reply_markup=get_lupa_department_keyboard(depts, 0),
        )
    await callback.answer()


@router.message(TicketWizardStates.LUPA_DESCRIPTION, F.text)
async def lupa_description(message: Message, state: FSMContext):
    """Шаг 4/5: ввод комментария (описание) → создание заявки."""
    if (message.text or "").strip().lower() == "/cancel":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return
    await state.update_data(description=(message.text or "").strip())
    data = await state.get_data()
    await state.clear()
    profile = get_user_profile(message.from_user.id) or {}
    subdivision = (data.get("subdivision") or profile.get("department") or "").strip()
    form_data = {
        "description": data.get("description", ""),
        "problematic_service": data.get("problematic_service", ""),
        "request_type": data.get("request_type", ""),
        "subdivision": subdivision,
        "city": data.get("city", ""),
    }
    success, issue_key, msg = await support_api.create_ticket(CHANNEL_ID, message.from_user.id, "lupa_search", form_data)
    display_text = msg or issue_key
    if success:
        await message.reply(f"✅ {display_text}", parse_mode="HTML", reply_markup=get_main_menu_keyboard(message.from_user.id))
    else:
        await message.reply(f"❌ {display_text}", parse_mode="HTML", reply_markup=get_main_menu_keyboard(message.from_user.id))
