"""
Клавиатуры бота: главное меню, регистрация, смена данных, админ.
"""
from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from config import is_admin
from core.pc_problem import PC_PROBLEM_KINDS
from core.orgtech import ORGTECH_KINDS
from core.peripheral_equipment import PERIPHERAL_KINDS


def get_start_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Кнопки для незарегистрированных: Зарегистрироваться, Привязать аккаунт."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Зарегистрироваться", callback_data="start_registration")],
        [InlineKeyboardButton(text="🔗 Привязать аккаунт", callback_data="bind_account")],
    ])


def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Главное меню: Создать заявку в ТП, Мои заявки, Помощь (+ админ)."""
    buttons = [
        [InlineKeyboardButton(text="📋 Создать заявку в ТП", callback_data="create_ticket_tp")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_tickets")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ]
    if user_id and is_admin(user_id):
        buttons.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_pc_problem_kind_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора типа проблемы ПК (customfield_11400)."""
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"pc_kind_{kind_id}")] for kind_id, label in PC_PROBLEM_KINDS]
    buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_orgtech_kind_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора типа оргтехники (customfield_12613)."""
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"orgtech_kind_{kind_id}")] for kind_id, label in ORGTECH_KINDS]
    buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_peripheral_kind_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора вида периферийного оборудования (customfield_11403)."""
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"peripheral_kind_{kind_id}")] for kind_id, label in PERIPHERAL_KINDS]
    buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_department_keyboard(
    departments: Optional[List[str]] = None,
    page: int = 0,
    items_per_page: int = 8,
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора подразделения (из Jira). Пагинация по 8 пунктов.
    departments — список; если None, подгружается из core.jira_departments.
    """
    if departments is None:
        from core.jira_departments import get_departments
        departments = get_departments()
    if not departments:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Список недоступен", callback_data="cancel")],
        ])
    start = page * items_per_page
    end = start + items_per_page
    page_items = departments[start:end]
    buttons = []
    for i, name in enumerate(page_items):
        idx = start + i
        buttons.append([
            InlineKeyboardButton(text=name, callback_data=f"department_{idx}"),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"department_page_{page - 1}"))
    if end < len(departments):
        nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"department_page_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_contact_request_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой «Поделиться контактом» для получения настоящего номера телефона."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    """Убрать reply-клавиатуру после ввода контакта."""
    return ReplyKeyboardRemove(remove_keyboard=True)


def get_wms_department_keyboard(
    departments: List[str],
    page: int = 0,
    items_per_page: int = 8,
) -> InlineKeyboardMarkup:
    """Клавиатура выбора подразделения WMS (список строк)."""
    if not departments:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Список недоступен", callback_data="cancel")],
        ])
    start = page * items_per_page
    end = start + items_per_page
    page_items = departments[start:end]
    buttons = []
    for i, name in enumerate(page_items):
        idx = start + i
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"wms_dept_{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"wms_dept_page_{page - 1}"))
    if end < len(departments):
        nav.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"wms_dept_page_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_wms_subtype_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора типа заявки WMS (как в the_bot_wms)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Проблема в работе WMS", callback_data="wms_type_issue")],
        [InlineKeyboardButton(text="⚙️ Изменение настроек системы WMS", callback_data="wms_type_settings")],
        [InlineKeyboardButton(text="👤 Создать/изменить/удалить пользователя PSIwms", callback_data="wms_type_psi_user")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="wms_type_back")],
    ])


def get_wms_process_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора процесса WMS — значения для Jira customfield_13803 (как в MAX)."""
    from core.wms_constants import WMS_PROCESSES
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"wms_process_{key}")]
        for key, name in WMS_PROCESSES.items()
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_wms_service_type_keyboard() -> InlineKeyboardMarkup:
    """Тип услуги «Изменение настроек системы WMS» (как the_bot_wms)."""
    from core.wms_constants import WMS_SERVICE_TYPES
    buttons = [
        [InlineKeyboardButton(text=f"🗺️ {name}", callback_data=key)]
        for key, name in WMS_SERVICE_TYPES.items()
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="wms_show_subtype_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


# --- Lupa (как the_bot_lupa): кнопки сервис, тип запроса, город ---

LUPA_SERVICE_BUTTONS = [
    ("📱 Приложение", "lupa_service_app"),
    ("🌐 Сайт (petrovich.ru)", "lupa_service_site"),
]
LUPA_SERVICE_VALUES = {"lupa_service_app": "Приложение", "lupa_service_site": "Сайт (petrovich.ru)"}

LUPA_REQUEST_TYPE_BUTTONS = [
    ("🔍 Проблемы с поиском", "lupa_request_search_issue"),
    ("❓ Вопросы по работе поиска", "lupa_request_search_question"),
    ("✅ Валидация сленга", "lupa_request_discount"),
]
LUPA_REQUEST_TYPE_VALUES = {
    "lupa_request_search_issue": "проблемы с поиском",
    "lupa_request_search_question": "вопросы по работе поиска",
    "lupa_request_discount": "валидация сленга",
}


def get_lupa_service_keyboard() -> InlineKeyboardMarkup:
    """Выбор проблемного сервиса (как the_bot_lupa)."""
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in LUPA_SERVICE_BUTTONS
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_lupa_request_type_keyboard() -> InlineKeyboardMarkup:
    """Выбор типа запроса (как the_bot_lupa)."""
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in LUPA_REQUEST_TYPE_BUTTONS
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_lupa_city_keyboard(popular_cities: List[str]) -> InlineKeyboardMarkup:
    """Города для выбора (первые 4 в 2 колонки + «Ввести вручную»), как the_bot_lupa."""
    buttons = []
    for i in range(0, min(4, len(popular_cities)), 2):
        row = []
        for j in range(2):
            if i + j < len(popular_cities):
                city = popular_cities[i + j]
                cb = f"lupa_city_{city.replace(' ', '_')}"
                row.append(InlineKeyboardButton(text=city, callback_data=cb))
        if row:
            buttons.append(row)
    buttons.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="lupa_city_manual")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_lupa_skip_comment_keyboard() -> InlineKeyboardMarkup:
    """Пропустить комментарий (описание), как the_bot_lupa."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить комментарий", callback_data="lupa_skip_comment")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def get_back_to_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
    ])


def get_admin_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ])


def get_admin_back_to_choice_only_keyboard() -> InlineKeyboardMarkup:
    """Одна кнопка «К выбору способа» (список / поиск / логин)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К выбору способа", callback_data="admin_del_back_choice")],
    ])


