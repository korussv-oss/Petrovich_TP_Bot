"""Пошаговое создание заявки «Электронная очередь» в MAX."""
from typing import Optional

from user_storage import is_user_registered
from core.electronic_queue import ELECTRONIC_QUEUE_SERVICE_TYPES, ELECTRONIC_QUEUE_SERVICE_TYPE_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]
_flow: dict[int, dict] = {}


def is_in_electronic_queue_flow(user_id: int) -> bool:
    return user_id in _flow


def _type_buttons() -> list:
    out = [{"id": f"eq_type_{sid}", "label": label} for sid, label in ELECTRONIC_QUEUE_SERVICE_TYPES]
    out.append({"id": "cancel", "label": "❌ Отмена"})
    return out


async def start_electronic_queue(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow[user_id] = {"step": "service_type", "data": {}}
    return {
        "text": "🎫 <b>Электронная очередь</b>\n\nВыберите тип услуги:",
        "parse_mode": "HTML",
        "buttons": _type_buttons(),
    }


async def handle_electronic_queue_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    step = state.get("step")
    data = state.get("data") or {}
    if step == "service_type" and callback_id.startswith("eq_type_"):
        sid = callback_id.replace("eq_type_", "", 1).strip()
        label = ELECTRONIC_QUEUE_SERVICE_TYPE_BY_ID.get(sid)
        if not label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _type_buttons()}
        data["service_type"] = label
        state["data"] = data
        state["step"] = "description"
        return {
            "text": (
                "🎫 <b>Электронная очередь</b>\n\n"
                f"✅ Тип услуги: {label}\n\n"
                "Введите подробное описание:"
            ),
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }
    return None


async def handle_electronic_queue_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    step = state.get("step")
    data = state.get("data") or {}
    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)
    if step == "service_type":
        return {"text": "Выберите тип услуги кнопкой ниже.", "parse_mode": "HTML", "buttons": _type_buttons()}
    if step == "description":
        desc = (text or "").strip()
        if not desc:
            return {"text": "Описание не может быть пустым.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        d = dict(data)
        _flow.pop(user_id, None)
        return {
            "create_ticket": {
                "ticket_type_id": "electronic_queue",
                "form_data": {
                    "summary": "Электронная очередь",
                    "service_type": (d.get("service_type") or "").strip(),
                    "description": desc,
                },
            }
        }
    return {"text": "Используйте кнопки ниже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
