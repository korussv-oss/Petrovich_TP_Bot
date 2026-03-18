"""
Хранение пользователей: telegram_id -> профиль (ФИО, логин, почта, телефон).
Индексы по логину и почте для проверки дубликатов; поиск по телефону для MAX.
Опционально: шифрование персональных полей в покое (ENCRYPT_USER_DATA=1, USER_DATA_ENCRYPTION_KEY).
"""
import json
import os
from typing import Optional, Dict, Any, Tuple, List

USERS_DB = "data/user_data.json"
INDEX_LOGIN = "data/index_by_login.json"
INDEX_EMAIL = "data/index_by_email.json"
INDEX_PHONE = "data/index_by_phone.json"
INDEX_EMPLOYEE_ID = "data/index_by_employee_id.json"
INDEX_MAX_USER = "data/index_by_max_user.json"  # max_user_id -> telegram_id (привязка канала MAX)

# Поля профиля, которые шифруются при хранении (если включено)
_ENCRYPTED_FIELDS: List[str] = ["full_name", "login", "email", "phone"]


def _encryption_enabled() -> bool:
    return os.getenv("ENCRYPT_USER_DATA", "").strip().lower() in ("1", "true", "yes")


def _get_fernet():
    """Возвращает Fernet или None, если шифрование недоступно."""
    if not _encryption_enabled():
        return None
    key = (os.getenv("USER_DATA_ENCRYPTION_KEY") or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def _encrypt_value(fernet, value: str) -> str:
    if not fernet or not value:
        return value
    try:
        return fernet.encrypt(value.encode("utf-8")).decode("ascii")
    except Exception:
        return value


def _decrypt_value(fernet, value: str) -> str:
    if not fernet or not value:
        return value
    try:
        return fernet.decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        return value


def _ensure_dir():
    for path in (USERS_DB, INDEX_LOGIN, INDEX_EMAIL, INDEX_PHONE, INDEX_EMPLOYEE_ID, INDEX_MAX_USER):
        os.makedirs(os.path.dirname(path), exist_ok=True)


def _load_json(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data: dict):
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_phone_key(phone: str) -> str:
    import re
    digits = re.sub(r"\D", "", (phone or "").strip())
    if len(digits) >= 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits[-10:] if len(digits) >= 10 else digits


def load_user_db() -> Dict[str, Dict[str, Any]]:
    """Ключи — строковые telegram_id. При включённом шифровании расшифровывает персональные поля."""
    _ensure_dir()
    raw = _load_json(USERS_DB, {})
    db = {str(k): dict(v) for k, v in raw.items()}
    fernet = _get_fernet()
    if fernet:
        for uid, profile in db.items():
            for key in _ENCRYPTED_FIELDS:
                if key in profile and isinstance(profile[key], str):
                    profile[key] = _decrypt_value(fernet, profile[key])
    return db


def save_user_db(db: Dict[str, Dict[str, Any]]):
    """Сохраняет БД. Индексы строятся по расшифрованным данным; в файл пишутся данные (при включённом шифровании — зашифрованные)."""
    _rebuild_indexes(db)
    to_save = db
    fernet = _get_fernet()
    if fernet:
        to_save = {}
        for uid, profile in db.items():
            to_save[uid] = dict(profile)
            for key in _ENCRYPTED_FIELDS:
                if key in to_save[uid] and isinstance(to_save[uid][key], str):
                    to_save[uid][key] = _encrypt_value(fernet, to_save[uid][key])
    _save_json(USERS_DB, to_save)


def _rebuild_indexes(db: Dict[str, Dict[str, Any]]):
    by_login = {}
    by_email = {}
    by_phone = {}
    by_employee_id = {}
    for uid, profile in db.items():
        login = (profile.get("login") or "").strip().lower()
        email = (profile.get("email") or "").strip().lower()
        phone = profile.get("phone") or ""
        employee_id = (profile.get("employee_id") or "").strip()
        if login:
            by_login[login] = uid
        if email:
            by_email[email] = uid
        if phone:
            key = _normalize_phone_key(phone)
            if key:
                by_phone[key] = uid
        if employee_id:
            by_employee_id[employee_id] = uid
    _save_json(INDEX_LOGIN, by_login)
    _save_json(INDEX_EMAIL, by_email)
    _save_json(INDEX_PHONE, by_phone)
    _save_json(INDEX_EMPLOYEE_ID, by_employee_id)


def resolve_channel_user_id(channel_id: str, user_id: int) -> int:
    """
    Преобразует (channel_id, user_id) в «основной» ключ профиля (telegram_id или max_user_id).
    Для Telegram: возвращает user_id. Для MAX: если есть привязка max_user_id -> telegram_id,
    возвращает telegram_id; иначе user_id (профиль может быть заведён по max_user_id).
    """
    if (channel_id or "").strip().lower() != "max":
        return int(user_id)
    idx = _load_json(INDEX_MAX_USER, {})
    primary = idx.get(str(user_id))
    return int(primary) if primary is not None else int(user_id)


def needs_phone_verification_channel(channel_id: str, user_id: int) -> bool:
    """
    Нужна ли этому пользователю (по основному профилю) актуализация номера телефона.
    Используется для профилей, импортированных из Лупы.
    """
    primary = resolve_channel_user_id(channel_id, user_id)
    db = load_user_db()
    profile = db.get(str(primary))
    if not profile:
        return False
    return bool(profile.get("phone_needs_verification"))


def update_phone_and_mark_verified_channel(channel_id: str, user_id: int, phone: str) -> None:
    """
    Обновить телефон в профиле (по основному user_id) и снять флаг проверки телефона.
    Канал (telegram/max) мапится в основной ключ через resolve_channel_user_id.
    """
    primary = resolve_channel_user_id(channel_id, user_id)
    db = load_user_db()
    profile = db.get(str(primary)) or {}
    profile["phone"] = phone
    # Флаг нужен только до первой успешной актуализации
    if "phone_needs_verification" in profile:
        profile.pop("phone_needs_verification", None)
    db[str(primary)] = profile
    save_user_db(db)


def get_user_profile(user_id: int, channel_id: str = "telegram") -> Optional[Dict[str, Any]]:
    """Профиль пользователя. Для MAX: user_id — это max_user_id, при необходимости разрешается в telegram_id."""
    primary = resolve_channel_user_id(channel_id, user_id)
    db = load_user_db()
    return db.get(str(primary))


def save_user_profile(user_id: int, profile: Dict[str, Any], old_profile: Optional[Dict[str, Any]] = None):
    """Сохраняет профиль и перестраивает индексы (логин, email, телефон)."""
    db = load_user_db()
    db[str(user_id)] = profile
    save_user_db(db)


def is_user_registered(user_id: int, channel_id: str = "telegram") -> bool:
    """Проверка: зарегистрирован ли пользователь. Для MAX учитывается привязка max_user_id -> профиль."""
    p = get_user_profile(user_id, channel_id)
    if not p:
        return False
    return all(p.get(f) for f in ("full_name", "login", "email", "phone"))


def link_max_user_to_telegram(max_user_id: int, telegram_id: int) -> None:
    """Привязать пользователя MAX к существующему профилю (по telegram_id)."""
    _ensure_dir()
    idx = _load_json(INDEX_MAX_USER, {})
    idx[str(max_user_id)] = str(telegram_id)
    _save_json(INDEX_MAX_USER, idx)


def get_linked_channel_user_pairs(channel_id: str, user_id: int) -> List[Tuple[str, int]]:
    """
    Возвращает список (channel_id, channel_user_id) для этого пользователя:
    текущий канал + привязанные (MAX↔Telegram). Нужно для «Мои заявки» — показывать заявки из обоих ботов.
    """
    out = [(channel_id.strip().lower() or "telegram", int(user_id))]
    idx = _load_json(INDEX_MAX_USER, {})
    if (channel_id or "").strip().lower() == "max":
        tg_id = idx.get(str(user_id))
        if tg_id:
            out.append(("telegram", int(tg_id)))
    else:
        tg_id = int(user_id)
        for mid, tid in idx.items():
            if str(tid) == str(tg_id):
                out.append(("max", int(mid)))
    return out


def find_by_login(login: str) -> Optional[int]:
    if not login:
        return None
    idx = _load_json(INDEX_LOGIN, {})
    uid = idx.get(login.strip().lower())
    return int(uid) if uid else None


def find_by_email(email: str) -> Optional[int]:
    if not email:
        return None
    idx = _load_json(INDEX_EMAIL, {})
    uid = idx.get(email.strip().lower())
    return int(uid) if uid else None


def find_by_phone(phone: str) -> Optional[int]:
    """Поиск по номеру телефона (для идентификации в MAX и привязки аккаунта)."""
    if not phone:
        return None
    key = _normalize_phone_key(phone)
    if not key:
        return None
    idx = _load_json(INDEX_PHONE, {})
    uid = idx.get(key)
    if not uid:
        for k, v in idx.items():
            if k.endswith(key) or key.endswith(k):
                return int(v)
        return None
    return int(uid)


def bind_account_by_phone(
    current_user_id: int, phone: str, channel_id: str = "telegram"
) -> Tuple[bool, str]:
    """
    Привязка аккаунта по телефону.
    Telegram: если есть профиль с этим номером — копируем в current_user_id (telegram_id), старый удаляем.
    MAX: если есть профиль с этим номером — привязываем max_user_id к этому профилю (link_max_user_to_telegram).
    Возвращает (успех, сообщение).
    """
    existing_uid = find_by_phone(phone)
    if existing_uid is None:
        return False, "Аккаунт с таким номером телефона не найден. Зарегистрируйтесь."
    if (channel_id or "").strip().lower() == "max":
        link_max_user_to_telegram(current_user_id, existing_uid)
        return True, "Аккаунт успешно привязан. Теперь вы можете пользоваться ботом в MAX."
    if existing_uid == current_user_id:
        return True, "Этот аккаунт уже привязан к вам."
    db = load_user_db()
    existing_profile = db.get(str(existing_uid))
    if not existing_profile:
        return False, "Профиль не найден."
    profile_copy = dict(existing_profile)
    db[str(current_user_id)] = profile_copy
    del db[str(existing_uid)]
    save_user_db(db)
    # Если старый ключ — max_user_id (профиль создан в MAX), сохраняем связь MAX↔TG,
    # чтобы пользователь мог продолжать заходить и из MAX, и из Telegram.
    link_max_user_to_telegram(existing_uid, current_user_id)
    return True, "Аккаунт успешно привязан."


def find_by_employee_id(employee_id: str) -> Optional[int]:
    """Поиск пользователя по табельному номеру."""
    eid = (employee_id or "").strip()
    if not eid:
        return None
    idx = _load_json(INDEX_EMPLOYEE_ID, {})
    uid = idx.get(eid)
    return int(uid) if uid else None


def check_employee_id_taken(employee_id: str, exclude_user_id: Optional[int] = None) -> Tuple[bool, Optional[int]]:
    """Проверяет, занят ли табельный номер другим пользователем. Возвращает (занято, user_id владельца)."""
    owner = find_by_employee_id(employee_id)
    if owner is None:
        return False, None
    if exclude_user_id is not None and owner == exclude_user_id:
        return False, None
    return True, owner


def check_login_or_email_taken(login: str, email: str, exclude_user_id: Optional[int] = None) -> Tuple[bool, str]:
    """
    Проверяет, заняты ли логин или почта другим пользователем.
    exclude_user_id — текущий пользователь (при смене учётных данных).
    Возвращает (занято, сообщение).
    """
    login = (login or "").strip().lower()
    email = (email or "").strip().lower()
    uid_login = find_by_login(login)
    uid_email = find_by_email(email)
    if uid_login is not None and uid_login != exclude_user_id:
        return True, "Пользователь с таким рабочим логином уже зарегистрирован. Обратитесь на первую линию поддержки."
    if uid_email is not None and uid_email != exclude_user_id:
        return True, "Пользователь с такой корпоративной почтой уже зарегистрирован. Обратитесь на первую линию поддержки."
    return False, ""


def delete_user(user_id: int) -> bool:
    """Удаляет пользователя по telegram_id. Возвращает True если был удалён."""
    db = load_user_db()
    uid_str = str(user_id)
    if uid_str not in db:
        return False
    del db[uid_str]
    save_user_db(db)
    return True


def get_all_user_ids() -> list:
    db = load_user_db()
    return [int(k) for k in db.keys()]


def get_all_users_sorted() -> list:
    """Список (user_id, profile) всех пользователей, отсортированный по ФИО."""
    db = load_user_db()
    items = [(int(uid), profile) for uid, profile in db.items()]
    items.sort(key=lambda x: (x[1].get("full_name") or "").strip().lower())
    return items


def find_users_by_jira_username(jira_username: str) -> list:
    """
    Возвращает список telegram_id, у которых в профиле jira_username совпадает.
    Сопоставление без учёта регистра.
    """
    target = (jira_username or "").strip().lower()
    if not target:
        return []
    db = load_user_db()
    out = []
    for uid, profile in db.items():
        ju = (profile.get("jira_username") or "").strip().lower()
        if ju and ju == target:
            try:
                out.append(int(uid))
            except Exception:
                continue
    return out


def get_linked_max_user_ids(telegram_id: int) -> list:
    """Список max_user_id, привязанных к telegram_id."""
    tg = str(int(telegram_id))
    idx = _load_json(INDEX_MAX_USER, {})
    out = []
    for max_uid, mapped_tg in idx.items():
        if str(mapped_tg) == tg:
            try:
                out.append(int(max_uid))
            except Exception:
                continue
    return out


def search_users_by_fio(partial: str, limit: int = 50) -> list:
    """Поиск по части ФИО (без учёта регистра). Возвращает список (user_id, profile)."""
    if not (partial or "").strip():
        return []
    part = (partial or "").strip().lower()
    all_users = get_all_users_sorted()
    return [
        (uid, profile)
        for uid, profile in all_users
        if part in ((profile.get("full_name") or "").strip().lower())
    ][:limit]
