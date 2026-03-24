"""
Единый API Support Core для адаптеров (Telegram, MAX).
Методы возвращают DTO (Text, Menu, Form, Error); адаптер рендерит их в канал.
"""
import logging
from typing import List, Optional

from config import CONFIG
from core.support.models import Text, Menu, MenuButton, Form, FormField, Error
from validators import sanitize_jira_text

logger = logging.getLogger(__name__)


def _channel_user_id(channel_id: str, user_id: int) -> tuple:
    """Идентификатор пользователя в канале (для реестра и профиля)."""
    return (channel_id, user_id)


# ---------------------------------------------------------------------------
# Start / главное меню
# ---------------------------------------------------------------------------

def get_start_response(channel_id: str, user_id: int) -> Text | Menu:
    """
    Ответ на /start: проверяем, знаем ли пользователя (по telegram_id или по привязке max_user_id).
    Если да — главное меню, если нет — меню регистрации (Зарегистрироваться / Привязать аккаунт).
    channel_id: "telegram" | "max"
    """
    from user_storage import is_user_registered
    from config import is_admin

    if is_user_registered(user_id, channel_id or "telegram"):
        return get_main_menu_response(channel_id, user_id)

    text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Для использования бота необходимо пройти регистрацию или привязать существующий аккаунт (по номеру телефона)."
    )
    buttons = [
        MenuButton(id="start_registration", label="✅ Зарегистрироваться"),
        MenuButton(id="bind_account", label="🔗 Привязать аккаунт"),
    ]
    return Menu(text=text, buttons=buttons)


def get_main_menu_response(channel_id: str, user_id: int) -> Menu:
    """
    Главное меню: Создать заявку в ТП, Мои заявки, Помощь (+ Админ-панель для ADMIN_IDS).
    Единый источник кнопок для Telegram и MAX. Для MAX проверка по привязке max_user_id.
    """
    from user_storage import is_user_registered
    from config import is_channel_admin, is_stc_sa

    if not is_user_registered(user_id, channel_id or "telegram"):
        return get_start_response(channel_id, user_id)

    buttons: List[MenuButton] = [
        MenuButton(id="create_ticket_tp", label="📋 Создать заявку в ТП"),
        MenuButton(id="my_tickets", label="📋 Мои заявки"),
        MenuButton(id="help", label="❓ Помощь"),
    ]
    if is_stc_sa(channel_id or "telegram", user_id):
        buttons.append(MenuButton(id="sa_stc_menu", label="🛠️ СА СТЦ"))
    from config import is_lupa_report_allowed
    if is_channel_admin(channel_id or "telegram", user_id) or is_lupa_report_allowed(channel_id or "telegram", user_id):
        buttons.append(MenuButton(id="admin_panel", label="⚙️ Админ-панель"))

    return Menu(text="Выберите действие:", buttons=buttons)


