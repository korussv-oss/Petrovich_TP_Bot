"""
Пошаговое создание заявки WMS в MAX (аналог handlers/create_ticket для Telegram).
Меню из 4 кнопок (проблема / настройки / пользователь PSIwms / назад), затем сценарий по типу.
"""
import logging
from typing import Optional

from core.wms_constants import WMS_PROCESSES, WMS_SERVICE_TYPES

from user_storage import (
    is_user_registered,
    get_user_profile,
    save_user_profile,
    resolve_channel_user_id,
)

logger = logging.getLogger(__name__)
CHANNEL_ID = "max"

# user_id (MAX) -> { step, data, departments, dept_page, wms_subtype? }
_flow: dict[int, dict] = {}

ITEMS_PER_PAGE = 8
BACK_BTN = [{"id": "back_to_main", "label": "🔙 В главное меню"}]
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

# Кнопки выбора типа WMS (как в the_bot_wms)
WMS_SUBTYPE_BUTTONS = [
    {"id": "wms_type_issue", "label": "🚨 Проблема в работе WMS"},
    {"id": "wms_type_settings", "label": "⚙️ Изменение настроек системы WMS"},
    {"id": "wms_type_psi_user", "label": "👤 Создать/изменить/удалить пользователя PSIwms"},
    {"id": "wms_type_back", "label": "⬅️ Назад"},
]

def _buttons_wms_departments(departments: list, page: int = 0) -> list:
    """Кнопки выбора подразделения WMS (одна в ряд) + навигация + Отмена."""
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
    """Кнопки выбора процесса WMS (как в the_bot_wms) — значения для Jira customfield_13803."""
    return [
        {"id": f"wms_process_{key}", "label": name}
        for key, name in WMS_PROCESSES.items()
    ] + [{"id": "cancel", "label": "❌ Отмена"}]


def _buttons_wms_service_type() -> list:
    """Тип услуги «Изменение настроек системы WMS» (как the_bot_wms)."""
    return [
        {"id": key, "label": f"{'🗺️' if key == 'wms_service_topology' else '⚙️'} {name}"}
        for key, name in WMS_SERVICE_TYPES.items()
    ] + [{"id": "wms_show_subtype", "label": "⬅️ Назад"}]


async def start_wms(user_id: int) -> Optional[dict]:
    """
    Начало сценария WMS: меню из 4 кнопок
    (Проблема в работе WMS / Изменение настроек / Пользователь PSIwms / Назад).
    Возвращает None если пользователь не зарегистрирован.
    """
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow.pop(user_id, None)
    _flow[user_id] = {"step": "subtype", "data": {}}
    return {
        "text": "📦 <b>WMS</b>\n\nГена на связи! Выберите тип заявки:",
        "parse_mode": "HTML",
        "buttons": WMS_SUBTYPE_BUTTONS,
    }


