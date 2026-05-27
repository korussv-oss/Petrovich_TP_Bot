"""
MAX: если create_ticket вернул NEED_PROFILE_DEPARTMENT — выбор подразделения из Jira (AA)
и сохранение в profile.department, затем повторная отправка заявки.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ITEMS = 8
_pending: Dict[int, Dict[str, Any]] = {}


def _clear(user_id: int) -> None:
    _pending.pop(int(user_id), None)


def _build_pick_ui(depts: List[str], page: int) -> Tuple[str, List[Dict[str, Any]]]:
    if not depts:
        return (
            "🏢 Не удалось загрузить список подразделений. Попробуйте позже.",
            [{"id": "back_to_main", "label": "🔙 В главное меню"}],
        )
    start = page * _ITEMS
    end = start + _ITEMS
    chunk = depts[start:end]
    rows: List[Dict[str, Any]] = []
    for i, name in enumerate(chunk):
        idx = start + i
        label = (name or "")[:64] if name else str(idx)
        rows.append({"id": f"jd_{idx}", "label": label})
    nav: List[Dict[str, Any]] = []
    if page > 0:
        nav.append({"id": f"jdpg_{page - 1}", "label": "◀️ Назад"})
    if end < len(depts):
        nav.append({"id": f"jdpg_{page + 1}", "label": "Вперёд ▶️"})
    buttons: List[Dict[str, Any]] = []
    for r in rows:
        buttons.append(r)
    if nav:
        buttons.extend(nav)
    buttons.append({"id": "jd_cancel", "label": "❌ Отмена"})
    text = (
        "🏢 <b>Подразделение для заявок в поддержку</b>\n\n"
        "Выберите подразделение — оно будет сохранено в профиле бота, затем заявка будет создана."
    )
    return text, buttons


async def _download_tokens_to_paths(bot, attachment_tokens: List[Any]) -> List[str]:
    # Ленивый импорт: избегаем цикла main_max ↔ этот модуль.
    from adapters.max import main_max as _mm

    temp_paths: List[str] = []
    for att in (attachment_tokens or [])[:10]:
        if not isinstance(att, dict) or not att.get("url"):
            continue
        try:
            logger.info("MAX attachments: download requested (url=%s)", (att.get("url") or "")[:128])
            downloaded = await _mm._download_attachment_max(bot, att)
        except Exception as e:
            logger.warning("MAX attachments: download failed: %s", e)
            continue
        if not downloaded:
            logger.warning("MAX attachments: download returned empty (url=%s)", (att.get("url") or "")[:128])
            continue
        content, name = downloaded
        logger.info("MAX attachments: download ok (name=%s, bytes=%s)", name, len(content) if content else 0)
        ext = os.path.splitext(name)[1] if name and "." in name else ".bin"
        f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="mxatt_")
        f.write(content)
        f.close()
        try:
            logger.info("MAX attachments: saved temp file (path=%s, bytes=%s)", f.name, os.path.getsize(f.name))
        except Exception:
            logger.info("MAX attachments: saved temp file (path=%s)", f.name)
        temp_paths.append(f.name)
    return temp_paths


async def max_submit_ticket_with_profile_department(
    bot,
    user_id: int,
    ticket_type_id: str,
    form_data: dict,
    attachment_tokens: Optional[List[Any]] = None,
    *,
    wms_attach_after_create: bool = False,
) -> dict:
    """
    Создаёт заявку в MAX или переводит пользователя к выбору подразделения Jira.
    wms_attach_after_create: для wms_issue — вложения добавляются после создания ключа.
    """
    from adapters.max.ticket_create_guard import get_max_ticket_create_guard

    guard = get_max_ticket_create_guard()
    return await guard.run(
        user_id,
        ticket_type_id,
        form_data,
        lambda: _max_submit_ticket_with_profile_department_impl(
            bot,
            user_id,
            ticket_type_id,
            form_data,
            attachment_tokens,
            wms_attach_after_create=wms_attach_after_create,
        ),
    )


async def _max_submit_ticket_with_profile_department_impl(
    bot,
    user_id: int,
    ticket_type_id: str,
    form_data: dict,
    attachment_tokens: Optional[List[Any]] = None,
    *,
    wms_attach_after_create: bool = False,
) -> dict:
    from core.support.api import NEED_PROFILE_DEPARTMENT, support_api

    uid = int(user_id)
    tokens = list(attachment_tokens or [])
    temp_paths: List[str] = []
    try:
        if not wms_attach_after_create:
            logger.info(
                "MAX ticket create: preparing attachments (ticket_type_id=%s, tokens=%s, user_id=%s)",
                ticket_type_id, len(tokens), uid,
            )
            temp_paths = await _download_tokens_to_paths(bot, tokens)
            logger.info(
                "MAX ticket create: attachments prepared (paths=%s, user_id=%s)",
                len(temp_paths), uid,
            )
            success, issue_key, user_msg = await support_api.create_ticket(
                "max", uid, ticket_type_id, form_data, attachment_paths=temp_paths or None
            )
        else:
            success, issue_key, user_msg = await support_api.create_ticket(
                "max", uid, ticket_type_id, form_data, attachment_paths=None
            )
            if success and issue_key and tokens:
                temp_paths = await _download_tokens_to_paths(bot, tokens)
                try:
                    from core.jira_wms import add_attachments_to_issue

                    added, _ = await add_attachments_to_issue(issue_key, temp_paths)
                    if added:
                        user_msg = (user_msg or "") + f"\n\n📎 Приложено файлов: {added}."
                except Exception as e:
                    logger.warning("MAX WMS: вложения после создания: %s", e)
        logger.info(
            "MAX ticket create: result (success=%s, issue_key=%s, ticket_type_id=%s, user_id=%s, attached_paths=%s)",
            success,
            issue_key,
            ticket_type_id,
            uid,
            len(temp_paths),
        )

        if not success and issue_key == NEED_PROFILE_DEPARTMENT:
            from core.jira_departments import get_departments_async

            depts = await get_departments_async()
            _pending[uid] = {
                "ticket_type_id": ticket_type_id,
                "form_data": dict(form_data or {}),
                "attachment_tokens": tokens,
                "dept_list": list(depts or []),
                "page": 0,
                "wms_attach_after_create": bool(wms_attach_after_create),
            }
            txt, btns = _build_pick_ui(_pending[uid]["dept_list"], 0)
            return {"text": txt, "parse_mode": "HTML", "buttons": btns}

        msg_show = user_msg if success else issue_key
        return {
            "text": f"✅ {msg_show}" if success else f"❌ {msg_show}",
            "parse_mode": "HTML",
            "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
        }
    finally:
        for p in temp_paths:
            try:
                os.unlink(p)
            except Exception:
                pass


async def handle_jira_dept_max_callback(bot, user_id: int, callback_id: str) -> Optional[dict]:
    """Обработка jd_*, jdpg_*, jd_cancel. Возвращает response или None если не наш callback."""
    if not callback_id.startswith(("jd_", "jdpg_", "jd_cancel")):
        return None
    uid = int(user_id)
    if callback_id == "jd_cancel":
        _clear(uid)
        return {
            "text": "❌ Создание заявки отменено. Подразделение в профиле не изменено.",
            "parse_mode": "HTML",
            "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
        }
    st = _pending.get(uid)
    if not st:
        return {
            "text": "⚠️ Сессия устарела. Начните создание заявки заново.",
            "parse_mode": "HTML",
            "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
        }
    depts: List[str] = list(st.get("dept_list") or [])
    if callback_id.startswith("jdpg_"):
        try:
            page = int(callback_id.replace("jdpg_", "", 1))
        except ValueError:
            return None
        if page < 0:
            page = 0
        st["page"] = page
        txt, btns = _build_pick_ui(depts, page)
        return {"text": txt, "parse_mode": "HTML", "buttons": btns}
    if not callback_id.startswith("jd_"):
        return {
            "text": "Неизвестное действие.",
            "parse_mode": "HTML",
            "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
        }
    try:
        idx = int(callback_id.replace("jd_", "", 1))
    except ValueError:
        return None
    if idx < 0 or idx >= len(depts):
        return {"text": "Неверный выбор.", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
    label = depts[idx]
    from user_storage import get_user_profile, save_user_profile

    old = get_user_profile(uid, "max") or {}
    profile = dict(old)
    profile["department"] = label
    save_user_profile(uid, profile, old_profile=old)

    ticket_type_id = (st.get("ticket_type_id") or "").strip()
    form_data = dict(st.get("form_data") or {})
    tokens = list(st.get("attachment_tokens") or [])
    wms_after = bool(st.get("wms_attach_after_create"))
    _clear(uid)
    return await max_submit_ticket_with_profile_department(
        bot, uid, ticket_type_id, form_data, tokens, wms_attach_after_create=wms_after
    )
