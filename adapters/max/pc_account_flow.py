"""Пошаговый сценарий MAX: «Учетная запись для входа на ПК» (AA, aa_pc_account)."""
from __future__ import annotations

from typing import Optional

from user_storage import is_user_registered, get_user_profile
from adapters.max._wizard_flow import WizardFlowStore

CHANNEL_ID = "max"

_store = WizardFlowStore()


def is_in_pc_account_flow(user_id: int) -> bool:
    return _store.has(user_id)


def _buttons_back_restart() -> list:
    return [{"id": "aa_pc_restart_flow", "label": "⬅️ К выбору действия"}, {"id": "cancel", "label": "❌ Отмена"}]


def _action_buttons(copy_l: str, unlock_l: str, group_l: str) -> list:
    return [
        {"id": "aa_pc_action_copy", "label": copy_l[:64]},
        {"id": "aa_pc_action_unlock", "label": unlock_l[:64]},
        {"id": "aa_pc_action_group", "label": group_l[:64]},
        {"id": "tp_group_access", "label": "⬅️ Назад"},
    ]


def _form_data_from_session(data: dict, *, position: str, phone: str) -> dict:
    fd = {
        "aa_ad_edit_type": (data.get("aa_pc_ad_edit_type") or "").strip(),
        "position": (position or "").strip(),
        "copy_rights_source": (data.get("aa_pc_copy_source") or "").strip(),
        "security_group_name": (data.get("aa_pc_security_group") or "").strip(),
        "existing_phone": (phone or "").strip(),
    }
    return fd


def _goto_position_or_ticket(user_id: int, data: dict) -> dict:
    profile = get_user_profile(user_id, CHANNEL_ID) or {}
    pos = (profile.get("position") or "").strip()
    if pos:
        d = dict(data)
        d["aa_pc_position"] = pos
        phone = (profile.get("phone") or "").strip()
        if phone:
            _store.clear(user_id)
            return {
                "create_ticket": {
                    "ticket_type_id": "aa_pc_account",
                    "form_data": _form_data_from_session(d, position=pos, phone=phone),
                }
            }
        _store.set_step(user_id, "phone", data=d)
        label = (d.get("aa_pc_ad_edit_type") or "").strip()
        return {
            "text": (
                f"🖥️ <b>Учетная запись для входа на ПК</b>\n\n✅ Требуемое действие: <b>{label}</b>\n\n"
                "Укажите <b>номер телефона</b> (Existing phone number). "
                "Он будет сохранён в профиле после создания заявки:"
            ),
            "parse_mode": "HTML",
            "buttons": _buttons_back_restart(),
        }
    _store.set_step(user_id, "position", data=data)
    label = (data.get("aa_pc_ad_edit_type") or "").strip()
    return {
        "text": (
            f"🖥️ <b>Учетная запись для входа на ПК</b>\n\n✅ Требуемое действие: <b>{label}</b>\n\n"
            "Укажите <b>должность (Position)</b> одним сообщением:"
        ),
        "parse_mode": "HTML",
        "buttons": _buttons_back_restart(),
    }


