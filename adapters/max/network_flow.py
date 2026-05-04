"""Пошаговое создание заявки «Проблемы в работе сети» в MAX."""
from typing import Optional

from core.support import ticket_wizard
from adapters.max._utils import collect_attachments
from user_storage import is_user_registered
from core.network_problem import (
    NETWORK_TYPES,
    NETWORK_TYPE_BY_ID,
    NETWORK_PROVIDERS,
    NETWORK_PROVIDER_BY_ID,
    NETWORK_WIFI_OWNERS,
    NETWORK_WIFI_OWNER_BY_ID,
    NETWORK_PC_TYPES,
    NETWORK_PC_TYPE_BY_ID,
)

from adapters.max._wizard_flow import WizardFlowStore

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]
_store = WizardFlowStore()


def is_in_network_flow(user_id: int) -> bool:
    return _store.has(user_id)


def _buttons(options: list[tuple[str, str]], prefix: str) -> list:
    out = [{"id": f"{prefix}{oid}", "label": label} for oid, label in options]
    out.append({"id": "cancel", "label": "❌ Отмена"})
    return out


def _desc_buttons() -> list:
    return [{"id": "network_skip_description", "label": "⏭ Пропустить"}, {"id": "cancel", "label": "❌ Отмена"}]


def _rms_buttons() -> list:
    return [{"id": "network_skip_rms", "label": "⏭ Пропустить"}, {"id": "cancel", "label": "❌ Отмена"}]


def _attachments_buttons() -> list:
    return [
        {"id": "network_finish_ticket", "label": "✅ Создать заявку"},
        {"id": "network_skip_attachments", "label": "⏭ Пропустить вложения"},
        {"id": "cancel", "label": "❌ Отмена"},
    ]


