"""
Создание задачи в Jira в проекте AA (смена пароля).
Поля: AD account (логин), Existing phone number, Password_new.
При наличии JIRA_AA_SERVICE_DESK_ID создаём через Service Desk API (requestTypeId=964) — тип «Смена пароля» выставляется автоматически (как в the_bot_wms).
"""
import asyncio
import logging
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict, overload, Literal
from urllib.parse import urljoin

import aiohttp

from config import CONFIG
from validators import validate_issue_key, sanitize_jira_text, normalize_phone_for_jira
from core.jira_labels import JIRA_LABEL_CHATBOT, merge_chatbot_into_labels
from core.jira_retry import retry_jira
from core.http_error_utils import classify_http_error
from core.log_rate_limit import log_rate_limited

logger = logging.getLogger(__name__)

JIRA_AA = CONFIG.get("JIRA_AA", {})
FIELDS = JIRA_AA.get("FIELDS", {})

# -----------------------------------------------------------------------------
# Lightweight in-memory caches (TTL) + in-flight dedup for Jira reads
# -----------------------------------------------------------------------------

# key -> (expires_at_mono, value)
_JIRA_TTL_CACHE: dict[tuple, tuple[float, Any]] = {}
# key -> asyncio.Task
_JIRA_INFLIGHT: dict[tuple, asyncio.Task] = {}

_JIRA_HTTP_SESSION: aiohttp.ClientSession | None = None
_JIRA_HTTP_SESSION_LOCK = asyncio.Lock()


async def _get_jira_http_session() -> aiohttp.ClientSession:
    """
    Shared aiohttp session for Jira reads.
    Reduces connect/close overhead and avoids connection storms under concurrency.
    """
    global _JIRA_HTTP_SESSION
    async with _JIRA_HTTP_SESSION_LOCK:
        if _JIRA_HTTP_SESSION is not None and not _JIRA_HTTP_SESSION.closed:
            return _JIRA_HTTP_SESSION
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
        _JIRA_HTTP_SESSION = aiohttp.ClientSession(connector=connector)
        return _JIRA_HTTP_SESSION


@dataclass(frozen=True)
class CommentsResult:
    comments: list[dict] | None
    http_status: int | None


class IssueInfo(TypedDict):
    summary: str
    status: str
    description: str


class IssueAdminDetails(TypedDict):
    summary: str
    status: str
    description: str
    assignee_display: str
    assignee_username: str
    reporter_display: str
    reporter_username: str
    issue_type: str
    project_key: str


def _cache_get(key: tuple) -> Any | None:
    now = time.monotonic()
    entry = _JIRA_TTL_CACHE.get(key)
    if not entry:
        return None
    exp, val = entry
    if exp <= now:
        _JIRA_TTL_CACHE.pop(key, None)
        return None
    return val


def _cache_set(key: tuple, val: Any, ttl_seconds: float) -> None:
    if ttl_seconds <= 0:
        return
    _JIRA_TTL_CACHE[key] = (time.monotonic() + float(ttl_seconds), val)


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
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-ExperimentalApi": "opt-in",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return set()
                data = await resp.json()
                rows = list(data.get("requestTypeFields") or data.get("values") or [])
                return {
                    item["fieldId"]
                    for item in rows
                    if isinstance(item, dict) and item.get("fieldId")
                }
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
            merge_chatbot_into_labels(request_field_values)
        request_field_values = {k: v for k, v in request_field_values.items() if k in allowed}
    else:
        logger.debug("JSM: список полей типа запроса пуст — не передаём labels в create (метка через REST после создания)")
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
                        if not allowed or "labels" not in allowed:
                            await _ensure_issue_has_chatbot_label(base_url, token, issue_key)
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


