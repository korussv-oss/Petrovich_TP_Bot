"""
Обработчики регистрации.
Режим AD: почта → контакт (телефон) → поиск в AD по телефону → доступ или ссылка на портал ТП.
"""
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import RegistrationStates, AdRegistrationStates
from keyboards import (
    get_main_menu_keyboard,
    get_start_keyboard,
    get_cancel_keyboard,
    get_department_keyboard,
    get_contact_request_keyboard,
    remove_reply_keyboard,
)
from validators import (
    validate_full_name,
    validate_work_login,
    validate_corporate_email,
    validate_phone,
    normalize_phone_display,
)
from core.registration import register_user, register_user_from_ad, _enrich_profile_with_jira_username
from user_storage import check_login_or_email_taken, get_user_profile, save_user_profile
from config import CONFIG

logger = logging.getLogger(__name__)
router = Router()


def _support_portal_message() -> str:
    url = (CONFIG.get("SUPPORT_PORTAL_URL") or "").strip()
    if url:
        return (
            "❌ В базе сотрудников (AD) по этому номеру телефона никого не найдено.\n\n"
            "Обратитесь в службу поддержки через портал:\n"
            f"<a href=\"{url}\">{url}</a>"
        )
    return (
        "❌ В базе сотрудников (AD) по этому номеру телефона никого не найдено.\n\n"
        "Обратитесь в службу поддержки (ссылку на портал уточните у администратора)."
    )


