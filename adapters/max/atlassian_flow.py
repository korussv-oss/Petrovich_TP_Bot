"""Пошаговый сценарий MAX для заявки «Техническая поддержка Atlassian»."""
from typing import Optional

from adapters.max._utils import collect_attachments
from core.atlassian_support import ATLASSIAN_SERVICE_TYPES, ATLASSIAN_SERVICE_BY_ID
from user_storage import is_user_registered

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]
_flow: dict[int, dict] = {}


def is_in_atlassian_flow(user_id: int) -> bool:
    return user_id in _flow


def _service_buttons() -> list:
    out = [{"id": f"atlassian_service_{sid}", "label": label} for sid, label in ATLASSIAN_SERVICE_TYPES]
    out.append({"id": "cancel", "label": "❌ Отмена"})
    return out


def _attachments_buttons() -> list:
    return [
        {"id": "atlassian_finish_ticket", "label": "✅ Создать заявку"},
        {"id": "atlassian_skip_attachments", "label": "⏭ Пропустить вложения"},
        {"id": "cancel", "label": "❌ Отмена"},
    ]


async def start_atlassian(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow[user_id] = {"step": "service_name", "data": {}}
    return {
        "text": "🧩 <b>Техническая поддержка Atlassian</b>\n\nС каким сервисом проблема?",
        "parse_mode": "HTML",
        "buttons": _service_buttons(),
    }


async def handle_atlassian_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    step = state.get("step")
    data = state.get("data") or {}
    if step == "service_name" and callback_id.startswith("atlassian_service_"):
        service_id = callback_id.replace("atlassian_service_", "", 1).strip()
        service_name = ATLASSIAN_SERVICE_BY_ID.get(service_id)
        if not service_name:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _service_buttons()}
        data["service_name"] = service_name
        state["data"] = data
        state["step"] = "description"
        return {
            "text": (
                "🧩 <b>Техническая поддержка Atlassian</b>\n\n"
                f"✅ Сервис: {service_name}\n\n"
                "Опишите подробно, в чём именно Вам нужна помощь:"
            ),
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    if step == "attachments" and callback_id in ("atlassian_finish_ticket", "atlassian_skip_attachments"):
        tokens = list(data.get("atlassian_attachment_tokens") or [])
        if callback_id == "atlassian_skip_attachments":
            tokens = []
        service_name = (data.get("service_name") or "").strip()
        description = (data.get("description") or "").strip()
        _flow.pop(user_id, None)
        return {
            "create_ticket": {
                "ticket_type_id": "atlassian_support",
                "form_data": {
                    "summary": "Запрос созданный через Бот ТП",
                    "service_name": service_name,
                    "description": description,
                },
                "attachment_tokens": tokens,
            }
        }
    return None


async def handle_atlassian_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    step = state.get("step")
    data = state.get("data") or {}
    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    if step == "service_name":
        return {"text": "Выберите сервис кнопкой ниже.", "parse_mode": "HTML", "buttons": _service_buttons()}
    if step == "description":
        description = (text or "").strip()
        if not description:
            return {"text": "Описание не может быть пустым.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        service_name = (data.get("service_name") or "").strip()
        state["step"] = "attachments"
        data["description"] = description
        data["atlassian_attachment_tokens"] = []
        state["data"] = data
        return {
            "text": (
                "📎 <b>Техническая поддержка Atlassian</b>\n\n"
                f"✅ Сервис: {service_name}\n"
                "Вложения: 0\n\n"
                "Прикрепите файлы (опционально), затем нажмите «Создать заявку»."
            ),
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }
    if step == "attachments":
        tokens = collect_attachments(data.get("atlassian_attachment_tokens") or [], attachment_list)
        data["atlassian_attachment_tokens"] = tokens
        state["data"] = data
        if attachment_list:
            return {
                "text": (
                    "📎 <b>Техническая поддержка Atlassian</b>\n\n"
                    f"✅ Сервис: {(data.get('service_name') or '').strip()}\n"
                    f"Вложения: {len(tokens)}\n\n"
                    "Можно прикрепить ещё файл или завершить создание заявки."
                ),
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {
            "text": "Пришлите вложение или нажмите «Создать заявку».",
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }
    return {"text": "Используйте кнопки ниже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