async def _ensure_issue_has_chatbot_label(base_url: str, token: str, issue_key: str) -> None:
    """Добавляет метку «чатбот» через REST, если при создании через JSM поле labels не в списке допустимых полей типа запроса."""
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    url_get = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}?fields=labels")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url_get, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("JSM KB labels: не прочитать labels %s: %s %s", issue_key, resp.status, (text or "")[:300])
                    return
                data = await resp.json()
    except Exception as e:
        logger.warning("JSM KB labels: GET labels %s: %s", issue_key, e)
        return
    existing = (data.get("fields") or {}).get("labels")
    if not isinstance(existing, list):
        existing = []
    if JIRA_LABEL_CHATBOT in existing:
        return
    new_labels = [str(x).strip() for x in existing if str(x).strip()]
    if JIRA_LABEL_CHATBOT not in new_labels:
        new_labels.append(JIRA_LABEL_CHATBOT)
    url_put = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}")
    body = {"fields": {"labels": new_labels}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(url_put, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (200, 204):
                    logger.debug("JSM KB: метка «%s» добавлена к %s", JIRA_LABEL_CHATBOT, issue_key)
                else:
                    text = await resp.text()
                    logger.warning(
                        "JSM KB: не удалось выставить метку «%s» для %s: %s %s",
                        JIRA_LABEL_CHATBOT,
                        issue_key,
                        resp.status,
                        (text or "")[:400],
                    )
    except Exception as e:
        logger.warning("JSM KB: PUT labels %s: %s", issue_key, e)


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


@retry_jira(max_attempts=3, base_delay=1.0)
async def _get_issue_info_http(issue_key: str) -> Optional[Dict[str, Any]]:
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
                return IssueInfo(summary=summary, status=status_name, description=desc)
    except Exception as e:
        logger.debug("get_issue_info %s: %s", issue_key, e)
        return None


async def get_issue_info(
    issue_key: str,
    *,
    timeout_total: float | None = None,
    ttl_seconds: float = 20.0,
) -> Optional[IssueInfo]:
    """
    Cached + in-flight dedup wrapper around Jira issue info.

    - ttl_seconds: короткий TTL для интерактивных экранов (уменьшает повторные запросы)
    - timeout_total: если задан, ограничивает общее ожидание (включая retries) через asyncio.wait_for
    """
    key = ("get_issue_info", (issue_key or "").strip().upper())
    cached = _cache_get(key)
    if cached is not None:
        return cached

    task = _JIRA_INFLIGHT.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_get_issue_info_http(issue_key))
        _JIRA_INFLIGHT[key] = task

    try:
        if timeout_total is not None:
            result = await asyncio.wait_for(task, timeout=float(timeout_total))
        else:
            result = await task
    finally:
        # cleanup finished tasks
        if task.done():
            _JIRA_INFLIGHT.pop(key, None)

    if result is not None:
        _cache_set(key, result, ttl_seconds)
    return result


@retry_jira(max_attempts=3, base_delay=1.0)
async def get_issue_admin_details(issue_key: str) -> Optional[IssueAdminDetails]:
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
                return IssueAdminDetails(
                    summary=(f.get("summary") or "").strip(),
                    status=(status_obj.get("name") or "").strip(),
                    description=desc,
                    assignee_display=(assignee_obj.get("displayName") or "").strip(),
                    assignee_username=(assignee_obj.get("name") or assignee_obj.get("key") or "").strip(),
                    reporter_display=(reporter_obj.get("displayName") or "").strip(),
                    reporter_username=(reporter_obj.get("name") or reporter_obj.get("key") or "").strip(),
                    issue_type=(issuetype_obj.get("name") or "").strip(),
                    project_key=(project_obj.get("key") or "").strip().upper(),
                )
    except Exception as e:
        logger.debug("get_issue_admin_details %s: %s", issue_key, e)
        return None


async def get_issues_admin_details_batch(
    issue_keys: List[str],
    *,
    batch_size: int = 50,
) -> Dict[str, IssueAdminDetails]:
    """
    Batch-версия get_issue_admin_details: одним JQL-запросом получает детали
    нескольких заявок (вместо N отдельных HTTP-запросов).

    Возвращает dict: issue_key -> details (та же структура, что у get_issue_admin_details).
    Заявки, которые Jira не вернула, отсутствуют в результате.
    """
    if not issue_keys:
        return {}
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA LOGIN_URL или TOKEN не задан")
        return {}

    fields_param = ["summary", "status", "description", "assignee", "reporter", "issuetype", "project"]
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "Authorization": f"Bearer {token}"}
    url = urljoin(base_url + "/", "rest/api/2/search")
    result: Dict[str, IssueAdminDetails] = {}

    valid_keys = [k for k in issue_keys if _safe_issue_key(k)]
    for start in range(0, len(valid_keys), batch_size):
        chunk = valid_keys[start: start + batch_size]
        jql = "key in (" + ", ".join(chunk) + ")"
        payload = {"jql": jql, "fields": fields_param, "maxResults": batch_size, "startAt": 0}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("get_issues_admin_details_batch: HTTP %s for chunk %s..%s",
                                       resp.status, start, start + len(chunk))
                        continue
                    data = await resp.json()
                    for issue in data.get("issues") or []:
                        key = (issue.get("key") or "").strip().upper()
                        if not key:
                            continue
                        f = issue.get("fields") or {}
                        status_obj = f.get("status") or {}
                        assignee_obj = f.get("assignee") or {}
                        reporter_obj = f.get("reporter") or {}
                        issuetype_obj = f.get("issuetype") or {}
                        project_obj = f.get("project") or {}
                        desc = (f.get("description") or "").strip()
                        if desc and len(desc) > 800:
                            desc = desc[:800] + "..."
                        result[key] = IssueAdminDetails(
                            summary=(f.get("summary") or "").strip(),
                            status=(status_obj.get("name") or "").strip(),
                            description=desc,
                            assignee_display=(assignee_obj.get("displayName") or "").strip(),
                            assignee_username=(assignee_obj.get("name") or assignee_obj.get("key") or "").strip(),
                            reporter_display=(reporter_obj.get("displayName") or "").strip(),
                            reporter_username=(reporter_obj.get("name") or reporter_obj.get("key") or "").strip(),
                            issue_type=(issuetype_obj.get("name") or "").strip(),
                            project_key=(project_obj.get("key") or "").strip().upper(),
                        )
        except Exception as e:
            logger.warning("get_issues_admin_details_batch chunk %s..%s: %s", start, start + len(chunk), e)
    return result


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


@overload
async def get_issue_comments(
    issue_key: str,
    include_internal: bool = False,
    return_http_status: Literal[False] = False,
    *,
    timeout_total: float | None = None,
    ttl_seconds: float = 10.0,
) -> list[dict] | None: ...


@overload
async def get_issue_comments(
    issue_key: str,
    include_internal: bool = False,
    return_http_status: Literal[True] = True,
    *,
    timeout_total: float | None = None,
    ttl_seconds: float = 10.0,
) -> CommentsResult: ...


