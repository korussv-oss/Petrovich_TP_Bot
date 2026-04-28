"""
Пошаговое создание заявки Lupa (Сайт / поиск petrovich.ru) в MAX.
Кнопки и логика — как в the_bot_lupa и TG: сервис → тип запроса → город → комментарий (подразделение из профиля).
"""
import logging
from typing import Optional

from core.support import ticket_wizard
from adapters.max._utils import collect_attachments
from user_storage import (
    is_user_registered,
    get_user_profile,
    save_user_profile,
    resolve_channel_user_id,
    check_employee_id_taken,
)

from adapters.max._wizard_flow import WizardFlowStore

logger = logging.getLogger(__name__)
CHANNEL_ID = "max"

_store = WizardFlowStore()

CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]
LUPA_ATTACHMENTS_BTNS = [
    {"id": "lupa_finish_ticket", "label": "✅ Завершить создание задачи"},
    {"id": "lupa_skip_attachments", "label": "⏭ Пропустить вложения"},
    {"id": "cancel", "label": "❌ Отмена"},
]

LUPA_SERVICE_VALUES = {"lupa_service_app": "Приложение", "lupa_service_site": "Сайт (petrovich.ru)"}
LUPA_REQUEST_TYPE_VALUES = {
    "lupa_request_search_issue": "проблемы с поиском",
    "lupa_request_search_question": "вопросы по работе поиска",
    "lupa_request_discount": "валидация сленга",
}

LUPA_SERVICE_BUTTONS = [
    {"id": "lupa_service_app", "label": "📱 Приложение"},
    {"id": "lupa_service_site", "label": "🌐 Сайт (petrovich.ru)"},
]
LUPA_REQUEST_TYPE_BUTTONS = [
    {"id": "lupa_request_search_issue", "label": "🔍 Проблемы с поиском"},
    {"id": "lupa_request_search_question", "label": "❓ Вопросы по работе поиска"},
    {"id": "lupa_request_discount", "label": "✅ Валидация сленга"},
]

EMPLOYEE_ID_HINT = (
    "💡 Табельный номер можно найти в расчётном листке. Он нужен для идентификации в заявке."
)

ITEMS_PER_PAGE = 8


def _buttons_lupa_departments(departments: list, page: int = 0) -> list:
    if not departments:
        return CANCEL_BTN
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    chunk = departments[start:end]
    buttons = [{"id": f"lupa_dept_{start + i}", "label": name} for i, name in enumerate(chunk)]
    if page > 0:
        buttons.append({"id": f"lupa_dept_page_{page - 1}", "label": "◀️ Назад"})
    if end < len(departments):
        buttons.append({"id": f"lupa_dept_page_{page + 1}", "label": "Вперёд ▶️"})
    buttons.append({"id": "cancel", "label": "❌ Отмена"})
    return buttons


def _city_buttons() -> list:
    from config import CONFIG
    cities = CONFIG.get("JIRA_LUPA", {}).get("CITIES", [])[:4]
    buttons = []
    for i in range(0, min(4, len(cities)), 2):
        for j in range(2):
            if i + j < len(cities):
                city = cities[i + j]
                buttons.append({"id": f"lupa_city_{city.replace(' ', '_')}", "label": city})
    buttons.append({"id": "lupa_city_manual", "label": "✏️ Ввести вручную"})
    return buttons


def is_in_lupa_flow(user_id: int) -> bool:
    return _store.has(user_id)


def _lupa_service_screen() -> dict:
    return {
        "text": ticket_wizard.lupa_service_screen().text,
        "parse_mode": "HTML",
        "buttons": LUPA_SERVICE_BUTTONS + CANCEL_BTN,
    }


