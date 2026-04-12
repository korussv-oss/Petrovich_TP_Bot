"""
Обработчики MAX: вызов core.support.api и рендер через adapters.max.render.
Регистрируются в main_max при наличии MAX SDK.
Все callback_id из главного меню и подменю должны быть обработаны.
"""
import logging
from typing import Optional
from core.support.api import support_api
from core.support.models import Text, Menu, Error
from adapters.max.render import menu_to_max, text_to_max, error_to_max
from user_storage import (
    get_user_profile,
    is_user_registered,
    save_user_profile,
    resolve_channel_user_id,
)

logger = logging.getLogger(__name__)
CHANNEL_ID = "max"

ADMIN_DELETE_INTRO = (
    "👤 <b>Удаление пользователя</b>\n\n"
    "Выберите способ: список всех пользователей (по 10 на страницу), поиск по части ФИО или ввод логина/ID."
)


def _admin_delete_choice_response() -> dict:
    """Экран выбора способа удаления (как в Telegram)."""
    return {
        "text": ADMIN_DELETE_INTRO,
        "parse_mode": "HTML",
        "buttons": [
            {"id": "admin_del_choice_list", "label": "📋 Список пользователей"},
            {"id": "admin_del_choice_search", "label": "🔍 Поиск по ФИО"},
            {"id": "admin_del_choice_login", "label": "✏️ Ввести логин или ID"},
            {"id": "admin_panel", "label": "🔙 Назад"},
        ],
    }


def _admin_delete_list_page_response(all_users: list, page: int, total_pages: int, per_page: int) -> dict:
    """Страница списка пользователей для удаления (до 10 кнопок + пагинация)."""
    start = page * per_page
    users_page = all_users[start : start + per_page]
    buttons = []
    for uid, profile in users_page:
        name = (profile.get("full_name") or "—").strip() or "—"
        login = (profile.get("login") or "").strip() or "—"
        label = f"{name} ({login})" if len(f"{name} ({login})") <= 40 else f"{name[:28]}… ({login})"
        buttons.append({"id": f"admin_del_uid_{uid}", "label": label})
    if page > 0:
        buttons.append({"id": f"admin_del_page_{page - 1}", "label": "◀ Назад"})
    if page < total_pages - 1:
        buttons.append({"id": f"admin_del_page_{page + 1}", "label": "Вперёд ▶"})
    buttons.append({"id": "admin_del_back_choice", "label": "🔙 К выбору способа"})
    return {
        "text": f"📋 <b>Список пользователей</b> (страница {page + 1} из {total_pages}):",
        "parse_mode": "HTML",
        "buttons": buttons,
    }


HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "Этот бот позволяет создавать заявки в техническую поддержку.\n"
    "Заявки можно отслеживать в разделе «Мои заявки».\n"
    "Если не нашли форму, которая подходит к вашей проблеме то обратитесь на первую линию 1111/8-921-888-17-61"
)

_ADMIN_DEPT_ITEMS = 8
_pending_admin_dept: dict[int, dict] = {}


def _admin_dept_build_ui(depts: list[str], page: int) -> dict:
    if not depts:
        return {
            "text": "🏢 Не удалось загрузить список подразделений. Попробуйте позже.",
            "parse_mode": "HTML",
            "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
        }
    start = page * _ADMIN_DEPT_ITEMS
    end = start + _ADMIN_DEPT_ITEMS
    chunk = depts[start:end]
    buttons: list[dict] = []
    for i, name in enumerate(chunk):
        idx = start + i
        label = (name or "")[:64] if name else str(idx)
        buttons.append({"id": f"admin_dept_{idx}", "label": label})
    if page > 0:
        buttons.append({"id": f"admin_dept_pg_{page - 1}", "label": "◀️ Назад"})
    if end < len(depts):
        buttons.append({"id": f"admin_dept_pg_{page + 1}", "label": "Вперёд ▶️"})
    buttons.append({"id": "admin_dept_cancel", "label": "❌ Отмена"})
    return {
        "text": "🏢 <b>Смена подразделения</b>\n\nВыберите новое <b>подразделение</b> (Department):",
        "parse_mode": "HTML",
        "buttons": buttons,
    }


