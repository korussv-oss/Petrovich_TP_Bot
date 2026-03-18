"""
Валидация: ФИО, рабочий логин, корпоративная почта, телефон +7-XXX-XXX-XX-XX.
"""
import re
from typing import Tuple

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def validate_full_name(full_name: str) -> Tuple[bool, str]:
    """ФИО только кириллицей (русские буквы), пробелы и дефис."""
    if not full_name or not full_name.strip():
        return False, "ФИО не может быть пустым"
    s = full_name.strip()
    if len(s) < 2:
        return False, "ФИО должно быть не короче 2 символов"
    if len(s) > 200:
        return False, "ФИО не длиннее 200 символов"
    if re.search(r"[a-zA-Z]", s):
        return False, "ФИО вводится только кириллицей (русские буквы). Латинские буквы не допускаются."
    if not re.match(r"^[А-Яа-яЁё\s\-]+$", s):
        return False, "ФИО только кириллицей (русские буквы), пробелы и дефис"
    return True, ""


def validate_work_login(login: str) -> Tuple[bool, str]:
    """Рабочий логин в формате i.ivanov (латиница, точки)."""
    if not login or not login.strip():
        return False, "Рабочий логин не может быть пустым"
    s = login.strip().lower()
    if len(s) < 2:
        return False, "Логин слишком короткий"
    if len(s) > 64:
        return False, "Логин не длиннее 64 символов"
    if not re.match(r"^[a-z0-9._-]+$", s):
        return False, "Логин только латиница (a-z), цифры, точки, дефис и подчёркивание (например: i.ivanov)"
    return True, ""


def validate_corporate_email(email: str) -> Tuple[bool, str]:
    """Корпоративная почта @petrovich.ru или @petrovich.tech."""
    if not email or not email.strip():
        return False, "Email не может быть пустым"
    s = email.strip().lower()
    if len(s) > 200:
        return False, "Email не длиннее 200 символов"
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", s):
        return False, "Неверный формат email"
    domain = s.split("@")[1] if "@" in s else ""
    if domain not in ("petrovich.ru", "petrovich.tech") and not (
        domain.endswith(".petrovich.ru") or domain.endswith(".petrovich.tech")
    ):
        return False, "Допускается только корпоративная почта @petrovich.ru или @petrovich.tech"
    return True, ""


def validate_phone(phone: str) -> Tuple[bool, str]:
    """Телефон: минимум 10 цифр; формат +7-XXX-XXX-XX-XX приветствуется."""
    if not phone or not phone.strip():
        return False, "Номер телефона не может быть пустым"
    s = phone.strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) < 10:
        return False, "В номере должно быть минимум 10 цифр"
    if len(digits) > 15:
        return False, "Слишком длинный номер"
    if not re.match(r"^\+?[0-9\s\-\(\)]+$", s):
        return False, "Недопустимые символы в номере"
    return True, ""


def validate_employee_id(employee_id: str) -> Tuple[bool, str]:
    """Табельный номер: 3–24 символа, цифры/буквы/дефис (например: 0000000311, Пв00000***)."""
    if not employee_id or not employee_id.strip():
        return False, "Табельный номер не может быть пустым"
    s = employee_id.strip()
    if len(s) < 3:
        return False, "Табельный номер не короче 3 символов"
    if len(s) > 24:
        return False, "Табельный номер не длиннее 24 символов"
    if not re.match(r"^[0-9A-Za-zА-Яа-яЁё\-]+$", s):
        return False, "Только цифры, буквы и дефис (например: 0000000311)"
    return True, ""


def normalize_phone_display(phone: str) -> str:
    """Приводит номер к виду +7-XXX-XXX-XX-XX для отображения/хранения."""
    digits = re.sub(r"\D", "", phone.strip())
    if len(digits) >= 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) >= 11 and digits.startswith("7"):
        digits = digits[:11]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits[0] == "7":
        return f"+7-{digits[1:4]}-{digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return phone.strip()


def normalize_phone_for_jira(phone: str) -> str:
    """
    Нормализует телефон под Jira-поле Existing phone number.
    В AA валидатор ожидает, как правило, 10 цифр (пример из заявки: 9526669983).
    """
    digits = re.sub(r"\D", "", (phone or "").strip())
    if len(digits) >= 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) >= 11 and digits.startswith("7"):
        digits = digits[:11]
    # Приводим к 10 цифрам (без ведущей 7)
    if len(digits) == 11 and digits.startswith("7"):
        return digits[1:]
    if len(digits) == 10:
        return digits
    return digits


def validate_issue_key(issue_key: str) -> Tuple[bool, str]:
    """
    Jira issue key: PROJECT-123 (например AA-12345, PW-25774).
    Используется для безопасной сборки URL к Jira API.
    """
    s = (issue_key or "").strip()
    if not s:
        return False, "Issue key не может быть пустым."
    if len(s) > 64:
        return False, "Issue key слишком длинный."
    if not ISSUE_KEY_RE.match(s):
        return False, "Неверный формат issue key."
    return True, ""


def sanitize_jira_text(text: str, max_len: int = 4000) -> str:
    """
    Базовая санитизация текста для Jira:
    - удаляет управляющие символы (кроме \\n и \\t),
    - нормализует переносы строк,
    - обрезает до max_len.
    """
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", s)
    if max_len > 0 and len(s) > max_len:
        s = s[:max_len]
    return s.strip()