async def start_pc_account(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    from config import CONFIG

    pc = CONFIG.get("JIRA_AA_PC_ACCOUNT") or {}
    if not (pc.get("REQUEST_TYPE_ID") or "").strip():
        return {
            "text": (
                "🖥️ <b>Учетная запись для входа на ПК</b>\n\n"
                "Сценарий не настроен: укажите <code>JIRA_AA_PC_ACCOUNT_REQUEST_TYPE_ID</code> в .env."
            ),
            "parse_mode": "HTML",
            "buttons": [{"id": "tp_group_access", "label": "⬅️ Назад"}],
        }

    profile = get_user_profile(user_id, CHANNEL_ID) or {}
    if not (profile.get("department") or "").strip():
        return {
            "text": (
                "🖥️ <b>Учетная запись для входа на ПК</b>\n\n"
                "В профиле не указано подразделение (Department). Сначала заполните его в другом сценарии."
            ),
            "parse_mode": "HTML",
            "buttons": [{"id": "tp_group_access", "label": "⬅️ Назад"}],
        }
    if not (profile.get("login") or "").strip() or not (profile.get("full_name") or "").strip():
        return {
            "text": (
                "🖥️ <b>Учетная запись для входа на ПК</b>\n\n"
                "В профиле не хватает ФИО или рабочего логина."
            ),
            "parse_mode": "HTML",
            "buttons": [{"id": "tp_group_access", "label": "⬅️ Назад"}],
        }

    copy_l = (pc.get("ACTION_COPY") or "Копировать права").strip()
    unlock_l = (pc.get("ACTION_UNLOCK") or "Разблокировать учетную запись").strip()
    group_l = (pc.get("ACTION_GROUP") or "Дать доступ к группе безопасности").strip()
    _store.create(user_id, ticket_type_id="aa_pc_account", step="action")
    return {
        "text": (
            "🖥️ <b>Учетная запись для входа на ПК</b>\n\n"
            "Выберите <b>требуемое действие</b> (поле AA AD Edit type):"
        ),
        "parse_mode": "HTML",
        "buttons": _action_buttons(copy_l, unlock_l, group_l),
    }


async def handle_pc_account_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    from config import CONFIG

    pc = CONFIG.get("JIRA_AA_PC_ACCOUNT") or {}
    copy_l = (pc.get("ACTION_COPY") or "Копировать права").strip()
    unlock_l = (pc.get("ACTION_UNLOCK") or "Разблокировать учетную запись").strip()
    group_l = (pc.get("ACTION_GROUP") or "Дать доступ к группе безопасности").strip()

    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu

        return handle_main_menu(user_id)

    if callback_id == "aa_pc_restart_flow":
        _store.set_step(user_id, "action", data={}, merge=False)
        return {
            "text": (
                "🖥️ <b>Учетная запись для входа на ПК</b>\n\n"
                "Выберите <b>требуемое действие</b> (поле AA AD Edit type):"
            ),
            "parse_mode": "HTML",
            "buttons": _action_buttons(copy_l, unlock_l, group_l),
        }

    if session.step == "action" and callback_id in ("aa_pc_action_copy", "aa_pc_action_unlock", "aa_pc_action_group"):
        labels = {
            "aa_pc_action_copy": copy_l,
            "aa_pc_action_unlock": unlock_l,
            "aa_pc_action_group": group_l,
        }
        label = labels[callback_id]
        data = {"aa_pc_ad_edit_type": label}
        if callback_id == "aa_pc_action_unlock":
            return _goto_position_or_ticket(user_id, data)
        if callback_id == "aa_pc_action_copy":
            _store.set_step(user_id, "copy_source", data=data)
            return {
                "text": (
                    f"🖥️ <b>Учетная запись для входа на ПК</b>\n\n✅ Требуемое действие: <b>{label}</b>\n\n"
                    "Укажите <b>логин, с кого копировать права</b>, в формате <code>i.ivanov</code>:"
                ),
                "parse_mode": "HTML",
                "buttons": _buttons_back_restart(),
            }
        _store.set_step(user_id, "security_group", data=data)
        return {
            "text": (
                f"🖥️ <b>Учетная запись для входа на ПК</b>\n\n✅ Требуемое действие: <b>{label}</b>\n\n"
                "Укажите <b>имя группы безопасности AD</b> одним сообщением:"
            ),
            "parse_mode": "HTML",
            "buttons": _buttons_back_restart(),
        }

    return None


async def handle_pc_account_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    t = (text or "").strip()
    if t.lower() in ("отмена", "cancel", "/cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu

        return handle_main_menu(user_id)

    if session.step == "copy_source":
        from validators import validate_work_login

        raw = t.lower()
        ok, err = validate_work_login(raw)
        if not ok:
            return {"text": f"❗ {err}", "parse_mode": "HTML", "buttons": _buttons_back_restart()}
        sess = _store.get(user_id)
        d = dict(sess.data or {}) if sess else {}
        d["aa_pc_copy_source"] = raw
        return _goto_position_or_ticket(user_id, d)

    if session.step == "security_group":
        if len(t) < 2:
            return {"text": "Введите имя группы (не короче 2 символов).", "parse_mode": "HTML", "buttons": _buttons_back_restart()}
        sess = _store.get(user_id)
        d = dict(sess.data or {}) if sess else {}
        d["aa_pc_security_group"] = t
        return _goto_position_or_ticket(user_id, d)

    if session.step == "position":
        if len(t) < 2:
            return {"text": "Введите должность (не короче 2 символов).", "parse_mode": "HTML", "buttons": _buttons_back_restart()}
        _store.update_data(user_id, aa_pc_position=t)
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        prof_phone = (profile.get("phone") or "").strip()
        if prof_phone:
            sess = _store.get(user_id)
            d = dict(sess.data or {}) if sess else {}
            _store.clear(user_id)
            return {
                "create_ticket": {
                    "ticket_type_id": "aa_pc_account",
                    "form_data": _form_data_from_session(d, position=t, phone=prof_phone),
                }
            }
        _store.set_step(user_id, "phone")
        return {
            "text": (
                "🖥️ <b>Учетная запись для входа на ПК</b>\n\n"
                "Укажите <b>номер телефона</b> (Existing phone number):"
            ),
            "parse_mode": "HTML",
            "buttons": _buttons_back_restart(),
        }

    if session.step == "phone":
        from validators import validate_phone

        ok, err = validate_phone(t)
        if not ok:
            return {"text": f"❗ {err}", "parse_mode": "HTML", "buttons": _buttons_back_restart()}
        sess = _store.get(user_id)
        data = dict(sess.data or {}) if sess else {}
        _store.clear(user_id)
        return {
            "create_ticket": {
                "ticket_type_id": "aa_pc_account",
                "form_data": _form_data_from_session(
                    data,
                    position=(data.get("aa_pc_position") or "").strip(),
                    phone=t,
                ),
            }
        }

    return None