def get_admin_panel_response(channel_id: str, user_id: int) -> Menu | Error:
    """
    Ответ «Админ-панель»: подменю для администраторов канала (ADMIN_IDS в TG, ADMIN_MAX_IDS в MAX).
    Кнопка «Отчёт Лупа» — для тех, у кого есть право на отчёт (ADMIN_IDS, ADMIN_MAX_IDS или ADMIN_LUPA_IDS).
    """
    from config import is_channel_admin, is_lupa_report_allowed
    if not is_channel_admin(channel_id or "telegram", user_id) and not is_lupa_report_allowed(channel_id or "telegram", user_id):
        return Error(message="Нет прав доступа.")
    buttons: List[MenuButton] = []
    if is_channel_admin(channel_id or "telegram", user_id):
        buttons.append(MenuButton(id="admin_delete_user", label="👤 Удалить пользователя"))
        if (channel_id or "telegram").strip().lower() == "max":
            buttons.append(MenuButton(id="admin_ticket_counter", label="🔢 Счётчик заявок"))
            buttons.append(MenuButton(id="admin_detailed_report", label="📥 Подробный отчёт"))
    if is_lupa_report_allowed(channel_id or "telegram", user_id):
        buttons.append(MenuButton(id="admin_lupa_excel_report", label="📊 Отчёт Лупа (Excel)"))
    buttons.append(MenuButton(id="back_to_main", label="🔙 В главное меню"))
    return Menu(
        text="⚙️ <b>Админ-панель</b>\n\nДоступные действия для администратора.",
        buttons=buttons,
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Каталог типов заявок (меню «Создать заявку»)
# ---------------------------------------------------------------------------

def get_ticket_types_menu(channel_id: str, user_id: int) -> Menu | Error:
    """
    Меню типов заявок из каталога (для кнопки «Создать заявку»).
    Пока один тип: rubik_password_change.
    """
    from core.support.ticket_catalog import get_catalog

    catalog = get_catalog()
    if not catalog:
        return Error(message="Каталог типов заявок недоступен.")

    buttons = []
    for tid, meta in catalog.items():
        visible = meta.get("visible", True)
        if not visible:
            continue
        label = meta.get("label") or tid
        buttons.append(MenuButton(id=f"ticket_{tid}", label=label))

    # Отдельный сценарий заявки «Проблема в работе ПК» (общий для TG/MAX),
    # показываем в меню «Создать заявку в ТП».
    buttons.append(MenuButton(id="pc_issue_start", label="🖥️ Проблема в работе ПК"))

    if not buttons:
        return Menu(text="Нет доступных типов заявок.", buttons=[])

    buttons.append(MenuButton(id="back_to_main", label="🔙 В главное меню"))
    return Menu(
        text="Выберите тип заявки:",
        buttons=buttons,
    )


# ---------------------------------------------------------------------------
# Создание заявки по типу из каталога (смена пароля — первый тип)
# ---------------------------------------------------------------------------

async def create_ticket(
    channel_id: str,
    user_id: int,
    ticket_type_id: str,
    form_data: dict,
    attachment_paths: Optional[List[str]] = None,
) -> tuple[bool, str, Optional[str]]:
    """
    Создание заявки по типу из каталога.
    attachment_paths: для wms_settings обязательны (хотя бы один файл), передаются в Jira при создании запроса.
    Возвращает (успех, сообщение для пользователя, issue_key или None).
    Для wms_issue при успехе issue_key нужен для добавления вложений после создания.
    """
    if ticket_type_id == "rubik_password_change":
        new_password = (form_data.get("password_new") or form_data.get("new_password") or "").strip()
        if not new_password:
            return False, "Введите непустой пароль.", None
        from core.password import request_password_change
        ok, msg = await request_password_change(user_id, new_password, channel_id)
        return ok, msg, None

    if ticket_type_id == "wms_issue":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.jira_wms import create_wms_issue
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        summary = (form_data.get("summary") or "").strip() or "Заявка по настройке WMS"
        description = (form_data.get("description") or "").strip()
        # Процесс: в MAX приходит значение из WMS_PROCESSES, в TG тоже (кнопки). Допускаем ключ proc_* → значение.
        from core.wms_constants import WMS_PROCESSES
        process_raw = (form_data.get("process") or "").strip()
        process = WMS_PROCESSES.get(process_raw, process_raw) if process_raw else ""
        department = (form_data.get("department") or profile.get("department_wms") or profile.get("department") or "").strip()
        if not department:
            return False, "Укажите подразделение (или заполните его в учётных данных).", None
        if not process:
            return False, "Укажите процесс.", None
        # Новый путь: forms_catalog + form_engine
        engine_form_data = {
            "summary": summary,
            "description": sanitize_jira_text(
                f"Контактное лицо: {(profile.get('full_name') or '').strip() or '—'}, {(profile.get('phone') or '').strip() or '—'}\n\n{description or 'Описание не предоставлено'}",
                max_len=4000,
            ),
            "department": department,
            "process": process,
        }
        ok, result, _ = await create_issue_from_form(
            "wms_issue",
            form_data=engine_form_data,
            profile=profile,
            attachment_paths=[],
        )
        if not ok:
            # Фолбэк на legacy-реализацию WMS
            ok, result = await create_wms_issue(
                summary=summary,
                description=description,
                department=department,
                process=process,
                full_name=(profile.get("full_name") or "").strip(),
                phone=(profile.get("phone") or "").strip(),
                jira_username=(profile.get("jira_username") or "").strip() or None,
            )
        if not ok:
            return False, result, None
        add_binding(channel_id, user_id, result, "PW", "wms_issue")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            link = f'{jira_url}/browse/{result}'
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "lupa_search":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.jira_lupa import create_lupa_issue
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        description = (form_data.get("description") or "").strip()
        if not description:
            return False, "Укажите описание проблемы.", None
        employee_id = (profile.get("employee_id") or "").strip()
        if employee_id:
            description = f"Табельный номер: {employee_id}\n\n{description}"
        # Подразделение — из формы или из карточки пользователя (Department при регистрации)
        subdivision = (
            (form_data.get("subdivision") or "").strip()
            or (profile.get("department") or "").strip()
            or (profile.get("department_wms") or "").strip()
        )
        if not subdivision:
            return False, "Укажите подразделение при создании заявки (выберите из списка).", None
        engine_form_data = {
            "summary": "Ошибка в поиске на petrovich.ru",
            "description": description,
            "problematic_service": (form_data.get("problematic_service") or "Сайт (petrovich.ru)").strip(),
            "request_type": (form_data.get("request_type") or "проблемы с поиском").strip(),
            "subdivision": subdivision,
            "city": (form_data.get("city") or "").strip(),
            "service_name": "Поиск",
        }
        ok, result, _ = await create_issue_from_form(
            "lupa_search",
            form_data=engine_form_data,
            profile=profile,
            attachment_paths=[],
        )
        if not ok:
            # Фолбэк на legacy-реализацию Лупы
            ok, result = await create_lupa_issue(
                description=description,
                problematic_service=(form_data.get("problematic_service") or "Сайт (petrovich.ru)").strip(),
                request_type=(form_data.get("request_type") or "проблемы с поиском").strip(),
                subdivision=subdivision,
                city=(form_data.get("city") or "").strip(),
                jira_username=(profile.get("jira_username") or "").strip() or None,
            )
        if not ok:
            return False, result, None
        add_binding(channel_id, user_id, result, "WHD", "lupa_search")
        try:
            from core.lupa_report import log_lupa_ticket
            log_lupa_ticket(
                channel_id=channel_id,
                user_id=user_id,
                issue_key=result,
                full_name=profile.get("full_name"),
                subdivision=subdivision,
                employee_id=profile.get("employee_id"),
            )
        except Exception:
            pass
        # Ссылка на заявку в портале Service Desk (как у WMS, но portal/5 для WHD)
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        portal_id = (CONFIG.get("JIRA_LUPA", {}).get("PORTAL_ID") or "5").strip()
        if jira_url:
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "pc_problem":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "pc_problem",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "HD").strip().upper()
        add_binding(channel_id, user_id, result, proj, "pc_problem")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("pc_problem") or {}).get("portal_id") or "1")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "orgtech_problem":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "orgtech_problem",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "HD").strip().upper()
        add_binding(channel_id, user_id, result, proj, "orgtech_problem")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("orgtech_problem") or {}).get("portal_id") or "1")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "peripheral_equipment":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "peripheral_equipment",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "HD").strip().upper()
        add_binding(channel_id, user_id, result, proj, "peripheral_equipment")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("peripheral_equipment") or {}).get("portal_id") or "1")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "network_problem":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "network_problem",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "HD").strip().upper()
        add_binding(channel_id, user_id, result, proj, "network_problem")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("network_problem") or {}).get("portal_id") or "1")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "electronic_queue":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "electronic_queue",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "HD").strip().upper()
        add_binding(channel_id, user_id, result, proj, "electronic_queue")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("electronic_queue") or {}).get("portal_id") or "1")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "email_owa_outlook":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "email_owa_outlook",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "ISR").strip().upper()
        add_binding(channel_id, user_id, result, proj, "email_owa_outlook")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("email_owa_outlook") or {}).get("portal_id") or "22")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "email_forwarding":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "email_forwarding",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "ISR").strip().upper()
        add_binding(channel_id, user_id, result, proj, "email_forwarding")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("email_forwarding") or {}).get("portal_id") or "22")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "email_groups":
        from user_storage import get_user_profile
        from core.jira_form_engine import create_issue_from_form
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        ok, result, project_key = await create_issue_from_form(
            "email_groups",
            form_data=form_data,
            profile=profile,
            attachment_paths=list(attachment_paths or []),
        )
        if not ok:
            return False, result, None
        proj = (project_key or "ISR").strip().upper()
        add_binding(channel_id, user_id, result, proj, "email_groups")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("email_groups") or {}).get("portal_id") or "22")
            link = f"{jira_url}/plugins/servlet/desk/portal/{portal_id}/{result}"
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "wms_settings":
        from user_storage import get_user_profile
        from core.jira_wms import create_wms_settings
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        department = (form_data.get("department") or profile.get("department_wms") or profile.get("department") or "").strip()
        service_type = (form_data.get("service_type") or "").strip()
        description = (form_data.get("description") or "").strip() or "-"
        if not department:
            return False, "Укажите подразделение.", None
        if not service_type:
            return False, "Укажите тип услуги (Изменение топологии / Другие настройки).", None
        file_paths = list(attachment_paths or [])
        if not file_paths:
            return False, "Добавьте хотя бы один файл (вложения обязательны для этого типа заявки).", None
        full_name = (profile.get("full_name") or "").strip()
        phone = (profile.get("phone") or "").strip()
        jira_username = (profile.get("jira_username") or "").strip() or None
        ok, result = await create_wms_settings(
            department=department,
            service_type=service_type,
            description=description,
            full_name=full_name,
            phone=phone,
            file_paths=file_paths,
            jira_username=jira_username,
        )
        if not ok:
            return False, result, None
        add_binding(channel_id, user_id, result, "PW", "wms_settings")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            link = f'{jira_url}/browse/{result}'
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    if ticket_type_id == "wms_psi_user":
        from user_storage import get_user_profile
        from core.jira_wms import create_wms_psi_user
        from core.support.issue_binding_registry import add_binding
        profile = get_user_profile(user_id, channel_id) or {}
        summary = (form_data.get("summary") or "").strip() or "Заявка на пользователя PSIwms"
        full_name = (form_data.get("full_name") or "").strip()
        department = (form_data.get("department") or profile.get("department_wms") or profile.get("department") or "").strip()
        comment = (form_data.get("comment") or "").strip()
        if not department:
            return False, "Укажите подразделение.", None
        if not full_name:
            return False, "Укажите ФИО и должность пользователя PSIwms.", None
        full_name_contact = (profile.get("full_name") or "").strip()
        phone = (profile.get("phone") or "").strip()
        jira_username = (profile.get("jira_username") or "").strip() or None
        # Тело описания; строка «Контактное лицо» один раз добавляется в create_wms_psi_user (jira_wms).
        description = (
            f"Учетная запись для корректировки: {full_name}\n\n"
            f"Суть изменений: {comment or '—'}"
        )
        ok, result = await create_wms_psi_user(
            summary=summary,
            description=description,
            department=department,
            full_name=full_name,
            full_name_contact=full_name_contact,
            phone=phone,
            jira_username=jira_username,
        )
        if not ok:
            return False, result, None
        add_binding(channel_id, user_id, result, "PW", "wms_psi_user")
        jira_url = (CONFIG.get("JIRA", {}).get("LOGIN_URL") or "").strip().rstrip("/")
        if jira_url:
            link = f'{jira_url}/browse/{result}'
            msg = f'Заявка <a href="{link}">{result}</a> создана. Отслеживать статус можно в разделе «Мои заявки».'
        else:
            msg = f"Заявка {result} создана. Отслеживать статус можно в разделе «Мои заявки»."
        return True, result, msg

    return False, f"Неизвестный тип заявки: {ticket_type_id}.", None