async def get_issue_comments(
    issue_key: str,
    include_internal: bool = False,
    return_http_status: bool = False,
    *,
    timeout_total: float | None = None,
    ttl_seconds: float = 10.0,
) -> list[dict] | None | CommentsResult:
    """
    Возвращает список комментариев задачи или None при сетевой/HTTP ошибке (не путать с пустым списком).

    Внутренние (internal) по умолчанию исключаются (для JSM внутренних заметок `sd.public.comment.internal == true`).

    Важно: Jira REST отдает комментарии постранично. Раньше код без `startAt/maxResults` мог возвращать
    только первые комментарии, из-за чего уведомления “о новых комментариях” не срабатывали.
    """
    cache_key = (
        "get_issue_comments",
        (issue_key or "").strip().upper(),
        bool(include_internal),
        bool(return_http_status),
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # in-flight dedup (без return_http_status в key было бы проще, но сохраняем совместимость типов)
    task = _JIRA_INFLIGHT.get(cache_key)
    if task is None or task.done():
        task = asyncio.create_task(_get_issue_comments_uncached(issue_key, include_internal=include_internal, return_http_status=return_http_status))
        _JIRA_INFLIGHT[cache_key] = task

    try:
        if timeout_total is not None:
            result = await asyncio.wait_for(task, timeout=float(timeout_total))
        else:
            result = await task
    finally:
        if task.done():
            _JIRA_INFLIGHT.pop(cache_key, None)

    # Кэшируем даже None на короткое время, чтобы “дёрганье” кнопки не делало N одинаковых запросов.
    _cache_set(cache_key, result, ttl_seconds)
    return result


async def _get_issue_comments_uncached(
    issue_key: str,
    *,
    include_internal: bool = False,
    return_http_status: bool = False,
) -> Any:
    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        return CommentsResult(None, None) if return_http_status else None
    issue_key = _safe_issue_key(issue_key)
    if not issue_key:
        return CommentsResult(None, None) if return_http_status else None
    # expand=properties нужен, чтобы отличать public/internal комментарии в JSM.
    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key}/comment")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    all_comments: list = []
    start_at = 0
    # Большое maxResults снижает кол-во запросов к Jira при типичных объемах.
    page_size = 100
    try:
        session = await _get_jira_http_session()
        while True:
            params = {
                "expand": "properties",
                "startAt": start_at,
                "maxResults": page_size,
            }
            async with session.get(
                url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    body_preview = (body or "")[:300]
                    info = classify_http_error(status=int(resp.status), body_text=body or "")
                    mapped = info.mapped_status
                    if info.log_level == "info":
                        logger.info("get_issue_comments %s: HTTP %s %s", issue_key, resp.status, body_preview)
                    else:
                        # Prevent log spam in background loops
                        log_rate_limited(
                            logger.warning,
                            key=f"jira:get_issue_comments:{issue_key}:{resp.status}",
                            interval_seconds=60.0,
                            msg="get_issue_comments %s: HTTP %s %s",
                            args=(issue_key, resp.status, body_preview),
                        )
                    return CommentsResult(None, int(mapped or resp.status)) if return_http_status else None
                data = await resp.json()
                comments_page = list(data.get("comments") or [])
                if not comments_page:
                    break
                all_comments.extend(comments_page)

                total = data.get("total")
                # total может отсутствовать в некоторых конфигурациях; тогда ориентируемся на размер страницы.
                if isinstance(total, int) and start_at + len(all_comments) >= total:
                    break

                # Jira может вернуть меньше page_size в последней странице.
                if len(comments_page) < page_size:
                    break
                start_at += len(comments_page)

        # Сделаем порядок комментариев детерминированным: Jira обычно возвращает в нужном порядке,
        # но при пагинации/настройках лучше сортировать явно.
        def _sort_key(c: dict) -> tuple:
            created_raw = (c.get("created") or "")
            cid = c.get("id")
            try:
                cid_num = int(cid)
            except Exception:
                cid_num = 0

            # Формат от Jira Cloud: 2021-01-17T12:34:00.000+0000 (без ':' в смещении)
            # datetime.fromisoformat понимает только с двоеточием (+00:00), поэтому нормализуем.
            if created_raw:
                created_norm = created_raw
                if len(created_norm) > 5 and created_norm[-5] in ("+", "-") and created_norm[-3] != ":":
                    created_norm = created_norm[:-5] + created_norm[-5:-2] + ":" + created_norm[-2:]
                try:
                    dt = datetime.fromisoformat(created_norm)
                    return (dt, cid_num)
                except Exception:
                    pass
            return (created_raw, cid_num)

        all_comments.sort(key=_sort_key)
        if include_internal:
            return CommentsResult(all_comments, 200) if return_http_status else all_comments
        filtered = [c for c in all_comments if not _is_internal_comment(c)]
        return CommentsResult(filtered, 200) if return_http_status else filtered
    except Exception as e:
        # str(TimeoutError()) is empty on some platforms, so log type too.
        log_rate_limited(
            logger.warning,
            key=f"jira:get_issue_comments:exc:{(issue_key or '').strip().upper()}:{type(e).__name__}",
            interval_seconds=60.0,
            msg="get_issue_comments %s: %s: %r",
            args=(issue_key, type(e).__name__, e),
        )
        return CommentsResult(None, None) if return_http_status else None


async def get_issue_comments_tail(
    issue_key: str,
    *,
    limit: int = 5,
    include_internal: bool = False,
    timeout_total: float | None = None,
    ttl_seconds: float = 10.0,
) -> CommentsResult:
    """
    Быстрый вариант для UI карточки: возвращает только последние N комментариев.
    Делает 1-2 запроса вместо полной пагинации.
    """
    key = ("get_issue_comments_tail", (issue_key or "").strip().upper(), int(limit), bool(include_internal))
    cached = _cache_get(key)
    if isinstance(cached, CommentsResult):
        return cached

    issue_key_safe = _safe_issue_key(issue_key)
    if not issue_key_safe:
        res = CommentsResult(None, None)
        _cache_set(key, res, ttl_seconds)
        return res

    jira = CONFIG.get("JIRA", {})
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        res = CommentsResult(None, None)
        _cache_set(key, res, ttl_seconds)
        return res

    url = urljoin(base_url + "/", f"rest/api/2/issue/{issue_key_safe}/comment")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    async def _do() -> CommentsResult:
        session = await _get_jira_http_session()
        # 1) Get total cheaply (maxResults=1) with expand=properties if we need internal filtering later.
        params1 = {"startAt": 0, "maxResults": 1, "expand": "properties"}
        async with session.get(url, headers=headers, params=params1, timeout=aiohttp.ClientTimeout(total=10)) as r1:
            if r1.status != 200:
                body = await r1.text()
                info = classify_http_error(status=int(r1.status), body_text=body or "")
                mapped = int(info.mapped_status or r1.status)
                return CommentsResult(None, mapped)
            data1 = await r1.json()
            try:
                total = int(data1.get("total") or 0)
            except Exception:
                total = 0

        # 2) Fetch tail page.
        n = max(1, int(limit))
        start_at = max(0, max(0, total - n))
        params2 = {"startAt": start_at, "maxResults": n, "expand": "properties"}
        async with session.get(url, headers=headers, params=params2, timeout=aiohttp.ClientTimeout(total=15)) as r2:
            if r2.status != 200:
                body = await r2.text()
                info = classify_http_error(status=int(r2.status), body_text=body or "")
                mapped = int(info.mapped_status or r2.status)
                return CommentsResult(None, mapped)
            data2 = await r2.json()
            comments_page = list(data2.get("comments") or [])
            # Keep deterministic order and apply internal filter if needed.
            if include_internal:
                return CommentsResult(comments_page, 200)
            filtered = [c for c in comments_page if isinstance(c, dict) and not _is_internal_comment(c)]
            return CommentsResult(filtered, 200)

    try:
        if timeout_total is not None:
            res = await asyncio.wait_for(_do(), timeout=float(timeout_total))
        else:
            res = await _do()
    except Exception as e:
        log_rate_limited(
            logger.warning,
            key=f"jira:get_issue_comments_tail:exc:{(issue_key or '').strip().upper()}:{type(e).__name__}",
            interval_seconds=60.0,
            msg="get_issue_comments_tail %s: %s: %r",
            args=(issue_key, type(e).__name__, e),
        )
        res = CommentsResult(None, None)

    _cache_set(key, res, ttl_seconds)
    return res


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

    fields_rest: Dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": issuetype_payload,
        "description": description,
    }
    merge_chatbot_into_labels(fields_rest)
    issue_data = {"fields": fields_rest}

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


