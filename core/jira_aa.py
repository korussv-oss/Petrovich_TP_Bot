"""
Создание задачи в Jira в проекте AA (смена пароля).
Поля: AD account (логин), Existing phone number, Password_new.
При наличии JIRA_AA_SERVICE_DESK_ID создаём через Service Desk API (requestTypeId=964) — тип «Смена пароля» выставляется автоматически (как в the_bot_wms).
"""
import asyncio
import logging
import json
from typing import Optional, Dict, Any, Set
from urllib.parse import urljoin

import aiohttp

from config import CONFIG
from validators import validate_issue_key, sanitize_jira_text

logger = logging.getLogger(__name__)

JIRA_AA = CONFIG.get("JIRA_AA", {})
FIELDS = JIRA_AA.get("FIELDS", {})


def _safe_issue_key(issue_key: str) -> Optional[str]:
    key = (issue_key or "").strip()
    ok, _ = validate_issue_key(key)
    if not ok:
        logger.warning("Некорректный issue_key: %r", issue_key)
        return None
    return key


async def _get_jsm_request_type_allowed_fields(
    base_url: str, token: str, service_desk_id: str, request_type_id: str
) -> Set[str]:
    """Возвращает множество допустимых fieldId для request type (как в the_bot_wms)."""
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
        logger.warning("JSM: не удалось получить список полей request type: %s", e)
        return set()


