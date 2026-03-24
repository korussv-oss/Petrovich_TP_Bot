"""
Создание заявок в Jira проект PW (WMS).
- «Проблема в работе WMS»: JSM с REQUEST_TYPE_ID (Type: Ошибка) или REST с issuetype Ошибка.
- «Изменение настроек системы WMS» и «Пользователь PSIwms»: только JSM; в Jira у соответствующих
  Request Type должен быть Issue Type = Поддержка (не Ошибка). Тип задаётся настройкой Request Type в Jira.
Вложения: add_attachments_to_issue после создания заявки.
"""
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Set, List
from urllib.parse import urljoin

import aiohttp

from config import CONFIG
from core.wms_constants import WMS_PROCESSES
from validators import sanitize_jira_text, validate_issue_key

logger = logging.getLogger(__name__)


def _process_field_payload(process: str) -> dict:
    """
    Значение для поля «WMS failed process» (customfield_13803).
    Jira может требовать option id, а не value; при наличии PROCESS_OPTION_IDS отправляем {"id": "..."}.
    """
    process = (process or "").strip()
    if not process:
        return {"value": ""}
    wms = CONFIG.get("JIRA_WMS", {})
    option_ids = wms.get("PROCESS_OPTION_IDS") or {}
    if not option_ids:
        return {"value": process}
    process_key = next((k for k, v in WMS_PROCESSES.items() if (v or "").strip() == process), None)
    option_id = option_ids.get(process_key) if process_key else None
    if option_id:
        return {"id": str(option_id)}
    return {"value": process}

MAX_ATTACHMENT_SIZE_MB = 10
MAX_ATTACHMENTS_PER_ISSUE = 10

LABEL_CHATBOT = "поддержка"


async def _attach_temporary_file(
    base_url: str,
    token: str,
    service_desk_id: str,
    file_path: str,
) -> Optional[str]:
    """
    Загружает файл как временное вложение (attachTemporaryFile).
    Возвращает temporaryAttachmentId или None.
    """
    path = Path(file_path)
    if not path.is_file():
        logger.debug("Вложение не найдено: %s", file_path)
        return None
    if path.stat().st_size > MAX_ATTACHMENT_SIZE_MB * 1024 * 1024:
        logger.warning("Файл %s превышает %s МБ", path.name, MAX_ATTACHMENT_SIZE_MB)
        return None
    url = urljoin(
        base_url + "/",
        f"rest/servicedeskapi/servicedesk/{service_desk_id}/attachTemporaryFile",
    )
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
                    logger.warning("attachTemporaryFile %s: %s %s", path.name, resp.status, (await resp.text())[:200])
                    return None
                body = await resp.json()
                temps = body.get("temporaryAttachments") or []
                if temps and temps[0].get("temporaryAttachmentId"):
                    logger.debug("attachTemporaryFile %s: temporaryAttachmentId=%s", path.name, temps[0]["temporaryAttachmentId"])
                    return temps[0]["temporaryAttachmentId"]
    except Exception as e:
        logger.warning("attachTemporaryFile %s: %s", path.name, e)
    return None