def get_admin_delete_choice_keyboard() -> InlineKeyboardMarkup:
    """Выбор способа удаления: список, поиск по ФИО, ввод логина/ID."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список пользователей", callback_data="admin_del_choice_list")],
        [InlineKeyboardButton(text="🔍 Поиск по ФИО", callback_data="admin_del_choice_search")],
        [InlineKeyboardButton(text="✏️ Ввести логин или ID", callback_data="admin_del_choice_login")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ])


def get_admin_user_list_keyboard(
    users_page: List[Tuple[int, dict]],
    page: int,
    total_pages: int,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Клавиатура страницы списка пользователей (до 10 кнопок) + пагинация."""
    buttons = []
    for uid, profile in users_page:
        name = (profile.get("full_name") or "—").strip() or "—"
        login = (profile.get("login") or "").strip() or "—"
        label = f"{name} ({login})" if len(f"{name} ({login})") <= 40 else f"{name[:28]}… ({login})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"admin_del_uid_{uid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"admin_del_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Вперёд ▶", callback_data=f"admin_del_page_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔙 К выбору способа", callback_data="admin_del_back_choice")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_user_matches_keyboard(matches: List[Tuple[int, dict]]) -> InlineKeyboardMarkup:
    """Клавиатура результатов поиска по ФИО (кнопки пользователей)."""
    buttons = []
    for uid, profile in matches:
        name = (profile.get("full_name") or "—").strip() or "—"
        login = (profile.get("login") or "").strip() or "—"
        label = f"{name} ({login})" if len(f"{name} ({login})") <= 40 else f"{name[:28]}… ({login})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"admin_del_uid_{uid}")])
    buttons.append([InlineKeyboardButton(text="🔙 К выбору способа", callback_data="admin_del_back_choice")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_confirm_delete_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления: Удалить / Отмена."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Удалить", callback_data=f"admin_del_confirm_{user_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_del_cancel"),
        ],
    ])