async def start_network(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    _store.create(user_id, ticket_type_id="network_problem", step="network_type")
    return {
        "text": ticket_wizard.network_type_screen().text,
        "parse_mode": "HTML",
        "buttons": _buttons(NETWORK_TYPES, "network_type_"),
    }


async def handle_network_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "network_type" and callback_id.startswith("network_type_"):
        t_id = callback_id.replace("network_type_", "", 1).strip()
        t_label = NETWORK_TYPE_BY_ID.get(t_id)
        if not t_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_TYPES, "network_type_")}
        base = {"network_type": t_label, "provider": "", "provider_other": "", "wifi_problem_owner": "", "pc_type": ""}
        if t_label == "Wi-Fi (беспроводная)":
            _store.set_step(user_id, "wifi_owner", data=base)
            return {
                "text": ticket_wizard.network_wifi_owner_screen(network_type=t_label).text,
                "parse_mode": "HTML",
                "buttons": _buttons(NETWORK_WIFI_OWNERS, "network_wifi_owner_"),
            }
        if t_label == "VPN":
            _store.set_step(user_id, "pc_type", data=base)
            return {
                "text": ticket_wizard.network_pc_type_screen(network_type=t_label).text,
                "parse_mode": "HTML",
                "buttons": _buttons(NETWORK_PC_TYPES, "network_pc_type_"),
            }
        _store.set_step(user_id, "provider", data=base)
        return {
            "text": ticket_wizard.network_provider_screen(network_type=t_label).text,
            "parse_mode": "HTML",
            "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_"),
        }

    if session.step == "wifi_owner" and callback_id.startswith("network_wifi_owner_"):
        o_id = callback_id.replace("network_wifi_owner_", "", 1).strip()
        o_label = NETWORK_WIFI_OWNER_BY_ID.get(o_id)
        if not o_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_WIFI_OWNERS, "network_wifi_owner_")}
        _store.set_step(user_id, "provider", data={"wifi_problem_owner": o_label})
        session = _store.get(user_id)
        return {
            "text": ticket_wizard.network_provider_screen(network_type=session.data.get("network_type", "")).text,
            "parse_mode": "HTML",
            "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_"),
        }

    if session.step == "pc_type" and callback_id.startswith("network_pc_type_"):
        p_id = callback_id.replace("network_pc_type_", "", 1).strip()
        p_label = NETWORK_PC_TYPE_BY_ID.get(p_id)
        if not p_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PC_TYPES, "network_pc_type_")}
        _store.set_step(user_id, "provider", data={"pc_type": p_label})
        session = _store.get(user_id)
        return {
            "text": ticket_wizard.network_provider_screen(network_type=session.data.get("network_type", "")).text,
            "parse_mode": "HTML",
            "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_"),
        }

    if session.step == "provider" and callback_id.startswith("network_provider_"):
        pr_id = callback_id.replace("network_provider_", "", 1).strip()
        pr_label = NETWORK_PROVIDER_BY_ID.get(pr_id)
        if not pr_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_")}
        if pr_label == "Другой":
            _store.set_step(user_id, "provider_other", data={"provider": pr_label})
            return {
                "text": ticket_wizard.network_provider_other_screen().text,
                "parse_mode": "HTML",
                "buttons": CANCEL_BTN,
            }
        _store.set_step(user_id, "rms", data={"provider": pr_label, "provider_other": ""})
        return {
            "text": ticket_wizard.network_rms_screen().text,
            "parse_mode": "HTML",
            "buttons": _rms_buttons(),
        }

    if session.step == "rms" and callback_id == "network_skip_rms":
        _store.set_step(user_id, "description", data={"rms_internet_id": "нет"})
        return {
            "text": ticket_wizard.network_description_screen().text,
            "parse_mode": "HTML",
            "buttons": _desc_buttons(),
        }

    if session.step == "description" and callback_id == "network_skip_description":
        _store.set_step(user_id, "attachments", data={"description": "", "network_attachment_tokens": []})
        return {
            "text": ticket_wizard.network_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if session.step == "attachments" and callback_id in ("network_finish_ticket", "network_skip_attachments"):
        session = _store.get(user_id)
        if callback_id == "network_skip_attachments":
            _store.update_data(user_id, network_attachment_tokens=[])
            session = _store.get(user_id)
        d = session.data
        attachments = list(d.get("network_attachment_tokens") or [])
        _store.clear(user_id)
        return {
            "create_ticket": {
                "ticket_type_id": "network_problem",
                "form_data": {
                    "network_type": (d.get("network_type") or "").strip(),
                    "provider": (d.get("provider") or "").strip(),
                    "provider_other": (d.get("provider_other") or "").strip(),
                    "wifi_problem_owner": (d.get("wifi_problem_owner") or "").strip(),
                    "pc_type": (d.get("pc_type") or "").strip(),
                    "description": (d.get("description") or "").strip(),
                    "rms_internet_id": (d.get("rms_internet_id") or "").strip() or "нет",
                    "ip_address": "нет",
                    "preferred_contact_time": "нет",
                },
                "attachment_tokens": attachments,
            }
        }
    return None


async def handle_network_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None

    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if session.step == "network_type":
        return {"text": "Выберите тип сети кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_TYPES, "network_type_")}
    if session.step == "wifi_owner":
        return {"text": "Выберите вариант кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_WIFI_OWNERS, "network_wifi_owner_")}
    if session.step == "pc_type":
        return {"text": "Выберите тип ПК кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PC_TYPES, "network_pc_type_")}
    if session.step == "provider":
        return {"text": "Выберите провайдера кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_")}
    if session.step == "provider_other":
        value = (text or "").strip()
        if not value:
            return {"text": "Укажите поставщика услуг.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        _store.set_step(user_id, "rms", data={"provider_other": value})
        return {
            "text": ticket_wizard.network_rms_screen().text,
            "parse_mode": "HTML",
            "buttons": _rms_buttons(),
        }
    if session.step == "rms":
        _store.set_step(user_id, "description", data={"rms_internet_id": (text or "").strip() or "нет"})
        return {
            "text": ticket_wizard.network_description_screen().text,
            "parse_mode": "HTML",
            "buttons": _desc_buttons(),
        }
    if session.step == "description":
        _store.set_step(user_id, "attachments", data={"description": (text or "").strip(), "network_attachment_tokens": []})
        return {
            "text": ticket_wizard.network_attachments_screen(added_count=0).text,
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }
    if session.step == "attachments":
        session = _store.get(user_id)
        tokens = collect_attachments(session.data.get("network_attachment_tokens") or [], attachment_list)
        _store.update_data(user_id, network_attachment_tokens=tokens)
        if attachment_list:
            return {
                "text": ticket_wizard.network_attachments_screen(added_count=len(tokens)).text,
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {"text": "Пришлите вложение или нажмите кнопку завершения.", "parse_mode": "HTML", "buttons": _attachments_buttons()}
    return {"text": "Используйте кнопки ниже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