async def _get_jsm_request_type_allowed_fields(
    base_url: str, token: str, service_desk_id: str, request_type_id: str
) -> Set[str]:
    """Допустимые fieldId для request type (для фильтрации полей при создании)."""
    url = urljoin(
        base_url + "/",
        f"rest/servicedeskapi/servicedesk/{service_desk_id}/requesttype/{request_type_id}/field",
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return set()
                data = await resp.json()
                values = data.get("values") or []
                return {item["fieldId"] for item in values if item.get("fieldId")}
    except Exception as e:
        logger.warning("JSM WMS: не удалось получить поля request type: %s", e)
        return set()


async def _create_wms_via_servicedesk(
    base_url: str,
    token: str,
    service_desk_id: str,
    request_type_id: str,
    summary: str,
    description: str,
    dept_field: str,
    process_field: str,
    department: str,
    process: str,
) -> Optional[Dict[str, Any]]:
    """
    Создаёт заявку «Проблема в работе WMS» через JSM.
    Тип (Ошибка) и Request type задаются requestTypeId.
    """
    allowed = await _get_jsm_request_type_allowed_fields(
        base_url, token, service_desk_id, request_type_id
    )
    if allowed:
        logger.debug("JSM WMS: допустимые поля requestTypeId=%s: %s", request_type_id, sorted(allowed))
    process_payload = _process_field_payload(process)
    request_field_values = {
        dept_field: {"value": department},
        process_field: process_payload,
    }
    if "summary" in allowed or not allowed:
        request_field_values["summary"] = summary[:255] if summary else "Заявка по настройке WMS"
    if "description" in allowed or not allowed:
        request_field_values["description"] = description
    if allowed:
        request_field_values = {k: v for k, v in request_field_values.items() if k in allowed}
    if not request_field_values:
        logger.error("JSM WMS: нет полей для отправки")
        return None
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
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    issue_key = data.get("issueKey")
                    if issue_key:
                        logger.info("WMS заявка создана через JSM: %s (Request type = Проблема в работе WMS)", issue_key)
                        return {"key": issue_key, "id": data.get("issueId")}
                text = await resp.text()
                logger.warning("JSM WMS create failed: %s %s", resp.status, text[:400])
    except Exception as e:
        logger.exception("JSM WMS создание заявки: %s", e)
    return None


async def create_wms_issue(
    summary: str,
    description: str,
    department: str,
    process: str,
    full_name: str = "",
    phone: str = "",
    jira_username: str | None = None,
) -> Tuple[bool, str]:
    """
    Создаёт заявку типа wms_issue в проекте PW (REST API).
    department, process — отображаемые значения для полей customfield_18215, customfield_13803.
    Возвращает (успех, issue_key или сообщение об ошибке).
    """
    jira = CONFIG.get("JIRA", {})
    wms = CONFIG.get("JIRA_WMS", {})
    base_url = (jira.get("LOGIN_URL") or "").rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA: не заданы LOGIN_URL или TOKEN")
        return False, "Не настроено подключение к Jira."

    project_key = (wms.get("PROJECT_KEY") or "PW").strip()
    service_desk_id = (wms.get("SERVICE_DESK_ID") or "").strip()
    request_type_id = (wms.get("REQUEST_TYPE_ID") or "").strip()
    dept_field = (wms.get("FIELD_DEPARTMENT") or "customfield_18215").strip()
    process_field = (wms.get("FIELD_PROCESS") or "customfield_13803").strip()
    service_field = (wms.get("FIELD_SERVICE_TYPE") or "customfield_10500").strip()

    summary = sanitize_jira_text((summary or "").strip() or "Заявка по настройке WMS", max_len=255)
    description = sanitize_jira_text((description or "").strip() or "Описание не предоставлено", max_len=4000)
    department = (department or "").strip()
    process = (process or "").strip()
    if full_name or phone:
        description = sanitize_jira_text(
            f"Контактное лицо: {full_name or '—'}, {phone or '—'}\n\n" + description,
            max_len=4000,
        )

    if not department:
        logger.error("WMS: пустое подразделение")
        return False, "Укажите подразделение."
    if not process:
        logger.error("WMS: пустой процесс (Jira customfield_13803 не принимает null)")
        return False, "Укажите процесс (выберите из списка)."

    # Создание через JSM: Type «Ошибка», Request type «Проблема в работе WMS» (как the_bot_wms)
    if service_desk_id and request_type_id:
        result = await _create_wms_via_servicedesk(
            base_url=base_url,
            token=token,
            service_desk_id=service_desk_id,
            request_type_id=request_type_id,
            summary=summary,
            description=description,
            dept_field=dept_field,
            process_field=process_field,
            department=department,
            process=process,
        )
        if result and result.get("key"):
            issue_key = result["key"]
            if jira_username:
                try:
                    from core.jira_aa import _set_reporter  # type: ignore[attr-defined]
                    await _set_reporter(base_url, token, issue_key, jira_username)
                except Exception as e:
                    logger.warning("WMS: не удалось изменить автора для %s на %s: %s", issue_key, jira_username, e)
            return True, issue_key
        logger.warning("JSM WMS не удалось, пробуем REST")

    # Запасной вариант: REST API, тип «Ошибка», поле Request type по возможности
    process_payload = _process_field_payload(process)
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary[:255],
            "description": description,
            "issuetype": {"name": "Ошибка"},
            "priority": {"name": "Medium"},
            "labels": [LABEL_CHATBOT],
            dept_field: {"value": department},
            process_field: process_payload,
            service_field: "Проблема в работе WMS",
        }
    }

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
                    logger.info("WMS заявка создана: %s", issue_key)
                    if jira_username:
                        try:
                            from core.jira_aa import _set_reporter  # type: ignore[attr-defined]
                            await _set_reporter(base_url, token, issue_key, jira_username)
                        except Exception as e:
                            logger.warning("WMS: не удалось изменить автора для %s на %s: %s", issue_key, jira_username, e)
                    return True, issue_key
                text = await resp.text()
                logger.warning("WMS создание заявки: HTTP %s %s", resp.status, text[:500])
                return False, f"Ошибка Jira: {resp.status}. {text[:200]}"
    except Exception as e:
        logger.exception("WMS создание заявки: %s", e)
        return False, str(e)


