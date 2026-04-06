"""Универсальный движок отправки JSM-форм по каталогу."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp

from config import CONFIG
from core.forms_catalog import get_form_definition
from validators import sanitize_jira_text, normalize_phone_for_jira

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE_MB = 10
MAX_ATTACHMENTS_PER_ISSUE = 10


def _resolve_source(source: str, form_data: Dict[str, Any], profile: Dict[str, Any], attachment_paths: List[str]) -> Any:
    s = (source or "").strip()
    if s == "attachments":
        return attachment_paths
    if s.startswith("form."):
        return form_data.get(s[5:])
    if s.startswith("profile."):
        return profile.get(s[8:])
    return None


def _friendly_required_field_message(field_id: str) -> str:
    if field_id == "customfield_11406":
        return (
            "Не заполнено подразделение в учётных данных бота. "
            "Бот должен был предложить выбрать подразделение — начните создание заявки заново."
        )
    return f"Не заполнено обязательное поле формы: {field_id}."


async def _get_request_type_fields(base_url: str, token: str, service_desk_id: str, request_type_id: str) -> List[dict]:
    url = urljoin(base_url + "/", f"rest/servicedeskapi/servicedesk/{service_desk_id}/requesttype/{request_type_id}/field")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}", "X-ExperimentalApi": "opt-in"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    logger.warning("FormEngine fields failed: %s %s", resp.status, await resp.text())
                    return []
                data = await resp.json()
        return list(data.get("requestTypeFields") or data.get("values") or [])
    except Exception as e:
        logger.warning("FormEngine fields exception: %s", e)
        return []


def _option_payload(field_schema: List[dict], field_id: str, selected: Any) -> Optional[dict]:
    target = (str(selected or "")).strip()
    if not target:
        return None
    target_l = target.lower()
    for f in field_schema:
        if not isinstance(f, dict) or f.get("fieldId") != field_id:
            continue
        for opt in (f.get("validValues") or []):
            if not isinstance(opt, dict):
                continue
            label = (opt.get("label") or "").strip()
            value = (opt.get("value") or "").strip()
            if label.lower() == target_l or value.lower() == target_l:
                if value:
                    return {"id": value}
                if label:
                    return {"value": label}
        break
    return {"value": target}


def _expects_option_array(field_schema: List[dict], field_id: str) -> bool:
    for f in field_schema:
        if not isinstance(f, dict) or f.get("fieldId") != field_id:
            continue
        schema = f.get("jiraSchema") or {}
        if not isinstance(schema, dict):
            return False
        return (schema.get("type") == "array") and (schema.get("items") == "option")
    return False


async def _attach_temporary_file(base_url: str, token: str, service_desk_id: str, file_path: str) -> Optional[str]:
    path = Path(file_path)
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_ATTACHMENT_SIZE_MB * 1024 * 1024:
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
                    return None
                body = await resp.json()
                temps = body.get("temporaryAttachments") or []
                if temps and temps[0].get("temporaryAttachmentId"):
                    return str(temps[0]["temporaryAttachmentId"])
    except Exception:
        return None
    return None


async def _get_createmeta_option_id(
    base_url: str,
    token: str,
    project_key: str,
    issue_type: str,
    field_id: str,
    option_value: str,
) -> Optional[str]:
    url = urljoin(base_url + "/", "rest/api/2/issue/createmeta")
    params = {
        "projectKeys": project_key,
        "issuetypeNames": issue_type,
        "expand": "projects.issuetypes.fields",
    }
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        target = (option_value or "").strip().lower()
        if not target:
            return None
        for p in data.get("projects", []):
            for it in p.get("issuetypes", []):
                fields = it.get("fields", {})
                if field_id not in fields:
                    continue
                for val in fields[field_id].get("allowedValues", []):
                    if not isinstance(val, dict):
                        continue
                    label = (val.get("value") or val.get("name") or "").strip().lower()
                    if label == target:
                        oid = val.get("id")
                        return str(oid) if oid is not None else None
        return None
    except Exception:
        return None


async def _create_jsm_issue(
    form: Dict[str, Any],
    jira_def: Dict[str, Any],
    form_data: Dict[str, Any],
    profile: Dict[str, Any],
    attachment_paths: List[str],
    base_url: str,
    token: str,
) -> Tuple[bool, str, Optional[str]]:
    project_key = (form.get("project_key") or "").strip() or None
    service_desk_id = (jira_def.get("service_desk_id") or "").strip()
    request_type_id = (jira_def.get("request_type_id") or "").strip()
    if not service_desk_id or not request_type_id:
        return False, "В форме не заданы service_desk_id/request_type_id.", project_key
    field_schema = await _get_request_type_fields(base_url, token, service_desk_id, request_type_id)
    allowed = {f.get("fieldId") for f in field_schema if isinstance(f, dict) and f.get("fieldId")}
    payload_fields: Dict[str, Any] = {}
    field_map = jira_def.get("fields") or {}
    for field_id, rule in field_map.items():
        if not isinstance(rule, dict):
            continue
        source = (rule.get("source") or "").strip()
        ftype = (rule.get("type") or "text").strip()
        required = bool(rule.get("required"))
        raw_value = _resolve_source(source, form_data, profile, attachment_paths)
        if ftype == "attachment":
            if not raw_value or (allowed and field_id not in allowed):
                continue
            temp_ids = []
            for p in list(raw_value)[:MAX_ATTACHMENTS_PER_ISSUE]:
                tid = await _attach_temporary_file(base_url, token, service_desk_id, p)
                if tid:
                    temp_ids.append(tid)
            if temp_ids:
                payload_fields[field_id] = temp_ids
            continue
        if ftype == "phone":
            value = normalize_phone_for_jira(str(raw_value or ""))
        elif ftype == "option":
            value = _option_payload(field_schema, field_id, raw_value)
            if value is not None and _expects_option_array(field_schema, field_id):
                value = [value]
        else:
            max_len = int(rule.get("max_len") or (4000 if field_id == "description" else 255))
            text = str(raw_value or "").strip()
            if not text and rule.get("default"):
                text = str(rule.get("default"))
            value = sanitize_jira_text(text, max_len=max_len) if text else ""
        is_empty = value is None or value == "" or value == {} or value == []
        if required and is_empty:
            return False, _friendly_required_field_message(field_id), project_key
        if is_empty or (allowed and field_id not in allowed):
            continue
        payload_fields[field_id] = value
    if not payload_fields:
        return False, "Не удалось собрать поля формы для отправки в Jira.", project_key
    payload = {
        "serviceDeskId": str(service_desk_id),
        "requestTypeId": str(request_type_id),
        "requestFieldValues": payload_fields,
    }
    url = urljoin(base_url + "/", "rest/servicedeskapi/request")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-ExperimentalApi": "opt-in",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                if resp.status not in (200, 201):
                    txt = await resp.text()
                    return False, f"Ошибка Jira: {resp.status}. {txt[:200]}", project_key
                data = await resp.json()
        issue_key = (data.get("issueKey") or "").strip()
        if not issue_key:
            return False, "Jira вернула пустой ключ заявки.", project_key
        return True, issue_key, project_key
    except Exception as e:
        return False, str(e), project_key


async def _create_rest_issue(
    form: Dict[str, Any],
    jira_def: Dict[str, Any],
    form_data: Dict[str, Any],
    profile: Dict[str, Any],
    base_url: str,
    token: str,
) -> Tuple[bool, str, Optional[str]]:
    project_key = (form.get("project_key") or "").strip() or None
    issue_type = (jira_def.get("issue_type") or form.get("issue_type") or "Incident").strip()
    if not project_key:
        return False, "В форме не задан project_key.", None
    field_map = jira_def.get("fields") or {}
    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
    }
    for field_id, rule in field_map.items():
        if not isinstance(rule, dict):
            continue
        source = (rule.get("source") or "").strip()
        ftype = (rule.get("type") or "text").strip()
        required = bool(rule.get("required"))
        raw_value = _resolve_source(source, form_data, profile, [])
        if ftype == "attachment":
            continue
        if ftype == "phone":
            value: Any = normalize_phone_for_jira(str(raw_value or ""))
        elif ftype == "option":
            label = str(raw_value or "").strip()
            option_payload = (rule.get("option_payload") or "").strip()
            if option_payload == "id_from_createmeta":
                oid = await _get_createmeta_option_id(base_url, token, project_key, issue_type, field_id, label)
                value = {"id": oid} if oid else None
            else:
                value = {"value": label} if label else None
        else:
            max_len = int(rule.get("max_len") or (4000 if field_id == "description" else 255))
            text = str(raw_value or "").strip()
            if not text and rule.get("default"):
                text = str(rule.get("default"))
            value = sanitize_jira_text(text, max_len=max_len) if text else ""
        is_empty = value is None or value == "" or value == {}
        if required and is_empty:
            return False, _friendly_required_field_message(field_id), project_key
        if not is_empty:
            fields[field_id] = value
    payload = {"fields": fields}
    url = urljoin(base_url + "/", "rest/api/2/issue")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                if resp.status != 201:
                    txt = await resp.text()
                    return False, f"Ошибка Jira: {resp.status}. {txt[:200]}", project_key
                data = await resp.json()
        issue_key = (data.get("key") or "").strip()
        if not issue_key:
            return False, "Jira вернула пустой ключ заявки.", project_key
        return True, issue_key, project_key
    except Exception as e:
        return False, str(e), project_key


async def create_issue_from_form(
    form_id: str,
    form_data: Dict[str, Any],
    profile: Dict[str, Any],
    attachment_paths: Optional[List[str]] = None,
) -> Tuple[bool, str, Optional[str]]:
    """
    Возвращает (ok, issue_key_or_error, project_key_or_none).
    """
    form = get_form_definition(form_id)
    if not form:
        return False, f"Форма {form_id} не найдена в каталоге.", None
    jira_def = form.get("jira") or {}
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return False, "Не настроено подключение к Jira.", (form.get("project_key") or "").strip() or None

    mode = (jira_def.get("mode") or "jsm").strip().lower()
    all_paths = list(attachment_paths or [])[:MAX_ATTACHMENTS_PER_ISSUE]
    if mode == "rest_issue":
        ok, result, project_key = await _create_rest_issue(form, jira_def, form_data, profile, base_url, token)
    else:
        ok, result, project_key = await _create_jsm_issue(form, jira_def, form_data, profile, all_paths, base_url, token)
    if not ok:
        return ok, result, project_key
    issue_key = result

    reporter_profile_key = (jira_def.get("reporter_profile_key") or "").strip()
    if reporter_profile_key:
        reporter_value = (profile.get(reporter_profile_key) or "").strip()
        if reporter_value:
            try:
                from core.jira_aa import _set_reporter  # type: ignore[attr-defined]
                await _set_reporter(base_url, token, issue_key, reporter_value)
            except Exception:
                pass
    return True, issue_key, project_key
