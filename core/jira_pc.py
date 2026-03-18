"""
Создание заявок «Проблема в работе ПК» в Jira (HD / Incident, request type 377).
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urljoin

import aiohttp

from config import CONFIG
from validators import sanitize_jira_text, normalize_phone_for_jira

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE_MB = 10
MAX_ATTACHMENTS_PER_ISSUE = 10

_DEPARTMENT_CACHE: Optional[Dict[str, Any]] = None
_DEPARTMENT_CACHE_TTL = 3600


async def _get_jsm_request_type_allowed_fields(
    base_url: str,
    token: str,
    service_desk_id: str,
    request_type_id: str,
) -> set[str]:
    """Возвращает список fieldId, разрешенных для request type."""
    url = urljoin(
        base_url + "/",
        f"rest/servicedeskapi/servicedesk/{service_desk_id}/requesttype/{request_type_id}/field",
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.warning("PC request type fields: %s %s", resp.status, await resp.text())
                    return set()
                data = await resp.json()
        values = data.get("values") or []
        return {v.get("fieldId") for v in values if isinstance(v, dict) and v.get("fieldId")}
    except Exception as e:
        logger.warning("PC request type fields failed: %s", e)
        return set()


async def _get_option_id_by_value(
    base_url: str,
    token: str,
    project_key: str,
    issue_type: str,
    field_id: str,
    option_value: str,
) -> Optional[str]:
    """Возвращает id опции для select-поля Jira (через createmeta)."""
    global _DEPARTMENT_CACHE
    now = datetime.now()
    cache_key = f"{project_key}:{issue_type}:{field_id}"
    cache_alive = (
        _DEPARTMENT_CACHE
        and _DEPARTMENT_CACHE.get("key") == cache_key
        and (now - _DEPARTMENT_CACHE.get("ts", now)).total_seconds() < _DEPARTMENT_CACHE_TTL
    )
    value_to_id: Dict[str, str] = {}
    if cache_alive:
        value_to_id = _DEPARTMENT_CACHE.get("value_to_id") or {}
    else:
        url = urljoin(base_url + "/", "rest/api/2/issue/createmeta")
        params = {
            "projectKeys": project_key,
            "issuetypeNames": issue_type,
            "expand": "projects.issuetypes.fields",
        }
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        logger.warning("PC createmeta: %s %s", resp.status, await resp.text())
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
            _DEPARTMENT_CACHE = {"key": cache_key, "ts": now, "value_to_id": value_to_id}
            logger.info("PC: загружено %s опций поля %s", len(value_to_id), field_id)
        except Exception as e:
            logger.exception("PC createmeta (%s): %s", field_id, e)
            return None

    target = (option_value or "").strip()
    if not target:
        return None
    if target in value_to_id:
        return value_to_id[target]
    target_norm = target.lower()
    for value, opt_id in value_to_id.items():
        if (value or "").strip().lower() == target_norm:
            return opt_id
    return None


async def _attach_temporary_file(
    base_url: str,
    token: str,
    service_desk_id: str,
    file_path: str,
) -> Optional[str]:
    """Загружает файл в JSM attachTemporaryFile и возвращает temporaryAttachmentId."""
    path = Path(file_path)
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_ATTACHMENT_SIZE_MB * 1024 * 1024:
        logger.warning("PC attach: файл %s превышает %s МБ", path.name, MAX_ATTACHMENT_SIZE_MB)
        return None

    url = urljoin(base_url + "/", f"rest/servicedeskapi/servicedesk/{service_desk_id}/attachTemporaryFile")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Atlassian-Token": "no-check",
        "X-ExperimentalApi": "opt-in",
    }
    try:
        data = aiohttp.FormData()
        data.add_field("file", path.read_bytes(), filename=path.name, content_type="application/octet-stream")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status not in (200, 201):
                    logger.warning("PC attachTemporaryFile %s: %s %s", path.name, resp.status, (await resp.text())[:200])
                    return None
                body = await resp.json()
                temps = body.get("temporaryAttachments") or []
                if temps and temps[0].get("temporaryAttachmentId"):
                    return str(temps[0]["temporaryAttachmentId"])
    except Exception as e:
        logger.warning("PC attachTemporaryFile %s: %s", path.name, e)
    return None


async def create_pc_issue(
    problem_kind_id: str,
    department: str,
    phone: str,
    jira_username: Optional[str],
    description: str = "",
    attachment_paths: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Создаёт заявку «Проблема в работе ПК». Возвращает (ok, issue_key|error)."""
    jira = CONFIG.get("JIRA", {})
    pc = CONFIG.get("JIRA_PC", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return False, "Не настроено подключение к Jira."

    project_key = (pc.get("PROJECT_KEY") or "HD").strip()
    issue_type = (pc.get("ISSUE_TYPE") or "Incident").strip()
    service_desk_id = (pc.get("SERVICE_DESK_ID") or "1").strip()
    request_type_id = (pc.get("REQUEST_TYPE_ID") or "377").strip()
    field_kind = (pc.get("FIELD_PC_PROBLEM_KIND") or "customfield_11400").strip()
    field_department = (pc.get("FIELD_DEPARTMENT") or "customfield_11406").strip()
    field_phone = (pc.get("FIELD_EXISTING_PHONE") or "customfield_13103").strip()

    problem_kind_id = (problem_kind_id or "").strip()
    department = (department or "").strip()
    phone_jira = normalize_phone_for_jira(phone or "")
    if not problem_kind_id:
        return False, "Выберите категорию проблемы с ПК."
    if not department:
        return False, "В профиле не указано подразделение. Сначала выберите подразделение в другой заявке (например, Lupa)."
    if not phone_jira:
        return False, "В профиле не указан телефон."

    department_id = await _get_option_id_by_value(
        base_url=base_url,
        token=token,
        project_key=project_key,
        issue_type=issue_type,
        field_id=field_department,
        option_value=department,
    )
    if not department_id:
        return False, f"Подразделение «{department}» не найдено в Jira."

    allowed_fields = await _get_jsm_request_type_allowed_fields(
        base_url=base_url,
        token=token,
        service_desk_id=service_desk_id,
        request_type_id=request_type_id,
    )
    if allowed_fields:
        logger.info("PC request type %s allowed fields: %s", request_type_id, sorted(allowed_fields))

    description = sanitize_jira_text((description or "").strip() or "Описание не предоставлено", max_len=4000)
    request_field_values: Dict[str, Any] = {}
    # summary для этого request type не передаём: Jira заполняет/вычисляет тему сама.
    if not allowed_fields or "description" in allowed_fields:
        request_field_values["description"] = description
    if not allowed_fields or field_kind in allowed_fields:
        request_field_values[field_kind] = [{"id": problem_kind_id}]
    if not allowed_fields or field_department in allowed_fields:
        request_field_values[field_department] = {"id": department_id}
    if not allowed_fields or field_phone in allowed_fields:
        request_field_values[field_phone] = phone_jira

    files = list(attachment_paths or [])[:MAX_ATTACHMENTS_PER_ISSUE]
    if files:
        temp_ids = []
        for file_path in files:
            tid = await _attach_temporary_file(base_url, token, service_desk_id, file_path)
            if tid:
                temp_ids.append(tid)
        if temp_ids and (not allowed_fields or "attachment" in allowed_fields):
            request_field_values["attachment"] = temp_ids

    payload = {
        "serviceDeskId": str(service_desk_id),
        "requestTypeId": str(request_type_id),
        "requestFieldValues": request_field_values,
    }
    url = urljoin(base_url + "/", "rest/servicedeskapi/request")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    issue_key = (data.get("issueKey") or "").strip()
                    if not issue_key:
                        return False, "Jira вернула пустой ключ заявки."
                    if jira_username:
                        try:
                            from core.jira_aa import _set_reporter  # type: ignore[attr-defined]
                            await _set_reporter(base_url, token, issue_key, jira_username)
                        except Exception as e:
                            logger.warning("PC: не удалось изменить автора для %s на %s: %s", issue_key, jira_username, e)
                    return True, issue_key
                text = await resp.text()
                logger.warning("PC create failed: %s %s", resp.status, text[:500])
                return False, f"Ошибка Jira: {resp.status}. {text[:200]}"
    except Exception as e:
        logger.exception("PC create exception: %s", e)
        return False, str(e)