# ---------------------------------------------------------------------------
# Мои заявки (по реестру привязок)
# ---------------------------------------------------------------------------

# В «Мои заявки» показываем только заявки, не находящиеся в этих статусах (сравнение без учёта регистра)
MY_TICKETS_EXCLUDED_STATUSES = frozenset({
    "отклонена", "отклонено", "выполнена", "выполнено",
    "resolved", "rejected", "done", "closed", "закрыто", "declined",
})


def get_ticket_type_label(ticket_type_id: Optional[str], project_key: Optional[str] = None) -> str:
    """Человекочитаемое имя типа заявки для списка «Мои заявки»."""
    tid = (ticket_type_id or "").strip()
    if not tid:
        return ""
    # Сначала проверяем каталог типов заявок.
    try:
        from core.support.ticket_catalog import get_ticket_type
        meta = get_ticket_type(tid)
        if isinstance(meta, dict):
            label = (meta.get("label") or "").strip()
            if label:
                return label
    except Exception:
        pass
    # Затем каталог форм (новый движок).
    try:
        from core.forms_catalog import get_form_definition
        fmeta = get_form_definition(tid)
        if isinstance(fmeta, dict):
            label = (fmeta.get("label") or "").strip()
            if label:
                return label
    except Exception:
        pass
    # Встроенные типы, добавленные напрямую в код.
    mapping = {
        "lupa_search": "Поиск/Сайт",
        "wms_issue": "Проблема в работе WMS",
        "wms_settings": "Изменение настроек системы WMS",
        "wms_psi_user": "Пользователь PSIwms",
        "pc_problem": "Проблема в работе ПК",
        "orgtech_problem": "Оргтехника",
        "peripheral_equipment": "Периферийное оборудование",
        "network_problem": "Проблемы в работе сети",
        "electronic_queue": "Электронная очередь",
        "email_owa_outlook": "Электронная почта (Owa/Outlook)",
        "email_forwarding": "Настройка переадресации",
        "email_groups": "Группы рассылки",
    }
    if tid in mapping:
        return mapping[tid]
    # Фолбэк по проекту, если ticket_type_id неизвестен.
    proj = (project_key or "").strip().upper()
    if proj == "HD":
        return "Проблема в работе ПК"
    if proj == "ISR":
        return "Электронная почта"
    if proj == "WHD":
        return "Поиск/Сайт"
    if proj == "PW":
        return "WMS"
    return tid


