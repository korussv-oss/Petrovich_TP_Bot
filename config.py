# config.py — конфигурация бота (Jira AA, Telegram, админы)

import os
import logging
from pathlib import Path
from typing import Dict, Any, List

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()

# Jira проект AA: ключ проекта и тип задачи для "смена пароля"
JIRA_AA_PROJECT_KEY = os.getenv("JIRA_AA_PROJECT_KEY", "AA")
JIRA_AA_ISSUE_TYPE = os.getenv("JIRA_AA_ISSUE_TYPE", "Задача")
# ID типа задачи (надёжнее имени: не зависит от кодировки). Узнать: scripts\jira_field_ids.py --list-issue-types
JIRA_AA_ISSUE_TYPE_ID = (os.getenv("JIRA_AA_ISSUE_TYPE_ID") or "").strip() or None

# Исполнитель по умолчанию (Робот Петрович-ТЕХ): username в Jira
JIRA_AA_ASSIGNEE_USERNAME = (os.getenv("JIRA_AA_ASSIGNEE_USERNAME") or "Robot_Scripts_PS").strip()
# Временно отключить установку исполнителя (0/false/no — не назначать)
JIRA_AA_SET_ASSIGNEE = os.getenv("JIRA_AA_SET_ASSIGNEE", "1").strip().lower() not in ("0", "false", "no", "off")

