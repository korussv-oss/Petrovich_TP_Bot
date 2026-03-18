"""Пошаговый сценарий MAX для заявки «Электронная почта (Owa\\Outlook)»."""
from typing import Optional

from user_storage import is_user_registered
from core.email_owa import EMAIL_OWA_REQUEST_KINDS, EMAIL_OWA_KIND_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_flow: dict[int, dict] = {}


def is_in_email_owa_flow(user_id: int) -> bool:
    return user_id in _flow


def _request_kind_buttons() -> list:
    buttons = [{"id": key, "label": label} for key, label in EMAIL_OWA_REQUEST_KINDS]
    buttons.append({"id": "cancel", "label": "❌ Отмена"})
    return buttons


def _workplace_buttons() -> list:
    return [{"id": "email_owa_skip_workplace", "label": "⏭ Пропустить"}, {"id": "cancel", "label": "❌ Отмена"}]


def _attachments_buttons() -> list:
    return [
        {"id": "email_owa_finish_ticket", "label": "✅ Создать заявку"},
        {"id": "email_owa_skip_attachments", "label": "⏭ Пропустить вложения"},
        {"id": "cancel", "label": "❌ Отмена"},
    ]


async def start_email_owa(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow[user_id] = {"step": "request_kind", "data": {}}
    return {
        "text": "📨 <b>Электронная почта (Owa\\Outlook)</b>\n\nВыберите ваш запрос:",
        "parse_mode": "HTML",
        "buttons": _request_kind_buttons(),
    }


async def handle_email_owa_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}

    if step == "request_kind" and callback_id in EMAIL_OWA_KIND_BY_ID:
        data["request_kind"] = EMAIL_OWA_KIND_BY_ID.get(callback_id)
        state["data"] = data
        state["step"] = "rms_or_ip"
        return {
            "text": f"📨 <b>Электронная почта (Owa\\Outlook)</b>\n\n✅ Запрос: {data['request_kind']}\n\nУкажите RMS или IP:",
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if step == "workplace" and callback_id == "email_owa_skip_workplace":
        data["workplace"] = ""
        state["data"] = data
        state["step"] = "description"
        return {"text": "Введите подробное описание проблемы:", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    if step == "attachments" and callback_id in ("email_owa_finish_ticket", "email_owa_skip_attachments"):
        if callback_id == "email_owa_skip_attachments":
            data["email_owa_attachment_tokens"] = []
        ticket_data = dict(data)
        tokens = list(ticket_data.get("email_owa_attachment_tokens") or [])
        _flow.pop(user_id, None)
        return {
            "create_ticket": {
                "ticket_type_id": "email_owa_outlook",
                "form_data": {
                    "request_kind": (ticket_data.get("request_kind") or "").strip(),
                    "rms_or_ip": (ticket_data.get("rms_or_ip") or "").strip(),
                    "workplace": (ticket_data.get("workplace") or "").strip(),
                    "description": (ticket_data.get("description") or "").strip(),
                },
                "attachment_tokens": tokens,
            }
        }
    return None


async def handle_email_owa_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}

    if step == "request_kind":
        return {"text": "Выберите тип запроса кнопкой ниже.", "parse_mode": "HTML", "buttons": _request_kind_buttons()}

    if step == "rms_or_ip":
        value = (text or "").strip()
        if not value:
            return {"text": "Укажите RMS или IP.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["rms_or_ip"] = value
        state["data"] = data
        state["step"] = "workplace"
        return {
            "text": "Укажите номер или местоположение рабочего места (опционально) или нажмите «Пропустить».",
            "parse_mode": "HTML",
            "buttons": _workplace_buttons(),
        }

    if step == "workplace":
        data["workplace"] = (text or "").strip()
        state["data"] = data
        state["step"] = "description"
        return {"text": "Введите подробное описание проблемы:", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    if step == "description":
        value = (text or "").strip()
        if not value:
            return {"text": "Описание не может быть пустым.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["description"] = value
        data["email_owa_attachment_tokens"] = []
        state["data"] = data
        state["step"] = "attachments"
        return {
            "text": "📎 Приложите фото/видео/документы (опционально) или нажмите «Создать заявку».",
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if step == "attachments":
        tokens = list(data.get("email_owa_attachment_tokens") or [])
        for att in (attachment_list or []):
            if not isinstance(att, dict):
                continue
            if len(tokens) >= 10:
                break
            if att.get("url"):
                tokens.append(att)
        data["email_owa_attachment_tokens"] = tokens
        state["data"] = data
        if attachment_list:
            return {
                "text": f"📎 Добавлено {len(tokens)} из 10. Можно добавить ещё или завершить создание заявки.",
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {
            "text": "Пришлите вложение или нажмите кнопку завершения.",
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }
    return None