def get_jira_customer_request_url(issue_key: str, project_key: Optional[str] = None) -> str:
    """
    Ссылка на заявку в Jira.
    PW (WMS): /browse/PW-xxx; WHD (Lupa): portal/5; HD (PC): portal/1; ISR (Email): portal/1.
    """
    key = (issue_key or "").strip()
    if not key:
        return ""
    jira = CONFIG.get("JIRA", {})
    base = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    proj = (project_key or "").strip().upper()
    # WMS (PW): ссылка вида https://jira.petrovich.tech/browse/PW-25800
    if proj == "PW":
        return f"{base}/browse/{key}"
    # Lupa (WHD): портал Service Desk
    if proj == "WHD":
        portal_id = (CONFIG.get("JIRA_LUPA", {}).get("PORTAL_ID") or "5").strip()
        return f"{base}/plugins/servlet/desk/portal/{portal_id}/{key}"
    # PC (HD): портал Service Desk для конечных пользователей
    if proj == "HD":
        try:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("pc_problem") or {}).get("portal_id") or "").strip()
        except Exception:
            portal_id = ""
        if not portal_id:
            portal_id = (CONFIG.get("JIRA_PC", {}).get("PORTAL_ID") or "1").strip()
        return f"{base}/plugins/servlet/desk/portal/{portal_id}/{key}"
    if proj == "ISR":
        try:
            from core.forms_catalog import get_form_definition
            portal_id = ((get_form_definition("email_owa_outlook") or {}).get("portal_id") or "").strip()
        except Exception:
            portal_id = ""
        if not portal_id:
            portal_id = (CONFIG.get("JIRA_EMAIL", {}).get("PORTAL_ID") or "22").strip()
        return f"{base}/plugins/servlet/desk/portal/{portal_id}/{key}"
    # остальные проекты — browse
    return f"{base}/browse/{key}"