# Фича-флаги
TICKET_WIZARD_WMS_ISSUE_ENABLED = os.getenv("TICKET_WIZARD_WMS_ISSUE", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_LUPA_SEARCH_ENABLED = os.getenv("TICKET_WIZARD_LUPA_SEARCH", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_WMS_SETTINGS_ENABLED = os.getenv("TICKET_WIZARD_WMS_SETTINGS", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_WMS_PSI_USER_ENABLED = os.getenv("TICKET_WIZARD_WMS_PSI_USER", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_PC_PROBLEM_ENABLED = os.getenv("TICKET_WIZARD_PC_PROBLEM", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_ORGTECH_PROBLEM_ENABLED = os.getenv("TICKET_WIZARD_ORGTECH_PROBLEM", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_PERIPHERAL_EQUIPMENT_ENABLED = os.getenv("TICKET_WIZARD_PERIPHERAL_EQUIPMENT", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_NETWORK_PROBLEM_ENABLED = os.getenv("TICKET_WIZARD_NETWORK_PROBLEM", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_ELECTRONIC_QUEUE_ENABLED = os.getenv("TICKET_WIZARD_ELECTRONIC_QUEUE", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_EMAIL_OWA_ENABLED = os.getenv("TICKET_WIZARD_EMAIL_OWA", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_EMAIL_FORWARDING_ENABLED = os.getenv("TICKET_WIZARD_EMAIL_FORWARDING", "0").strip().lower() in ("1", "true", "yes", "on")
TICKET_WIZARD_EMAIL_GROUPS_ENABLED = os.getenv("TICKET_WIZARD_EMAIL_GROUPS", "0").strip().lower() in ("1", "true", "yes", "on")

# Создание через Service Desk API (как в the_bot_wms): тип «Смена пароля» задаётся requestTypeId, не полем 10500.
# Из AA-78207: servicedesk/23/requesttype/964 → service_desk_id=23, request_type_id=964
JIRA_AA_SERVICE_DESK_ID = (os.getenv("JIRA_AA_SERVICE_DESK_ID") or "").strip()
JIRA_AA_REQUEST_TYPE_ID = (os.getenv("JIRA_AA_REQUEST_TYPE_ID") or os.getenv("JIRA_AA_CUSTOMER_REQUEST_TYPE_ID") or "964").strip()

# Запасной вариант (REST): поле 10500 — передаём значение текстом «Смена пароля» (без id)
JIRA_AA_FIELD_CUSTOMER_REQUEST_TYPE = (os.getenv("JIRA_AA_FIELD_CUSTOMER_REQUEST_TYPE") or "customfield_10500").strip()
JIRA_AA_REQUEST_TYPE_VALUE = (os.getenv("JIRA_AA_REQUEST_TYPE_VALUE") or "Смена пароля").strip()

# Имена/ID кастомных полей в Jira AA (заполнить по факту в вашей Jira: /rest/api/2/field)
# Подразделение (Department) — обязательно для типа «Смена пароля» в JSM (как в the_bot_lupa: customfield_11406)
JIRA_AA_FIELD_DEPARTMENT = (os.getenv("JIRA_AA_FIELD_DEPARTMENT") or os.getenv("JIRA_AA_FIELD_SUBDIVISION") or "customfield_11406").strip()
JIRA_AA_FIELDS = {
    "AD_ACCOUNT": os.getenv("JIRA_AA_FIELD_AD_ACCOUNT", "customfield_10001"),
    "EXISTING_PHONE": os.getenv("JIRA_AA_FIELD_EXISTING_PHONE", "customfield_10002"),
    "PASSWORD_NEW": os.getenv("JIRA_AA_FIELD_PASSWORD_NEW", "customfield_10003"),
    "DEPARTMENT": JIRA_AA_FIELD_DEPARTMENT,
}


def _parse_int_list(env_key: str, default: List[int] = None) -> List[int]:
    if default is None:
        default = []
    s = os.getenv(env_key, "")
    if not s:
        return default
    out = []
    for part in s.split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out or default


def _parse_str_list(env_key: str, default: List[str] = None) -> List[str]:
    """Список строк из env (через запятую). Для LUPA_CITIES и т.п."""
    if default is None:
        default = []
    s = os.getenv(env_key, "")
    if not s:
        return default
    out = [part.strip() for part in s.split(",") if part.strip()]
    return out if out else default


def _parse_process_option_ids() -> Dict[str, str]:
    """Маппинг ключей процессов WMS (proc_placement и т.д.) на ID опций в Jira (customfield_13803)."""
    s = (os.getenv("JIRA_WMS_PROCESS_OPTION_IDS") or "").strip()
    if s:
        try:
            import json
            data = json.loads(s)
            out = {str(k): str(v) for k, v in (data or {}).items() if k and v is not None}
            if out:
                return out
        except Exception as e:
            logger.warning("JIRA_WMS_PROCESS_OPTION_IDS: не удалось разобрать JSON: %s", e)
    # Значения по умолчанию для проекта PW (customfield_13803). Приёмка: при несовпадении задайте в .env.
    return {
        "proc_placement": "13103",
        "proc_reserve": "19812",
        "proc_receiving": "13102",
        "proc_pick": "19905",
        "proc_control": "13106",
        "proc_shipment": "13107",
        "proc_replenishment": "19906",
        "proc_inventory": "13104",
        "proc_app": "19813",
        "proc_report": "19814",
        "proc_assembly": "13105",
        "proc_other": "13108",
    }


def load_config() -> Dict[str, Any]:
    admin_ids = _parse_int_list("ADMIN_IDS")
    config = {
        "TELEGRAM": {
            "TOKEN": (
                os.getenv("TELEGRAM_TOKEN")
                or os.getenv("TELEGRAM_TOKEN_RUBIK")
                or ""
            ).strip(),
            "TOKEN_WMS": (os.getenv("TELEGRAM_TOKEN_WMS") or "").strip(),
            "TOKEN_LUPA": (os.getenv("TELEGRAM_TOKEN_LUPA") or "").strip(),
            "ADMIN_IDS": admin_ids,
            # Админы Лупы: могут формировать Excel-отчёт по заявкам поиска (плюс ADMIN_IDS имеют доступ)
            "ADMIN_LUPA_IDS": _parse_int_list("ADMIN_LUPA_IDS"),
            # Системные администраторы СТЦ: раздел «СА СТЦ» в главном меню.
            "STC_SA_IDS": _parse_int_list("STC_SA_IDS"),
        },
        "JIRA": {
            "LOGIN_URL": (os.getenv("JIRA_LOGIN_URL") or "https://jira.petrovich.tech").strip().rstrip("/"),
            "TOKEN": (os.getenv("JIRA_TOKEN") or "").strip(),
            "USERNAME": (os.getenv("JIRA_USERNAME") or "").strip(),
            "PASSWORD": (os.getenv("JIRA_PASSWORD") or "").strip(),
        },
        "MAX": {
            "BOT_TOKEN": (os.getenv("MAX_BOT_TOKEN") or os.getenv("MAX_TOKEN") or "").strip(),
            # ADMIN_MAX_IDS — user_id в MAX, которым показывается кнопка «Админ-панель» (через запятую)
            "ADMIN_IDS": _parse_int_list("ADMIN_MAX_IDS"),
            # Админы Лупы в MAX: могут формировать Excel-отчёт по заявкам поиска
            "ADMIN_LUPA_IDS": _parse_int_list("ADMIN_LUPA_MAX_IDS"),
            # Системные администраторы СТЦ в MAX.
            "STC_SA_IDS": _parse_int_list("STC_SA_MAX_IDS"),
        },
        "JIRA_AA": {
            "PROJECT_KEY": JIRA_AA_PROJECT_KEY,
            "ISSUE_TYPE": JIRA_AA_ISSUE_TYPE,
            "ISSUE_TYPE_ID": JIRA_AA_ISSUE_TYPE_ID,
            "ASSIGNEE_USERNAME": JIRA_AA_ASSIGNEE_USERNAME,
            "SET_ASSIGNEE": JIRA_AA_SET_ASSIGNEE,
            "SERVICE_DESK_ID": JIRA_AA_SERVICE_DESK_ID,
            "REQUEST_TYPE_ID": JIRA_AA_REQUEST_TYPE_ID,
            "FIELD_CUSTOMER_REQUEST_TYPE": JIRA_AA_FIELD_CUSTOMER_REQUEST_TYPE,
            "REQUEST_TYPE_VALUE": JIRA_AA_REQUEST_TYPE_VALUE,
            "FIELD_DEPARTMENT": JIRA_AA_FIELD_DEPARTMENT,
            "FIELDS": JIRA_AA_FIELDS,
        },
        # Проекты WMS (PW) и Lupa (WHD) — PLAN_UNIFIED_MAX_BOT_APPEND п. 11 (JIRA_WMS_*, JIRA_LUPA_*)
        "JIRA_WMS": {
            "PROJECT_KEY": (os.getenv("JIRA_WMS_PROJECT_KEY") or os.getenv("JIRA_PW_PROJECT_KEY") or "PW").strip(),
            "SERVICE_DESK_ID": (os.getenv("JIRA_WMS_SERVICE_DESK_ID") or os.getenv("JIRA_PW_SERVICE_DESK_ID") or "31").strip(),
            # Request type «Проблема в работе WMS» (Type: Ошибка). Узнать ID: Service Desk → настройки типа запроса или API requesttype.
            "REQUEST_TYPE_ID": (os.getenv("JIRA_WMS_REQUEST_TYPE_ID") or os.getenv("JIRA_PW_REQUEST_TYPE_ID") or "").strip(),
            # Типы «Поддержка» (не Ошибка): в Jira у Request Type должен быть Issue Type = Поддержка.
            "REQUEST_TYPE_ID_SETTINGS": (os.getenv("JIRA_WMS_REQUEST_TYPE_ID_SETTINGS") or "1165").strip(),
            "REQUEST_TYPE_ID_PSI_USER": (os.getenv("JIRA_WMS_REQUEST_TYPE_ID_PSI_USER") or "").strip(),
            "FIELD_DEPARTMENT": (os.getenv("JIRA_WMS_FIELD_DEPARTMENT") or "customfield_18215").strip(),
            "FIELD_PROCESS": (os.getenv("JIRA_WMS_FIELD_PROCESS") or "customfield_13803").strip(),
            "FIELD_SERVICE_TYPE": (os.getenv("JIRA_WMS_FIELD_SERVICE_TYPE") or "customfield_10500").strip(),
            # Поле «WMS service»: пользователь выбирает при создании заявки только «Изменение топологии» или «Другие настройки».
            "FIELD_WMS_SETTINGS_SERVICE": (os.getenv("JIRA_WMS_FIELD_WMS_SETTINGS_SERVICE") or "customfield_18402").strip(),
            "FIELD_PSI_USER_FULL_NAME": (os.getenv("JIRA_WMS_FIELD_PSI_USER_FULL_NAME") or "customfield_12406").strip(),
            # ID значений поля WMS service в Jira (соответствуют WMS_SERVICE_TYPES).
            "WMS_SETTINGS_SERVICE_TYPE_IDS": {
                "Изменение топологии": (os.getenv("JIRA_WMS_SETTINGS_TOPOLOGY_ID") or "19810").strip(),
                "Другие настройки": (os.getenv("JIRA_WMS_SETTINGS_OTHER_ID") or "19811").strip(),
            },
            # ID опций поля «WMS failed process» (customfield_13803), если Jira принимает только id, не value.
            # Формат в .env: JSON, ключи — proc_placement, proc_receiving и т.д. (см. wms_constants.WMS_PROCESSES).
            # Пример: JIRA_WMS_PROCESS_OPTION_IDS={"proc_placement":"10001","proc_receiving":"10002",...}
            "PROCESS_OPTION_IDS": _parse_process_option_ids(),
        },
        "JIRA_LUPA": {
            "PROJECT_KEY": (os.getenv("JIRA_LUPA_PROJECT_KEY") or os.getenv("JIRA_WHD_PROJECT_KEY") or "WHD").strip(),
            "ISSUE_TYPE": (os.getenv("JIRA_LUPA_ISSUE_TYPE") or os.getenv("JIRA_WHD_ISSUE_TYPE") or "Incident").strip(),
            "FIELD_PROBLEMATIC_SERVICE": (os.getenv("JIRA_LUPA_FIELD_PROBLEMATIC_SERVICE") or "customfield_12312").strip(),
            "FIELD_REQUEST_TYPE": (os.getenv("JIRA_LUPA_FIELD_REQUEST_TYPE") or "customfield_15800").strip(),
            "FIELD_SUBDIVISION": (os.getenv("JIRA_LUPA_FIELD_SUBDIVISION") or "customfield_11406").strip(),
            "FIELD_SERVICE": (os.getenv("JIRA_LUPA_FIELD_SERVICE") or "customfield_10500").strip(),
            "FIELD_ADDRESS_CITY": (os.getenv("JIRA_LUPA_FIELD_ADDRESS_CITY") or "customfield_12403").strip(),
            # ID портала Service Desk для ссылки на заявку: .../portal/5/WHD-xxxxx
            "PORTAL_ID": (os.getenv("JIRA_LUPA_PORTAL_ID") or os.getenv("JIRA_WHD_PORTAL_ID") or "5").strip(),
            # Список городов для кнопок (как в the_bot_lupa). LUPA_CITIES через запятую в .env или по умолчанию
            "CITIES": _parse_str_list(
                "LUPA_CITIES",
                ["Санкт-Петербург", "Москва", "Екатеринбург", "Великий Новгород", "Казань", "Нижний Новгород", "Краснодар", "Челябинск", "Самара", "Уфа"],
            ),
        },
        "JIRA_PC": {
            "PROJECT_KEY": (os.getenv("JIRA_PC_PROJECT_KEY") or "HD").strip(),
            "ISSUE_TYPE": (os.getenv("JIRA_PC_ISSUE_TYPE") or "Incident").strip(),
            "SERVICE_DESK_ID": (os.getenv("JIRA_PC_SERVICE_DESK_ID") or "1").strip(),
            "REQUEST_TYPE_ID": (os.getenv("JIRA_PC_REQUEST_TYPE_ID") or "377").strip(),
            "PORTAL_ID": (os.getenv("JIRA_PC_PORTAL_ID") or "1").strip(),
            "FIELD_PC_PROBLEM_KIND": (os.getenv("JIRA_PC_FIELD_PROBLEM_KIND") or "customfield_11400").strip(),
            "FIELD_DEPARTMENT": (os.getenv("JIRA_PC_FIELD_DEPARTMENT") or "customfield_11406").strip(),
            "FIELD_EXISTING_PHONE": (os.getenv("JIRA_PC_FIELD_EXISTING_PHONE") or "customfield_13103").strip(),
        },
        "JIRA_EMAIL": {
            "PROJECT_KEY": (os.getenv("JIRA_EMAIL_PROJECT_KEY") or "ISR").strip(),
            "SERVICE_DESK_ID": (os.getenv("JIRA_EMAIL_SERVICE_DESK_ID") or "22").strip(),
            "REQUEST_TYPE_ID_OWA": (os.getenv("JIRA_EMAIL_REQUEST_TYPE_ID_OWA") or "1257").strip(),
            "PORTAL_ID": (os.getenv("JIRA_EMAIL_PORTAL_ID") or os.getenv("JIRA_EMAIL_SERVICE_DESK_ID") or "22").strip(),
            "FIELD_PHONE": (os.getenv("JIRA_EMAIL_FIELD_PHONE") or "customfield_13103").strip(),
            "FIELD_RMS_OR_IP": (os.getenv("JIRA_EMAIL_FIELD_RMS_OR_IP") or "customfield_14075").strip(),
            "FIELD_DEPARTMENT": (os.getenv("JIRA_EMAIL_FIELD_DEPARTMENT") or "customfield_11406").strip(),
            "FIELD_REQUEST_KIND": (os.getenv("JIRA_EMAIL_FIELD_REQUEST_KIND") or "customfield_19107").strip(),
            "FIELD_WORKPLACE": (os.getenv("JIRA_EMAIL_FIELD_WORKPLACE") or "customfield_11402").strip(),
        },
        # AD/LDAP: проверка сотрудника при регистрации (поиск по телефону). Все значения только из .env.
        "AD_LDAP": {
            "URL": (os.getenv("AD_LDAP_URL") or "").strip(),
            "BIND_USER": (os.getenv("AD_LDAP_BIND_USER") or "").strip(),
            "BIND_PASSWORD": (os.getenv("AD_LDAP_BIND_PASSWORD") or "").strip(),
            "BASE_DN": (os.getenv("AD_LDAP_BASE_DN") or "").strip(),
            "VERIFY_SSL": os.getenv("AD_LDAP_VERIFY_SSL", "").strip().lower() not in ("0", "false", "no", "off"),
        },
        "SUPPORT_PORTAL_URL": (os.getenv("SUPPORT_PORTAL_URL") or "").strip(),
    }
    # Алиасы для обратной совместимости (JIRA_PW / JIRA_WHD)
    config["JIRA_PW"] = {"PROJECT_KEY": config["JIRA_WMS"]["PROJECT_KEY"], "SERVICE_DESK_ID": config["JIRA_WMS"]["SERVICE_DESK_ID"]}
    config["JIRA_WHD"] = {"PROJECT_KEY": config["JIRA_LUPA"]["PROJECT_KEY"], "ISSUE_TYPE": config["JIRA_LUPA"]["ISSUE_TYPE"]}
    return config


CONFIG = load_config()


def is_admin(user_id: int) -> bool:
    """Проверка: user_id — администратор в Telegram (ADMIN_IDS)."""
    return user_id in CONFIG.get("TELEGRAM", {}).get("ADMIN_IDS", [])


def is_max_admin(user_id: int) -> bool:
    """Проверка: user_id — администратор в MAX (ADMIN_MAX_IDS)."""
    return user_id in CONFIG.get("MAX", {}).get("ADMIN_IDS", [])


def is_channel_admin(channel_id: str, user_id: int) -> bool:
    """Проверка прав администратора в канале: Telegram — ADMIN_IDS, MAX — ADMIN_MAX_IDS."""
    if (channel_id or "").strip().lower() == "max":
        return is_max_admin(user_id)
    return is_admin(user_id)


def is_lupa_report_allowed(channel_id: str, user_id: int) -> bool:
    """
    Может ли пользователь запрашивать Excel-отчёт по заявкам Лупа (поиск на сайте).
    Доступно: ADMIN_IDS / ADMIN_MAX_IDS и отдельно ADMIN_LUPA_IDS / ADMIN_LUPA_MAX_IDS.
    """
    if is_channel_admin(channel_id or "telegram", user_id):
        return True
    if (channel_id or "").strip().lower() == "max":
        return user_id in CONFIG.get("MAX", {}).get("ADMIN_LUPA_IDS", [])
    return user_id in CONFIG.get("TELEGRAM", {}).get("ADMIN_LUPA_IDS", [])


def is_stc_sa(channel_id: str, user_id: int) -> bool:
    """Проверка роли «Системный администратор СТЦ» для канала."""
    if (channel_id or "").strip().lower() == "max":
        return user_id in CONFIG.get("MAX", {}).get("STC_SA_IDS", [])
    return user_id in CONFIG.get("TELEGRAM", {}).get("STC_SA_IDS", [])
