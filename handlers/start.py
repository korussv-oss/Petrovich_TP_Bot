"""
/start, отмена, возврат в главное меню.
Ответы берутся из Core API; здесь только рендер в Telegram (тонкий адаптер).
"""
import logging
import random
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from aiogram.filters import Command

from user_storage import is_user_registered, bind_account_by_phone, needs_phone_verification_channel, update_phone_and_mark_verified_channel
from core.support.api import support_api
from core.support.models import Text, Menu
from adapters.telegram.render import render_menu_to_kwargs, render_text_to_kwargs
from states import BindAccountStates
from keyboards import get_contact_request_keyboard, remove_reply_keyboard, get_main_menu_keyboard

logger = logging.getLogger(__name__)
router = Router()
CHANNEL_ID = "telegram"


@router.callback_query(lambda c: c.data == "bind_account")
async def bind_account_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BindAccountStates.WAITING_FOR_CONTACT)
    await state.update_data(mode="bind")
    await callback.message.edit_text(
        "🔗 <b>Привязать аккаунт</b>\n\n"
        "Нажмите кнопку ниже, чтобы поделиться номером телефона. "
        "Если этот номер уже зарегистрирован в системе (например, в MAX), аккаунт будет привязан к этому чату.",
        parse_mode="HTML",
    )
    await callback.message.answer(
        "Поделитесь контактом:",
        reply_markup=get_contact_request_keyboard(),
    )
    await callback.answer()


@router.message(BindAccountStates.WAITING_FOR_CONTACT, F.contact)
async def bind_account_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("mode") or "bind"
    if not message.contact or message.contact.user_id != message.from_user.id:
        await message.reply(
            "Пожалуйста, поделитесь именно своим контактом (кнопка «Поделиться контактом»).",
            reply_markup=get_contact_request_keyboard(),
        )
        return
    phone = (message.contact.phone_number or "").strip()
    if not phone:
        await message.reply("Не удалось получить номер. Попробуйте ещё раз.", reply_markup=get_contact_request_keyboard())
        return

    user_id = message.from_user.id

    if mode == "verify_phone":
        # Обновляем телефон в профиле и снимаем флаг проверки
        update_phone_and_mark_verified_channel(CHANNEL_ID, user_id, phone)
        await state.clear()
        await message.reply(
            "✅ Номер телефона обновлён и подтверждён.",
            reply_markup=remove_reply_keyboard(),
        )
        response = support_api.get_main_menu(CHANNEL_ID, user_id)
        kwargs = render_menu_to_kwargs(response)
        await message.answer(**kwargs)
        return

    ok, msg = bind_account_by_phone(user_id, phone)
    await state.clear()
    await message.reply(msg, reply_markup=remove_reply_keyboard())
    if ok:
        response = support_api.get_main_menu(CHANNEL_ID, user_id)
        kwargs = render_menu_to_kwargs(response)
        await message.answer(**kwargs)
    else:
        response = support_api.get_start(CHANNEL_ID, user_id)
        kwargs = render_menu_to_kwargs(response) if isinstance(response, Menu) else render_text_to_kwargs(response)
        await message.answer(**kwargs)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    response = support_api.get_start(CHANNEL_ID, user_id)
    kwargs = render_menu_to_kwargs(response) if isinstance(response, Menu) else render_text_to_kwargs(response)
    await message.answer(**kwargs)

    # Для мигрированных пользователей из Лупы: просим подтвердить номер телефона
    if needs_phone_verification_channel(CHANNEL_ID, user_id):
        await state.set_state(BindAccountStates.WAITING_FOR_CONTACT)
        await state.update_data(mode="verify_phone")
        await message.answer(
            "📱 Для продолжения подтвердите номер телефона.\n\n"
            "Нажмите кнопку «Поделиться контактом», чтобы обновить номер в профиле.",
            reply_markup=get_contact_request_keyboard(),
        )


@router.callback_query(lambda c: c.data == "cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    response = support_api.get_start(CHANNEL_ID, user_id)
    await callback.message.edit_text("❌ Действие отменено." if is_user_registered(user_id) else "❌ Регистрация отменена.")
    kwargs = render_menu_to_kwargs(response)
    await callback.message.answer(**kwargs)
    await callback.answer()


@router.message(Command("cancel"))
@router.message(F.text == "/cancel")
async def cancel_message(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    response = support_api.get_start(CHANNEL_ID, user_id)
    kwargs = render_menu_to_kwargs(response)
    kwargs["text"] = "❌ Действие отменено." if is_user_registered(user_id) else "Регистрация отменена."
    await message.answer(**kwargs)


# Приветствие для неавторизованных: при любом сообщении без FSM (кроме /start и др. команд) — подсказка про /start.
# Важно: Telegram не присылает событие «пользователь открыл чат»; ответ возможен только после его сообщения.
WELCOME_UNREGISTERED = (
    "Привет! Чтобы начать работу, используй команду /start."
)


@router.message(StateFilter(None))
async def welcome_unregistered(message: Message, state: FSMContext):
    if is_user_registered(message.from_user.id):
        return
    text = (message.text or "").strip()
    if text.lower() in ("/start", "/cancel"):
        return
    if text.startswith("/"):
        return
    await message.answer(WELCOME_UNREGISTERED)


@router.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    response = support_api.get_main_menu(CHANNEL_ID, user_id)
    kwargs = render_menu_to_kwargs(response)
    await callback.message.edit_text(**kwargs)
    await callback.answer()


@router.message(Command("showracemenu"))
@router.message(F.text == "/showracemenu")
async def cmd_showracemenu(message: Message):
    pict_dir = Path(__file__).resolve().parents[1] / "Pict"
    if pict_dir.is_dir():
        exts = ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp")
        files = []
        for ext in exts:
            files.extend(pict_dir.glob(ext))
        if files:
            path = random.choice(files)
            await message.answer_photo(FSInputFile(path))
            return
    await message.answer("…")