def get_jira_browse_url(issue_key: str) -> str:
    """Ссылка формата /browse/KEY (для исполнителей/администраторов)."""
    key = (issue_key or "").strip()
    if not key:
        return ""
    jira = CONFIG.get("JIRA", {})
    base = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/browse/{key}"


def get_my_tickets(channel_id: str, user_id: int) -> List[dict]:
    """
    Список заявок пользователя по реестру привязок (все заявки из текущего канала и привязанного).
    Объединяет заявки, созданные через бота MAX и Telegram (если аккаунты привязаны).
    Возвращает список dict с ключами issue_key, project_key, ticket_type_id, customer_request_url.
    """
    from core.support.issue_binding_registry import get_bindings_by_user
    from user_storage import get_linked_channel_user_pairs
    seen_keys = set()
    result = []
    for ch, uid in get_linked_channel_user_pairs(channel_id, user_id):
        for b in get_bindings_by_user(ch, uid):
            key = (b.get("issue_key") or "").strip().upper()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            result.append({
                "issue_key": b.get("issue_key"),
                "project_key": b.get("project_key"),
                "ticket_type_id": b.get("ticket_type_id"),
                "request_type_label": get_ticket_type_label(b.get("ticket_type_id"), b.get("project_key")),
                "customer_request_url": get_jira_customer_request_url(b.get("issue_key"), b.get("project_key")),
            })
    return result


