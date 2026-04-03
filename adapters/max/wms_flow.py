"""
Пошаговое создание заявки WMS в MAX (аналог handlers/create_ticket для Telegram).
Меню из 4 кнопок (проблема / настройки / пользователь PSIwms / назад), затем сценарий по типу.
"""
import logging
from typing import Optional

from core.wms_constants import WMS_PROCESSES, WMS_SERVICE_TYPES
from core.support import ticket_wizard
from adapters.max._utils import collect_attachments

from user_storage import (
    is_user_registered,
    get_user_profile,
    save_user_profile,
    resolve_channel_user_id,
)

from adapters.max._wizard_flow import WizardFlowStore

logger = logging.getLogger(__name__)
CHANNEL_ID = "max"

_store = WizardFlowStore()

ITEMS_PER_PAGE = 8
BACK_BTN = [{"id": "back_to_main", "label": "🔙 В главное меню"}]
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

WMS_SUBTYPE_BUTTONS = [
    {"id": "wms_type_issue", "label": "🚨 Проблема в работе WMS"},
    {"id": "wms_type_settings", "label": "⚙️ Изменение настроек системы WMS"},
    {"id": "wms_type_psi_user", "label": "👤 Создать/изменить/удалить пользователя PSIwms"},
    {"id": "wms_type_back", "label": "⬅️ Назад"},
]


def _buttons_wms_departments(departments: list, page: int = 0) -> list:
    if not departments:
        return CANCEL_BTN
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    chunk = departments[start:end]
    buttons = [{"id": f"wms_dept_{start + i}", "label": name} for i, name in enumerate(chunk)]
    if page > 0:
        buttons.append({"id": f"wms_dept_page_{page - 1}", "label": "◀️ Назад"})
    if end < len(departments):
        buttons.append({"id": f"wms_dept_page_{page + 1}", "label": "Вперёд ▶️"})
    buttons.append({"id": "cancel", "label": "❌ Отмена"})
    return buttons


def _buttons_wms_process() -> list:
    return [
        {"id": f"wms_process_{key}", "label": name}
        for key, name in WMS_PROCESSES.items()
    ] + [{"id": "cancel", "label": "❌ Отмена"}]


def _buttons_wms_service_type() -> list:
    return [
        {"id": key, "label": f"{'🗺️' if key == 'wms_service_topology' else '⚙️'} {name}"}
        for key, name in WMS_SERVICE_TYPES.items()
    ] + [{"id": "wms_show_subtype", "label": "⬅️ Назад"}]


