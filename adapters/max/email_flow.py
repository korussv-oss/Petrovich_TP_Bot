"""Пошаговый сценарий MAX для заявки «Электронная почта (Owa/Outlook)»."""
from typing import Optional

from core.support import ticket_wizard
from adapters.max._utils import collect_attachments
from adapters.max._wizard_flow import WizardFlowStore
from user_storage import is_user_registered
from core.email_owa import EMAIL_OWA_REQUEST_KINDS, EMAIL_OWA_KIND_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_store = WizardFlowStore()


def is_in_email_owa_flow(user_id: int) -> bool:
    return _store.has(user_id)


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
    _store.create(user_id, ticket_type_id="email_owa_outlook", step="request_kind")
    return {
        "text": ticket_wizard.email_owa_request_kind_screen().text,
        "parse_mode": "HTML",
        "buttons": _request_kind_buttons(),
    }


async def handle_email_owa_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "request_kind" and callback_id in EMAIL_OWA_KIND_BY_ID:
        kind_label = EMAIL_OWA_KIND_BY_ID.get(callback_id)
        _store.set_step(user_id, "rms_or_ip", data={"request_kind": kind_label})
        return {
            "text": ticket_wizard.email_owa_rms_or_ip_screen(request_kind=kind_label).text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if session.step == "workplace" and callback_id == "email_owa_skip_workplace":
        _store.set_step(user_id, "description", data={"workplace": ""})
        return {
            "text": ticket_wizard.email_owa_description_screen().text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if session.step == "attachments" and callback_id in ("email_owa_finish_ticket", "email_owa_skip_attachments"):
        session = _store.get(user_id)
        if callback_id == "email_owa_skip_attachments":
            _store.update_data(user_id, email_owa_attachment_tokens=[])
            session = _store.get(user_id)
        tokens = list(session.data.get("email_owa_attachment_tokens") or [])
        _store.clear(user_id)
        return {
            "create_ticket": {
                "ticket_type_id": "email_owa_outlook",
                "form_data": {
                    "request_kind": (session.data.get("request_kind") or "").strip(),
                    "rms_or_ip": (session.data.get("rms_or_ip") or "").strip(),
                    "workplace": (session.data.get("workplace") or "").strip(),
                    "description": (session.data.get("description") or "").strip(),
                },
                "attachment_tokens": tokens,
            }
        }
    return None


async def handle_email_owa_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "request_kind":
        return {"text": "Выберите тип запроса кнопкой ниже.", "parse_mode": "HTML", "buttons": _request_kind_buttons()}

    if session.step == "rms_or_ip":
        value = (text or "").strip()
        if not value:
            return {"text": "Укажите RMS или IP.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        _store.set_step(user_id, "workplace", data={"rms_or_ip": value})
        return {
            "text": ticket_wizard.email_owa_workplace_screen().text,
            "parse_mode": "HTML",
            "buttons": _workplace_buttons(),
        }

    if session.step == "workplace":
        _store.set_step(user_id, "description", data={"workplace": (text or "").strip()})
        return {
            "text": ticket_wizard.email_owa_description_screen().text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if session.step == "description":
        value = (text or "").strip()
        if not value:
            return {"text": "Описание не может быть пустым.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        _store.set_step(user_id, "attachments", data={"description": value, "email_owa_attachment_tokens": []})
        return {
            "text": ticket_wizard.email_owa_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if session.step == "attachments":
        session = _store.get(user_id)
        tokens = collect_attachments(session.data.get("email_owa_attachment_tokens") or [], attachment_list)
        _store.update_data(user_id, email_owa_attachment_tokens=tokens)
        if attachment_list:
            return {
                "text": ticket_wizard.email_owa_attachments_screen(added_count=len(tokens)).text,
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {"text": "Пришлите вложение или нажмите кнопку завершения.", "parse_mode": "HTML", "buttons": _attachments_buttons()}
    return None