async def _create_via_servicedesk(
    base_url: str,
    token: str,
    service_desk_id: str,
    request_type_id: str,
    summary: str,
    description: str,
    field_ad: str,
    field_phone: str,
    field_pass: str,
    ad_account: str,
    existing_phone: str,
    password_new: str,
    assignee_username: str,
    field_department: str = "",
    department_value: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Создаёт заявку через Jira Service Management API.
    Тип «Смена пароля» задаётся requestTypeId. Обязательное поле «Подразделение» передаётся как department_value.
    """
    allowed = await _get_jsm_request_type_allowed_fields(
        base_url, token, service_desk_id, request_type_id
    )
    if allowed:
        logger.info("JSM: допустимые поля для requestTypeId=%s: %s", request_type_id, sorted(allowed))
    request_field_values = {
        field_ad: ad_account,
        field_phone: existing_phone,
        field_pass: password_new,
    }
    if field_department and department_value and department_value.strip():
        # Jira select-поле «Подразделение» обычно принимает {"value": "Название"}
        request_field_values[field_department] = {"value": department_value.strip()}
    if allowed:
        if "summary" in allowed:
            request_field_values["summary"] = summary
        if "description" in allowed:
            request_field_values["description"] = description
        if "labels" in allowed:
            request_field_values["labels"] = ["чатбот"]
        request_field_values = {k: v for k, v in request_field_values.items() if k in allowed}
    else:
        logger.debug("JSM: отправляем только кастомные поля (summary/description не передаём)")
    if not request_field_values:
        logger.error("JSM: нет полей для отправки")
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
                        logger.info("Задача AA создана через JSM: %s (Request type = Смена пароля)", issue_key)
                        result = {"key": issue_key, "id": data.get("issueId")}
                        if assignee_username and JIRA_AA.get("SET_ASSIGNEE", True):
                            await _set_assignee(base_url, token, issue_key, assignee_username)
                        return result
                text = await resp.text()
                logger.warning("JSM create failed: %s %s", resp.status, text[:400])
    except asyncio.TimeoutError:
        logger.error("Timeout при создании через JSM")
    except Exception as e:
        logger.exception("Ошибка создания через JSM: %s", e)
    return None


async def _get_jira_current_user(base_url: str, token: str) -> Optional[str]:
    """Возвращает имя пользователя Jira, от имени которого действует токен (для подсказки в логах)."""
    url = urljoin(base_url + "/", "rest/api/2/myself")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return (data.get("name") or data.get("key") or data.get("displayName") or "").strip() or None
    except Exception:
        return None


async def _set_assignee(base_url: str, token: str, issue_key: str, username: str) -> bool:
    """
    Устанавливает исполнителя по REST API.
    Используем отдельный эндпоинт PUT .../issue/{key}/assignee, а не редактирование issue:
    иначе Jira может вернуть 400 «assignee not on the appropriate screen» (как при создании).
    """
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return False
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/assignee")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    body = {"name": username}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 204:
                    logger.debug("Assignee %s установлен для %s", username, issue_key)
                    return True
                elif resp.status == 403:
                    body_403 = await resp.text()
                    jira_user = await _get_jira_current_user(base_url, token)
                    logger.warning(
                        "Исполнитель не назначен для %s (назначение на %s). 403: %s",
                        issue_key,
                        username,
                        (body_403 or "").strip()[:400],
                    )
                    if jira_user:
                        logger.warning(
                            "Токен бота = пользователь «%s». Если в скрипте jira_whoami.py право Assign issues = да, "
                            "но назначение падает с 403 — добавьте пользователя «%s» в роль «Assignable User» в проекте AA.",
                            jira_user,
                            username,
                        )
                else:
                    text = await resp.text()
                    logger.warning("Assignee для %s: %s %s", issue_key, resp.status, (text or "")[:300])
    except Exception as e:
        logger.warning("Не удалось установить assignee: %s", e)
    return False


async def _set_reporter(base_url: str, token: str, issue_key: str, username: str) -> None:
    """
    Меняет автора задачи (reporter) через REST API.
    Требует права Modify Reporter и наличие поля Reporter на экране редактирования.
    """
    if not username:
        return
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    body = {"fields": {"reporter": {"name": username}}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status in (200, 204):
                    logger.debug("Reporter %s установлен для %s", username, issue_key)
                elif resp.status in (400, 403):
                    text = await resp.text()
                    logger.warning(
                        "Не удалось изменить автора для %s на %s: %s %s",
                        issue_key,
                        username,
                        resp.status,
                        (text or "")[:400],
                    )
                else:
                    text = await resp.text()
                    logger.warning("Ошибка изменения автора для %s: %s %s", issue_key, resp.status, (text or "")[:300])
    except Exception as e:
        logger.warning("Не удалось установить reporter: %s", e)


async def issue_exists(issue_key: str) -> Optional[bool]:
    """
    Проверяет, существует ли заявка в Jira.
    Возвращает True (есть), False (404 — удалена/не найдена), None (ошибка сети/таймаут).
    """
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return None
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return None
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}?fields=summary")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return True
                if resp.status == 404:
                    return False
                return None
    except Exception as e:
        logger.debug("issue_exists %s: %s", issue_key, e)
        return None


async def get_issue_status(issue_key: str) -> Optional[str]:
    """
    Возвращает имя текущего статуса задачи (например "Resolved", "Отклонено") или None при ошибке.
    """
    info = await get_issue_info(issue_key)
    return info.get("status") if info else None


async def get_issue_info(issue_key: str) -> Optional[Dict[str, Any]]:
    """
    Возвращает краткую информацию о задаче: summary, status, description (первые 200 символов).
    Нужно для экрана «Мои заявки» → просмотр заявки (TG и MAX).
    """
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return None
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return None
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}?fields=summary,status,description")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                fields = data.get("fields") or {}
                status = fields.get("status")
                status_name = (status.get("name") or "").strip() if isinstance(status, dict) else ""
                summary = (fields.get("summary") or "").strip()
                desc = (fields.get("description") or "").strip()
                if desc and len(desc) > 200:
                    desc = desc[:200] + "..."
                return {"summary": summary, "status": status_name, "description": desc}
    except Exception as e:
        logger.debug("get_issue_info %s: %s", issue_key, e)
        return None


async def get_issue_admin_details(issue_key: str) -> Optional[Dict[str, Any]]:
    """
    Расширенная информация о задаче для карточки СА:
    summary, status, description, assignee, reporter, issuetype, project.
    """
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return None
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return None
    fields = "summary,status,description,assignee,reporter,issuetype,project"
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}?fields={fields}")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                f = data.get("fields") or {}
                status_obj = f.get("status") or {}
                assignee_obj = f.get("assignee") or {}
                reporter_obj = f.get("reporter") or {}
                issuetype_obj = f.get("issuetype") or {}
                project_obj = f.get("project") or {}
                desc = (f.get("description") or "").strip()
                if desc and len(desc) > 800:
                    desc = desc[:800] + "..."
                return {
                    "summary": (f.get("summary") or "").strip(),
                    "status": (status_obj.get("name") or "").strip(),
                    "description": desc,
                    "assignee_display": (assignee_obj.get("displayName") or "").strip(),
                    "assignee_username": (assignee_obj.get("name") or assignee_obj.get("key") or "").strip(),
                    "reporter_display": (reporter_obj.get("displayName") or "").strip(),
                    "reporter_username": (reporter_obj.get("name") or reporter_obj.get("key") or "").strip(),
                    "issue_type": (issuetype_obj.get("name") or "").strip(),
                    "project_key": (project_obj.get("key") or "").strip().upper(),
                }
    except Exception as e:
        logger.debug("get_issue_admin_details %s: %s", issue_key, e)
        return None


async def get_issue_transitions(issue_key: str) -> list:
    """Доступные workflow-переходы задачи."""
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return []
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return []
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/transitions")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                out = []
                for t in data.get("transitions") or []:
                    if not isinstance(t, dict):
                        continue
                    tid = (t.get("id") or "").strip()
                    name = (t.get("name") or "").strip()
                    to_name = ((t.get("to") or {}).get("name") or "").strip() if isinstance(t.get("to"), dict) else ""
                    if tid and name:
                        out.append({"id": tid, "name": name, "to_name": to_name})
                return out
    except Exception as e:
        logger.debug("get_issue_transitions %s: %s", issue_key, e)
        return []


async def _get_transition_with_fields(issue_key: str, transition_id: str) -> Optional[Dict[str, Any]]:
    """Вернуть transition с описанием полей (expand=transitions.fields)."""
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return None
    issue_key = _safe_issue_key(issue_key)
    transition_id = (transition_id or "").strip()
    if not issue_key or not transition_id:
        return None
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/transitions?expand=transitions.fields")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                for t in data.get("transitions") or []:
                    if not isinstance(t, dict):
                        continue
                    if (t.get("id") or "").strip() == transition_id:
                        return t
    except Exception as e:
        logger.debug("get transition fields %s/%s: %s", issue_key, transition_id, e)
    return None


def _pick_done_resolution_payload(transition_obj: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Подобрать payload поля resolution по transition metadata.
    Предпочтение: Done, иначе первое допустимое значение.
    """
    if not isinstance(transition_obj, dict):
        return {"name": "Done"}
    fields = transition_obj.get("fields") or {}
    if not isinstance(fields, dict):
        return {"name": "Done"}
    rmeta = fields.get("resolution") or {}
    if not isinstance(rmeta, dict):
        return {"name": "Done"}
    allowed = rmeta.get("allowedValues") or []
    if not isinstance(allowed, list) or not allowed:
        return {"name": "Done"}
    # 1) Пытаемся найти Done.
    for v in allowed:
        if not isinstance(v, dict):
            continue
        name = (v.get("name") or v.get("value") or "").strip()
        if name.lower() == "done":
            vid = (v.get("id") or "").strip()
            return {"id": vid} if vid else {"name": name}
    # 2) Иначе берём первое допустимое.
    first = allowed[0]
    if isinstance(first, dict):
        fid = (first.get("id") or "").strip()
        fname = (first.get("name") or first.get("value") or "").strip()
        if fid:
            return {"id": fid}
        if fname:
            return {"name": fname}
    return {"name": "Done"}


async def transition_issue(
    issue_key: str,
    transition_id: str,
    preserve_assignee_username: Optional[str] = None,
    default_time_spent: str = "5m",
) -> tuple[bool, str]:
    """Выполнить workflow-переход задачи по transition_id."""
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return False, "Не настроено подключение к Jira."
    issue_key = _safe_issue_key(issue_key)
    transition_id = (transition_id or "").strip()
    if not issue_key or not transition_id:
        return False, "Некорректный ключ заявки или переход."
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/transitions")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {"transition": {"id": transition_id}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 204:
                    if preserve_assignee_username:
                        restored = await _set_assignee(base_url, token, issue_key, preserve_assignee_username)
                        if restored:
                            try:
                                from core.notifications import set_issue_last_assignee_baseline
                                set_issue_last_assignee_baseline(issue_key, preserve_assignee_username)
                            except Exception:
                                pass
                            return True, "Статус обновлён, исполнитель восстановлен."
                        return True, "Статус обновлён (исполнителя восстановить не удалось)."
                    return True, "Статус обновлён."
                text = await resp.text()
                # Некоторые переходы (например Resolve) требуют worklog + resolution.
                if resp.status == 400:
                    err_lower = (text or "").lower()
                    needs_worklog = ("затр" in err_lower) or ("time spent" in err_lower) or ("worklog" in err_lower)
                    needs_resolution = "resolution" in err_lower
                    if needs_worklog or needs_resolution:
                        t_obj = await _get_transition_with_fields(issue_key, transition_id)
                        fields_payload: Dict[str, Any] = {}
                        update_payload: Dict[str, Any] = {}
                        if needs_resolution:
                            fields_payload["resolution"] = _pick_done_resolution_payload(t_obj)
                        if needs_worklog:
                            update_payload["worklog"] = [{"add": {"timeSpent": default_time_spent}}]
                        retry_payload: Dict[str, Any] = {"transition": {"id": transition_id}}
                        if fields_payload:
                            retry_payload["fields"] = fields_payload
                        if update_payload:
                            retry_payload["update"] = update_payload
                        async with aiohttp.ClientSession() as s2:
                            async with s2.post(
                                url, json=retry_payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                            ) as r2:
                                if r2.status == 204:
                                    if preserve_assignee_username:
                                        restored = await _set_assignee(base_url, token, issue_key, preserve_assignee_username)
                                        if restored:
                                            try:
                                                from core.notifications import set_issue_last_assignee_baseline
                                                set_issue_last_assignee_baseline(issue_key, preserve_assignee_username)
                                            except Exception:
                                                pass
                                            return True, "Статус обновлён, исполнитель восстановлен."
                                        return True, "Статус обновлён (исполнителя восстановить не удалось)."
                                    return True, "Статус обновлён."
                                t2 = await r2.text()
                                return False, f"Ошибка Jira: {r2.status}. {(t2 or '')[:200]}"
                return False, f"Ошибка Jira: {resp.status}. {(text or '')[:200]}"
    except Exception as e:
        return False, str(e)


def _is_internal_comment(comment: Dict[str, Any]) -> bool:
    """
    True для внутренних комментариев Jira Service Management.
    Признак: property sd.public.comment.value.internal == true.
    """
    props = comment.get("properties") or []
    if not isinstance(props, list):
        return False
    for p in props:
        if not isinstance(p, dict):
            continue
        if (p.get("key") or "").strip() != "sd.public.comment":
            continue
        val = p.get("value")
        if isinstance(val, dict) and bool(val.get("internal")):
            return True
    return False


async def get_issue_comments(issue_key: str, include_internal: bool = False) -> list:
    """Возвращает список комментариев задачи. Внутренние (internal) по умолчанию исключаются."""
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return []
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return []
    # expand=properties нужен, чтобы отличать public/internal комментарии в JSM.
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/comment?expand=properties")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                comments = list(data.get("comments") or [])
                if include_internal:
                    return comments
                return [c for c in comments if not _is_internal_comment(c)]
    except Exception as e:
        logger.warning("get_issue_comments %s: %s", issue_key, e)
        return []


async def add_comment(issue_key: str, body: str) -> bool:
    """Добавляет комментарий к задаче. Возвращает True при успехе."""
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    issue_key = _safe_issue_key(issue_key)
    body_clean = sanitize_jira_text(body or "", max_len=5000)
    if not issue_key or not base_url or not token or not body_clean:
        return False
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/comment")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {"body": body_clean}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status in (200, 201):
                    return True
                logger.warning("add_comment %s: %s %s", issue_key, resp.status, await resp.text())
                return False
    except Exception as e:
        logger.warning("add_comment %s: %s", issue_key, e)
        return False


async def get_issue_request_type_value(issue_key: str, field_id: Optional[str] = None) -> Optional[Any]:
    """
    Возвращает текущее значение поля «Тип запроса» (customfield_10500) у задачи.
    Нужно для копирования значения из задачи, где тип уже проставлен (вручную или через JSM).
    """
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return None
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return None
    fid = (field_id or JIRA_AA.get("FIELD_CUSTOMER_REQUEST_TYPE", "") or "customfield_10500").strip()
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}?fields={fid}")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return (data.get("fields") or {}).get(fid)
    except Exception as e:
        logger.warning("Не удалось прочитать поле типа запроса у %s: %s", issue_key, e)
        return None


