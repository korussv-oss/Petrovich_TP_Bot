"""
Смена пароля: создание задачи в Jira AA.
Используются данные из профиля: логин (AD account), телефон (Existing phone number).
"""
import logging
from typing import Tuple, Optional

from user_storage import get_user_profile
from core.jira_aa import create_password_change_issue, _set_reporter  # type: ignore[attr-defined]
from core.jira_form_engine import create_issue_from_form
from validators import normalize_phone_for_jira

logger = logging.getLogger(__name__)


async def request_password_change(
    user_id: int,
    new_password: str,
    channel_id: str = "telegram",
) -> Tuple[bool, str]:
    """
    Создаёт задачу в Jira AA на смену пароля.
    AD account = логин из профиля, Existing phone = телефон из профиля, Password_new = new_password.
    Возвращает (успех, сообщение).
    """
    if not new_password or not new_password.strip():
        return False, "Пароль не может быть пустым."

    profile = get_user_profile(user_id, channel_id)
    if not profile:
        return False, "Профиль не найден. Пройдите регистрацию."
    login = profile.get("login")
    phone = profile.get("phone")
    department = (profile.get("department") or "").strip() or None
    if not login:
        return False, "В профиле не указан рабочий логин."
    if not phone:
        return False, "В профиле не указан номер телефона."

    jira_phone = normalize_phone_for_jira(phone)
    # Новый путь: универсальный движок форм (forms_catalog -> rubik_password_change).
    success_new, result_new, _ = await create_issue_from_form(
        "rubik_password_change",
        form_data={"password_new": new_password.strip()},
        profile=profile,
        attachment_paths=[],
    )
    key = result_new if success_new else ""
    result = None
    if not key:
        # Фолбэк: legacy-создание (для обратной совместимости окружений).
        result = await create_password_change_issue(
            ad_account=login,
            existing_phone=jira_phone,
            password_new=new_password.strip(),
            department=department,
        )
        if result and result.get("key"):
            key = result["key"]

    if key:
        # Пытаемся поменять автора на пользователя, который инициировал запрос (если есть jira_username)
        jira_username = (profile.get("jira_username") or "").strip()
        if jira_username:
            from config import CONFIG as _CONFIG

            jira_cfg = _CONFIG.get("JIRA", {})
            base_url = (jira_cfg.get("LOGIN_URL") or "").strip().rstrip("/")
            token = (jira_cfg.get("TOKEN") or "").strip()
            if base_url and token:
                try:
                    await _set_reporter(base_url, token, key, jira_username)
                except Exception as e:
                    logger.warning("Не удалось изменить автора для %s на %s: %s", key, jira_username, e)
        from core.password_requests import add_pending
        add_pending(key, user_id, channel_id)
        try:
            from core.support.issue_binding_registry import add_binding
            add_binding(channel_id, user_id, key, "AA", "rubik_password_change")
        except Exception as e:
            logger.warning("Не удалось записать привязку в реестр: %s", e)
        base = "https://jira.petrovich.tech"
        url = f"{base}/browse/{key}" if base else key
        return True, f"Заявка на смену пароля создана: {key}. Ссылка: {url}"
    # Если новый движок вернул валидную ошибку — покажем пользователю.
    if not success_new and result_new:
        return False, str(result_new)
    # Если Jira вернула ошибки валидации полей — покажем их пользователю
    if isinstance(result, dict) and result.get("errors"):
        from config import CONFIG
        errors = result.get("errors") or {}
        password_field_id = (CONFIG.get("JIRA_AA") or {}).get("FIELDS") or {}
        password_field_id = password_field_id.get("PASSWORD_NEW", "customfield_17506")
        msgs = []
        for fid, v in errors.items():
            if not v or not isinstance(v, str):
                continue
            v = v.strip()
            # Ошибка по полю «новый пароль» — однотипное сообщение для пользователя
            if fid == password_field_id or "пароль" in v.lower() and ("условиям" in v or "шапке" in v or "удовлетворяющий" in v):
                msgs.append("Пароль не удовлетворяет требованиям информационной безопасности, введите другой пароль.")
            else:
                msgs.append(v)
        if msgs:
            return False, "\n".join(msgs)
    return False, "Не удалось создать заявку в Jira. Попробуйте позже или обратитесь на первую линию."