async def start_wms(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _store.clear(user_id)
    _store.create(user_id, ticket_type_id="wms", step="subtype")
    return {
        "text": "📦 <b>WMS</b>\n\nГена на связи! Выберите тип заявки:",
        "parse_mode": "HTML",
        "buttons": WMS_SUBTYPE_BUTTONS,
    }


async def handle_wms_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    if callback_id == "wms_type_back" and session.step == "subtype":
        _store.clear(user_id)
        from adapters.max.handlers import _tp_programs_menu
        return _tp_programs_menu()
    if callback_id == "wms_show_subtype":
        _store.create(user_id, ticket_type_id="wms", step="subtype")
        return {
            "text": "📦 <b>WMS</b>\n\nГена на связи! Выберите тип заявки:",
            "parse_mode": "HTML",
            "buttons": WMS_SUBTYPE_BUTTONS,
        }

    # --- wms_issue: старт ---
    if callback_id == "wms_type_issue" and session.step == "subtype":
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        dept_wms = (profile.get("department_wms") or "").strip()
        if dept_wms:
            _store.create(user_id, ticket_type_id="wms_issue", step="process", data={"ticket_type_id": "wms_issue"})
            return {
                "text": ticket_wizard.wms_issue_start_screen(has_department_wms=True, departments=None).text,
                "parse_mode": "HTML",
                "buttons": _buttons_wms_process(),
            }
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async()
        _store.create(user_id, ticket_type_id="wms_issue", step="department",
                      data={"ticket_type_id": "wms_issue", "departments": depts or [], "dept_page": 0})
        if not depts:
            return {"text": "Список подразделений WMS недоступен. Попробуйте позже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        return {
            "text": ticket_wizard.wms_issue_start_screen(has_department_wms=False, departments=depts).text,
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, 0),
        }

    # --- wms_settings: старт ---
    if callback_id == "wms_type_settings" and session.step == "subtype":
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        dept_wms = (profile.get("department_wms") or "").strip()
        if dept_wms:
            _store.create(user_id, ticket_type_id="wms_settings", step="settings_service_type",
                          data={"ticket_type_id": "wms_settings", "department": dept_wms})
            return {
                "text": ticket_wizard.wms_settings_service_type_screen().text,
                "parse_mode": "HTML",
                "buttons": _buttons_wms_service_type(),
            }
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async()
        _store.create(user_id, ticket_type_id="wms_settings", step="settings_department",
                      data={"ticket_type_id": "wms_settings", "departments": depts or [], "dept_page": 0})
        if not depts:
            return {"text": "Список подразделений WMS недоступен. Попробуйте позже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        return {
            "text": ticket_wizard.wms_settings_department_screen(depts).text,
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, 0),
        }

    # --- wms_psi_user: старт ---
    if callback_id == "wms_type_psi_user" and session.step == "subtype":
        _store.create(user_id, ticket_type_id="wms_psi_user", step="psi_title", data={"ticket_type_id": "wms_psi_user"})
        return {
            "text": ticket_wizard.psi_title_screen().text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    # --- wms_issue: выбор процесса ---
    if session.step == "process" and callback_id.startswith("wms_process_"):
        key = callback_id.replace("wms_process_", "", 1)
        process_name = WMS_PROCESSES.get(key)
        if not process_name:
            return {"text": "Неверный выбор. Выберите процесс:", "parse_mode": "HTML", "buttons": _buttons_wms_process()}
        _store.set_step(user_id, "summary", data={"process": process_name})
        return {
            "text": ticket_wizard.wms_issue_summary_screen().text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    # --- wms_issue: пропуск описания ---
    if session.step == "description" and callback_id == "wms_skip_description":
        _store.set_step(user_id, "attachments", data={"description": "", "wms_attachment_tokens": []})
        return {
            "text": ticket_wizard.wms_issue_description_screen().text,
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    # --- wms_issue: завершение (вложения) ---
    if session.step == "attachments" and callback_id == "wms_finish_ticket":
        session = _store.get(user_id)
        data = session.data
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        department = (profile.get("department_wms") or profile.get("department") or "").strip()
        if not department:
            return {"text": "Укажите подразделение в профиле.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        form_data = dict(data)
        form_data["department"] = department
        form_data["summary"] = (data.get("summary") or "").strip() or "Заявка по настройке WMS"
        attachment_tokens = data.get("wms_attachment_tokens") or []
        ticket_type_id = data.get("ticket_type_id") or "wms_issue"
        _store.clear(user_id)
        return {"create_ticket": {"ticket_type_id": ticket_type_id, "form_data": form_data, "attachment_tokens": list(attachment_tokens)}}

    # --- wms_settings: выбор типа услуги ---
    if session.step == "settings_service_type" and callback_id in ("wms_service_topology", "wms_service_other"):
        service_type = WMS_SERVICE_TYPES.get(callback_id)
        if not service_type:
            return {"text": "Неверный выбор. Выберите тип услуги:", "parse_mode": "HTML", "buttons": _buttons_wms_service_type()}
        _store.set_step(user_id, "settings_description", data={"service_type": service_type, "wms_settings_attachment_tokens": []})
        return {
            "text": ticket_wizard.wms_settings_description_screen().text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    # --- wms_settings: завершение (вложения обязательны) ---
    if session.step == "settings_attachments" and callback_id == "finish_wms_settings":
        session = _store.get(user_id)
        data = session.data
        attachment_tokens = data.get("wms_settings_attachment_tokens") or []
        if not attachment_tokens:
            return {
                "text": ticket_wizard.wms_settings_attachments_screen(added_count=0).text,
                "parse_mode": "HTML",
                "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
            }
        department = (data.get("department") or "").strip()
        if not department:
            return {"text": "Укажите подразделение.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        form_data = {
            "department": department,
            "service_type": (data.get("service_type") or "").strip(),
            "description": (data.get("description") or "").strip() or "-",
        }
        _store.clear(user_id)
        return {"create_ticket": {"ticket_type_id": "wms_settings", "form_data": form_data, "attachment_tokens": list(attachment_tokens)}}

    # --- psi_user: завершение (вложения опционально) ---
    if session.step == "psi_attachments" and callback_id in ("finish_psi_user", "skip_psi_attachment"):
        session = _store.get(user_id)
        data = session.data
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        department = (profile.get("department_wms") or profile.get("department") or data.get("department") or "").strip()
        if not department:
            return {"text": "Укажите подразделение.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        form_data = {
            "summary": (data.get("summary") or "").strip(),
            "full_name": (data.get("full_name") or "").strip(),
            "department": department,
            "comment": (data.get("comment") or "").strip(),
        }
        if not form_data["full_name"]:
            return {"text": "Ошибка: не указаны ФИО и должность.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        attachment_tokens = (data.get("psi_attachment_tokens") or []) if callback_id == "finish_psi_user" else []
        _store.clear(user_id)
        return {"create_ticket": {"ticket_type_id": "wms_psi_user", "form_data": form_data, "attachment_tokens": list(attachment_tokens)}}

    # --- пагинация подразделений ---
    if callback_id.startswith("wms_dept_page_"):
        try:
            page = int(callback_id.replace("wms_dept_page_", ""))
        except ValueError:
            return None
        session = _store.get(user_id)
        depts = session.data.get("departments") or []
        safe_page = max(0, min(page, (len(depts) - 1) // ITEMS_PER_PAGE if depts else 0))
        _store.update_data(user_id, dept_page=safe_page)
        if session.step == "settings_department":
            title = "⚙️ <b>Изменение настроек системы WMS</b>"
        elif session.step == "psi_department":
            title = "👤 <b>Создать/изменить/удалить пользователя PSIwms</b>"
        else:
            title = "🚨 <b>Проблема в работе WMS</b>"
        return {
            "text": f"{title}\n\nВыберите ваше подразделение:",
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, safe_page),
        }

    # --- выбор подразделения ---
    if callback_id.startswith("wms_dept_"):
        try:
            idx = int(callback_id.replace("wms_dept_", ""))
        except ValueError:
            return None
        session = _store.get(user_id)
        depts = session.data.get("departments") or []
        if idx < 0 or idx >= len(depts):
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        value = depts[idx]
        primary = resolve_channel_user_id(CHANNEL_ID, user_id)
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        profile["department_wms"] = value
        save_user_profile(primary, profile)
        if session.step == "settings_department":
            _store.set_step(user_id, "settings_service_type", data={"department": value})
            return {
                "text": ticket_wizard.wms_settings_service_type_screen().text,
                "parse_mode": "HTML",
                "buttons": _buttons_wms_service_type(),
            }
        if session.step == "psi_department":
            _store.set_step(user_id, "psi_comment", data={"department": value})
            return {
                "text": ticket_wizard.psi_comment_screen().text,
                "parse_mode": "HTML",
                "buttons": CANCEL_BTN,
            }
        _store.set_step(user_id, "process")
        return {
            "text": ticket_wizard.wms_issue_process_screen().text,
            "parse_mode": "HTML",
            "buttons": _buttons_wms_process(),
        }
    return None


async def handle_wms_message(user_id: int, text: str, attachment_list: Optional[list] = None) -> Optional[dict]:
    session = _store.get(user_id)

    # --- вложения wms_issue ---
    if session and session.step == "attachments" and attachment_list:
        tokens = collect_attachments(session.data.get("wms_attachment_tokens") or [], attachment_list)
        _store.update_data(user_id, wms_attachment_tokens=tokens)
        n = len(tokens)
        return {
            "text": f"📎 Добавлено вложений: {n} из 10. Приложите ещё или нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    # --- вложения wms_settings (обязательны) ---
    if session and session.step == "settings_attachments" and attachment_list:
        tokens = collect_attachments(session.data.get("wms_settings_attachment_tokens") or [], attachment_list)
        _store.update_data(user_id, wms_settings_attachment_tokens=tokens)
        n = len(tokens)
        return {
            "text": ticket_wizard.wms_settings_attachments_screen(added_count=n).text,
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    # --- вложения psi_user (опционально) ---
    if session and session.step == "psi_attachments" and attachment_list:
        tokens = collect_attachments(session.data.get("psi_attachment_tokens") or [], attachment_list)
        _store.update_data(user_id, psi_attachment_tokens=tokens)
        n = len(tokens)
        return {
            "text": ticket_wizard.psi_attachments_screen(added_count=n).text,
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_psi_user", "label": "✅ Завершить создание задачи"}, {"id": "skip_psi_attachment", "label": "⏭ Пропустить вложения"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    text = (text or "").strip()
    if not text:
        return None
    if session and text.lower() in ("/cancel", "отмена", "cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    if not session:
        return None

    session = _store.get(user_id)

    if session.step == "summary":
        _store.set_step(user_id, "description", data={"summary": text})
        return {
            "text": ticket_wizard.wms_issue_description_screen().text,
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_skip_description", "label": "⏭ Пропустить"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if session.step == "description":
        _store.set_step(user_id, "attachments", data={"description": text, "wms_attachment_tokens": []})
        return {
            "text": "📎 Приложите фото, видео или документы (до 10 файлов) или нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if session.step == "process":
        return {"text": "Выберите процесс кнопкой ниже:", "parse_mode": "HTML", "buttons": _buttons_wms_process()}
    if session.step == "attachments":
        return {
            "text": "Нажмите «✅ Завершить создание задачи» для создания заявки.",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    # --- wms_settings: описание ---
    if session.step == "settings_description":
        _store.set_step(user_id, "settings_attachments",
                        data={"description": text if text != "—" else "", "wms_settings_attachment_tokens": []})
        return {
            "text": ticket_wizard.wms_settings_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if session.step == "settings_attachments":
        return {
            "text": "Загрузите вложения (обязательно) и нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }

    # --- psi_user: шаги ---
    if session.step == "psi_title":
        if len(text) < 3:
            return {"text": "Тема должна быть не менее 3 символов. Введите тему задачи:", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        _store.set_step(user_id, "psi_full_name", data={"summary": text})
        return {
            "text": ticket_wizard.psi_full_name_screen().text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    if session.step == "psi_full_name":
        _store.update_data(user_id, full_name=text)
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        dept_wms = (profile.get("department_wms") or "").strip()
        if dept_wms:
            _store.set_step(user_id, "psi_comment", data={"department": dept_wms})
            return {"text": ticket_wizard.psi_comment_screen().text, "parse_mode": "HTML", "buttons": CANCEL_BTN}
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async()
        _store.set_step(user_id, "psi_department", data={"departments": depts or [], "dept_page": 0})
        if not depts:
            return {"text": "Список подразделений недоступен. Введите подразделение текстом или нажмите Отмена.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        return {
            "text": ticket_wizard.psi_department_screen(depts).text,
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, 0),
        }
    if session.step == "psi_comment":
        _store.set_step(user_id, "psi_attachments",
                        data={"comment": text if text != "—" else "", "psi_attachment_tokens": []})
        return {
            "text": ticket_wizard.psi_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_psi_user", "label": "✅ Завершить создание задачи"}, {"id": "skip_psi_attachment", "label": "⏭ Пропустить вложения"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if session.step == "psi_attachments":
        return {
            "text": "Нажмите «✅ Завершить создание задачи» или «⏭ Пропустить вложения».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_psi_user", "label": "✅ Завершить создание задачи"}, {"id": "skip_psi_attachment", "label": "⏭ Пропустить вложения"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    return None


def is_in_wms_flow(user_id: int) -> bool:
    return _store.has(user_id)
