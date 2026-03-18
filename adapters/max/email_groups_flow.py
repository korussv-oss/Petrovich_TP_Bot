"""Пошаговый сценарий MAX для заявки «Группы рассылки» (ISR, request type 381)."""

from __future__ import annotations

import re
from typing import Optional

from user_storage import is_user_registered
from core.email_groups import EMAIL_GROUPS_WHAT_TO_DO, EMAIL_GROUPS_WHAT_TO_DO_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_flow: dict[int, dict] = {}


def is_in_email_groups_flow(user_id: int) -> bool:
    return user_id in _flow


def _what_to_do_buttons() -> list:
    buttons = [{"id": f"email_groups_do:{oid}", "label": label} for oid, label in EMAIL_GROUPS_WHAT_TO_DO]
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


def _extract_email_maybe(value: str) -> str:
    v = (value or "").strip()
    m = re.search(r"<([^>]+@[^>]+)>", v)
    if m:
        return m.group(1).strip()
    return v


def _looks_like_ad_login(value: str) -> bool:
    v = (value or "").strip()
    if not v or " " in v or "@" in v or "." not in v:
        return False
    left, _, right = v.partition(".")
    if not left or not right:
        return False
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789._-"
    vv = v.lower()
    return all(c in allowed for c in vv)


async def start_email_groups(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _flow[user_id] = {"step": "what_to_do", "data": {}}
    return {
        "text": "👥 <b>Группы рассылки</b>\n\nКакой тип работ вас интересует?",
        "parse_mode": "HTML",
        "buttons": _what_to_do_buttons(),
    }


async def handle_email_groups_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}

    if step == "what_to_do" and callback_id.startswith("email_groups_do:"):
        oid = callback_id.split(":", 1)[1].strip()
        label = EMAIL_GROUPS_WHAT_TO_DO_BY_ID.get(oid)
        if not label:
            return {"text": "Неверный выбор. Попробуйте ещё раз.", "parse_mode": "HTML", "buttons": _what_to_do_buttons()}
        data["what_to_do_id"] = oid
        data["what_to_do_label"] = label
        state["data"] = data
        if oid in ("13012", "13013"):
            state["step"] = "group_name"
            return {"text": f"✅ Тип работ: {label}\n\nВведите <b>Название группы рассылки</b>:", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        state["step"] = "group_email"
        return {"text": f"✅ Тип работ: {label}\n\nВведите <b>Адрес группы рассылки</b> (email):", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    return None


async def handle_email_groups_message(user_id: int, text: str) -> Optional[dict]:
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

    if step == "what_to_do":
        return {"text": "Выберите тип работ кнопкой ниже.", "parse_mode": "HTML", "buttons": _what_to_do_buttons()}

    if step == "group_name":
        if not value:
            return {"text": "❌ Название не может быть пустым. Введите название группы.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["group_name"] = value
        state["data"] = data
        if (data.get("what_to_do_id") or "").strip() == "13012":
            state["step"] = "group_owner"
            return {"text": "Введите <b>Владельца группы рассылки</b> (email):", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        state["step"] = "description"
        return {"text": "Введите <b>Причину изменения</b>:", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    if step == "group_owner":
        if not _looks_like_email(value):
            return {"text": "❌ Похоже, это не email. Введите адрес в формате name@domain.tld.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["group_owner"] = value
        state["data"] = data
        state["step"] = "group_membership"
        return {
            "text": "Введите <b>Кто будет входить в группу рассылки</b>.\nМожно перечислить несколько email через запятую/перенос строки:",
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if step == "group_membership":
        if not value:
            return {"text": "❌ Поле не может быть пустым. Укажите хотя бы одного участника (email).", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["group_membership"] = value
        state["data"] = data
        state["step"] = "description"
        return {"text": "Введите <b>Причину изменения</b>:", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    if step == "group_email":
        email = _extract_email_maybe(value)
        if not _looks_like_email(email):
            return {"text": "❌ Похоже, это не email. Введите адрес группы рассылки (например, group@petrovich.ru).", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["group_email"] = email
        state["data"] = data
        state["step"] = "ad_login"
        return {"text": "Введите <b>Имя учетной записи сотрудника</b> (AD Login) в формате <b>i.vanov</b>:", "parse_mode": "HTML", "buttons": CANCEL_BTN}

    if step == "ad_login":
        if not _looks_like_ad_login(value):
            return {"text": "❌ Нужен AD Login строго в формате <b>i.vanov</b> (без @ и домена).", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["ad_login"] = value
        ticket_data = dict(data)
        _flow.pop(user_id, None)
        return {
            "create_ticket": {
                "ticket_type_id": "email_groups",
                "form_data": {
                    "what_to_do": (ticket_data.get("what_to_do_id") or "").strip(),
                    "group_email": (ticket_data.get("group_email") or "").strip(),
                    "ad_login": (ticket_data.get("ad_login") or "").strip(),
                },
                "attachment_tokens": [],
            }
        }

    if step == "description":
        if not value:
            return {"text": "❌ Причина изменения не может быть пустой.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["description"] = value
        ticket_data = dict(data)
        _flow.pop(user_id, None)
        what_id = (ticket_data.get("what_to_do_id") or "").strip()
        form_data = {"what_to_do": what_id, "description": (ticket_data.get("description") or "").strip()}
        if what_id == "13012":
            form_data.update({
                "group_name": (ticket_data.get("group_name") or "").strip(),
                "group_owner": (ticket_data.get("group_owner") or "").strip(),
                "group_membership": (ticket_data.get("group_membership") or "").strip(),
            })
        elif what_id == "13013":
            form_data.update({"group_name": (ticket_data.get("group_name") or "").strip()})
        return {"create_ticket": {"ticket_type_id": "email_groups", "form_data": form_data, "attachment_tokens": []}}

    return None

