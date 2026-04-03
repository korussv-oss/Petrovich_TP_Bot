"""
Регистрация и обновление учётных данных.
Вся логика в core для возможности вызова из Telegram и MAX.
"""
import logging
from typing import Tuple, Optional, Dict, Any

from user_storage import (
    get_user_profile,
    save_user_profile,
    check_login_or_email_taken,
    is_user_registered,
)
from validators import (
    validate_full_name,
    validate_work_login,
    validate_corporate_email,
    validate_phone,
    normalize_phone_display,
)

logger = logging.getLogger(__name__)


async def _enrich_profile_with_jira_username(profile: dict) -> dict:
    """
    Если у пользователя есть email и jira-токен, пытаемся найти соответствующего пользователя в Jira
    и записать его логин (name/key) в поле jira_username.
    """
    email = (profile.get("email") or "").strip()
    if not email:
        return profile
    from config import CONFIG

    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return profile

    import aiohttp
    from urllib.parse import urljoin, quote

    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    search_paths = [
        f"rest/api/2/user/search?query={quote(email)}",
        f"rest/api/2/user/search?username={quote(email)}",
    ]
    try:
        async with aiohttp.ClientSession() as session:
            jira_user = None
            for rel in search_paths:
                url = urljoin(base_url + "/", rel)
                try:
                    async with session.get(url, headers=headers, timeout=10) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        if isinstance(data, list) and data:
                            # Ищем точное совпадение по emailAddress
                            for u in data:
                                jira_email = (u.get("emailAddress") or "").strip().lower()
                                if jira_email == email.lower():
                                    jira_user = u
                                    break
                            if not jira_user:
                                jira_user = data[0]
                            break
                except Exception:
                    continue
            if jira_user:
                jira_name = (jira_user.get("name") or jira_user.get("key") or "").strip()
                if jira_name:
                    profile["jira_username"] = jira_name
    except Exception:
        # Не считаем ошибку критичной для регистрации
        return profile
    return profile


async def register_user(
    user_id: int,
    full_name: str,
    login: str,
    email: str,
    phone: str,
    department: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Регистрирует пользователя. Проверяет дубликаты по логину и почте.
    department — подразделение из Jira (для заявки «Смена пароля» по JSM).
    Возвращает (успех, сообщение).
    """
    ok, msg = validate_full_name(full_name)
    if not ok:
        return False, msg
    ok, msg = validate_work_login(login)
    if not ok:
        return False, msg
    ok, msg = validate_corporate_email(email)
    if not ok:
        return False, msg
    ok, msg = validate_phone(phone)
    if not ok:
        return False, msg

    taken, taken_msg = check_login_or_email_taken(login, email, exclude_user_id=None)
    if taken:
        return False, taken_msg

    phone_norm = normalize_phone_display(phone)
    profile = {
        "full_name": full_name.strip(),
        "login": login.strip().lower(),
        "email": email.strip().lower(),
        "phone": phone_norm,
    }
    if department and department.strip():
        profile["department"] = department.strip()
    # Пытаемся обогатить профиль jira_username (если есть соответствие в Jira)
    profile = await _enrich_profile_with_jira_username(profile)
    save_user_profile(user_id, profile)
    logger.info("Пользователь %s зарегистрирован", user_id)
    return True, "Регистрация завершена."


def update_credentials(
    user_id: int,
    full_name: str,
    login: str,
    email: str,
    phone: str,
    department: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Обновляет учётные данные пользователя. Дубликаты по логину/почте не допускаются
    (кроме текущего user_id). department — подразделение для заявок JSM.
    """
    ok, msg = validate_full_name(full_name)
    if not ok:
        return False, msg
    ok, msg = validate_work_login(login)
    if not ok:
        return False, msg
    ok, msg = validate_corporate_email(email)
    if not ok:
        return False, msg
    ok, msg = validate_phone(phone)
    if not ok:
        return False, msg

    taken, taken_msg = check_login_or_email_taken(login, email, exclude_user_id=user_id)
    if taken:
        return False, taken_msg

    phone_norm = normalize_phone_display(phone)
    profile = {
        "full_name": full_name.strip(),
        "login": login.strip().lower(),
        "email": email.strip().lower(),
        "phone": phone_norm,
    }
    if department is not None:
        profile["department"] = department.strip() if department and department.strip() else ""
    else:
        old = get_user_profile(user_id)
        if old and "department" in old:
            profile["department"] = old.get("department", "")
    save_user_profile(user_id, profile)
    logger.info("Учётные данные обновлены для user_id=%s", user_id)
    return True, "Данные обновлены."


def get_profile_for_edit(user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает профиль для отображения/редактирования или None."""
    return get_user_profile(user_id)


def register_user_from_ad(
    user_id: int, profile: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Регистрирует пользователя по профилю из AD (без проверки дубликатов по логину/почте;
    вызывающий код должен сам проверить check_login_or_email_taken при необходимости).
    Возвращает (успех, сообщение).
    """
    full_name = (profile.get("full_name") or "").strip()
    login = (profile.get("login") or "").strip().lower()
    email = (profile.get("email") or "").strip().lower()
    phone = (profile.get("phone") or "").strip()
    if not all([full_name, login, email, phone]):
        return False, "Неполный профиль из AD."
    taken, taken_msg = check_login_or_email_taken(login, email, exclude_user_id=user_id)
    if taken:
        return False, "Этот аккаунт уже зарегистрирован в боте. Используйте «Привязать аккаунт» с номера телефона этого сотрудника."
    profile_to_save = {
        "full_name": full_name,
        "login": login,
        "email": email,
        "phone": phone,
    }
    # Подразделение из AD не сохраняем: в Jira другие названия. Запросим при первой заявке (Lupa/смена пароля и т.д.).
    # Обогащение jira_username выполняется асинхронно — вызывающий код может вызвать _enrich_profile_with_jira_username после save
    save_user_profile(user_id, profile_to_save)
    logger.info("Пользователь %s зарегистрирован из AD", user_id)
    return True, "Регистрация завершена."