async def get_issue_editmeta(issue_key: str) -> Dict[str, Any]:
    """GET editmeta для задачи — список полей, доступных для редактирования, и их схемы."""
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return {}
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return {}
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/editmeta")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
    except Exception as e:
        logger.warning("Не удалось получить editmeta для %s: %s", issue_key, e)
        return {}


async def update_issue_request_type(
    issue_key: str,
    field_id: Optional[str] = None,
    value: Optional[Any] = None,
    source_issue_key: Optional[str] = None,
) -> bool:
    """
    Обновляет тип запроса (Request type) у уже созданной задачи через REST API.
    source_issue_key — скопировать значение из другой задачи (например AA-78683, где тип уже «Смена пароля»).
    value — явное значение; если задано source_issue_key, сначала пробуем значение из source.
    """
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA LOGIN_URL или TOKEN не заданы")
        return False

    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return False
    field_id = (field_id or JIRA_AA.get("FIELD_CUSTOMER_REQUEST_TYPE", "") or "customfield_10500").strip()
    if not field_id:
        logger.error("Не задано поле типа запроса")
        return False

    # Копируем значение из другой задачи (рекомендуемый способ)
    if source_issue_key:
        src_key = _safe_issue_key(source_issue_key)
        copied = await get_issue_request_type_value(src_key, field_id) if src_key else None
        if copied is not None:
            logger.info("Скопировано значение типа запроса из %s", source_issue_key)
            # Jira GET возвращает полный объект; при записи часто нужен только id
            if isinstance(copied, dict):
                rt = copied.get("requestType") or copied
                if isinstance(rt, dict) and rt.get("id"):
                    value = {"id": str(rt["id"])}
                else:
                    value = copied
            else:
                value = copied

    request_type_id = JIRA_AA.get("REQUEST_TYPE_ID", "964").strip()
    project_key = JIRA_AA.get("PROJECT_KEY", "AA")
    candidates = (
        [value]
        if value is not None
        else [
            {"id": request_type_id},
            {"id": str(int(request_type_id)) if request_type_id.isdigit() else None},
            f"{project_key}/{request_type_id}",
            JIRA_AA.get("REQUEST_TYPE_VALUE", "Смена пароля").strip(),
        ]
    )
    candidates = [c for c in candidates if c is not None]

    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    for val in candidates:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    json={"fields": {field_id: val}},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 204:
                        logger.info("Тип запроса обновлён для %s: %s", issue_key, val)
                        return True
                    if resp.status == 400:
                        body = await resp.text()
                        logger.warning("PUT тип запроса для %s (value=%s): 400 %s", issue_key, val, body[:400])
        except Exception as e:
            logger.warning("Ошибка при установке типа запроса (value=%s): %s", val, e)

    # Поле не на экране редактирования — пробуем установить через transition (экран перехода может содержать поле)
    if value is not None:
        logger.info("Пробуем установить тип запроса через workflow transition...")
        if await _try_set_request_type_via_transition(base_url, token, issue_key, field_id, value):
            return True
        logger.warning("Ни один переход по workflow не принял поле типа запроса")

    logger.error(
        "Не удалось установить тип запроса для %s. Поле customfield_10500 не на экране редактирования и не на экранах переходов. Варианты: добавить поле на Edit screen в Jira для типа «Задача», или использовать ScriptRunner/автоматизацию на стороне Jira.",
        issue_key,
    )
    return False


