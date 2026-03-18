"""Пошаговое создание заявки «Оргтехника» в MAX."""
from typing import Optional

from user_storage import is_user_registered
from core.orgtech import ORGTECH_KINDS, ORGTECH_KIND_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_flow: dict[int, dict] = {}


def is_in_orgtech_flow(user_id: int) -> bool:
    return user_id in _flow


def _kind_buttons() -> list:
    buttons = [{"id": f"orgtech_kind_{kind_id}", "label": label} for kind_id, label in ORGTECH_KINDS]
    buttons.append({"id": "cancel", "label": "❌ Отмена"})
    return buttons


def _desc_buttons() -> list:
    return [{"id": "orgtech_skip_description", "label": "⏭ Пропустить"}, {"id": "cancel", "label": "❌ Отмена"}]


def _attachments_buttons() -> list:
    return [
        {"id": "orgtech_finish_ticket", "label": "✅ Создать заявку"},
        {"id": "orgtech_skip_attachments", "label": "⏭ Пропустить вложения"},
        {"id": "cancel", "label": "❌ Отмена"},
    ]


async def start_orgtech(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow[user_id] = {"step": "kind", "data": {}}
    return {
        "text": "🖨️ <b>Оргтехника</b>\n\nУкажите тип оргтехники:",
        "parse_mode": "HTML",
        "buttons": _kind_buttons(),
    }


async def handle_orgtech_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}

    if step == "kind" and callback_id.startswith("orgtech_kind_"):
        kind_id = callback_id.replace("orgtech_kind_", "", 1).strip()
        label = ORGTECH_KIND_BY_ID.get(kind_id)
        if not label:
            return {"text": "Неверный выбор. Выберите тип оргтехники.", "parse_mode": "HTML", "buttons": _kind_buttons()}
        data["orgtech_kind"] = label
        state["data"] = data
        state["step"] = "location"
        return {
            "text": (
                "🖨️ <b>Оргтехника</b>\n\n"
                f"✅ Тип: {label}\n\n"
                "Укажите местоположение (обязательно)."
            ),
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if step == "description" and callback_id == "orgtech_skip_description":
        data["description"] = ""
        data["orgtech_attachment_tokens"] = []
        state["data"] = data
        state["step"] = "attachments"
        return {
            "text": (
                "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
                "Или нажмите «Создать заявку» / «Пропустить вложения»."
            ),
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if step == "attachments" and callback_id in ("orgtech_finish_ticket", "orgtech_skip_attachments"):
        if callback_id == "orgtech_skip_attachments":
            data["orgtech_attachment_tokens"] = []
        ticket_data = dict(data)
        attachment_tokens = list(ticket_data.get("orgtech_attachment_tokens") or [])
        _flow.pop(user_id, None)
        return {
            "create_ticket": {
                "ticket_type_id": "orgtech_problem",
                "form_data": {
                    "orgtech_kind": (ticket_data.get("orgtech_kind") or "").strip(),
                    "location": (ticket_data.get("location") or "").strip(),
                    "description": (ticket_data.get("description") or "").strip(),
                },
                "attachment_tokens": attachment_tokens,
            }
        }
    return None


async def handle_orgtech_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    step = state.get("step")
    data = state.get("data") or {}

    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if step == "kind":
        return {"text": "Выберите тип оргтехники кнопкой ниже.", "parse_mode": "HTML", "buttons": _kind_buttons()}

    if step == "location":
        location = (text or "").strip()
        if not location:
            return {"text": "Укажите местоположение (обязательно).", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["location"] = location
        state["data"] = data
        state["step"] = "description"
        return {
            "text": "Опишите проблему (Description) или нажмите «Пропустить».",
            "parse_mode": "HTML",
            "buttons": _desc_buttons(),
        }

    if step == "description":
        data["description"] = (text or "").strip()
        data["orgtech_attachment_tokens"] = []
        state["data"] = data
        state["step"] = "attachments"
        return {
            "text": (
                "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
                "Или нажмите «Создать заявку» / «Пропустить вложения»."
            ),
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if step == "attachments":
        tokens = list(data.get("orgtech_attachment_tokens") or [])
        for att in (attachment_list or []):
            if not isinstance(att, dict):
                continue
            if len(tokens) >= 10:
                break
            if att.get("url"):
                tokens.append(att)
        data["orgtech_attachment_tokens"] = tokens
        state["data"] = data
        if attachment_list:
            return {
                "text": f"📎 Добавлено {len(tokens)} из 10. Можно приложить ещё или завершить создание заявки.",
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {
            "text": "Пришлите вложение или нажмите кнопку завершения.",
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    return {"text": "Используйте кнопки ниже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