@router.callback_query(lambda c: c.data == "start_registration")
async def start_registration(callback: CallbackQuery, state: FSMContext):
    """Старт регистрации через AD: шаг 1 — рабочая почта."""
    await state.clear()
    await callback.message.edit_text(
        "📝 <b>Регистрация</b>\n\n"
        "Шаг 1/2: Введите вашу <b>рабочую почту</b> (@petrovich.ru или @petrovich.tech):",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await state.set_state(AdRegistrationStates.WAITING_FOR_EMAIL)
    await callback.answer()


@router.message(AdRegistrationStates.WAITING_FOR_EMAIL, F.text)
async def process_ad_email(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    ok, err = validate_corporate_email(text)
    if not ok:
        await message.reply(f"❗ {err}\n\nПопробуйте снова или нажмите Отмена.", reply_markup=get_cancel_keyboard())
        return
    email_lower = text.lower()
    await state.update_data(email=email_lower)
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(AdRegistrationStates.WAITING_FOR_CONTACT)
    await message.answer(
        "✅ Почта сохранена.\n\n"
        "Шаг 2/2: Поделитесь номером телефона — нажмите кнопку ниже (так мы проверим вас в базе сотрудников):",
        parse_mode="HTML",
        reply_markup=get_contact_request_keyboard(),
    )


@router.message(AdRegistrationStates.WAITING_FOR_CONTACT, F.contact)
async def process_ad_contact(message: Message, state: FSMContext):
    contact = message.contact
    if not contact or contact.user_id != message.from_user.id:
        await message.reply(
            "❌ Пожалуйста, поделитесь именно своим контактом (кнопка «Поделиться контактом»).",
            reply_markup=get_contact_request_keyboard(),
        )
        return
    raw_phone = (contact.phone_number or "").strip()
    if not raw_phone:
        await message.reply(
            "❌ Не удалось получить номер из контакта. Попробуйте ещё раз.",
            reply_markup=get_contact_request_keyboard(),
        )
        return
    ok, err = validate_phone(raw_phone)
    if not ok:
        await message.reply(
            f"❗ {err}\n\nПоделитесь контактом снова или нажмите Отмена.",
            reply_markup=get_contact_request_keyboard(),
        )
        return
    phone_norm = normalize_phone_display(raw_phone)
    data = await state.get_data()
    email_entered = (data.get("email") or "").strip().lower()
    await state.clear()

    # Поиск в AD по телефону (синхронный ldap3 — в потоке)
    from core.ad_ldap import search_user_by_phone
    profile = await asyncio.to_thread(search_user_by_phone, raw_phone)
    if not profile:
        # Fallback: телефон не найден, но возможно в AD есть запись по email
        if email_entered:
            from core.ad_ldap import search_users_by_query

            found_by_email = await asyncio.to_thread(search_users_by_query, email_entered, limit=5)
            if found_by_email:
                url = (CONFIG.get("SUPPORT_PORTAL_URL") or "").strip()
                support = (
                    f" Обратитесь в службу поддержки через портал: <a href=\"{url}\">{url}</a>."
                    if url
                    else " Обратитесь в службу поддержки."
                )
                await message.reply(
                    "❌ Сотрудник найден в базе сотрудников (AD) по email, но по этому номеру телефона не найден. "
                    "Возможно, в контакте другой номер. "
                    "Попробуйте поделиться контактом с правильного номера."
                    + support,
                    parse_mode="HTML",
                    reply_markup=remove_reply_keyboard(),
                )
                await message.reply("Начните заново:", reply_markup=get_start_keyboard(message.from_user.id))
                return

        await message.reply(
            _support_portal_message(),
            parse_mode="HTML",
            reply_markup=remove_reply_keyboard(),
        )
        await message.reply("Начните заново:", reply_markup=get_start_keyboard(message.from_user.id))
        return
    # Проверка: почта из AD должна совпадать с введённой (игнорируем регистр)
    if email_entered and profile.get("email") and (profile["email"].lower() != email_entered):
        await message.reply(
            "❌ Почта, которую вы ввели, не совпадает с записью в базе сотрудников по этому номеру телефона. "
            "Проверьте почту или поделитесь контактом с правильного номера.",
            reply_markup=remove_reply_keyboard(),
        )
        await message.reply("Начните заново:", reply_markup=get_start_keyboard(message.from_user.id))
        return
    success, msg = register_user_from_ad(message.from_user.id, profile)
    if not success:
        await message.reply(msg, reply_markup=remove_reply_keyboard())
        await message.reply("Начните заново:", reply_markup=get_start_keyboard(message.from_user.id))
        return
    # Обогащение jira_username (асинхронно)
    try:
        current = get_user_profile(message.from_user.id)
        if current:
            enriched = await _enrich_profile_with_jira_username(dict(current))
            save_user_profile(message.from_user.id, enriched)
    except Exception:
        pass
    await message.reply(
        "✅ <b>Регистрация завершена!</b>",
        parse_mode="HTML",
        reply_markup=remove_reply_keyboard(),
    )
    await message.reply("Выберите действие:", reply_markup=get_main_menu_keyboard(message.from_user.id))


# --- Старый сценарий (5 шагов: ФИО, логин, почта, подразделение, телефон) — оставлен для совместимости,
#     но не вызывается из start_registration (старт переведён на AD). Можно удалить, если не нужен.


@router.message(RegistrationStates.WAITING_FOR_FULL_NAME, F.text)
async def process_full_name(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    ok, err = validate_full_name(text)
    if not ok:
        await message.reply(f"❗ {err}\n\nПопробуйте снова или нажмите Отмена.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(full_name=text)
    await state.set_state(RegistrationStates.WAITING_FOR_LOGIN)
    await message.reply(
        "✅ ФИО сохранено.\n\n"
        "Шаг 2/5: Введите <b>рабочий логин</b> (например: i.ivanov):",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(RegistrationStates.WAITING_FOR_LOGIN, F.text)
async def process_login(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    ok, err = validate_work_login(text)
    if not ok:
        await message.reply(f"❗ {err}\n\nПопробуйте снова или нажмите Отмена.", reply_markup=get_cancel_keyboard())
        return
    login_lower = text.lower()
    taken, taken_msg = check_login_or_email_taken(login_lower, "", exclude_user_id=None)
    if taken:
        await message.reply(
            "❌ Пользователь с таким рабочим логином уже зарегистрирован. Обратитесь на первую линию поддержки.",
            reply_markup=get_cancel_keyboard(),
        )
        return
    await state.update_data(login=login_lower)
    await state.set_state(RegistrationStates.WAITING_FOR_EMAIL)
    await message.reply(
        "✅ Логин сохранён.\n\n"
        "Шаг 3/5: Введите <b>корпоративную почту</b> (@petrovich.ru или @petrovich.tech):",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(RegistrationStates.WAITING_FOR_EMAIL, F.text)
async def process_email(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    ok, err = validate_corporate_email(text)
    if not ok:
        await message.reply(f"❗ {err}\n\nПопробуйте снова или нажмите Отмена.", reply_markup=get_cancel_keyboard())
        return
    email_lower = text.lower()
    data = await state.get_data()
    login = data.get("login", "")
    taken, _ = check_login_or_email_taken(login, email_lower, exclude_user_id=None)
    if taken:
        await message.reply(
            "❌ Пользователь с такой корпоративной почтой уже зарегистрирован. Обратитесь на первую линию поддержки.",
            reply_markup=get_cancel_keyboard(),
        )
        return
    await state.update_data(email=email_lower)
    await state.set_state(RegistrationStates.WAITING_FOR_DEPARTMENT)
    from core.jira_departments import get_departments_async
    departments = await get_departments_async()
    await message.reply(
        "✅ Почта сохранена.\n\n"
        "Шаг 4/5: Выберите ваше <b>подразделение</b> (Department):",
        parse_mode="HTML",
        reply_markup=get_department_keyboard(departments=departments),
    )


@router.callback_query(RegistrationStates.WAITING_FOR_DEPARTMENT, F.data.startswith("department_page_"))
async def process_department_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int(callback.data.replace("department_page_", ""))
    except ValueError:
        await callback.answer()
        return
    from core.jira_departments import get_departments_async
    departments = await get_departments_async()
    await callback.message.edit_reply_markup(reply_markup=get_department_keyboard(departments=departments, page=page))
    await callback.answer()


@router.callback_query(RegistrationStates.WAITING_FOR_DEPARTMENT, F.data.startswith("department_"))
async def process_department_select(callback: CallbackQuery, state: FSMContext):
    from core.jira_departments import get_departments_async
    departments = await get_departments_async()
    raw = callback.data.replace("department_", "")
    if raw.isdigit():
        idx = int(raw)
        if 0 <= idx < len(departments):
            selected = departments[idx]
            await state.update_data(department=selected)
            await state.set_state(RegistrationStates.WAITING_FOR_PHONE)
            await callback.message.edit_text(
                "✅ Подразделение сохранено.",
                parse_mode="HTML",
                reply_markup=get_cancel_keyboard(),
            )
            await callback.message.answer(
                "Шаг 5/5: Поделитесь номером телефона — нажмите кнопку ниже (так мы получим ваш настоящий номер):",
                reply_markup=get_contact_request_keyboard(),
            )
    await callback.answer()


@router.message(RegistrationStates.WAITING_FOR_PHONE, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    """Принимаем только контакт (поделиться номером) — так получаем настоящий номер телефона."""
    contact = message.contact
    if not contact or contact.user_id != message.from_user.id:
        await message.reply(
            "❌ Пожалуйста, поделитесь именно своим контактом (кнопка «Поделиться контактом»).",
            reply_markup=get_contact_request_keyboard(),
        )
        return
    raw_phone = (contact.phone_number or "").strip()
    if not raw_phone:
        await message.reply(
            "❌ Не удалось получить номер из контакта. Попробуйте ещё раз.",
            reply_markup=get_contact_request_keyboard(),
        )
        return
    ok, err = validate_phone(raw_phone)
    if not ok:
        await message.reply(
            f"❗ {err}\n\nПоделитесь контактом снова или нажмите Отмена.",
            reply_markup=get_contact_request_keyboard(),
        )
        return
    phone_norm = normalize_phone_display(raw_phone)
    data = await state.get_data()
    full_name = data.get("full_name")
    login = data.get("login")
    email = data.get("email")
    department = data.get("department", "").strip()
    if not all([full_name, login, email]):
        await message.reply("❌ Ошибка: данные регистрации потеряны. Начните с /start.", reply_markup=remove_reply_keyboard())
        await state.clear()
        return

    success, msg = await register_user(
        user_id=message.from_user.id,
        full_name=full_name,
        login=login,
        email=email,
        phone=phone_norm,
        department=department or None,
    )
    await state.clear()
    if success:
        lines = [f"• ФИО: {full_name}", f"• Логин: {login}", f"• Почта: {email}"]
        if department:
            lines.append(f"• Подразделение: {department}")
        lines.append(f"• Телефон: {phone_norm}")
        await message.reply(
            "✅ <b>Регистрация завершена</b>\n\n"
            + "\n".join(lines)
            + "\n\nТеперь вам доступны кнопки «Поменять пароль» и «Поменять учётные данные».",
            parse_mode="HTML",
            reply_markup=remove_reply_keyboard(),
        )
        await message.reply("Выберите действие:", reply_markup=get_main_menu_keyboard(message.from_user.id))
    else:
        await message.reply(f"❌ {msg}", reply_markup=remove_reply_keyboard())
        await message.reply("Начните заново:", reply_markup=get_start_keyboard(message.from_user.id))
