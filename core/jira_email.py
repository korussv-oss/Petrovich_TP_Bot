"""Создание заявок «Электронная почта (Owa\\Outlook)» в Jira Service Management (ISR)."""
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urljoin

import aiohttp

from config import CONFIG
from core.jira_labels import merge_chatbot_into_labels
from validators import sanitize_jira_text, normalize_phone_for_jira

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE_MB = 10
MAX_ATTACHMENTS_PER_ISSUE = 10


def _select_option_payload(field_schema: List[dict], field_id: str, selected_label: str) -> Optional[dict]:
    """
    Строит payload для select-поля JSM в формате {"id": "..."} или {"value": "..."}.
    Ищет совпадение по label/value без учета регистра.
    """
    target = (selected_label or "").strip()
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
                # Для JSM select чаще всего подходит id/value опции в поле "id".
                if value:
                    return {"id": value}
                if label:
                    return {"value": label}
        break
    # fallback: пробуем как value отображаемого текста
    return {"value": target}


async def _get_request_type_fields(
    base_url: str,
    token: str,
    service_desk_id: str,
    request_type_id: str,
) -> List[dict]:
    """Получает схему полей request type (JSM)."""
    url = urljoin(
        base_url + "/",
        f"rest/servicedeskapi/servicedesk/{service_desk_id}/requesttype/{request_type_id}/field",
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-ExperimentalApi": "opt-in",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    logger.warning("Email request fields failed: %s %s", resp.status, await resp.text())
                    return []
                data = await resp.json()
        return list(data.get("requestTypeFields") or data.get("values") or [])
    except Exception as e:
        logger.warning("Email request fields exception: %s", e)
        return []


async def _attach_temporary_file(
    base_url: str,
    token: str,
    service_desk_id: str,
    file_path: str,
) -> Optional[str]:
    path = Path(file_path)
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_ATTACHMENT_SIZE_MB * 1024 * 1024:
        logger.warning("Email attach: %s too large", path.name)
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
                    logger.warning("Email attachTemporaryFile %s: %s %s", path.name, resp.status, (await resp.text())[:200])
                    return None
                body = await resp.json()
                temps = body.get("temporaryAttachments") or []
                if temps and temps[0].get("temporaryAttachmentId"):
                    return str(temps[0]["temporaryAttachmentId"])
    except Exception as e:
        logger.warning("Email attachTemporaryFile %s: %s", path.name, e)
    return None


async def create_owa_outlook_issue(
    request_kind: str,
    rms_or_ip: str,
    workplace: str,
    description: str,
    department: str,
    phone: str,
    jira_username: Optional[str],
    attachment_paths: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Создаёт заявку ISR «Электронная почта (Owa\\Outlook)»."""
    jira = CONFIG.get("JIRA", {})
    email_cfg = CONFIG.get("JIRA_EMAIL", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return False, "Не настроено подключение к Jira."

    service_desk_id = (email_cfg.get("SERVICE_DESK_ID") or "22").strip()
    request_type_id = (email_cfg.get("REQUEST_TYPE_ID_OWA") or "1257").strip()
    f_phone = (email_cfg.get("FIELD_PHONE") or "customfield_13103").strip()
    f_rms = (email_cfg.get("FIELD_RMS_OR_IP") or "customfield_14075").strip()
    f_department = (email_cfg.get("FIELD_DEPARTMENT") or "customfield_11406").strip()
    f_request_kind = (email_cfg.get("FIELD_REQUEST_KIND") or "customfield_19107").strip()
    f_workplace = (email_cfg.get("FIELD_WORKPLACE") or "customfield_11402").strip()

    request_kind = (request_kind or "").strip()
    rms_or_ip = sanitize_jira_text((rms_or_ip or "").strip(), max_len=255)
    workplace = sanitize_jira_text((workplace or "").strip(), max_len=255)
    description = sanitize_jira_text((description or "").strip(), max_len=4000)
    department = (department or "").strip()
    phone_jira = normalize_phone_for_jira(phone or "")

    if not request_kind:
        return False, "Выберите тип запроса по электронной почте."
    if not rms_or_ip:
        return False, "Укажите RMS или IP."
    if not description:
        return False, "Укажите подробное описание."
    if not department:
        return False, "В профиле не указано подразделение."
    if not phone_jira:
        return False, "В профиле не указан телефон."

    field_schema = await _get_request_type_fields(base_url, token, service_desk_id, request_type_id)
    allowed_fields = {f.get("fieldId") for f in field_schema if isinstance(f, dict) and f.get("fieldId")}

    department_payload = _select_option_payload(field_schema, f_department, department)
    if not department_payload:
        return False, "Не удалось подготовить значение поля «Подразделение»."
    request_kind_payload = _select_option_payload(field_schema, f_request_kind, request_kind)
    if not request_kind_payload:
        return False, "Не удалось подготовить значение поля «Укажите ваш запрос»."

    request_field_values: Dict[str, Any] = {}
    if not allowed_fields or f_phone in allowed_fields:
        request_field_values[f_phone] = phone_jira
    if not allowed_fields or f_rms in allowed_fields:
        request_field_values[f_rms] = rms_or_ip
    if not allowed_fields or f_department in allowed_fields:
        request_field_values[f_department] = department_payload
    if not allowed_fields or f_request_kind in allowed_fields:
        request_field_values[f_request_kind] = request_kind_payload
    if workplace and (not allowed_fields or f_workplace in allowed_fields):
        request_field_values[f_workplace] = workplace
    if not allowed_fields or "description" in allowed_fields:
        request_field_values["description"] = description
    if allowed_fields and "labels" in allowed_fields:
        merge_chatbot_into_labels(request_field_values)

    files = list(attachment_paths or [])[:MAX_ATTACHMENTS_PER_ISSUE]
    if files and (not allowed_fields or "attachment" in allowed_fields):
        temp_ids = []
        for file_path in files:
            tid = await _attach_temporary_file(base_url, token, service_desk_id, file_path)
            if tid:
                temp_ids.append(tid)
        if temp_ids:
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
        "X-ExperimentalApi": "opt-in",
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
                            logger.warning("Email OWA: set reporter failed for %s (%s): %s", issue_key, jira_username, e)
                    if not allowed_fields or "labels" not in allowed_fields:
                        from core.jira_aa import _ensure_issue_has_chatbot_label  # type: ignore[attr-defined]

                        await _ensure_issue_has_chatbot_label(base_url, token, issue_key)
                    return True, issue_key
                text = await resp.text()
                logger.warning("Email OWA create failed: %s %s", resp.status, text[:500])
                return False, f"Ошибка Jira: {resp.status}. {text[:200]}"
    except Exception as e:
        logger.exception("Email OWA create exception: %s", e)
        return False, str(e)