def _admin_profile_response(user_id: int) -> dict:
    profile = get_user_profile(user_id, CHANNEL_ID) or {}

    def _v(key: str) -> str:
        val = profile.get(key)
        if val is None:
            return "—"
        s = str(val).strip()
        return s if s else "—"

    primary = resolve_channel_user_id(CHANNEL_ID, int(user_id))
    primary_line = f"\n<b>Основной ID профиля:</b> {primary}" if int(primary) != int(user_id) else ""
    text = (
        "👤 <b>Профиль</b>\n\n"
        f"<b>ФИО:</b> {_v('full_name')}\n"
        f"<b>Телефон:</b> {_v('phone')}\n"
        f"<b>MAX ID:</b> {user_id}"
        f"{primary_line}\n"
        f"<b>Логин:</b> {_v('login')}\n"
        f"<b>Email:</b> {_v('email')}\n"
        f"<b>Табельный номер:</b> {_v('employee_id')}\n"
        f"<b>Подразделение:</b> {_v('department')}\n"
        f"<b>Должность:</b> {_v('position')}\n"
        f"<b>Подразделение WMS:</b> {_v('department_wms')}\n"
        f"<b>Jira username:</b> {_v('jira_username')}\n"
    )
    return {
        "text": text,
        "parse_mode": "HTML",
        "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
    }


def admin_change_department_start_with_depts(user_id: int, depts: list[str]) -> dict:
    """MAX: старт UI смены подразделения, когда список уже получен асинхронно."""
    _pending_admin_dept[int(user_id)] = {"dept_list": list(depts or []), "page": 0}
    return _admin_dept_build_ui(_pending_admin_dept[int(user_id)]["dept_list"], 0)


def _tp_root_menu(user_id: int) -> dict:
    result = support_api.get_tp_root_menu(CHANNEL_ID, user_id)
    if isinstance(result, Menu):
        return menu_to_max(result)
    if isinstance(result, Error):
        return error_to_max(result)
    return {"text": str(result)}


def _tp_programs_menu(user_id: int) -> dict:
    result = support_api.get_tp_programs_menu(CHANNEL_ID, user_id)
    if isinstance(result, Menu):
        return menu_to_max(result)
    if isinstance(result, Error):
        return error_to_max(result)
    return {"text": str(result)}


def _tp_equipment_menu(user_id: int) -> dict:
    result = support_api.get_tp_equipment_menu(CHANNEL_ID, user_id)
    if isinstance(result, Menu):
        return menu_to_max(result)
    if isinstance(result, Error):
        return error_to_max(result)
    return {"text": str(result)}


def _tp_services_menu(user_id: int) -> dict:
    result = support_api.get_tp_services_menu(CHANNEL_ID, user_id)
    if isinstance(result, Menu):
        return menu_to_max(result)
    if isinstance(result, Error):
        return error_to_max(result)
    return {"text": str(result)}


def _tp_access_accounts_menu(user_id: int) -> dict:
    result = support_api.get_tp_access_accounts_menu(CHANNEL_ID, user_id)
    if isinstance(result, Menu):
        return menu_to_max(result)
    if isinstance(result, Error):
        return error_to_max(result)
    return {"text": str(result)}


def handle_start(user_id: int) -> dict:
    """Обработка /start: приветствие или главное меню. user_id — id в канале MAX."""
    result = support_api.get_start(CHANNEL_ID, user_id)
    if isinstance(result, Menu):
        return menu_to_max(result)
    if isinstance(result, Text):
        return text_to_max(result)
    if isinstance(result, Error):
        return error_to_max(result)
    return {"text": str(result)}