# --- JSM: «Чат-бот по базам знаний» (AA), конфиг JIRA_AA_KB_CHATBOT ---


async def _jsm_fetch_request_type_field_rows(
    base_url: str,
    token: str,
    service_desk_id: str,
    request_type_id: str,
) -> List[Dict[str, Any]]:
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
                    logger.warning("JSM KB: поля request type: HTTP %s %s", resp.status, (await resp.text())[:300])
                    return []
                data = await resp.json()
        rows = list(data.get("requestTypeFields") or data.get("values") or [])
        return [r for r in rows if isinstance(r, dict)]
    except Exception as e:
        logger.warning("JSM KB: поля request type: %s", e)
        return []


def _kb_row_by_field_id(rows: List[Dict[str, Any]], field_id: str) -> Optional[Dict[str, Any]]:
    fid = (field_id or "").strip()
    if not fid:
        return None
    for r in rows:
        if (r.get("fieldId") or "").strip() == fid:
            return r
    return None


def _kb_collect_valid_values(field_row: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not field_row or not isinstance(field_row, dict):
        return []
    for key in ("validValues", "values", "choices"):
        raw = field_row.get(key)
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
    return []


def _kb_select_option_payload(field_row: Optional[Dict[str, Any]], selected_label: str) -> Optional[Any]:
    target = (selected_label or "").strip()
    if not target:
        return None
    target_l = target.lower()
    for opt in _kb_collect_valid_values(field_row):
        label = (opt.get("label") or "").strip()
        value = opt.get("value")
        value_s = str(value).strip() if value is not None else ""
        if label.lower() == target_l or value_s.lower() == target_l:
            oid = opt.get("id")
            if oid is not None and str(oid).strip():
                return {"id": str(oid)}
            if value_s:
                return {"id": value_s} if str(value).isdigit() else {"value": value_s}
            if label:
                return {"value": label}
    return {"value": target}


def _kb_expects_option_array(field_row: Optional[Dict[str, Any]]) -> bool:
    """Чекбоксы / multi-select в JSM отдают jiraSchema type=array, items=option — в API нужен список опций."""
    if not field_row or not isinstance(field_row, dict):
        return False
    schema = field_row.get("jiraSchema") or {}
    if not isinstance(schema, dict):
        return False
    return (schema.get("type") == "array") and (schema.get("items") == "option")


def _kb_put_option_in_rfv(
    rfv: Dict[str, Any],
    field_id: str,
    field_row: Optional[Dict[str, Any]],
    selected_label: str,
    *,
    fallback_plain: Optional[str] = None,
) -> None:
    label = (selected_label or "").strip() or (fallback_plain or "").strip()
    if not label:
        return
    pl = _kb_select_option_payload(field_row, label)
    if pl is None:
        pl = {"value": label}
    if _kb_expects_option_array(field_row):
        rfv[field_id] = [pl]
    else:
        rfv[field_id] = pl


def _kb_resolve_field_id(
    rows: List[Dict[str, Any]],
    allowed: Set[str],
    explicit: str,
    name_hints: tuple[str, ...],
) -> str:
    exp = (explicit or "").strip()
    if exp and (not allowed or exp in allowed):
        return exp
    for r in rows:
        fid = (r.get("fieldId") or "").strip()
        if not fid:
            continue
        if allowed and fid not in allowed:
            continue
        name = (r.get("name") or r.get("label") or "").lower()
        if any(h in name for h in name_hints):
            return fid
    return ""


def _kb_resolve_edit_type_field_id(
    rows: List[Dict[str, Any]],
    allowed: Set[str],
    candidates: List[str],
) -> str:
    for c in candidates or []:
        cid = (c or "").strip()
        if cid and (not allowed or cid in allowed):
            return cid
    return _kb_resolve_field_id(rows, allowed, "", ("aa edit type", "edit type"))


def _kb_resolve_field_id_candidates(
    rows: List[Dict[str, Any]],
    allowed: Set[str],
    candidates: List[str],
    name_hints: tuple[str, ...],
) -> str:
    for c in candidates or []:
        cid = (str(c) or "").strip()
        if cid and (not allowed or cid in allowed):
            return cid
    return _kb_resolve_field_id(rows, allowed, "", name_hints)


def _kb_resolve_pc_account_service_checkbox_field_id(
    rows: List[Dict[str, Any]],
    allowed: Set[str],
    explicit: str,
    option_label: str,
) -> str:
    """
    Чекбокс «нужен сервис: учётная запись для входа на ПК» (как почта/чат-бот на том же request type).
    Без него Jira отвечает 400 про «не выбран ни один из необходимых сервисов».
    """
    exp = (explicit or "").strip()
    if exp and (not allowed or exp in allowed):
        return exp

    opt_target = (option_label or "Нужно").strip().lower()
    exclude_sub = (
        "почт",
        "браузер",
        "owa",
        "outlook",
        "чат-бот",
        "chatbot",
        "баз знан",
        "knowledge",
        "mail browser",
        "корпоративной почт",
    )
    keyword_sub = (
        "пк",
        "ldap",
        "учетн",
        "учётн",
        "вход на пк",
        "входа на пк",
        "учетная запись",
        "учётная запись",
        "active directory",
        "рабочей станц",
        "рабочая станц",
    )

    def _row_name(r: Dict[str, Any]) -> str:
        return ((r.get("name") or "") + " " + (r.get("label") or "")).strip()

    def _name_matches_pc_service(nm: str) -> bool:
        n = nm.lower()
        if any(x in n for x in exclude_sub):
            return False
        return any(k in n for k in keyword_sub)

    for r in rows:
        fid = (r.get("fieldId") or "").strip()
        if not fid or (allowed and fid not in allowed):
            continue
        nm = _row_name(r)
        if not _name_matches_pc_service(nm):
            continue
        vals = _kb_collect_valid_values(r)
        for opt in vals:
            lab = (opt.get("label") or "").strip().lower()
            if lab == opt_target or (opt_target and opt_target in lab):
                logger.info("JSM PCAccount: чекбокс сервиса ПК: %s (%s)", fid, nm[:120])
                return fid

    return ""


async def create_aa_knowledge_chatbot_issue(
    *,
    full_name: str,
    position: str,
    department: str,
    aa_edit_type: str,
    ad_account: str,
    existing_phone: str,
    description: str,
    jira_username: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Создаёт заявку в JSM AA для сценария «Чат-бот по базам знаний».
    Поля подбираются по схеме request type + JIRA_AA_KB_CHATBOT_* в .env.
    """
    jira = CONFIG.get("JIRA", {})
    kb = CONFIG.get("JIRA_AA_KB_CHATBOT") or {}
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA LOGIN_URL или TOKEN не заданы")
        return False, "Не настроено подключение к Jira."

    service_desk_id = (kb.get("SERVICE_DESK_ID") or "").strip()
    request_type_id = (kb.get("REQUEST_TYPE_ID") or "").strip()
    if not service_desk_id or not request_type_id:
        return False, (
            "Заявка не настроена: задайте в .env JIRA_AA_KB_CHATBOT_REQUEST_TYPE_ID "
            "(и при необходимости JIRA_AA_KB_CHATBOT_SERVICE_DESK_ID)."
        )

    summary = sanitize_jira_text((kb.get("SUMMARY") or "Чат-бот по базам знаний").strip(), max_len=255)
    desc_stripped = (description or "").strip()
    description_payload = sanitize_jira_text(desc_stripped, max_len=4000) if desc_stripped else ""
    phone_jira = normalize_phone_for_jira(existing_phone or "")

    rows = await _jsm_fetch_request_type_field_rows(base_url, token, service_desk_id, request_type_id)
    allowed = {r.get("fieldId") for r in rows if r.get("fieldId")}
    if allowed:
        logger.info("JSM KB: допустимые поля requestTypeId=%s: %s", request_type_id, sorted(allowed))

    dept_fid = (JIRA_AA.get("FIELD_DEPARTMENT") or FIELDS.get("DEPARTMENT") or "").strip()
    ad_fid = (FIELDS.get("AD_ACCOUNT") or "").strip()
    phone_fid = (FIELDS.get("EXISTING_PHONE") or "").strip()

    fn_fid = _kb_resolve_field_id(
        rows, allowed, (kb.get("FIELD_FULL_NAME") or "").strip(), ("full name", "фио", "полное имя")
    )
    pos_fid = _kb_resolve_field_id(
        rows, allowed, (kb.get("FIELD_POSITION") or "").strip(), ("position", "должност")
    )
    edit_fid = _kb_resolve_edit_type_field_id(
        rows, allowed, list(kb.get("FIELD_EDIT_TYPE_CANDIDATES") or [])
    )
    chatbot_fid = _kb_resolve_field_id(
        rows,
        allowed,
        (kb.get("FIELD_AA_CHATBOT") or "").strip(),
        ("aa chatbot", "chatbot", "чат-бот", "баз знан", "базы знан"),
    )
    req_fid = _kb_resolve_field_id(
        rows,
        allowed,
        (kb.get("FIELD_REQUEST_TYPE_SELECT") or "").strip(),
        ("request type", "тип запроса"),
    )

    rfv: Dict[str, Any] = {}

    if fn_fid:
        rfv[fn_fid] = sanitize_jira_text((full_name or "").strip(), max_len=255)
    if pos_fid:
        rfv[pos_fid] = sanitize_jira_text((position or "").strip(), max_len=255)

    if edit_fid:
        er = _kb_row_by_field_id(rows, edit_fid)
        pl = _kb_select_option_payload(er, aa_edit_type)
        val: Any = pl if pl is not None else {"value": aa_edit_type}
        rfv[edit_fid] = [val] if _kb_expects_option_array(er) else val

    if chatbot_fid:
        cr = _kb_row_by_field_id(rows, chatbot_fid)
        chatbot_label = (kb.get("AA_CHATBOT_OPTION_LABEL") or "").strip() or "Нужно"
        _kb_put_option_in_rfv(rfv, chatbot_fid, cr, chatbot_label)

    req_label = (kb.get("REQUEST_TYPE_OPTION_LABEL") or "").strip()
    if req_fid and req_label:
        rr = _kb_row_by_field_id(rows, req_fid)
        _kb_put_option_in_rfv(rfv, req_fid, rr, req_label)

    if dept_fid and (department or "").strip():
        rfv[dept_fid] = {"value": (department or "").strip()}
    if ad_fid:
        rfv[ad_fid] = (ad_account or "").strip()
    if phone_fid:
        rfv[phone_fid] = phone_jira

    if not allowed or "summary" in allowed:
        rfv["summary"] = summary
    if description_payload and (not allowed or "description" in allowed):
        rfv["description"] = description_payload

    if allowed and "labels" in allowed:
        merge_chatbot_into_labels(rfv)
    if allowed:
        rfv = {k: v for k, v in rfv.items() if k in allowed}
    else:
        logger.debug("JSM KB: пустой список полей API — отправляем все собранные ключи")

    if not rfv:
        logger.error("JSM KB: нечего отправлять в requestFieldValues")
        return False, "Не удалось сопоставить поля Jira для этого типа запроса. Проверьте .env."

    payload = {
        "serviceDeskId": str(service_desk_id),
        "requestTypeId": str(request_type_id),
        "requestFieldValues": rfv,
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
                    if issue_key:
                        logger.info("JSM KB: создана заявка %s", issue_key)
                        assignee_username = (JIRA_AA.get("ASSIGNEE_USERNAME") or "").strip()
                        if assignee_username and JIRA_AA.get("SET_ASSIGNEE", True):
                            await _set_assignee(base_url, token, issue_key, assignee_username)
                        if jira_username:
                            try:
                                await _set_reporter(base_url, token, issue_key, jira_username)
                            except Exception as e:
                                logger.warning("JSM KB: не удалось сменить автора %s: %s", issue_key, e)
                        if not allowed or "labels" not in allowed:
                            await _ensure_issue_has_chatbot_label(base_url, token, issue_key)
                        return True, issue_key
                text = await resp.text()
                logger.warning("JSM KB create failed: %s %s", resp.status, text[:500])
                return False, f"Ошибка Jira: {resp.status}. {(text or '')[:200]}"
    except Exception as e:
        logger.exception("JSM KB create: %s", e)
        return False, str(e)


async def create_aa_mail_browser_issue(
    *,
    full_name: str,
    position: str,
    department: str,
    aa_edit_type: str,
    ad_account: str,
    existing_phone: str,
    description: str,
    jira_username: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    JSM AA: «Доступ к корпоративной почте через браузер» (чекбокс + те же базовые поля, что у чат-бота).
    Конфиг JIRA_AA_MAIL_BROWSER_*; при пустых desk/request type подставляются значения из JIRA_AA_KB_CHATBOT_*.
    """
    jira = CONFIG.get("JIRA", {})
    mb = CONFIG.get("JIRA_AA_MAIL_BROWSER") or {}
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA LOGIN_URL или TOKEN не заданы")
        return False, "Не настроено подключение к Jira."

    service_desk_id = (mb.get("SERVICE_DESK_ID") or "").strip()
    request_type_id = (mb.get("REQUEST_TYPE_ID") or "").strip()
    if not service_desk_id or not request_type_id:
        return False, (
            "Заявка не настроена: задайте в .env JIRA_AA_MAIL_BROWSER_REQUEST_TYPE_ID "
            "(или JIRA_AA_KB_CHATBOT_REQUEST_TYPE_ID) и при необходимости SERVICE_DESK_ID."
        )

    summary = sanitize_jira_text((mb.get("SUMMARY") or "Доступ к корпоративной почте через браузер").strip(), max_len=255)
    desc_stripped = (description or "").strip()
    description_payload = sanitize_jira_text(desc_stripped, max_len=4000) if desc_stripped else ""
    phone_jira = normalize_phone_for_jira(existing_phone or "")

    rows = await _jsm_fetch_request_type_field_rows(base_url, token, service_desk_id, request_type_id)
    allowed = {r.get("fieldId") for r in rows if r.get("fieldId")}
    if allowed:
        logger.info("JSM MailBrowser: допустимые поля requestTypeId=%s: %s", request_type_id, sorted(allowed))

    dept_fid = (JIRA_AA.get("FIELD_DEPARTMENT") or FIELDS.get("DEPARTMENT") or "").strip()
    ad_fid = (FIELDS.get("AD_ACCOUNT") or "").strip()
    phone_fid = (FIELDS.get("EXISTING_PHONE") or "").strip()

    fn_fid = _kb_resolve_field_id(
        rows, allowed, (mb.get("FIELD_FULL_NAME") or "").strip(), ("full name", "фио", "полное имя")
    )
    pos_fid = _kb_resolve_field_id(
        rows, allowed, (mb.get("FIELD_POSITION") or "").strip(), ("position", "должност")
    )
    edit_fid = _kb_resolve_edit_type_field_id(
        rows, allowed, list(mb.get("FIELD_EDIT_TYPE_CANDIDATES") or [])
    )
    mail_fid = _kb_resolve_field_id(
        rows,
        allowed,
        (mb.get("FIELD_MAIL_BROWSER") or "").strip(),
        (
            "почт",
            "браузер",
            "корпоратив",
            "owa",
            "outlook",
            "web",
            "mail browser",
            "доступ к корпоративной почте",
        ),
    )
    req_fid = _kb_resolve_field_id(
        rows,
        allowed,
        (mb.get("FIELD_REQUEST_TYPE_SELECT") or "").strip(),
        ("request type", "тип запроса"),
    )

    rfv: Dict[str, Any] = {}

    if fn_fid:
        rfv[fn_fid] = sanitize_jira_text((full_name or "").strip(), max_len=255)
    if pos_fid:
        rfv[pos_fid] = sanitize_jira_text((position or "").strip(), max_len=255)

    if edit_fid:
        er = _kb_row_by_field_id(rows, edit_fid)
        pl = _kb_select_option_payload(er, aa_edit_type)
        val: Any = pl if pl is not None else {"value": aa_edit_type}
        rfv[edit_fid] = [val] if _kb_expects_option_array(er) else val

    if mail_fid:
        mr = _kb_row_by_field_id(rows, mail_fid)
        mail_lbl = (mb.get("MAIL_BROWSER_OPTION_LABEL") or "").strip() or "Нужно"
        _kb_put_option_in_rfv(rfv, mail_fid, mr, mail_lbl)

    req_label = (mb.get("REQUEST_TYPE_OPTION_LABEL") or "").strip()
    if req_fid and req_label:
        rr = _kb_row_by_field_id(rows, req_fid)
        _kb_put_option_in_rfv(rfv, req_fid, rr, req_label)

    if dept_fid and (department or "").strip():
        rfv[dept_fid] = {"value": (department or "").strip()}
    if ad_fid:
        rfv[ad_fid] = (ad_account or "").strip()
    if phone_fid:
        rfv[phone_fid] = phone_jira

    if not allowed or "summary" in allowed:
        rfv["summary"] = summary
    if description_payload and (not allowed or "description" in allowed):
        rfv["description"] = description_payload

    if allowed and "labels" in allowed:
        merge_chatbot_into_labels(rfv)
    if allowed:
        rfv = {k: v for k, v in rfv.items() if k in allowed}
    else:
        logger.debug("JSM MailBrowser: пустой список полей API — отправляем все собранные ключи")

    if not rfv:
        logger.error("JSM MailBrowser: нечего отправлять в requestFieldValues")
        return False, "Не удалось сопоставить поля Jira для этого типа запроса. Проверьте .env."

    payload = {
        "serviceDeskId": str(service_desk_id),
        "requestTypeId": str(request_type_id),
        "requestFieldValues": rfv,
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
                    if issue_key:
                        logger.info("JSM MailBrowser: создана заявка %s", issue_key)
                        assignee_username = (JIRA_AA.get("ASSIGNEE_USERNAME") or "").strip()
                        if assignee_username and JIRA_AA.get("SET_ASSIGNEE", True):
                            await _set_assignee(base_url, token, issue_key, assignee_username)
                        if jira_username:
                            try:
                                await _set_reporter(base_url, token, issue_key, jira_username)
                            except Exception as e:
                                logger.warning("JSM MailBrowser: не удалось сменить автора %s: %s", issue_key, e)
                        if not allowed or "labels" not in allowed:
                            await _ensure_issue_has_chatbot_label(base_url, token, issue_key)
                        return True, issue_key
                text = await resp.text()
                logger.warning("JSM MailBrowser create failed: %s %s", resp.status, text[:500])
                return False, f"Ошибка Jira: {resp.status}. {(text or '')[:200]}"
    except Exception as e:
        logger.exception("JSM MailBrowser create: %s", e)
        return False, str(e)


async def create_aa_pc_account_issue(
    *,
    full_name: str,
    position: str,
    department: str,
    ad_edit_type: str,
    ad_account: str,
    existing_phone: str,
    copy_rights_source: str,
    security_group_name: str,
    description: str,
    jira_username: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    JSM AA: «Учетная запись для входа на ПК» — поле AA AD Edit type (select) и опционально
    «с кого копировать» / «имя группы безопасности». Конфиг JIRA_AA_PC_ACCOUNT_*.
    """
    jira = CONFIG.get("JIRA", {})
    pc = CONFIG.get("JIRA_AA_PC_ACCOUNT") or {}
    base_url = (jira.get("LOGIN_URL") or "").strip().rstrip("/")
    token = (jira.get("TOKEN") or "").strip()
    if not base_url or not token:
        logger.error("JIRA LOGIN_URL или TOKEN не заданы")
        return False, "Не настроено подключение к Jira."

    service_desk_id = (pc.get("SERVICE_DESK_ID") or "").strip()
    request_type_id = (pc.get("REQUEST_TYPE_ID") or "").strip()
    if not service_desk_id or not request_type_id:
        return False, (
            "Заявка не настроена: задайте в .env JIRA_AA_PC_ACCOUNT_REQUEST_TYPE_ID "
            "(или JIRA_AA_MAIL_BROWSER_REQUEST_TYPE_ID / JIRA_AA_KB_CHATBOT_REQUEST_TYPE_ID) "
            "и при необходимости SERVICE_DESK_ID."
        )

    summary = sanitize_jira_text((pc.get("SUMMARY") or "Учетная запись для входа на ПК").strip(), max_len=255)
    desc_stripped = (description or "").strip()
    description_payload = sanitize_jira_text(desc_stripped, max_len=4000) if desc_stripped else ""
    phone_jira = normalize_phone_for_jira(existing_phone or "")

    rows = await _jsm_fetch_request_type_field_rows(base_url, token, service_desk_id, request_type_id)
    allowed = {r.get("fieldId") for r in rows if r.get("fieldId")}
    if allowed:
        logger.info("JSM PCAccount: допустимые поля requestTypeId=%s: %s", request_type_id, sorted(allowed))

    dept_fid = (JIRA_AA.get("FIELD_DEPARTMENT") or FIELDS.get("DEPARTMENT") or "").strip()
    ad_fid = (FIELDS.get("AD_ACCOUNT") or "").strip()
    phone_fid = (FIELDS.get("EXISTING_PHONE") or "").strip()

    fn_fid = _kb_resolve_field_id(
        rows, allowed, (pc.get("FIELD_FULL_NAME") or "").strip(), ("full name", "фио", "полное имя")
    )
    pos_fid = _kb_resolve_field_id(
        rows, allowed, (pc.get("FIELD_POSITION") or "").strip(), ("position", "должност")
    )
    ad_edit_fid = _kb_resolve_field_id_candidates(
        rows,
        allowed,
        list(pc.get("FIELD_AD_EDIT_TYPE_CANDIDATES") or []),
        ("aa ad edit", "ad edit type", "требуем действ", "требуемые действ"),
    )
    copy_fid = _kb_resolve_field_id(
        rows,
        allowed,
        (pc.get("FIELD_COPY_SOURCE") or "").strip(),
        ("копир", "прав", "источник", "от кого", "у кого", "copy rights", "template"),
    )
    group_fid = _kb_resolve_field_id(
        rows,
        allowed,
        (pc.get("FIELD_SECURITY_GROUP") or "").strip(),
        ("групп", "безопасност", "security group", "имя групп", "ad group"),
    )
    pc_chk_fid = _kb_resolve_pc_account_service_checkbox_field_id(
        rows,
        allowed,
        (pc.get("FIELD_PC_ACCOUNT") or "").strip(),
        (pc.get("PC_OPTION_LABEL") or "").strip() or "Нужно",
    )
    req_fid = _kb_resolve_field_id(
        rows,
        allowed,
        (pc.get("FIELD_REQUEST_TYPE_SELECT") or "").strip(),
        ("request type", "тип запроса"),
    )

    rfv: Dict[str, Any] = {}

    if fn_fid:
        rfv[fn_fid] = sanitize_jira_text((full_name or "").strip(), max_len=255)
    if pos_fid:
        rfv[pos_fid] = sanitize_jira_text((position or "").strip(), max_len=255)

    if ad_edit_fid:
        er = _kb_row_by_field_id(rows, ad_edit_fid)
        pl = _kb_select_option_payload(er, ad_edit_type)
        val: Any = pl if pl is not None else {"value": ad_edit_type}
        rfv[ad_edit_fid] = [val] if _kb_expects_option_array(er) else val

    if copy_fid and (copy_rights_source or "").strip():
        rfv[copy_fid] = sanitize_jira_text((copy_rights_source or "").strip().lower(), max_len=255)
    if group_fid and (security_group_name or "").strip():
        rfv[group_fid] = sanitize_jira_text((security_group_name or "").strip(), max_len=255)

    if allowed and not pc_chk_fid:
        return (
            False,
            "Не найден чекбокс сервиса «Учетная запись для входа на ПК» в полях JSM. "
            "Укажите в .env JIRA_AA_PC_ACCOUNT_FIELD_PC_ACCOUNT=customfield_XXXXX "
            "(из API полей request type) и при необходимости JIRA_AA_PC_ACCOUNT_PC_OPTION_LABEL.",
        )

    if pc_chk_fid:
        cr = _kb_row_by_field_id(rows, pc_chk_fid)
        pc_lbl = (pc.get("PC_OPTION_LABEL") or "").strip() or "Нужно"
        _kb_put_option_in_rfv(rfv, pc_chk_fid, cr, pc_lbl)

    req_label = (pc.get("REQUEST_TYPE_OPTION_LABEL") or "").strip()
    if req_fid and req_label:
        rr = _kb_row_by_field_id(rows, req_fid)
        _kb_put_option_in_rfv(rfv, req_fid, rr, req_label)

    if dept_fid and (department or "").strip():
        rfv[dept_fid] = {"value": (department or "").strip()}
    if ad_fid:
        rfv[ad_fid] = (ad_account or "").strip()
    if phone_fid:
        rfv[phone_fid] = phone_jira

    if not allowed or "summary" in allowed:
        rfv["summary"] = summary
    if description_payload and (not allowed or "description" in allowed):
        rfv["description"] = description_payload

    if allowed and "labels" in allowed:
        merge_chatbot_into_labels(rfv)
    if allowed:
        rfv = {k: v for k, v in rfv.items() if k in allowed}
    else:
        logger.debug("JSM PCAccount: пустой список полей API — отправляем все собранные ключи")

    if not rfv:
        logger.error("JSM PCAccount: нечего отправлять в requestFieldValues")
        return False, "Не удалось сопоставить поля Jira для этого типа запроса. Проверьте .env."

    payload = {
        "serviceDeskId": str(service_desk_id),
        "requestTypeId": str(request_type_id),
        "requestFieldValues": rfv,
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
                    if issue_key:
                        logger.info("JSM PCAccount: создана заявка %s", issue_key)
                        assignee_username = (JIRA_AA.get("ASSIGNEE_USERNAME") or "").strip()
                        if assignee_username and JIRA_AA.get("SET_ASSIGNEE", True):
                            await _set_assignee(base_url, token, issue_key, assignee_username)
                        if jira_username:
                            try:
                                await _set_reporter(base_url, token, issue_key, jira_username)
                            except Exception as e:
                                logger.warning("JSM PCAccount: не удалось сменить автора %s: %s", issue_key, e)
                        if not allowed or "labels" not in allowed:
                            await _ensure_issue_has_chatbot_label(base_url, token, issue_key)
                        return True, issue_key
                text = await resp.text()
                logger.warning("JSM PCAccount create failed: %s %s", resp.status, text[:500])
                return False, f"Ошибка Jira: {resp.status}. {(text or '')[:200]}"
    except Exception as e:
        logger.exception("JSM PCAccount create: %s", e)
        return False, str(e)