async def start_lupa(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _store.clear(user_id)
    profile = get_user_profile(user_id, CHANNEL_ID) or {}
    employee_id = (profile.get("employee_id") or "").strip()
    if not employee_id:
        _store.create(user_id, ticket_type_id="lupa_search", step="employee_id", data={"ticket_type_id": "lupa_search"})
        return {
            "text": (
                "🌐 <b>Сайт (Lupa)</b>\n\n"
                "Укажите ваш <b>табельный номер</b> (например: 0000000311):\n\n"
                f"{EMPLOYEE_ID_HINT}"
            ),
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    department = (profile.get("department") or "").strip()
    if not department:
        from core.jira_departments import get_departments_async
        depts = await get_departments_async()
        if not depts:
            return {
                "text": "Список подразделений недоступен. Попробуйте позже или укажите подразделение в Личном кабинете.",
                "parse_mode": "HTML",
                "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
            }
        _store.create(user_id, ticket_type_id="lupa_search", step="department",
                      data={"ticket_type_id": "lupa_search", "departments": depts, "dept_page": 0})
        return {
            "text": ticket_wizard.lupa_department_screen(depts).text,
            "parse_mode": "HTML",
            "buttons": _buttons_lupa_departments(depts, 0),
        }
    _store.create(user_id, ticket_type_id="lupa_search", step="service", data={"ticket_type_id": "lupa_search"})
    return _lupa_service_screen()


def handle_lupa_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None

    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    # Шаг: выбор подразделения
    if session.step == "department":
        if callback_id.startswith("lupa_dept_page_"):
            try:
                page = int(callback_id.replace("lupa_dept_page_", ""))
            except ValueError:
                return None
            depts = session.data.get("departments") or []
            _store.update_data(user_id, dept_page=page)
            return {
                "text": ticket_wizard.lupa_department_screen(depts).text,
                "parse_mode": "HTML",
                "buttons": _buttons_lupa_departments(depts, page),
            }
        if callback_id.startswith("lupa_dept_") and not callback_id.startswith("lupa_dept_page_"):
            try:
                idx = int(callback_id.replace("lupa_dept_", ""))
            except ValueError:
                return None
            depts = session.data.get("departments") or []
            if idx < 0 or idx >= len(depts):
                return None
            value = depts[idx]
            primary = resolve_channel_user_id(CHANNEL_ID, user_id)
            profile = get_user_profile(user_id, CHANNEL_ID) or {}
            profile["department"] = value
            save_user_profile(primary, profile)
            _store.set_step(user_id, "service")
            return _lupa_service_screen()

    # Шаг 1: выбор сервиса
    if session.step == "service" and callback_id in LUPA_SERVICE_VALUES:
        service = LUPA_SERVICE_VALUES[callback_id]
        _store.set_step(user_id, "request_type", data={"problematic_service": service})
        return {
            "text": ticket_wizard.lupa_request_type_screen(service=service).text,
            "parse_mode": "HTML",
            "buttons": LUPA_REQUEST_TYPE_BUTTONS + CANCEL_BTN,
        }

    # Шаг 2: выбор типа запроса
    if session.step == "request_type" and callback_id in LUPA_REQUEST_TYPE_VALUES:
        request_type = LUPA_REQUEST_TYPE_VALUES[callback_id]
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        subdivision = (profile.get("department") or "").strip()
        _store.set_step(user_id, "city", data={"request_type": request_type, "subdivision": subdivision})
        session = _store.get(user_id)
        return {
            "text": ticket_wizard.lupa_city_screen(request_type=request_type, subdivision=subdivision).text,
            "parse_mode": "HTML",
            "buttons": _city_buttons() + CANCEL_BTN,
        }

    # Шаг 3: выбор города
    if session.step == "city" and callback_id.startswith("lupa_city_"):
        if callback_id == "lupa_city_manual":
            _store.set_step(user_id, "city_manual")
            return {
                "text": ticket_wizard.lupa_city_manual_screen().text,
                "parse_mode": "HTML",
                "buttons": CANCEL_BTN,
            }
        city = callback_id.replace("lupa_city_", "", 1).replace("_", " ")
        _store.set_step(user_id, "description", data={"city": city})
        session = _store.get(user_id)
        return {
            "text": ticket_wizard.lupa_description_screen(city=city).text,
            "parse_mode": "HTML",
            "buttons": [{"id": "lupa_skip_comment", "label": "⏭ Пропустить комментарий"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    # Шаг 4: пропуск комментария → создание
    if session.step == "description" and callback_id == "lupa_skip_comment":
        data = session.data
        _store.set_step(
            user_id,
            "attachments",
            data={
                "description": "",
                "lupa_attachment_tokens": list(data.get("lupa_attachment_tokens") or []),
            },
        )
        return {
            "text": (
                "📎 Приложите фото, видео или документы (до 10 файлов) "
                "или нажмите «✅ Завершить создание задачи»."
            ),
            "parse_mode": "HTML",
            "buttons": LUPA_ATTACHMENTS_BTNS,
        }

    # Шаг 5: завершение (вложения)
    if session.step == "attachments" and callback_id in ("lupa_finish_ticket", "lupa_skip_attachments"):
        data = session.data
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        subdivision = (data.get("subdivision") or profile.get("department") or "").strip()
        form_data = {
            "description": (data.get("description") or "").strip(),
            "problematic_service": data.get("problematic_service", ""),
            "request_type": data.get("request_type", ""),
            "subdivision": subdivision,
            "city": data.get("city", ""),
        }
        attachment_tokens = [] if callback_id == "lupa_skip_attachments" else (data.get("lupa_attachment_tokens") or [])
        _store.clear(user_id)
        return {
            "create_ticket": {
                "ticket_type_id": "lupa_search",
                "form_data": form_data,
                "attachment_tokens": list(attachment_tokens),
            }
        }

    return None


async def handle_lupa_message(user_id: int, text: str, attachment_list: Optional[list] = None) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None

    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    text_val = (text or "").strip()

    if session.step == "employee_id":
        from validators import validate_employee_id
        ok, err = validate_employee_id(text_val)
        if not ok:
            return {"text": f"❗ {err}\n\n{EMPLOYEE_ID_HINT}", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        primary = resolve_channel_user_id(CHANNEL_ID, user_id)
        taken, _ = check_employee_id_taken(text_val, exclude_user_id=primary)
        if taken:
            return {
                "text": "❗ Этот табельный номер уже привязан к другому пользователю. Введите другой номер или нажмите Отмена.",
                "parse_mode": "HTML",
                "buttons": CANCEL_BTN,
            }
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        profile["employee_id"] = text_val
        save_user_profile(primary, profile)
        department = (profile.get("department") or "").strip()
        if not department:
            from core.jira_departments import get_departments_async
            depts = await get_departments_async()
            if not depts:
                _store.set_step(user_id, "service")
                return {"text": "Список подразделений недоступен. Выберите сервис.", "parse_mode": "HTML", "buttons": LUPA_SERVICE_BUTTONS + CANCEL_BTN}
            _store.set_step(user_id, "department", data={"departments": depts, "dept_page": 0})
            return {
                "text": ticket_wizard.lupa_department_screen(depts).text,
                "parse_mode": "HTML",
                "buttons": _buttons_lupa_departments(depts, 0),
            }
        _store.set_step(user_id, "service")
        return _lupa_service_screen()

    if session.step == "city_manual":
        if not text_val:
            return {"text": "Введите название города или нажмите Отмена.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        _store.set_step(user_id, "description", data={"city": text_val})
        return {
            "text": ticket_wizard.lupa_description_screen(city=text_val).text,
            "parse_mode": "HTML",
            "buttons": [{"id": "lupa_skip_comment", "label": "⏭ Пропустить комментарий"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    if session.step == "attachments":
        if attachment_list:
            tokens = collect_attachments(session.data.get("lupa_attachment_tokens") or [], attachment_list)
            _store.update_data(user_id, lupa_attachment_tokens=tokens)
            n = len(tokens)
            logger.info("MAX lupa attachments: collected=%s (user_id=%s)", n, user_id)
            return {
                "text": (
                    f"📎 Добавлено вложений: {n} из 10.\n\n"
                    "Приложите ещё или нажмите «✅ Завершить создание задачи» / «⏭ Пропустить вложения»."
                ),
                "parse_mode": "HTML",
                "buttons": LUPA_ATTACHMENTS_BTNS,
            }
        # Игнорируем текст на шаге вложений: пользователь должен нажать кнопку
        return {
            "text": "📎 Приложите файлы или нажмите «✅ Завершить создание задачи» / «⏭ Пропустить вложения».",
            "parse_mode": "HTML",
            "buttons": LUPA_ATTACHMENTS_BTNS,
        }

    if session.step == "description":
        # MAX: пользователь может прислать файлы отдельным сообщением на шаге комментария.
        if attachment_list:
            tokens = collect_attachments(session.data.get("lupa_attachment_tokens") or [], attachment_list)
            _store.update_data(user_id, lupa_attachment_tokens=tokens)
            n = len(tokens)
            logger.info("MAX lupa attachments: collected=%s (user_id=%s)", n, user_id)
            return {
                "text": (
                    f"📎 Добавлено вложений: {n} из 10.\n\n"
                    "Отправьте комментарий текстом или нажмите «⏭ Пропустить комментарий»."
                ),
                "parse_mode": "HTML",
                "buttons": [
                    {"id": "lupa_skip_comment", "label": "⏭ Пропустить комментарий"},
                    {"id": "cancel", "label": "❌ Отмена"},
                ],
            }
        # Текст комментария введён → переходим на шаг вложений (как в WMS).
        data = session.data
        _store.set_step(
            user_id,
            "attachments",
            data={
                "description": text_val,
                "lupa_attachment_tokens": list(data.get("lupa_attachment_tokens") or []),
            },
        )
        return {
            "text": (
                "📎 Приложите фото, видео или документы (до 10 файлов) "
                "или нажмите «✅ Завершить создание задачи»."
            ),
            "parse_mode": "HTML",
            "buttons": LUPA_ATTACHMENTS_BTNS,
        }

    return None