async def _create_wms_request_via_servicedesk(
    base_url: str,
    token: str,
    service_desk_id: str,
    request_type_id: str,
    request_field_values: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Создаёт заявку в PW через JSM (servicedeskapi/request).
    request_field_values — поля для request (summary, description, customfield_*).
    Возвращает {"key": issue_key} или None.
    """
    allowed = await _get_jsm_request_type_allowed_fields(
        base_url, token, service_desk_id, request_type_id
    )
    if allowed:
        request_field_values = {k: v for k, v in request_field_values.items() if k in allowed}
    if not request_field_values:
        logger.error("JSM WMS: нет полей для отправки (requestTypeId=%s)", request_type_id)
        return None
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
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    issue_key = data.get("issueKey")
                    if issue_key:
                        return {"key": issue_key, "id": data.get("issueId")}
                text = await resp.text()
                logger.warning("JSM WMS request failed: %s %s", resp.status, text[:400])
    except Exception as e:
        logger.exception("JSM WMS создание request: %s", e)
    return None


async def create_wms_settings(
    department: str,
    service_type: str,
    description: str,
    full_name: str = "",
    phone: str = "",
    file_paths: Optional[List[str]] = None,
    jira_username: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Создаёт заявку «Изменение настроек системы WMS» (как the_bot_wms).
    Поле «WMS service» задаётся пользователем при создании заявки; допускаются только
    «Изменение топологии» или «Другие настройки» (service_type).
    В Jira для этого типа запроса обязательно хотя бы одно вложение — file_paths не должны быть пусты.
    В Jira: Request Type (REQUEST_TYPE_ID_SETTINGS) должен быть с Issue Type = Поддержка, не Ошибка.
    """
    jira = CONFIG.get("JIRA", {})
    wms = CONFIG.get("JIRA_WMS", {})
    base_url = (jira.get("LOGIN_URL") or "").rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA: не заданы LOGIN_URL или TOKEN")
        return False, "Не настроено подключение к Jira."

    service_desk_id = (wms.get("SERVICE_DESK_ID") or "").strip()
    request_type_id = (wms.get("REQUEST_TYPE_ID_SETTINGS") or "1165").strip()
    dept_field = (wms.get("FIELD_DEPARTMENT") or "customfield_18215").strip()
    # Поле WMS service в Jira: только «Изменение топологии» или «Другие настройки» (выбор пользователя).
    settings_field = (wms.get("FIELD_WMS_SETTINGS_SERVICE") or "customfield_18402").strip()
    type_ids = wms.get("WMS_SETTINGS_SERVICE_TYPE_IDS") or {"Изменение топологии": "19810", "Другие настройки": "19811"}
    service_type_id = type_ids.get(service_type)
    if not service_type_id:
        logger.error("WMS settings: неизвестный тип услуги %s", service_type)
        return False, f"Неизвестный тип услуги: {service_type}"

    file_paths = list(file_paths or [])[:MAX_ATTACHMENTS_PER_ISSUE]
    if not file_paths:
        return False, "Добавьте хотя бы один файл (вложения обязательны для этого типа заявки)."

    # Загружаем файлы как временные вложения и получаем ID для поля attachment при создании запроса
    temp_ids: List[str] = []
    for fp in file_paths:
        tid = await _attach_temporary_file(base_url, token, service_desk_id, fp)
        if tid:
            temp_ids.append(tid)
    if not temp_ids:
        return False, "Не удалось загрузить файлы. Проверьте размер (до 10 МБ) и повторите."

    description = sanitize_jira_text((description or "").strip() or "Описание не предоставлено", max_len=4000)
    if full_name or phone:
        description = sanitize_jira_text(
            f"Контактное лицо: {full_name or '—'}, {phone or '—'}\n\n" + description,
            max_len=4000,
        )
    department = (department or "").strip()
    if not department:
        return False, "Укажите подразделение."

    # У этого типа запроса поле summary не допускается — тема задаётся в Jira автоматически.
    # Поле attachment — массив temporaryAttachmentId (JSM Field input formats).
    request_field_values = {
        "description": description,
        dept_field: {"value": department},
        settings_field: {"id": str(service_type_id)},
        "attachment": temp_ids,
    }
    result = await _create_wms_request_via_servicedesk(
        base_url=base_url,
        token=token,
        service_desk_id=service_desk_id,
        request_type_id=request_type_id,
        request_field_values=request_field_values,
    )
    if result and result.get("key"):
        issue_key = result["key"]
        logger.info("WMS заявка создана через JSM: %s (Request type = Изменение настроек системы WMS)", issue_key)
        ju = (jira_username or "").strip()
        if ju:
            try:
                from core.jira_aa import _set_reporter  # type: ignore[attr-defined]

                await _set_reporter(base_url, token, issue_key, ju)
            except Exception as e:
                logger.warning("WMS settings: не удалось сменить reporter для %s на %s: %s", issue_key, ju, e)
        return True, issue_key
    return False, (result or "Не удалось создать заявку.")


async def create_wms_psi_user(
    summary: str,
    description: str,
    department: str,
    full_name: str,
    full_name_contact: str = "",
    phone: str = "",
    jira_username: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Создаёт заявку «Создать/изменить/удалить пользователя PSIwms» (как the_bot_wms).
    full_name — значение поля «Полное имя» в Jira (customfield_12406).
    Если переданы full_name_contact/phone, в начало description добавляется блок «Контактное лицо: …».
    В Jira: Request Type (REQUEST_TYPE_ID_PSI_USER) должен быть с Issue Type = Поддержка, не Ошибка.
    """
    jira = CONFIG.get("JIRA", {})
    wms = CONFIG.get("JIRA_WMS", {})
    base_url = (jira.get("LOGIN_URL") or "").rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA: не заданы LOGIN_URL или TOKEN")
        return False, "Не настроено подключение к Jira."

    service_desk_id = (wms.get("SERVICE_DESK_ID") or "").strip()
    request_type_id = (wms.get("REQUEST_TYPE_ID_PSI_USER") or "").strip()
    if not request_type_id:
        logger.warning("JIRA_WMS_REQUEST_TYPE_ID_PSI_USER не задан, заявка PSIwms не создаётся")
        return False, "Тип заявки «Пользователь PSIwms» не настроен (REQUEST_TYPE_ID_PSI_USER)."

    dept_field = (wms.get("FIELD_DEPARTMENT") or "customfield_18215").strip()
    psi_field = (wms.get("FIELD_PSI_USER_FULL_NAME") or "customfield_12406").strip()

    summary = sanitize_jira_text((summary or "").strip() or "Заявка на пользователя PSIwms", max_len=255)
    description = sanitize_jira_text((description or "").strip() or "Описание не предоставлено", max_len=4000)
    if full_name_contact or phone:
        description = sanitize_jira_text(
            f"Контактное лицо: {full_name_contact or '—'}, {phone or '—'}\n\n" + description,
            max_len=4000,
        )
    department = (department or "").strip()
    full_name = (full_name or "").strip()
    if not department:
        return False, "Укажите подразделение."
    if not full_name:
        return False, "Укажите ФИО и должность пользователя."

    request_field_values = {
        "summary": summary,
        "description": description,
        dept_field: {"value": department},
        psi_field: full_name,
    }
    result = await _create_wms_request_via_servicedesk(
        base_url=base_url,
        token=token,
        service_desk_id=service_desk_id,
        request_type_id=request_type_id,
        request_field_values=request_field_values,
    )
    if result and result.get("key"):
        issue_key = result["key"]
        logger.info("WMS заявка создана через JSM: %s (Request type = Пользователь PSIwms)", issue_key)
        ju = (jira_username or "").strip()
        if ju:
            try:
                from core.jira_aa import _set_reporter  # type: ignore[attr-defined]

                await _set_reporter(base_url, token, issue_key, ju)
            except Exception as e:
                logger.warning("WMS PSI user: не удалось сменить reporter для %s на %s: %s", issue_key, ju, e)
        return True, issue_key
    return False, (result or "Не удалось создать заявку.")


async def add_attachments_to_issue(issue_key: str, file_paths: List[str]) -> Tuple[int, int]:
    """
    Добавляет вложения к заявке в Jira (REST API).
    file_paths — список путей к файлам (до 10 МБ каждый, до 10 файлов).
    Возвращает (успешно_добавлено, всего_попыток).
    """
    if not issue_key or not file_paths:
        return 0, 0
    ok, _ = validate_issue_key(issue_key)
    if not ok:
        logger.warning("Некорректный issue_key для вложений: %r", issue_key)
        return 0, min(len(file_paths), MAX_ATTACHMENTS_PER_ISSUE)
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.warning("JIRA: не заданы LOGIN_URL или TOKEN для вложений")
        return 0, len(file_paths)
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/attachments")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}", "X-Atlassian-Token": "no-check"}
    success_count = 0
    for path in file_paths[:MAX_ATTACHMENTS_PER_ISSUE]:
        p = Path(path)
        if not p.is_file():
            logger.debug("Вложение не найдено: %s", path)
            continue
        size_mb = p.stat().st_size / (1024 * 1024)
        if size_mb > MAX_ATTACHMENT_SIZE_MB:
            logger.warning("Файл %s превышает %s МБ, пропуск", p.name, MAX_ATTACHMENT_SIZE_MB)
            continue
        try:
            body = p.read_bytes()
            data = aiohttp.FormData()
            data.add_field("file", body, filename=p.name, content_type="application/octet-stream")
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        success_count += 1
                        logger.info("Вложение добавлено к %s: %s", issue_key, p.name)
                    else:
                        logger.warning("Вложение %s не добавлено: %s %s", p.name, resp.status, (await resp.text())[:200])
        except Exception as e:
            logger.warning("Ошибка добавления вложения %s к %s: %s", p.name, issue_key, e)
    return success_count, min(len(file_paths), MAX_ATTACHMENTS_PER_ISSUE)