async def _try_set_request_type_via_transition(
    base_url: str, token: str, issue_key: str, field_id: str, value: Any
) -> bool:
    """
    Пробует установить тип запроса через POST transition.
    На экране перехода поле Request type может быть доступно, даже если его нет в editmeta.
    Внимание: может изменить статус задачи (переход по workflow).
    """
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return False
    url_list = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/transitions")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url_list, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                transitions = data.get("transitions") or []
            for t in transitions:
                tid = t.get("id")
                tname = (t.get("name") or "").strip()
                if not tid:
                    continue
                url_post = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/transitions")
                body = {"transition": {"id": tid}, "fields": {field_id: value}}
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url_post, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                    ) as r:
                        if r.status == 204:
                            logger.info("Тип запроса установлен для %s через transition %s (%s)", issue_key, tname, tid)
                            return True
                        if r.status == 400:
                            err = await r.text()
                            logger.debug("Transition %s для %s: 400 %s", tid, issue_key, err[:300])
    except Exception as e:
        logger.warning("Ошибка при установке типа через transition: %s", e)
    return False


async def create_password_change_issue(
    ad_account: str,
    existing_phone: str,
    password_new: str,
    department: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Создаёт задачу в проекте AA на смену пароля.
    department — подразделение из профиля (обязательно для JSM, иначе «Заполните обязательное поле Подразделение»).
    """
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA LOGIN_URL или TOKEN не заданы")
        return None

    summary = sanitize_jira_text(f"Смена пароля: {ad_account}", max_len=255)
    description = sanitize_jira_text(
        f"Запрос на смену пароля через бота.\n"
        f"AD account: {ad_account}\n"
        f"Existing phone: {existing_phone}\n"
        f"Password_new: (заполнено в кастомном поле)"
    , max_len=4000)
    field_ad = FIELDS.get("AD_ACCOUNT") or ""
    field_phone = FIELDS.get("EXISTING_PHONE") or ""
    field_pass = FIELDS.get("PASSWORD_NEW") or ""
    field_department = JIRA_AA.get("FIELD_DEPARTMENT") or FIELDS.get("DEPARTMENT") or ""
    assignee_username = JIRA_AA.get("ASSIGNEE_USERNAME", "").strip()
    service_desk_id = JIRA_AA.get("SERVICE_DESK_ID", "").strip()
    request_type_id = JIRA_AA.get("REQUEST_TYPE_ID", "").strip()

    if service_desk_id and request_type_id and field_ad and field_phone and field_pass:
        result = await _create_via_servicedesk(
            base_url=base_url,
            token=token,
            service_desk_id=service_desk_id,
            request_type_id=request_type_id,
            summary=summary,
            description=description,
            field_ad=field_ad,
            field_phone=field_phone,
            field_pass=field_pass,
            ad_account=ad_account,
            existing_phone=existing_phone,
            password_new=password_new,
            assignee_username=assignee_username,
            field_department=field_department,
            department_value=department,
        )
        if result is not None:
            return result
        logger.warning("JSM создание не удалось, пробуем REST API")

    project_key = JIRA_AA.get("PROJECT_KEY", "AA")
    issue_type_name = JIRA_AA.get("ISSUE_TYPE", "Задача")
    issue_type_id = JIRA_AA.get("ISSUE_TYPE_ID")

    # Jira надёжнее принимает тип по id (не зависит от кодировки/регистра имени)
    if issue_type_id:
        issuetype_payload = {"id": str(issue_type_id)}
        logger.debug("Jira AA: создаём задачу с issuetype id=%s", issue_type_id)
    else:
        issuetype_payload = {"name": issue_type_name}
        logger.debug("Jira AA: создаём задачу с issuetype name=%r", issue_type_name)

    customer_request_type_field = JIRA_AA.get("FIELD_CUSTOMER_REQUEST_TYPE", "").strip()
    request_type_value = JIRA_AA.get("REQUEST_TYPE_VALUE", "Смена пароля").strip()

    issue_data = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": issuetype_payload,
            "description": description,
            "labels": ["чатбот"],
        }
    }

    # Assignee при создании через REST недоступен на экране — выставим после создания (PUT).

    # Поле customfield_10500 (Request type) при создании через REST недоступно — «not on the appropriate screen».
    # Не отправляем, чтобы не получать 400.

    # Кастомные поля (ID из конфига/ .env)
    if field_ad:
        issue_data["fields"][field_ad] = ad_account
    if field_phone:
        issue_data["fields"][field_phone] = existing_phone
    if field_pass:
        issue_data["fields"][field_pass] = password_new

    url = urljoin(base_url + "/", "rest/api/2/issue")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=issue_data, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    issue_key = data.get("key")
                    logger.info("Задача AA создана: %s", issue_key)
                    if issue_key and assignee_username and JIRA_AA.get("SET_ASSIGNEE", True):
                        await _set_assignee(base_url, token, issue_key, assignee_username)
                    return data
                # Пытаемся вернуть структурированную ошибку, чтобы показать пользователю причину
                if resp.status == 400:
                    try:
                        data = await resp.json()
                        if isinstance(data, dict):
                            logger.warning("Jira AA create failed: 400 %s", json.dumps(data, ensure_ascii=False)[:500])
                            return {
                                "ok": False,
                                "status": 400,
                                "errors": data.get("errors") or {},
                                "errorMessages": data.get("errorMessages") or [],
                            }
                    except Exception:
                        pass
                text = await resp.text()
                logger.warning("Jira AA create failed: %s %s", resp.status, (text or "")[:500])
                return {"ok": False, "status": resp.status, "text": (text or "")[:2000]}
    except asyncio.TimeoutError:
        logger.error("Timeout при создании задачи AA")
        return {"ok": False, "status": "timeout"}
    except Exception as e:
        logger.exception("Ошибка создания задачи AA: %s", e)
        return {"ok": False, "status": "exception", "error": str(e)}