def handle_main_menu(user_id: int) -> dict:
    """Главное меню по (channel_id, user_id)."""
    result = support_api.get_main_menu(CHANNEL_ID, user_id)
    if isinstance(result, Menu):
        return menu_to_max(result)
    if isinstance(result, Error):
        return error_to_max(result)
    return {"text": str(result)}


def handle_callback(callback_id: str, user_id: int, my_tickets: Optional[list] = None) -> Optional[dict]:
    """
    Обработка нажатия кнопки (callback_id).
    Все кнопки главного меню и подменю должны быть обработаны.
    """
    back_btn = [{"id": "back_to_main", "label": "🔙 В главное меню"}]

    if callback_id == "back_to_main":
        return handle_main_menu(user_id)
    if callback_id == "bind_account":
        return {
            "text": (
                "🔗 <b>Привязать аккаунт</b>\n\n"
                "Нажмите кнопку ниже, чтобы поделиться контактом. "
                "Если этот номер уже зарегистрирован в системе, аккаунт будет привязан."
            ),
            "parse_mode": "HTML",
            "buttons": [
                {"type": "request_contact", "label": "📱 Поделиться контактом"},
                {"id": "back_to_main", "label": "◀️ Отмена"},
            ],
        }
    if callback_id == "start_registration":
        # Обрабатывается в main_max: задаётся состояние и первый шаг (email)
        return None
    if callback_id in ("sa_stc_menu", "sa_stc_my_tasks"):
        # Обрабатывается в main_max (асинхронные запросы к Jira/реестру).
        return None
    if callback_id and (
        callback_id.startswith("stc_open_issue:")
        or callback_id.startswith("stc_set_status:")
        or callback_id.startswith("stc_apply_status:")
        or callback_id.startswith("stc_ask_timespent:")
        or callback_id.startswith("stc_apply_status_ts:")
        or callback_id.startswith("stc_open_jira:")
    ):
        return None
    if callback_id == "pc_issue_start":
        # Обрабатывается в main_max: пошаговый сценарий заявки «Проблема в работе ПК»
        return None
    if callback_id == "orgtech_issue_start":
        # Обрабатывается в main_max: пошаговый сценарий заявки «Оргтехника»
        return None
    if callback_id == "peripheral_issue_start":
        # Обрабатывается в main_max: пошаговый сценарий заявки «Периферийное оборудование»
        return None
    if callback_id == "network_issue_start":
        # Обрабатывается в main_max: пошаговый сценарий заявки «Проблемы в работе сети»
        return None
    if callback_id == "electronic_queue_start":
        # Обрабатывается в main_max: пошаговый сценарий заявки «Электронная очередь»
        return None
    if callback_id == "tp_section_wms":
        return None
    if callback_id == "tp_section_site":
        return None
    if callback_id == "tp_section_email":
        return {
            "text": "📧 <b>Электронная почта</b>\n\nВыберите направление:",
            "parse_mode": "HTML",
            "buttons": [
                {"id": "tp_email_owa_outlook", "label": "📨 Электронная почта (Owa\\Outlook)"},
                {"id": "tp_email_groups", "label": "👥 Группы рассылки"},
                {"id": "tp_email_forwarding", "label": "↪️ Настройка переадресации"},
                {"id": "tp_group_programs", "label": "⬅️ Назад"},
                {"id": "back_to_main", "label": "🔙 В главное меню"},
            ],
        }
    if callback_id == "tp_email_owa_outlook":
        return None
    if callback_id == "tp_email_groups":
        # Обрабатывается в main_max: пошаговый сценарий заявки «Группы рассылки»
        return None
    if callback_id == "tp_email_forwarding":
        # Обрабатывается в main_max: пошаговый сценарий заявки «Настройка переадресации»
        return None
    if callback_id == "tp_access_kb_chatbot":
        return None
    if callback_id in ("aa_kb_edit_create", "aa_kb_edit_edit", "aa_kb_restart_flow"):
        return None
    if callback_id in ("aa_mail_edit_create", "aa_mail_edit_edit", "aa_mail_restart_flow"):
        return None
    if callback_id == "tp_access_mail_browser":
        return None
    if callback_id == "tp_access_pc_account":
        return None
    if callback_id in (
        "aa_pc_action_copy",
        "aa_pc_action_unlock",
        "aa_pc_action_group",
        "aa_pc_restart_flow",
    ):
        return None
    # Главное меню (для зарегистрированных)
    if callback_id == "create_ticket_tp":
        return _tp_root_menu(user_id)
    if callback_id == "tp_group_access":
        return _tp_access_accounts_menu(user_id)
    if callback_id == "tp_group_programs":
        return _tp_programs_menu(user_id)
    if callback_id == "tp_group_equipment":
        return _tp_equipment_menu(user_id)
    if callback_id == "tp_group_services":
        return _tp_services_menu(user_id)
    if callback_id == "help":
        if not is_user_registered(user_id, CHANNEL_ID):
            return {"text": "Сначала пройдите регистрацию или привяжите аккаунт.", "parse_mode": "HTML", "buttons": back_btn}
        return {"text": HELP_TEXT, "parse_mode": "HTML", "buttons": back_btn}
    # Админ-панель
    if callback_id == "admin_panel":
        result = support_api.get_admin_panel(CHANNEL_ID, user_id)
        if isinstance(result, Menu):
            return menu_to_max(result)
        if isinstance(result, Error):
            return error_to_max(result)
    if callback_id == "admin_profile":
        from config import is_channel_admin
        if not is_channel_admin(CHANNEL_ID, user_id):
            return {"text": "Нет прав доступа.", "parse_mode": "HTML", "buttons": back_btn}
        return _admin_profile_response(user_id)
    if callback_id == "admin_change_department":
        from config import is_channel_admin
        if not is_channel_admin(CHANNEL_ID, user_id):
            return {"text": "Нет прав доступа.", "parse_mode": "HTML", "buttons": back_btn}
        from core.jira_departments import get_departments
        depts = get_departments() or []
        _pending_admin_dept[int(user_id)] = {"dept_list": list(depts), "page": 0}
        return _admin_dept_build_ui(_pending_admin_dept[int(user_id)]["dept_list"], 0)
    if callback_id == "admin_dept_cancel":
        _pending_admin_dept.pop(int(user_id), None)
        return {"text": "❌ Смена подразделения отменена.", "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}]}
    if callback_id and callback_id.startswith("admin_dept_pg_"):
        st = _pending_admin_dept.get(int(user_id)) or {}
        depts = list(st.get("dept_list") or [])
        try:
            page = int(callback_id.replace("admin_dept_pg_", "", 1))
        except ValueError:
            page = 0
        if page < 0:
            page = 0
        st["page"] = page
        _pending_admin_dept[int(user_id)] = st
        return _admin_dept_build_ui(depts, page)
    if callback_id and callback_id.startswith("admin_dept_"):
        st = _pending_admin_dept.get(int(user_id))
        if not st:
            return {"text": "⚠️ Сессия устарела. Откройте «Сменить подразделение» ещё раз.", "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}]}
        depts = list(st.get("dept_list") or [])
        raw = callback_id.replace("admin_dept_", "", 1)
        if not raw.isdigit():
            return None
        idx = int(raw)
        if idx < 0 or idx >= len(depts):
            return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}]}
        selected = depts[idx]
        primary = resolve_channel_user_id(CHANNEL_ID, int(user_id))
        old = get_user_profile(int(primary), "telegram") if int(primary) != int(user_id) else get_user_profile(int(primary), CHANNEL_ID)
        old = old or {}
        profile = dict(old)
        profile["department"] = selected
        save_user_profile(int(primary), profile, old_profile=old)
        _pending_admin_dept.pop(int(user_id), None)
        return {
            "text": f"✅ Подразделение обновлено: <b>{selected}</b>",
            "parse_mode": "HTML",
            "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
        }
    if callback_id == "admin_delete_user":
        from config import is_channel_admin
        if not is_channel_admin(CHANNEL_ID, user_id):
            return {"text": "Нет прав доступа.", "parse_mode": "HTML", "buttons": back_btn}
        return _admin_delete_choice_response()
    if callback_id == "admin_del_back_choice" or callback_id == "admin_del_cancel":
        from config import is_channel_admin
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        return _admin_delete_choice_response()
    if callback_id == "admin_del_choice_list":
        from config import is_channel_admin
        from user_storage import get_all_users_sorted
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        all_users = get_all_users_sorted()
        if not all_users:
            return {
                "text": "Нет зарегистрированных пользователей.",
                "parse_mode": "HTML",
                "buttons": [{"id": "admin_del_back_choice", "label": "🔙 К выбору способа"}],
            }
        per_page = 10
        total_pages = max(1, (len(all_users) + per_page - 1) // per_page)
        return _admin_delete_list_page_response(all_users, 0, total_pages, per_page)
    if callback_id and callback_id.startswith("admin_del_page_"):
        try:
            page = int(callback_id.replace("admin_del_page_", "").strip())
        except ValueError:
            return handle_main_menu(user_id)
        from config import is_channel_admin
        from user_storage import get_all_users_sorted
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        all_users = get_all_users_sorted()
        per_page = 10
        total_pages = max(1, (len(all_users) + per_page - 1) // per_page)
        if page < 0 or page >= total_pages:
            return _admin_delete_choice_response()
        return _admin_delete_list_page_response(all_users, page, total_pages, per_page)
    if callback_id and callback_id.startswith("admin_del_uid_"):
        try:
            uid = int(callback_id.replace("admin_del_uid_", "").strip())
        except ValueError:
            return handle_main_menu(user_id)
        from config import is_channel_admin
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        profile = get_user_profile(uid)
        if not profile:
            return {"text": "Пользователь не найден в базе.", "parse_mode": "HTML", "buttons": [{"id": "admin_del_back_choice", "label": "🔙 К выбору способа"}]}
        name = profile.get("full_name", "—") or "—"
        login = profile.get("login", "—") or "—"
        return {
            "text": f"Удалить пользователя?\n\n<b>{name}</b>\nЛогин: {login}\nID: {uid}",
            "parse_mode": "HTML",
            "buttons": [
                {"id": f"admin_del_confirm_{uid}", "label": "✅ Удалить"},
                {"id": "admin_del_cancel", "label": "❌ Отмена"},
            ],
        }
    if callback_id and callback_id.startswith("admin_del_confirm_"):
        try:
            uid = int(callback_id.replace("admin_del_confirm_", "").strip())
        except ValueError:
            return handle_main_menu(user_id)
        from config import is_channel_admin
        from user_storage import delete_user
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        profile = get_user_profile(uid)
        deleted = delete_user(uid)
        if deleted:
            text = f"✅ Пользователь удалён: {profile.get('full_name', '—')} ({profile.get('login', '—')}, ID {uid})."
            logger.info("MAX админ %s удалил пользователя %s", user_id, uid)
        else:
            text = "Не удалось удалить пользователя."
        return {"text": text, "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}]}
    if callback_id == "admin_del_choice_search":
        from config import is_channel_admin
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        return {
            "text": "🔍 <b>Поиск по ФИО</b>\n\nВведите часть фамилии, имени или отчества:",
            "parse_mode": "HTML",
            "buttons": [{"id": "admin_del_back_choice", "label": "🔙 К выбору способа"}],
            "_set_pending_admin_search": True,
        }
    if callback_id == "admin_del_choice_login":
        from config import is_channel_admin
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        return {
            "text": "✏️ <b>Ввод логина или ID</b>\n\nВведите Telegram ID (число) или рабочий логин (например i.ivanov):",
            "parse_mode": "HTML",
            "buttons": [{"id": "admin_del_back_choice", "label": "🔙 К выбору способа"}],
            "_set_pending_admin_delete": True,
        }
    if callback_id == "admin_ticket_counter":
        from config import is_channel_admin
        from core.admin_ticket_report import get_total_created_tickets_count
        if not is_channel_admin(CHANNEL_ID, user_id):
            return handle_main_menu(user_id)
        cnt = get_total_created_tickets_count()
        return {
            "text": f"Через бот создано <b>{cnt}</b> заявок.",
            "parse_mode": "HTML",
            "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
        }
    if callback_id == "admin_detailed_report":
        # Асинхронная выгрузка файла обрабатывается в main_max.py
        return None
    # Отчёт Лупа (Excel): в MAX файл не отправляется — предлагаем скачать в Telegram
    if callback_id == "admin_lupa_excel_report":
        from config import is_lupa_report_allowed
        if not is_lupa_report_allowed(CHANNEL_ID, user_id):
            result = support_api.get_main_menu(CHANNEL_ID, user_id)
            if isinstance(result, Menu):
                return menu_to_max(result)
            return {"text": "Нет доступа.", "parse_mode": "HTML", "buttons": back_btn}
        return {
            "text": "📊 Отчёт по заявкам Лупа (поиск на сайте) формируется автоматически. Скачать Excel-файл можно в Telegram-боте: Админ-панель → Отчёт Лупа (Excel).",
            "parse_mode": "HTML",
            "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
        }
    # Мои заявки — список со ссылками на Jira (без статусов Отклонена/Выполнена/Resolved)
    if callback_id == "my_tickets":
        if not is_user_registered(user_id, CHANNEL_ID):
            return {"text": "Сначала пройдите регистрацию или привяжите аккаунт.", "parse_mode": "HTML", "buttons": back_btn}
        tickets = my_tickets if my_tickets is not None else support_api.get_my_tickets(CHANNEL_ID, user_id)
        if not tickets:
            return {"text": "📋 Мои заявки\n\nУ вас пока нет заявок.", "parse_mode": "HTML", "buttons": back_btn}
        lines = []
        for t in tickets:
            issue_key = t.get("issue_key") or "—"
            req_label = (t.get("request_type_label") or "").strip()
            tail = f" {req_label}" if req_label else ""
            url = t.get("customer_request_url") or ""
            if url and issue_key != "—":
                lines.append(f'• <a href="{url}">{issue_key}</a>{tail}')
            else:
                lines.append(f"• {issue_key}{tail}")
        text = "📋 <b>Мои заявки</b>\n\n" + "\n".join(lines) + "\n\nВыберите заявку (или откройте по ссылке):"
        buttons = [{"id": f"open_issue:{t.get('issue_key')}", "label": t.get("issue_key") or "—"} for t in tickets]
        buttons.append({"id": "back_to_main", "label": "🔙 В главное меню"})
        return {"text": text, "parse_mode": "HTML", "buttons": buttons}
    # Выбор типа заявки (id из каталога). ticket_wms_issue и ticket_lupa_search обрабатываются в main_max.
    if callback_id and callback_id.startswith("ticket_"):
        ticket_type_id = callback_id[7:].strip()
        if ticket_type_id == "wms_issue":
            return None  # обрабатывается в main_max (wms_flow.start_wms)
        if ticket_type_id == "lupa_search":
            return None  # обрабатывается в main_max (lupa_flow.start_lupa)
        if ticket_type_id:
            return {
                "text": (
                    f"Создание заявки «{ticket_type_id}» в MAX пока не поддерживается. "
                    "Откройте бота в Telegram для создания заявки."
                ),
                "parse_mode": "HTML",
                "buttons": back_btn,
            }
    return None
