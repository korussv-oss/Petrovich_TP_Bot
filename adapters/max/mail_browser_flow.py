"""Пошаговый сценарий MAX: «Доступ к корпоративной почте через браузер» (AA, aa_mail_browser)."""
from __future__ import annotations

from typing import Optional

from user_storage import is_user_registered, get_user_profile
from adapters.max._wizard_flow import WizardFlowStore

CHANNEL_ID = "max"

_store = WizardFlowStore()


def is_in_mail_browser_flow(user_id: int) -> bool:
    return _store.has(user_id)


def _buttons_back_restart() -> list:
    return [{"id": "aa_mail_restart_flow", "label": "⬅️ К выбору действия"}, {"id": "cancel", "label": "❌ Отмена"}]


def _edit_type_buttons(create_l: str, edit_l: str) -> list:
    return [
        {"id": "aa_mail_edit_create", "label": create_l[:64]},
        {"id": "aa_mail_edit_edit", "label": edit_l[:64]},
        {"id": "tp_group_access", "label": "⬅️ Назад"},
    ]


async def start_mail_browser(user_id: int) -> Optional[dict]:
    if not is_user_registered(user_id, CHANNEL_ID):
        return None
    from config import CONFIG

    mb = CONFIG.get("JIRA_AA_MAIL_BROWSER") or {}
    if not (mb.get("REQUEST_TYPE_ID") or "").strip():
        return {
            "text": (
                "🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n"
                "Сценарий не настроен: укажите <code>JIRA_AA_MAIL_BROWSER_REQUEST_TYPE_ID</code> "
                "или <code>JIRA_AA_KB_CHATBOT_REQUEST_TYPE_ID</code> в .env."
            ),
            "parse_mode": "HTML",
            "buttons": [{"id": "tp_group_access", "label": "⬅️ Назад"}],
        }

    profile = get_user_profile(user_id, CHANNEL_ID) or {}
    if not (profile.get("department") or "").strip():
        return {
            "text": (
                "🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n"
                "В профиле не указано подразделение (Department). Сначала заполните его в другом сценарии."
            ),
            "parse_mode": "HTML",
            "buttons": [{"id": "tp_group_access", "label": "⬅️ Назад"}],
        }
    if not (profile.get("login") or "").strip() or not (profile.get("full_name") or "").strip():
        return {
            "text": (
                "🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n"
                "В профиле не хватает ФИО или рабочего логина."
            ),
            "parse_mode": "HTML",
            "buttons": [{"id": "tp_group_access", "label": "⬅️ Назад"}],
        }

    kb = CONFIG.get("JIRA_AA_KB_CHATBOT") or {}
    create_l = (mb.get("EDIT_OPTION_CREATE") or kb.get("EDIT_OPTION_CREATE") or "Создать").strip()
    edit_l = (mb.get("EDIT_OPTION_EDIT") or kb.get("EDIT_OPTION_EDIT") or "Редактировать").strip()
    _store.create(user_id, ticket_type_id="aa_mail_browser", step="edit_type")
    return {
        "text": (
            "🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n"
            "Выберите значение поля <b>AA Edit Type</b> (Создать или Редактировать):"
        ),
        "parse_mode": "HTML",
        "buttons": _edit_type_buttons(create_l, edit_l),
    }


async def handle_mail_browser_callback(user_id: int, callback_id: str) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    from config import CONFIG

    mb = CONFIG.get("JIRA_AA_MAIL_BROWSER") or {}
    kb = CONFIG.get("JIRA_AA_KB_CHATBOT") or {}
    create_l = (mb.get("EDIT_OPTION_CREATE") or kb.get("EDIT_OPTION_CREATE") or "Создать").strip()
    edit_l = (mb.get("EDIT_OPTION_EDIT") or kb.get("EDIT_OPTION_EDIT") or "Редактировать").strip()

    if callback_id == "cancel":
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu

        return handle_main_menu(user_id)

    if callback_id == "aa_mail_restart_flow":
        _store.set_step(user_id, "edit_type", data={}, merge=False)
        return {
            "text": (
                "🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n"
                "Выберите значение поля <b>AA Edit Type</b> (Создать или Редактировать):"
            ),
            "parse_mode": "HTML",
            "buttons": _edit_type_buttons(create_l, edit_l),
        }

    if session.step == "edit_type" and callback_id in ("aa_mail_edit_create", "aa_mail_edit_edit"):
        label = create_l if callback_id == "aa_mail_edit_create" else edit_l
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        pos = (profile.get("position") or "").strip()
        data = {"aa_mail_edit_type": label}
        if pos:
            data["aa_mail_position"] = pos
            phone = (profile.get("phone") or "").strip()
            if phone:
                form_data = {
                    "aa_edit_type": label,
                    "position": pos,
                    "existing_phone": phone,
                }
                _store.clear(user_id)
                return {"create_ticket": {"ticket_type_id": "aa_mail_browser", "form_data": form_data}}
            _store.set_step(user_id, "phone", data=data)
            return {
                "text": (
                    f"🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n✅ Действие: <b>{label}</b>\n\n"
                    "Укажите <b>номер телефона</b> (Existing phone number). "
                    "Он будет сохранён в профиле после создания заявки:"
                ),
                "parse_mode": "HTML",
                "buttons": _buttons_back_restart(),
            }
        _store.set_step(user_id, "position", data=data)
        return {
            "text": (
                f"🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n✅ Действие: <b>{label}</b>\n\n"
                "Укажите <b>должность (Position)</b> одним сообщением:"
            ),
            "parse_mode": "HTML",
            "buttons": _buttons_back_restart(),
        }

    return None


async def handle_mail_browser_message(user_id: int, text: str, attachment_list: list | None = None) -> Optional[dict]:
    session = _store.get(user_id)
    if not session:
        return None
    t = (text or "").strip()
    if t.lower() in ("отмена", "cancel", "/cancel"):
        _store.clear(user_id)
        from adapters.max.handlers import handle_main_menu

        return handle_main_menu(user_id)

    if session.step == "position":
        if len(t) < 2:
            return {"text": "Введите должность (не короче 2 символов).", "parse_mode": "HTML", "buttons": _buttons_back_restart()}
        _store.update_data(user_id, aa_mail_position=t)
        profile = get_user_profile(user_id, CHANNEL_ID) or {}
        prof_phone = (profile.get("phone") or "").strip()
        if prof_phone:
            sess = _store.get(user_id)
            d = dict(sess.data or {}) if sess else {}
            form_data = {
                "aa_edit_type": (d.get("aa_mail_edit_type") or "").strip(),
                "position": t,
                "existing_phone": prof_phone,
            }
            _store.clear(user_id)
            return {"create_ticket": {"ticket_type_id": "aa_mail_browser", "form_data": form_data}}
        _store.set_step(user_id, "phone")
        return {
            "text": (
                "🔐 <b>Доступ к корпоративной почте через браузер</b>\n\n"
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
        _store.update_data(user_id, aa_mail_phone=t)
        sess = _store.get(user_id)
        data = dict(sess.data or {}) if sess else {}
        form_data = {
            "aa_edit_type": (data.get("aa_mail_edit_type") or "").strip(),
            "position": (data.get("aa_mail_position") or "").strip(),
            "existing_phone": t,
        }
        _store.clear(user_id)
        return {"create_ticket": {"ticket_type_id": "aa_mail_browser", "form_data": form_data}}

    return None
