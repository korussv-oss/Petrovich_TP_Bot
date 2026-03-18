"""Пошаговое создание заявки «Проблемы в работе сети» в MAX."""
from typing import Optional

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

CHANNEL_ID = "max"
CANCEL_BTN = [{"id": "cancel", "label": "❌ Отмена"}]
_flow: dict[int, dict] = {}


def is_in_network_flow(user_id: int) -> bool:
    return user_id in _flow


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
    _flow[user_id] = {"step": "network_type", "data": {}}
    return {
        "text": "🌐 <b>Проблемы в работе сети</b>\n\nВыберите тип проблемной сети:",
        "parse_mode": "HTML",
        "buttons": _buttons(NETWORK_TYPES, "network_type_"),
    }


async def handle_network_callback(user_id: int, callback_id: str) -> Optional[dict]:
    state = _flow.get(user_id)
    if not state:
        return None
    if callback_id == "cancel":
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    step = state.get("step")
    data = state.get("data") or {}

    if step == "network_type" and callback_id.startswith("network_type_"):
        t_id = callback_id.replace("network_type_", "", 1).strip()
        t_label = NETWORK_TYPE_BY_ID.get(t_id)
        if not t_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_TYPES, "network_type_")}
        data.update({"network_type": t_label, "provider": "", "provider_other": "", "wifi_problem_owner": "", "pc_type": ""})
        state["data"] = data
        if t_label == "Wi-Fi (беспроводная)":
            state["step"] = "wifi_owner"
            return {
                "text": (
                    "🌐 <b>Проблемы в работе сети</b>\n\n"
                    f"✅ Тип сети: {t_label}\n\n"
                    "Укажите, у кого проблемы:"
                ),
                "parse_mode": "HTML",
                "buttons": _buttons(NETWORK_WIFI_OWNERS, "network_wifi_owner_"),
            }
        if t_label == "VPN":
            state["step"] = "pc_type"
            return {
                "text": (
                    "🌐 <b>Проблемы в работе сети</b>\n\n"
                    f"✅ Тип сети: {t_label}\n\n"
                    "Выберите тип ПК:"
                ),
                "parse_mode": "HTML",
                "buttons": _buttons(NETWORK_PC_TYPES, "network_pc_type_"),
            }
        state["step"] = "provider"
        return {
            "text": (
                "🌐 <b>Проблемы в работе сети</b>\n\n"
                f"✅ Тип сети: {t_label}\n\n"
                "Выберите провайдера:"
            ),
            "parse_mode": "HTML",
            "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_"),
        }

    if step == "wifi_owner" and callback_id.startswith("network_wifi_owner_"):
        o_id = callback_id.replace("network_wifi_owner_", "", 1).strip()
        o_label = NETWORK_WIFI_OWNER_BY_ID.get(o_id)
        if not o_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_WIFI_OWNERS, "network_wifi_owner_")}
        data["wifi_problem_owner"] = o_label
        state["data"] = data
        state["step"] = "rms"
        return {"text": "Укажите RMS Internet ID (опционально) или нажмите «Пропустить».", "parse_mode": "HTML", "buttons": _rms_buttons()}

    if step == "pc_type" and callback_id.startswith("network_pc_type_"):
        p_id = callback_id.replace("network_pc_type_", "", 1).strip()
        p_label = NETWORK_PC_TYPE_BY_ID.get(p_id)
        if not p_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PC_TYPES, "network_pc_type_")}
        data["pc_type"] = p_label
        state["data"] = data
        state["step"] = "provider"
        return {"text": "Выберите провайдера:", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_")}

    if step == "provider" and callback_id.startswith("network_provider_"):
        pr_id = callback_id.replace("network_provider_", "", 1).strip()
        pr_label = NETWORK_PROVIDER_BY_ID.get(pr_id)
        if not pr_label:
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_")}
        data["provider"] = pr_label
        state["data"] = data
        if pr_label == "Другой":
            state["step"] = "provider_other"
            return {"text": "Укажите название поставщика услуг (поле Other):", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["provider_other"] = ""
        state["step"] = "rms"
        return {"text": "Укажите RMS Internet ID (опционально) или нажмите «Пропустить».", "parse_mode": "HTML", "buttons": _rms_buttons()}

    if step == "rms" and callback_id == "network_skip_rms":
        data["rms_internet_id"] = "нет"
        state["data"] = data
        state["step"] = "description"
        return {"text": "Опишите проблему (Description) или нажмите «Пропустить».", "parse_mode": "HTML", "buttons": _desc_buttons()}

    if step == "description" and callback_id == "network_skip_description":
        data["description"] = ""
        data["network_attachment_tokens"] = []
        state["data"] = data
        state["step"] = "attachments"
        return {
            "text": "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).",
            "parse_mode": "HTML",
            "buttons": _attachments_buttons(),
        }

    if step == "attachments" and callback_id in ("network_finish_ticket", "network_skip_attachments"):
        if callback_id == "network_skip_attachments":
            data["network_attachment_tokens"] = []
        d = dict(data)
        attachments = list(d.get("network_attachment_tokens") or [])
        _flow.pop(user_id, None)
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
    state = _flow.get(user_id)
    if not state:
        return None
    step = state.get("step")
    data = state.get("data") or {}

    if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        _flow.pop(user_id, None)
        from adapters.max.handlers import handle_main_menu
        return handle_main_menu(user_id)

    if step == "network_type":
        return {"text": "Выберите тип сети кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_TYPES, "network_type_")}
    if step == "wifi_owner":
        return {"text": "Выберите вариант кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_WIFI_OWNERS, "network_wifi_owner_")}
    if step == "pc_type":
        return {"text": "Выберите тип ПК кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PC_TYPES, "network_pc_type_")}
    if step == "provider":
        return {"text": "Выберите провайдера кнопкой ниже.", "parse_mode": "HTML", "buttons": _buttons(NETWORK_PROVIDERS, "network_provider_")}
    if step == "provider_other":
        value = (text or "").strip()
        if not value:
            return {"text": "Укажите поставщика услуг.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
        data["provider_other"] = value
        state["data"] = data
        state["step"] = "rms"
        return {"text": "Укажите RMS Internet ID (опционально) или нажмите «Пропустить».", "parse_mode": "HTML", "buttons": _rms_buttons()}
    if step == "rms":
        data["rms_internet_id"] = (text or "").strip() or "нет"
        state["data"] = data
        state["step"] = "description"
        return {"text": "Опишите проблему (Description) или нажмите «Пропустить».", "parse_mode": "HTML", "buttons": _desc_buttons()}
    if step == "description":
        data["description"] = (text or "").strip()
        data["network_attachment_tokens"] = []
        state["data"] = data
        state["step"] = "attachments"
        return {"text": "📎 Приложите вложения или завершите создание заявки.", "parse_mode": "HTML", "buttons": _attachments_buttons()}
    if step == "attachments":
        tokens = list(data.get("network_attachment_tokens") or [])
        for att in (attachment_list or []):
            if not isinstance(att, dict):
                continue
            if len(tokens) >= 10:
                break
            if att.get("url"):
                tokens.append(att)
        data["network_attachment_tokens"] = tokens
        state["data"] = data
        if attachment_list:
            return {
                "text": f"📎 Добавлено {len(tokens)} из 10. Можно приложить ещё или завершить создание заявки.",
                "parse_mode": "HTML",
                "buttons": _attachments_buttons(),
            }
        return {"text": "Пришлите вложение или нажмите кнопку завершения.", "parse_mode": "HTML", "buttons": _attachments_buttons()}
    return {"text": "Используйте кнопки ниже.", "parse_mode": "HTML", "buttons": CANCEL_BTN}
