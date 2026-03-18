"""Пошаговое создание заявки «Периферийное оборудование» в MAX."""
from typing import Optional

from user_storage import is_user_registered
from core.peripheral_equipment import PERIPHERAL_KINDS, PERIPHERAL_KIND_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_flow: dict[int, dict] = {}


def is_in_peripheral_flow(user_id: int) -> bool:
    return user_id in _flow


def _kind_buttons() -> list:
    buttons = [{"id": f"peripheral_kind_{kind_id}", "label": label} for kind_id, label in PERIPHERAL_KINDS]
    buttons.append({"id": "cancel", "label": "❌ Отмена"})
    return buttons


def _desc_buttons() -> list:
    return [{"id": "peripheral_skip_description", "label": "⏭ Пропустить"}, {"id": "cancel", "label": "❌ Отмена"}]


def _attachments_buttons() -> list:
    return [
        {"id": "peripheral_finish_ticket", "label": "✅ Создать заявку"},
        {"id": "peripheral_skip_attachments", "label": "⏭ Пропустить вложения"},
        {"id": "cancel", "label": "❌ Отмена"},
    ]


async def start_peripheral(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow[user_id] = {"step": "kind", "data": {}}
    return {
        "text": "🧩 <b>Периферийное оборудование</b>\n\nВыберите вид оборудования:",
        "parse_mode": "HTML",
        "buttons": _kind_buttons(),
    }


async def handle_peripheral_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}

    if step == "kind" and callback_id.startswith("peripheral_kind_"):
        kind_id = callback_id.replace("peripheral_kind_", "", 1).strip()
        label = PERIPHERAL_KIND_BY_ID.get(kind_id)
        if not label:
            return {"text": "Неверный выбор. Выберите вид оборудования.", "parse_mode": "HTML", "buttons": _kind_buttons()}
        data["peripheral_kind"] = label
        state["data"] = data
        state["step"] = "ip"
        return {
            "text": (
                "🧩 <b>Периферийное оборудование</b>\n\n"
                f"✅ Вид оборудования: {label}\n\n"
                "Укажите IP адрес (если нет, напишите «нет»)."
            ),
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if step == "description" and callback_id == "peripheral_skip_description":
        data["description"] = ""
        data["peripheral_attachment_tokens"] = []
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

    if step == "attachments" and callback_id in ("peripheral_finish_ticket", "peripheral_skip_attachments"):
        if callback_id == "peripheral_skip_attachments":
            data["peripheral_attachment_tokens"] = []
        ticket_data = dict(data)
        attachment_tokens = list(ticket_data.get("peripheral_attachment_tokens") or [])
        _flow.pop(user_id, None)
        return {
            "create_ticket": {
                "ticket_type_id": "peripheral_equipment",
                "form_data": {
                    "peripheral_kind": (ticket_data.get("peripheral_kind") or "").strip(),
                    "ip_address": (ticket_data.get("ip_address") or "").strip(),
                    "description": (ticket_data.get("description") or "").strip(),
                },
                "attachment_tokens": attachment_tokens,
            }
        }
    return None


async def handle_peripheral_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
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
        return {"text": "Выберите вид оборудования кнопкой ниже.", "parse_mode": "HTML", "buttons": _kind_buttons()}

    if step == "ip":
        ip = (text or "").strip()
        if not ip:
            return {"text": "Укажите IP адрес или «нет».", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["ip_address"] = ip
        state["data"] = data
        state["step"] = "description"
        return {
            "text": "Опишите проблему (Description) или нажмите «Пропустить».",
            "parse_mode": "HTML",
            "buttons": _desc_buttons(),
        }

    if step == "description":
        data["description"] = (text or "").strip()
        data["peripheral_attachment_tokens"] = []
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
        tokens = list(data.get("peripheral_attachment_tokens") or [])
        for att in (attachment_list or []):
            if not isinstance(att, dict):
                continue
            if len(tokens) >= 10:
                break
            if att.get("url"):
                tokens.append(att)
        data["peripheral_attachment_tokens"] = tokens
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
