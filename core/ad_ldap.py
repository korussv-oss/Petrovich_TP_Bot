"""
Поиск пользователя в AD по номеру телефона (и опционально по почте).
Используется при регистрации: почта + контакт → поиск по телефону в AD → профиль или ссылка на портал.
"""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Атрибуты AD для профиля бота (поиск по телефону)
AD_ATTRS = [
    "displayName",
    "cn",
    "sAMAccountName",
    "mail",
    "userPrincipalName",
    "telephoneNumber",
    "mobile",
    "ipPhone",
    "department",
    "title",
]

# Атрибуты для проверки статуса пароля
AD_PASSWORD_ATTRS = [
    "pwdLastSet",
    "userAccountControl",
    "msDS-UserPasswordExpiryTimeComputed",
]


def _normalize_phone_digits(phone: str) -> str:
    """Последние 10 цифр номера (без ведущей 7) для поиска в AD."""
    digits = re.sub(r"\D", "", (phone or "").strip())
    if len(digits) >= 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def _decode_value(val: Any) -> str:
    """Строка из атрибута AD (может быть bytes/base64 в LDIF)."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8")
        except Exception:
            return ""
    if isinstance(val, list):
        return _decode_value(val[0]) if val else ""
    s = str(val).strip()
    # LDIF base64 (::) — уже декодируется ldap3 в строку или bytes
    return s


def _get_first(entry_attrs: Dict[str, List], key: str) -> str:
    raw = entry_attrs.get(key)
    if raw is None:
        return ""
    if isinstance(raw, list) and raw:
        v = raw[0]
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8").strip()
            except Exception:
                return ""
        return str(v).strip()
    return str(raw).strip() if raw else ""


def _ad_config() -> Optional[Dict[str, Any]]:
    from config import CONFIG

    ad = CONFIG.get("AD_LDAP") or {}
    url = (ad.get("URL") or "").strip()
    bind_user = (ad.get("BIND_USER") or "").strip()
    bind_password = (ad.get("BIND_PASSWORD") or "").strip()
    base_dn = (ad.get("BASE_DN") or "").strip()
    verify_ssl = ad.get("VERIFY_SSL", False)

    if not url or not bind_user or not bind_password or not base_dn:
        logger.warning("AD_LDAP не настроен (URL/BIND_USER/BIND_PASSWORD/BASE_DN)")
        return None

    return {
        "url": url,
        "bind_user": bind_user,
        "bind_password": bind_password,
        "base_dn": base_dn,
        "verify_ssl": verify_ssl,
    }


def _connect_ad(url: str, bind_user: str, bind_password: str, verify_ssl: bool):
    from ldap3 import Server, Connection, ALL, Tls
    import ssl

    use_ssl = url.lower().startswith("ldaps://")
    if "://" in url:
        url = url.split("://", 1)[1]
    host, _, port_str = url.partition(":")
    port = int(port_str) if port_str else (636 if use_ssl else 389)

    tls = None
    if use_ssl and not verify_ssl:
        tls = Tls(validate=ssl.CERT_NONE)

    server = Server(host, port=port, use_ssl=use_ssl, tls=tls, get_info=ALL)
    return Connection(server, user=bind_user, password=bind_password, auto_bind=True)


def _derive_domain_base_dn(base_dn: str) -> str:
    """
    Пытается вывести корневой DN домена из BASE_DN (например, из OU=...,DC=a,DC=b -> DC=a,DC=b).
    Если не удалось, возвращает исходный base_dn.
    """
    parts = [p.strip() for p in (base_dn or "").split(",") if p.strip()]
    dcs = [p for p in parts if p.lower().startswith("dc=")]
    return ",".join(dcs) if dcs else (base_dn or "")


def _profile_from_attrs(attrs: Dict[str, List], fallback_phone: str = "") -> Optional[Dict[str, str]]:
    full_name = _get_first(attrs, "displayName") or _get_first(attrs, "cn")
    login = _get_first(attrs, "sAMAccountName")
    email = _get_first(attrs, "mail") or _get_first(attrs, "userPrincipalName")
    phone_raw = (
        _get_first(attrs, "mobile") or _get_first(attrs, "telephoneNumber") or _get_first(attrs, "ipPhone")
    )
    department = _get_first(attrs, "department")
    title = _get_first(attrs, "title")

    if not login and not email:
        return None

    from validators import normalize_phone_display

    phone_display = (
        normalize_phone_display(phone_raw) if phone_raw else (normalize_phone_display(fallback_phone) if fallback_phone else "")
    )

    out: Dict[str, str] = {
        "full_name": full_name or "",
        "login": (login or "").strip().lower(),
        "email": (email or "").strip().lower(),
        "phone": phone_display,
        "department": (department or "").strip(),
    }
    if (title or "").strip():
        # Должность для JSM (AA и др.): в AD обычно в атрибуте title
        out["position"] = (title or "").strip()
    return out


def search_users_by_query(query: str, *, limit: int = 10) -> List[Dict[str, str]]:
    """
    Ищет пользователей в AD по произвольному запросу: ФИО/логин/почта/часть строки.
    Возвращает список профилей (могут быть несколько совпадений).

    Важно: функция не используется в боевом флоу регистрации; сделана для диагностики.
    """
    cfg = _ad_config()
    if not cfg:
        return []

    q = (query or "").strip()
    if not q:
        return []

    tokens = [t for t in re.split(r"\s+", q) if t]
    if not tokens:
        return []

    def _escape_ldap(s: str) -> str:
        # Минимальное экранирование LDAP filter special chars: \ * ( ) NUL
        return (
            s.replace("\\", r"\5c")
            .replace("*", r"\2a")
            .replace("(", r"\28")
            .replace(")", r"\29")
            .replace("\x00", r"\00")
        )

    # Для каждого токена — OR по ключевым атрибутам; все токены — AND.
    # Это позволяет искать "Ноздря Петр" как пересечение.
    token_filters: List[str] = []
    for t in tokens:
        et = _escape_ldap(t)
        token_filters.append(
            "(|"
            f"(displayName=*{et}*)"
            f"(cn=*{et}*)"
            f"(givenName=*{et}*)"
            f"(sn=*{et}*)"
            f"(sAMAccountName=*{et}*)"
            f"(mail=*{et}*)"
            f"(userPrincipalName=*{et}*)"
            ")"
        )
    search_filter = "(&" + "".join(token_filters) + ")"

    try:
        from ldap3 import SUBTREE

        conn = _connect_ad(cfg["url"], cfg["bind_user"], cfg["bind_password"], cfg["verify_ssl"])
        bases = [cfg["base_dn"]]
        domain_base = _derive_domain_base_dn(cfg["base_dn"])
        if domain_base and domain_base != cfg["base_dn"]:
            bases.append(domain_base)

        entries = []
        for base_dn in bases:
            conn.search(
                base_dn,
                search_filter,
                search_scope=SUBTREE,
                attributes=AD_ATTRS,
                size_limit=max(1, int(limit)),
            )
            if conn.entries:
                entries = list(conn.entries)
                break
        conn.unbind()

        out: List[Dict[str, str]] = []
        for entry in entries:
            attrs = entry.entry_attributes_as_dict
            p = _profile_from_attrs(attrs)
            if p:
                out.append(p)
        return out
    except Exception as e:
        logger.exception("AD search by query failed: %s", e)
        return []


def search_user_by_phone(phone: str) -> Optional[Dict[str, str]]:
    """
    Ищет в AD пользователя по номеру телефона (telephoneNumber, mobile, ipPhone).
    Возвращает профиль для бота: full_name, login, email, phone, department;
    при наличии в AD — position (из title).
    или None, если не найден / AD недоступен.
    """
    cfg = _ad_config()
    if not cfg:
        return None

    digits = _normalize_phone_digits(phone)
    if len(digits) < 10:
        return None

    try:
        from ldap3 import SUBTREE

        conn = _connect_ad(cfg["url"], cfg["bind_user"], cfg["bind_password"], cfg["verify_ssl"])

        # Поиск по телефону: в AD номера могут быть +7911..., 8911..., 911...
        search_filter = (
            f"(|(telephoneNumber=*{digits}*)(mobile=*{digits}*)(ipPhone=*{digits}*))"
        )
        bases = [cfg["base_dn"]]
        domain_base = _derive_domain_base_dn(cfg["base_dn"])
        if domain_base and domain_base != cfg["base_dn"]:
            bases.append(domain_base)

        found_entry = None
        for base_dn in bases:
            conn.search(
                base_dn,
                search_filter,
                search_scope=SUBTREE,
                attributes=AD_ATTRS,
                size_limit=5,
            )
            if conn.entries:
                found_entry = conn.entries[0]
                break

        if not found_entry:
            conn.unbind()
            return None

        entry = found_entry
        conn.unbind()
        attrs = entry.entry_attributes_as_dict
        return _profile_from_attrs(attrs, fallback_phone=phone)
    except Exception as e:
        logger.exception("AD search by phone failed: %s", e)
        return None


def is_password_expired(login: str) -> Optional[bool]:
    """
    Проверяет по AD, помечён ли пароль пользователя как истёкший.
    Возвращает:
      - True  — пароль истёк (PasswordExpired);
      - False — пароль не истёк;
      - None  — не удалось определить (ошибка подключения/поиска).
    Безопасное поведение на стороне вызывающего кода: при None лучше не разрешать смену
    пароля через бота и предложить обратиться в поддержку.
    """
    cfg = _ad_config()
    if not cfg:
        return None

    login = (login or "").strip()
    if not login:
        return None

    try:
        from ldap3 import SUBTREE
        from datetime import datetime, timezone

        conn = _connect_ad(cfg["url"], cfg["bind_user"], cfg["bind_password"], cfg["verify_ssl"])

        search_filter = f"(sAMAccountName={login})"
        conn.search(
            cfg["base_dn"],
            search_filter,
            search_scope=SUBTREE,
            attributes=AD_PASSWORD_ATTRS,
            size_limit=1,
        )
        if not conn.entries:
            conn.unbind()
            return None
        entry = conn.entries[0]
        conn.unbind()
        attrs = entry.entry_attributes_as_dict

        # Явный флаг PASSWORD_EXPIRED (0x800000) в userAccountControl
        uac_raw = _get_first(attrs, "userAccountControl")
        try:
            uac = int(uac_raw) if uac_raw else 0
        except Exception:
            uac = 0
        PASSWORD_EXPIRED_FLAG = 0x800000
        if uac & PASSWORD_EXPIRED_FLAG:
            return True

        # Сравнение msDS-UserPasswordExpiryTimeComputed с текущим временем
        exp_raw = _get_first(attrs, "msDS-UserPasswordExpiryTimeComputed")
        if not exp_raw:
            return None
        filetime = int(exp_raw)
        # FILETIME (100-нс тики с 1601-01-01) -> Unix epoch
        unix_ts = (filetime - 116444736000000000) / 10**7
        expiry_dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        return now >= expiry_dt
    except Exception as e:
        logger.exception("AD password expired check failed for %s: %s", login, e)
        return None
