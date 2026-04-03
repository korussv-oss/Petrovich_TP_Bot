"""Пошаговое создание заявки «Оргтехника» в MAX."""
from typing import Optional

from core.support import ticket_wizard
from adapters.max._utils import collect_attachments
from adapters.max._wizard_flow import WizardFlowStore
from user_storage import is_user_registered
from core.orgtech import ORGTECH_KINDS, ORGTECH_KIND_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_store = WizardFlowStore()


def is_in_orgtech_flow(user_id: int) -> bool:
    return _store.has(user_id)


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
    _store.create(user_id, ticket_type_id="orgtech_problem", step="kind")
    return {
        "text": ticket_wizard.orgtech_kind_screen().text,
        "parse_mode": "HTML",
        "buttons": _kind_buttons(),
    }


async def handle_orgtech_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "kind" and callback_id.startswith("orgtech_kind_"):
        kind_id = callback_id.replace("orgtech_kind_", "", 1).strip()
        label = ORGTECH_KIND_BY_ID.get(kind_id)
        if not label:
            return {"text": "Неверный выбор. Выберите тип оргтехники.", "parse_mode": "HTML", "buttons": _kind_buttons()}
        _store.set_step(user_id, "location", data={"orgtech_kind": label})
        return {
            "text": ticket_wizard.orgtech_location_screen(kind_label=label).text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if session.step == "description" and callback_id == "orgtech_skip_description":
        _store.set_step(user_id, "attachments", data={"description": "", "orgtech_attachment_tokens": []})
        return {
            "text": ticket_wizard.orgtech_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if session.step == "attachments" and callback_id in ("orgtech_finish_ticket", "orgtech_skip_attachments"):
        session = _store.get(user_id)
        if callback_id == "orgtech_skip_attachments":
            _store.update_data(user_id, orgtech_attachment_tokens=[])
            session = _store.get(user_id)
        attachment_tokens = list(session.data.get("orgtech_attachment_tokens") or [])
        _store.clear(user_id)
        return {
            "create_ticket": {
                "ticket_type_id": "orgtech_problem",
                "form_data": {
                    "orgtech_kind": (session.data.get("orgtech_kind") or "").strip(),
                    "location": (session.data.get("location") or "").strip(),
                    "description": (session.data.get("description") or "").strip(),
                },
                "attachment_tokens": attachment_tokens,
            }
        }
    return None


async def handle_orgtech_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None

    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "kind":
        return {"text": "Выберите тип оргтехники кнопкой ниже.", "parse_mode": "HTML", "buttons": _kind_buttons()}

    if session.step == "location":
        location = (text or "").strip()
        if not location:
            return {"text": "Укажите местоположение (обязательно).", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        _store.set_step(user_id, "description", data={"location": location})
        return {
            "text": ticket_wizard.orgtech_description_screen().text,
            "parse_mode": "HTML",
            "buttons": _desc_buttons(),
        }

    if session.step == "description":
        _store.set_step(user_id, "attachments", data={"description": (text or "").strip(), "orgtech_attachment_tokens": []})
        return {
            "text": ticket_wizard.orgtech_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if session.step == "attachments":
        session = _store.get(user_id)
        tokens = collect_attachments(session.data.get("orgtech_attachment_tokens") or [], attachment_list)
        _store.update_data(user_id, orgtech_attachment_tokens=tokens)
        if attachment_list:
            return {
                "text": ticket_wizard.orgtech_attachments_screen(added_count=len(tokens)).text,
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {"text": "Пришлите вложение или нажмите кнопку завершения.", "parse_mode": "HTML", "buttons": _attachments_buttons()}

    return {"text": "Используйте кнопки ниже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
