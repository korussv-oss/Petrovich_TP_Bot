"""
Создание заявок в Jira проект WHD (Lupa, поиск petrovich.ru).
Тип Incident, кастомные поля: problematic_service, request_type, subdivision, service, address_city.
Поле subdivision (customfield_11406) в WHD принимает option id, а не value — получаем id через createmeta.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urljoin

import aiohttp

from config import CONFIG
from core.jira_labels import JIRA_LABELS_CHATBOT
from validators import sanitize_jira_text

logger = logging.getLogger(__name__)

_SUBDIVISION_CACHE: Optional[Dict[str, Any]] = None
_SUBDIVISION_CACHE_TTL = 3600


async def _get_lupa_subdivision_id(
    base_url: str,
    token: str,
    project_key: str,
    issue_type: str,
    field_id: str,
    subdivision_value: str,
) -> Optional[str]:
    """
    Возвращает id опции подразделения для поля customfield_11406 в проекте WHD (createmeta).
    Jira ожидает {"id": "..."}, иначе "Option id 'null' is not valid".
    """
    global _SUBDIVISION_CACHE
    now = datetime.now()
    if _SUBDIVISION_CACHE and (now - _SUBDIVISION_CACHE.get("ts", now)).total_seconds() < _SUBDIVISION_CACHE_TTL:
        value_to_id = _SUBDIVISION_CACHE.get("value_to_id", {})
        # Точное совпадение
        opt_id = value_to_id.get(subdivision_value)
        if opt_id:
            return opt_id
        # Без учёта регистра
        val_norm = subdivision_value.strip().lower()
        for v, oid in value_to_id.items():
            if (v or "").strip().lower() == val_norm:
                return oid
        return None

    url = urljoin(base_url + "/", "rest/api/2/issue/createmeta")
    params = {
        "projectKeys": project_key,
        "issuetypeNames": issue_type,
        "expand": "projects.issuetypes.fields",
    }
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    value_to_id: Dict[str, str] = {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.warning("Lupa createmeta: %s %s", resp.status, await resp.text())
                    return None
                data = await resp.json()
        for project in data.get("projects", []):
            for it in project.get("issuetypes", []):
                fields = it.get("fields", {})
                if field_id not in fields:
                    continue
                for val in fields[field_id].get("allowedValues", []):
                    if not isinstance(val, dict):
                        continue
                    opt_id = val.get("id")
                    name = (val.get("value") or val.get("name") or "").strip()
                    if opt_id is not None and name:
                        value_to_id[name] = str(opt_id)
        _SUBDIVISION_CACHE = {"ts": now, "value_to_id": value_to_id}
        logger.info("Lupa: загружено %s опций подразделения из createmeta", len(value_to_id))
    except Exception as e:
        logger.exception("Lupa createmeta подразделений: %s", e)
        return None

    opt_id = value_to_id.get(subdivision_value)
    if opt_id:
        return opt_id
    val_norm = subdivision_value.strip().lower()
    for v, oid in value_to_id.items():
        if (v or "").strip().lower() == val_norm:
            return oid
    return None


async def create_lupa_issue(
    description: str,
    problematic_service: Optional[str] = None,
    request_type: Optional[str] = None,
    subdivision: Optional[str] = None,
    city: Optional[str] = None,
    jira_username: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Создаёт заявку (Incident) в проекте WHD.
    Поля — отображаемые значения для select-полей Jira ({ "value": "..." }).
    Возвращает (успех, issue_key или сообщение об ошибке).
    """
    jira = CONFIG.get("JIRA", {})
    lupa = CONFIG.get("JIRA_LUPA", {})
    base_url = (jira.get("LOGIN_URL") or "").rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA: не заданы LOGIN_URL или TOKEN")
        return False, "Не настроено подключение к Jira."

    project_key = (lupa.get("PROJECT_KEY") or "WHD").strip()
    issue_type = (lupa.get("ISSUE_TYPE") or "Incident").strip()
    f_problematic = (lupa.get("FIELD_PROBLEMATIC_SERVICE") or "customfield_12312").strip()
    f_request_type = (lupa.get("FIELD_REQUEST_TYPE") or "customfield_15800").strip()
    f_subdivision = (lupa.get("FIELD_SUBDIVISION") or "customfield_11406").strip()
    f_service = (lupa.get("FIELD_SERVICE") or "customfield_10500").strip()
    f_city = (lupa.get("FIELD_ADDRESS_CITY") or "customfield_12403").strip()

    description = sanitize_jira_text((description or "").strip() or "Описание не предоставлено", max_len=4000)
    problematic_service = (problematic_service or "Сайт (petrovich.ru)").strip()
    request_type = (request_type or "проблемы с поиском").strip()
    subdivision = (subdivision or "").strip()
    if not subdivision:
        return False, "Укажите подразделение."

    # customfield_11406 в WHD принимает только option id, иначе "Option id 'null' is not valid"
    subdivision_id = await _get_lupa_subdivision_id(
        base_url, token, project_key, issue_type, f_subdivision, subdivision
    )
    if not subdivision_id:
        return False, f'Подразделение «{subdivision}» не найдено в Jira. Выберите в Личном кабинете подразделение из списка (или уточните написание, например «Петрович-Тех»).'

    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": "Ошибка в поиске на petrovich.ru",
            "description": description,
            "issuetype": {"name": issue_type},
            "priority": {"name": "Medium"},
            "labels": list(JIRA_LABELS_CHATBOT),
            f_problematic: {"value": problematic_service},
            f_request_type: {"value": request_type},
            f_subdivision: {"id": subdivision_id},
            f_service: "Поиск",
        }
    }
    if city and (city := sanitize_jira_text((city or "").strip(), max_len=120)):
        payload["fields"][f_city] = city

    url = urljoin(base_url + "/", "rest/api/2/issue")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    issue_key = data.get("key", "")
                    logger.info("Lupa заявка создана: %s", issue_key)
                    # Пытаемся сменить автора на jira_username (если настроен)
                    if jira_username:
                        try:
                            from core.jira_aa import _set_reporter  # type: ignore[attr-defined]
                            await _set_reporter(base_url, token, issue_key, jira_username)
                        except Exception as e:
                            logger.warning("Lupa: не удалось изменить автора для %s на %s: %s", issue_key, jira_username, e)
                    return True, issue_key
                text = await resp.text()
                logger.warning("Lupa создание заявки: HTTP %s %s", resp.status, text[:500])
                return False, f"Ошибка Jira: {resp.status}. {text[:200]}"
    except Exception as e:
        logger.exception("Lupa создание заявки: %s", e)
        return False, str(e)