async def get_my_tickets_filtered(channel_id: str, user_id: int) -> List[dict]:
    """
    Список заявок пользователя по реестру привязок, без заявок в статусах
    «Отклонена», «Выполнена», «Resolved» и их аналогов (см. MY_TICKETS_EXCLUDED_STATUSES).
    Если заявка в Jira удалена (404), привязка удаляется из реестра и заявка не показывается.
    """
    from core.jira_aa import get_issue_status, issue_exists
    from core.support.issue_binding_registry import remove_bindings_by_issue
    raw = get_my_tickets(channel_id, user_id)
    if not raw:
        return []
    result = []
    for item in raw:
        issue_key = (item.get("issue_key") or "").strip()
        if not issue_key:
            continue
        exists = await issue_exists(issue_key)
        if exists is False:
            remove_bindings_by_issue(issue_key)
            continue
        if exists is None:
            result.append(item)
            continue
        status = await get_issue_status(issue_key)
        if status is None:
            result.append(item)
            continue
        if (status.strip().lower()) in MY_TICKETS_EXCLUDED_STATUSES:
            continue
        result.append(item)
    return result


def user_owns_issue(channel_id: str, user_id: int, issue_key: str) -> bool:
    """
    Проверяет, что заявка привязана к пользователю в канале или в привязанном канале (MAX↔Telegram).
    Тогда из любого мессенджера пользователь получает доступ к своим заявкам.
    """
    from core.support.issue_binding_registry import get_bindings_by_user
    from user_storage import get_linked_channel_user_pairs
    key = (issue_key or "").strip().upper()
    if not key:
        return False
    for ch, uid in get_linked_channel_user_pairs(channel_id, user_id):
        for b in get_bindings_by_user(ch, uid):
            if (b.get("issue_key") or "").upper() == key:
                return True
    return False


# ---------------------------------------------------------------------------
# Регистрация (делегируем в core.registration)
# ---------------------------------------------------------------------------

def get_registration_step_response(
    channel_id: str,
    user_id: int,
    step_name: str,
    user_input: Optional[str] = None,
    callback_data: Optional[str] = None,
) -> Text | Menu | Form | Error:
    """
    Ответ на шаге регистрации: текст/форма/ошибка.
    step_name: full_name | phone | login | email | personnel_number | department
    Адаптер вызывает это при каждом шаге и передаёт user_input или callback_data.
    """
    # Пока возвращаем заглушку; полная логика FSM регистрации остаётся в handlers.
    # Для тонкого адаптера можно вынести шаги в Core и возвращать Form/Text/Error.
    return Error(message="Регистрация через Core API: шаги пока в адаптере.")


# ---------------------------------------------------------------------------
# Синглтон / фасад для адаптеров
# ---------------------------------------------------------------------------

class SupportAPI:
    """Фасад API Support Core."""

    def get_start(self, channel_id: str, user_id: int) -> Text | Menu:
        return get_start_response(channel_id, user_id)

    def get_main_menu(self, channel_id: str, user_id: int) -> Menu:
        return get_main_menu_response(channel_id, user_id)

    def get_admin_panel(self, channel_id: str, user_id: int) -> Menu | Error:
        return get_admin_panel_response(channel_id, user_id)

    def get_ticket_types_menu(self, channel_id: str, user_id: int) -> Menu | Error:
        return get_ticket_types_menu(channel_id, user_id)

    async def create_ticket(
        self,
        channel_id: str,
        user_id: int,
        ticket_type_id: str,
        form_data: dict,
        attachment_paths: Optional[List[str]] = None,
    ) -> tuple[bool, str, Optional[str]]:
        return await create_ticket(channel_id, user_id, ticket_type_id, form_data, attachment_paths)

    def get_my_tickets(self, channel_id: str, user_id: int) -> List[dict]:
        return get_my_tickets(channel_id, user_id)

    async def get_my_tickets_filtered(self, channel_id: str, user_id: int) -> List[dict]:
        return await get_my_tickets_filtered(channel_id, user_id)

    def user_owns_issue(self, channel_id: str, user_id: int, issue_key: str) -> bool:
        return user_owns_issue(channel_id, user_id, issue_key)

    def get_jira_customer_request_url(self, issue_key: str) -> str:
        return get_jira_customer_request_url(issue_key)

    def get_jira_browse_url(self, issue_key: str) -> str:
        return get_jira_browse_url(issue_key)


support_api = SupportAPI()
