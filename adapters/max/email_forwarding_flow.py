"""Пошаговый сценарий MAX для заявки «Настройка переадресации» (ISR, request type 394)."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from user_storage import is_user_registered
from core.email_forwarding import EMAIL_FORWARDING_ON_OFF, EMAIL_FORWARDING_ON_OFF_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_flow: dict[int, dict] = {}


def is_in_email_forwarding_flow(user_id: int) -> bool:
    return user_id in _flow


def _on_off_buttons() -> list:
    buttons = [{"id": f"email_fwd_onoff:{oid}", "label": label} for oid, label in EMAIL_FORWARDING_ON_OFF]
    buttons.append({"id": "cancel", "label": "❌ Отмена"})
    return buttons


def _looks_like_email(value: str) -> bool:
    v = (value or "").strip()
    if len(v) < 5 or "@" not in v or " " in v:
        return False
    local, _, domain = v.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return True


def _parse_date_to_yyyy_mm_dd(value: str) -> str | None:
    v = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat()
        except Exception:
            pass
    return None


async def start_email_forwarding(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow[user_id] = {"step": "on_off", "data": {}}
    return {
        "text": "↪️ <b>Настройка переадресации</b>\n\nВыберите действие:",
        "parse_mode": "HTML",
        "buttons": _on_off_buttons(),
    }


async def handle_email_forwarding_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}

    if step == "on_off" and callback_id.startswith("email_fwd_onoff:"):
        oid = callback_id.split(":", 1)[1].strip()
        if oid not in EMAIL_FORWARDING_ON_OFF_BY_ID:
            return {"text": "Неверный выбор. Попробуйте ещё раз.", "parse_mode": "HTML", "buttons": _on_off_buttons()}
        data["on_off"] = oid
        state["data"] = data
        state["step"] = "email_from"
        return {"text": "Введите email, <b>с которого</b> нужно установить переадресацию (например, name@petrovich.ru):", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    return None


async def handle_email_forwarding_message(user_id: int, text: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}
    value = (text or "").strip()

    if step == "on_off":
        return {"text": "Выберите действие кнопкой ниже.", "parse_mode": "HTML", "buttons": _on_off_buttons()}

    if step == "email_from":
        if not _looks_like_email(value):
            return {"text": "❌ Похоже, это не email. Введите адрес в формате name@domain.tld.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["email_from"] = value
        state["data"] = data
        state["step"] = "email_to"
        return {"text": "Введите email, <b>на который</b> нужно установить переадресацию:", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    if step == "email_to":
        if not _looks_like_email(value):
            return {"text": "❌ Похоже, это не email. Введите адрес в формате name@domain.tld.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["email_to"] = value
        state["data"] = data
        state["step"] = "date"
        return {
            "text": "Введите дату включения/выключения переадресации.\n\nФормат: <b>YYYY-MM-DD</b> (например, 2026-03-16) или <b>DD.MM.YYYY</b>.",
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if step == "date":
        d = _parse_date_to_yyyy_mm_dd(value)
        if not d:
            return {"text": "❌ Не понял дату. Введите YYYY-MM-DD или DD.MM.YYYY.", "parse_mode": "HTML", "buttons": CANCEL_BTN}

        # Jira validValues для customfield_13688:
        # 13006 = Включить, 13007 = Выключить
        on_off = (data.get("on_off") or "").strip()
        on_off_value = "13006" if on_off == "email_fwd_on" else "13007"

        ticket_data = dict(data)
        _flow.pop(user_id, None)
        return {
            "create_ticket": {
                "ticket_type_id": "email_forwarding",
                "form_data": {
                    "on_off": on_off_value,
                    "email_from": (ticket_data.get("email_from") or "").strip(),
                    "email_to": (ticket_data.get("email_to") or "").strip(),
                    "redirection_date": d,
                },
                "attachment_tokens": [],
            }
        }

    return None