async def handle_wms_callback(user_id: int, callback_id: str) -> Optional[dict]:
    """
    Обработка callback в сценарии WMS: subtype (4 кнопки), wms_type_*, wms_dept_*, cancel.
    Возвращает ответ или None (при cancel — вызывающий покажет главное меню).
    """
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    # Назад из меню типа WMS → раздел «Программы и сайт»
    if callback_id == "wms_type_back" and state.get("step") == "subtype":
        _flow.pop(user_id, None)
        from adapters.max.handlers import _tp_programs_menu
        return _tp_programs_menu()
    # Вернуться к выбору типа заявки (из любого шага WMS)
    if callback_id == "wms_show_subtype":
        _flow[user_id] = {"step": "subtype", "data": {}}
        return {
            "text": "📦 <b>WMS</b>\n\nГена на связи! Выберите тип заявки:",
            "parse_mode": "HTML",
            "buttons": WMS_SUBTYPE_BUTTONS,
        }
    # Проблема в работе WMS (как the_bot_wms): подразделение → процесс → тема → описание (пропустить) → завершить
    if callback_id == "wms_type_issue" and state.get("step") == "subtype":
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        dept_wms = (profile.get("department_wms") or "").strip()
        if dept_wms:
            _flow[user_id] = {"step": "process", "data": {"ticket_type_id": "wms_issue"}}
            return {
                "text": "🚨 <b>Проблема в работе WMS</b>\n\nВыберите <b>сбойный процесс</b>:",
                "parse_mode": "HTML",
                "buttons": _buttons_wms_process(),
            }
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async()
        _flow[user_id] = {"step": "department", "data": {"ticket_type_id": "wms_issue"}, "departments": depts or [], "dept_page": 0}
        if not depts:
            return {"text": "Список подразделений WMS недоступен. Попробуйте позже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        return {
            "text": "🚨 <b>Проблема в работе WMS</b>\n\nВыберите ваше подразделение:",
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, 0),
        }
    # Изменение настроек системы WMS: подразделение (если нет в профиле) → тип услуги → описание → вложения (обязательно) → завершить
    if callback_id == "wms_type_settings" and state.get("step") == "subtype":
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        dept_wms = (profile.get("department_wms") or "").strip()
        if dept_wms:
            _flow[user_id] = {"step": "settings_service_type", "data": {"ticket_type_id": "wms_settings", "department": dept_wms}}
            return {
                "text": "⚙️ <b>Изменение настроек системы WMS</b>\n\nВыберите тип услуги:",
                "parse_mode": "HTML",
                "buttons": _buttons_wms_service_type(),
            }
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async()
        _flow[user_id] = {"step": "settings_department", "data": {"ticket_type_id": "wms_settings"}, "departments": depts or [], "dept_page": 0}
        if not depts:
            return {"text": "Список подразделений WMS недоступен. Попробуйте позже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        return {
            "text": "⚙️ <b>Изменение настроек системы WMS</b>\n\nВыберите ваше подразделение:",
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, 0),
        }
    # Пользователь PSIwms: тема → ФИО+должность → подразделение (если нет) → комментарий → вложения (опционально) → завершить
    if callback_id == "wms_type_psi_user" and state.get("step") == "subtype":
        _flow[user_id] = {"step": "psi_title", "data": {"ticket_type_id": "wms_psi_user"}}
        return {
            "text": "👤 <b>Создать/изменить/удалить пользователя PSIwms</b>\n\nВведите тему задачи (не менее 3 символов):",
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    # Шаг 2: выбор процесса
    if state.get("step") == "process" and callback_id.startswith("wms_process_"):
        key = callback_id.replace("wms_process_", "", 1)
        process_name = WMS_PROCESSES.get(key)
        if not process_name:
            return {"text": "Неверный выбор. Выберите процесс:", "parse_mode": "HTML", "buttons": _buttons_wms_process()}
        data = state.get("data", {})
        data["process"] = process_name
        state["data"] = data
        state["step"] = "summary"
        return {
            "text": "🚨 <b>Проблема в работе WMS</b>\n\nВведите <b>тему</b> проблемы (кратко):",
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    # Пропуск описания (wms_issue)
    if state.get("step") == "description" and callback_id == "wms_skip_description":
        data = state.get("data", {})
        data["description"] = ""
        state["data"] = data
        state["step"] = "attachments"
        return {
            "text": "📎 Приложите фото, видео или документы (до 10 файлов) или нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    # Завершить создание заявки WMS (шаг вложений)
    if state.get("step") == "attachments" and callback_id == "wms_finish_ticket":
        data = state.get("data", {})
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        department = (profile.get("department_wms") or profile.get("department") or "").strip()
        if not department:
            return {"text": "Укажите подразделение в профиле.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["department"] = department
        data["summary"] = (data.get("summary") or "").strip() or "Заявка по настройке WMS"
        attachment_tokens = data.get("wms_attachment_tokens") or []
        ticket_type_id = data.get("ticket_type_id") or "wms_issue"
        _flow.pop(user_id, None)
        return {"create_ticket": {"ticket_type_id": ticket_type_id, "form_data": dict(data), "attachment_tokens": list(attachment_tokens)}}
    # Тип услуги «Настройки WMS»: Изменение топологии / Другие настройки
    if state.get("step") == "settings_service_type" and callback_id in ("wms_service_topology", "wms_service_other"):
        service_type = WMS_SERVICE_TYPES.get(callback_id)
        if not service_type:
            return {"text": "Неверный выбор. Выберите тип услуги:", "parse_mode": "HTML", "buttons": _buttons_wms_service_type()}
        data = state.get("data", {})
        data["service_type"] = service_type
        state["data"] = data
        state["step"] = "settings_description"
        return {
            "text": "⚙️ <b>Изменение настроек системы WMS</b>\n\n📝 Введите описание изменений (или «-» для пропуска):",
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    # Завершить «Настройки WMS» (вложения обязательны)
    if state.get("step") == "settings_attachments" and callback_id == "finish_wms_settings":
        data = state.get("data", {})
        attachment_tokens = data.get("wms_settings_attachment_tokens") or []
        if not attachment_tokens:
            return {
                "text": "Вложения обязательны. Загрузите хотя бы один файл.",
                "parse_mode": "HTML",
                "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
            }
        department = (data.get("department") or "").strip()
        if not department:
            return {"text": "Укажите подразделение.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        form_data = {"department": department, "service_type": (data.get("service_type") or "").strip(), "description": (data.get("description") or "").strip() or "-"}
        _flow.pop(user_id, None)
        return {"create_ticket": {"ticket_type_id": "wms_settings", "form_data": form_data, "attachment_tokens": list(attachment_tokens)}}
    # Пользователь PSIwms: завершить с вложениями или пропустить вложения
    if state.get("step") == "psi_attachments" and callback_id in ("finish_psi_user", "skip_psi_attachment"):
        data = state.get("data", {})
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
        _flow.pop(user_id, None)
        return {"create_ticket": {"ticket_type_id": "wms_psi_user", "form_data": form_data, "attachment_tokens": list(attachment_tokens)}}

    # Пагинация подразделений
    if callback_id.startswith("wms_dept_page_"):
        try:
            page = int(callback_id.replace("wms_dept_page_", ""))
        except ValueError:
            return None
        depts = state.get("departments") or []
        state["dept_page"] = max(0, min(page, (len(depts) - 1) // ITEMS_PER_PAGE))
        if state.get("step") == "settings_department":
            title = "⚙️ <b>Изменение настроек системы WMS</b>"
        elif state.get("step") == "psi_department":
            title = "👤 <b>Создать/изменить/удалить пользователя PSIwms</b>"
        else:
            title = "🚨 <b>Проблема в работе WMS</b>"
        return {
            "text": f"{title}\n\nВыберите ваше подразделение:",
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, state["dept_page"]),
        }
    # Выбор подразделения
    if callback_id.startswith("wms_dept_"):
        try:
            idx = int(callback_id.replace("wms_dept_", ""))
        except ValueError:
            return None
        depts = state.get("departments") or []
        if idx < 0 or idx >= len(depts):
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        value = depts[idx]
        primary = resolve_channel_user_id(CHANNEL_ID, user_id)
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        profile["department_wms"] = value
        save_user_profile(primary, profile)
        step = state.get("step")
        data = state.get("data", {})
        if step == "settings_department":
            data["department"] = value
            _flow[user_id] = {"step": "settings_service_type", "data": data}
            return {
                "text": "⚙️ <b>Изменение настроек системы WMS</b>\n\nВыберите тип услуги:",
                "parse_mode": "HTML",
                "buttons": _buttons_wms_service_type(),
            }
        if step == "psi_department":
            data["department"] = value
            _flow[user_id] = {"step": "psi_comment", "data": data}
            return {
                "text": "👤 <b>Создать/изменить/удалить пользователя PSIwms</b>\n\nЧто нужно сделать?",
                "parse_mode": "HTML",
                "buttons": CANCEL_BTN,
            }
        _flow[user_id] = {"step": "process", "data": data}
        return {
            "text": "🚨 <b>Проблема в работе WMS</b>\n\nВыберите <b>сбойный процесс</b>:",
            "parse_mode": "HTML",
            "buttons": _buttons_wms_process(),
        }
    return None


async def handle_wms_message(user_id: int, text: str, attachment_list: Optional[list] = None) -> Optional[dict]:
    """
    Обработка текстового сообщения в сценарии WMS.
    attachment_list: список dict с ключами type, token (из входящего сообщения MAX) для шага attachments.
    Возвращает следующий вопрос или dict с ключом "create_ticket" и form_data для вызова create_ticket в main_max.
    """
    state = _flow.get(user_id)
    # Вложения (wms_issue)
    if state and state.get("step") == "attachments" and attachment_list:
        tokens = state.get("data", {}).get("wms_attachment_tokens") or []
        max_count = 10
        for att in attachment_list:
            if not isinstance(att, dict) or len(tokens) >= max_count:
                continue
            if att.get("url"):
                item = {"type": att.get("type") or "file", "url": att["url"]}
                if att.get("filename"):
                    item["filename"] = att["filename"]
                tokens.append(item)
            elif att.get("token"):
                tokens.append({"type": att.get("type") or "file", "token": att["token"]})
        state["data"] = state.get("data", {})
        state["data"]["wms_attachment_tokens"] = tokens
        n = len(tokens)
        return {
            "text": f"📎 Добавлено вложений: {n} из {max_count}. Приложите ещё или нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    # Вложения «Настройки WMS» (обязательны)
    if state and state.get("step") == "settings_attachments" and attachment_list:
        tokens = state.get("data", {}).get("wms_settings_attachment_tokens") or []
        max_count = 10
        for att in attachment_list:
            if not isinstance(att, dict) or len(tokens) >= max_count:
                continue
            if att.get("url"):
                item = {"type": att.get("type") or "file", "url": att["url"]}
                if att.get("filename"):
                    item["filename"] = att["filename"]
                tokens.append(item)
            elif att.get("token"):
                tokens.append({"type": att.get("type") or "file", "token": att["token"]})
        state["data"] = state.get("data", {})
        state["data"]["wms_settings_attachment_tokens"] = tokens
        n = len(tokens)
        return {
            "text": f"📎 Добавлено {n} из {max_count}. Приложите файлы и нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    # Вложения «Пользователь PSIwms» (опционально)
    if state and state.get("step") == "psi_attachments" and attachment_list:
        tokens = state.get("data", {}).get("psi_attachment_tokens") or []
        max_count = 10
        for att in attachment_list:
            if not isinstance(att, dict) or len(tokens) >= max_count:
                continue
            if att.get("url"):
                item = {"type": att.get("type") or "file", "url": att["url"]}
                if att.get("filename"):
                    item["filename"] = att["filename"]
                tokens.append(item)
            elif att.get("token"):
                tokens.append({"type": att.get("type") or "file", "token": att["token"]})
        state["data"] = state.get("data", {})
        state["data"]["psi_attachment_tokens"] = tokens
        n = len(tokens)
        return {
            "text": f"📎 Добавлено {n} из {max_count}. «✅ Завершить создание задачи» или «⏭ Пропустить вложения».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_psi_user", "label": "✅ Завершить создание задачи"}, {"id": "skip_psi_attachment", "label": "⏭ Пропустить вложения"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    text = (text or "").strip()
    if not text:
        return None
    if state and text.lower() in ("/cancel", "отмена", "cancel"):
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    if not state:
        return None
    step = state.get("step")
    data = state.get("data", {})
    if step == "summary":
        data["summary"] = text
        state["step"] = "description"
        state["data"] = data
        return {
            "text": "Введите <b>подробное описание</b> проблемы или нажмите «Пропустить»:",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_skip_description", "label": "⏭ Пропустить"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if step == "description":
        data["description"] = text
        state["step"] = "attachments"
        state["data"] = data
        return {
            "text": "📎 Приложите фото, видео или документы (до 10 файлов) или нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if step == "process":
        return {
            "text": "Выберите процесс кнопкой ниже:",
            "parse_mode": "HTML",
            "buttons": _buttons_wms_process(),
        }
    if step == "attachments":
        return {
            "text": "Нажмите «✅ Завершить создание задачи» для создания заявки.",
            "parse_mode": "HTML",
            "buttons": [{"id": "wms_finish_ticket", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    # Изменение настроек WMS
    if step == "settings_description":
        desc = text if text != "—" else ""
        data["description"] = desc
        data["wms_settings_attachment_tokens"] = []
        state["step"] = "settings_attachments"
        state["data"] = data
        return {
            "text": "⚙️ <b>Изменение настроек системы WMS</b>\n\n📎 Загрузите вложения (обязательно). Добавлено: 0. Затем нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if step == "settings_attachments":
        return {
            "text": "Загрузите вложения (обязательно) и нажмите «✅ Завершить создание задачи».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_wms_settings", "label": "✅ Завершить создание задачи"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    # Пользователь PSIwms
    if step == "psi_title":
        if len(text) < 3:
            return {"text": "Тема должна быть не менее 3 символов. Введите тему задачи:", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["summary"] = text
        state["step"] = "psi_full_name"
        state["data"] = data
        return {
            "text": "👤 Введите ФИО полностью и должность пользователя, кому нужно внести корректировки или создать учетную запись",
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    if step == "psi_full_name":
        data["full_name"] = text
        state["data"] = data
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        dept_wms = (profile.get("department_wms") or "").strip()
        if dept_wms:
            data["department"] = dept_wms
            state["step"] = "psi_comment"
            return {
                "text": "👤 Что нужно сделать?",
                "parse_mode": "HTML",
                "buttons": CANCEL_BTN,
            }
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async()
        _flow[user_id] = {"step": "psi_department", "data": dict(data), "departments": depts or [], "dept_page": 0}
        if not depts:
            return {"text": "Список подразделений недоступен. Введите подразделение текстом или нажмите Отмена.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        return {
            "text": "👤 Выберите подразделение:",
            "parse_mode": "HTML",
            "buttons": _buttons_wms_departments(depts, 0),
        }
    if step == "psi_comment":
        comment = text if text != "—" else ""
        data["comment"] = comment
        data["psi_attachment_tokens"] = []
        state["step"] = "psi_attachments"
        state["data"] = data
        return {
            "text": "👤 <b>Создать/изменить/удалить пользователя PSIwms</b>\n\n📎 Вложения (опционально). Добавлено: 0. Нажмите «✅ Завершить создание задачи» или «⏭ Пропустить вложения».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_psi_user", "label": "✅ Завершить создание задачи"}, {"id": "skip_psi_attachment", "label": "⏭ Пропустить вложения"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    if step == "psi_attachments":
        return {
            "text": "Нажмите «✅ Завершить создание задачи» или «⏭ Пропустить вложения».",
            "parse_mode": "HTML",
            "buttons": [{"id": "finish_psi_user", "label": "✅ Завершить создание задачи"}, {"id": "skip_psi_attachment", "label": "⏭ Пропустить вложения"}, {"id": "cancel", "label": "❌ Отмена"}],
        }
    return None


def is_in_wms_flow(user_id: int) -> bool:
    return user_id in _flow
