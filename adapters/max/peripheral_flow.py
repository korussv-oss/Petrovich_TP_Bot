"""Пошаговое создание заявки «Периферийное оборудование» в MAX."""
from typing import Optional

from core.support import ticket_wizard
from adapters.max._utils import collect_attachments
from adapters.max._wizard_flow import WizardFlowStore
from user_storage import is_user_registered
from core.peripheral_equipment import PERIPHERAL_KINDS, PERIPHERAL_KIND_BY_ID

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]

_store = WizardFlowStore()


def is_in_peripheral_flow(user_id: int) -> bool:
    return _store.has(user_id)


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
    _store.create(user_id, ticket_type_id="peripheral_equipment", step="kind")
    return {
        "text": ticket_wizard.peripheral_kind_screen().text,
        "parse_mode": "HTML",
        "buttons": _kind_buttons(),
    }


async def handle_peripheral_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "kind" and callback_id.startswith("peripheral_kind_"):
        kind_id = callback_id.replace("peripheral_kind_", "", 1).strip()
        label = PERIPHERAL_KIND_BY_ID.get(kind_id)
        if not label:
            return {"text": "Неверный выбор. Выберите вид оборудования.", "parse_mode": "HTML", "buttons": _kind_buttons()}
        _store.set_step(user_id, "ip", data={"peripheral_kind": label})
        return {
            "text": ticket_wizard.peripheral_ip_screen(kind_label=label).text,
            "parse_mode": "HTML",
            "buttons": CANCEL_BTN,
        }

    if session.step == "description" and callback_id == "peripheral_skip_description":
        _store.set_step(user_id, "attachments", data={"description": "", "peripheral_attachment_tokens": []})
        return {
            "text": ticket_wizard.peripheral_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if session.step == "attachments" and callback_id in ("peripheral_finish_ticket", "peripheral_skip_attachments"):
        session = _store.get(user_id)
        if callback_id == "peripheral_skip_attachments":
            _store.update_data(user_id, peripheral_attachment_tokens=[])
            session = _store.get(user_id)
        attachment_tokens = list(session.data.get("peripheral_attachment_tokens") or [])
        _store.clear(user_id)
        return {
            "create_ticket": {
                "ticket_type_id": "peripheral_equipment",
                "form_data": {
                    "peripheral_kind": (session.data.get("peripheral_kind") or "").strip(),
                    "ip_address": (session.data.get("ip_address") or "").strip(),
                    "description": (session.data.get("description") or "").strip(),
                },
                "attachment_tokens": attachment_tokens,
            }
        }
    return None


async def handle_peripheral_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None

    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "kind":
        return {"text": "Выберите вид оборудования кнопкой ниже.", "parse_mode": "HTML", "buttons": _kind_buttons()}

    if session.step == "ip":
        ip = (text or "").strip()
        if not ip:
            return {"text": "Укажите IP адрес или «нет».", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        _store.set_step(user_id, "description", data={"ip_address": ip})
        return {
            "text": ticket_wizard.peripheral_description_screen().text,
            "parse_mode": "HTML",
            "buttons": _desc_buttons(),
        }

    if session.step == "description":
        _store.set_step(user_id, "attachments", data={"description": (text or "").strip(), "peripheral_attachment_tokens": []})
        return {
            "text": ticket_wizard.peripheral_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if session.step == "attachments":
        session = _store.get(user_id)
        tokens = collect_attachments(session.data.get("peripheral_attachment_tokens") or [], attachment_list)
        _store.update_data(user_id, peripheral_attachment_tokens=tokens)
        if attachment_list:
            return {
                "text": ticket_wizard.peripheral_attachments_screen(added_count=len(tokens)).text,
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {"text": "Пришлите вложение или нажмите кнопку завершения.", "parse_mode": "HTML", "buttons": _attachments_buttons()}

    return {"text": "Используйте кнопки ниже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
