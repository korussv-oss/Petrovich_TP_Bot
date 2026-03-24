"""
Точка входа MAX-адаптера: long polling через MaxBotAPI (botapi.max.ru).
При наличии MAX_BOT_TOKEN и установленном пакете maxbotapi — запуск бота в одном процессе с Telegram.
"""
import asyncio
import logging
import json
import os
import random
import re
import time
from pathlib import Path

from core.jira_status_ru import jira_status_display_ru

logger = logging.getLogger(__name__)

# Состояние polling-offset по update_id, чтобы после рестарта MAX не переобрабатывал “старые” команды.
_MAX_POLL_STATE_FILE = Path(__file__).resolve().parents[2] / "data" / "max_poll_state.json"
_MAX_UPDATE_DEDUP_FILE = Path(__file__).resolve().parents[2] / "data" / "max_update_dedup.json"
_MAX_UPDATE_DEDUP_SAVE_EVERY_UPDATES = int(os.getenv("MAX_UPDATE_DEDUP_SAVE_EVERY_UPDATES", "25"))


def _load_max_poll_state() -> dict:
    try:
        if not _MAX_POLL_STATE_FILE.exists():
            return {}
        with open(_MAX_POLL_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_max_poll_state(state: dict) -> None:
    try:
        _MAX_POLL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_MAX_POLL_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        # Не валим бот, если не удалось сохранить baseline.
        return


def _get_max_last_update_id(token: str) -> int | None:
    token_key = (token or "").strip()
    if not token_key:
        return None
    state = _load_max_poll_state()
    last = state.get(token_key, {}).get("last_update_id")
    try:
        return int(last) if last is not None else None
    except Exception:
        return None


def _set_max_last_update_id(token: str, last_update_id: int) -> None:
    token_key = (token or "").strip()
    if not token_key:
        return
    state = _load_max_poll_state()
    if token_key not in state:
        state[token_key] = {}
    state[token_key]["last_update_id"] = int(last_update_id)
    _save_max_poll_state(state)


def _load_max_update_dedup_map() -> dict:
    """
    Задача: не переобрабатывать “старые” апдейты после рестарта (особенно когда update_id отсутствует/не работает offset).
    Храним отпечатки апдейтов с TTL.
    """
    ttl_seconds = int(os.getenv("MAX_UPDATE_DEDUP_TTL_SECONDS", "300"))
    now = time.monotonic()
    try:
        if not _MAX_UPDATE_DEDUP_FILE.exists():
            return {}
        with open(_MAX_UPDATE_DEDUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # значения могут быть как float (monotonic), так и string/None — аккуратно фильтруем
        out: dict = {}
        for k, v in data.items():
            try:
                ts = float(v)
            except Exception:
                continue
            if now - ts <= ttl_seconds:
                out[str(k)] = ts
        return out
    except Exception:
        return {}


def _save_max_update_dedup_map(dedup_map: dict) -> None:
    try:
        _MAX_UPDATE_DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_MAX_UPDATE_DEDUP_FILE, "w", encoding="utf-8") as f:
            json.dump(dedup_map, f, ensure_ascii=False, indent=2)
    except Exception:
        return


def _extract_message_mid_from_raw(raw: dict) -> str:
    payload = raw.get("payload") or raw
    if not isinstance(payload, dict):
        return ""
    msg = payload.get("message") or payload.get("edited_message")
    if not isinstance(msg, dict):
        cb = payload.get("callback")
        if isinstance(cb, dict):
            msg = cb.get("message")
    if not isinstance(msg, dict):
        return ""
    mid = msg.get("mid") or msg.get("message_id") or msg.get("id") or ""
    return str(mid).strip()


def _fingerprint_max_update(
    *,
    r_chat,
    r_user,
    sender_uid,
    source,
    msg_mid: str,
    update_id: object = None,
) -> str:
    update_id_part = str(update_id).strip() if update_id is not None else ""
    if isinstance(source, tuple) and len(source) == 2:
        src_kind, src_val = source
        src_val_s = str(src_val).strip()
        src_val_s = src_val_s[:80]
        src_part = f"{src_kind}:{src_val_s}"
    else:
        src_part = str(source).strip()[:80] if source is not None else ""
    return f"{update_id_part}|{r_chat or ''}|{r_user or ''}|{sender_uid or ''}|{src_part}|{msg_mid or ''}"

# Импорт под другим именем: PyPI-пакет MaxBotAPI, модуль maxbotapi
try:
    import maxbotapi
    HAS_MAX_SDK = True
except ImportError:
    maxbotapi = None
    HAS_MAX_SDK = False


def _get_max_token() -> str:
    from config import CONFIG
    return (CONFIG.get("MAX") or {}).get("BOT_TOKEN") or os.getenv("MAX_BOT_TOKEN") or os.getenv("MAX_TOKEN") or ""


def _buttons_to_attachments_max(buttons: list) -> list:
    """
    Формат кнопок как в maxapi: inline_keyboard с type callback или request_contact.
    В главном меню MAX — по одной кнопке в ряд.
    """
    if not buttons:
        return []
    rows = []
    for b in buttons:
        if b.get("type") == "request_contact":
            btn = {"type": "request_contact", "text": b.get("label", "📱 Поделиться контактом")}
        else:
            btn = {"type": "callback", "text": b.get("label", b.get("id", "")), "payload": b.get("id", "")}
        rows.append([btn])
    if not rows:
        return []
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


def _image_attachment_from_token(token: str) -> list:
    """Вложение изображения по токену (после загрузки через MAX uploads API)."""
    if not token:
        return []
    return [{"type": "image", "payload": {"token": token}}]


def _file_attachment_from_token(token: str, filename: str = None) -> list:
    """Вложение файла (документа) по токену для отправки в MAX."""
    if not token:
        return []
    payload = {"token": token}
    if filename:
        payload["filename"] = filename
    return [{"type": "file", "payload": payload}]


async def _upload_image_max(bot, image_path: str) -> str | None:
    """
    Загрузка изображения через MAX API: POST /uploads (get_upload_url) → загрузка файла по URL → token.
    Возвращает token для вложения в сообщение или None при ошибке.
    """
    if not image_path or not os.path.isfile(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        logger.warning("MAX: не удалось прочитать файл %s: %s", image_path, e)
        return None
    file_name = os.path.basename(image_path) or "WMS.jpg"
    file_size = len(raw)
    mime_type = "image/jpeg"
    if image_path.lower().endswith(".png"):
        mime_type = "image/png"
    elif image_path.lower().endswith(".gif"):
        mime_type = "image/gif"
    elif image_path.lower().endswith(".webp"):
        mime_type = "image/webp"
    # MAX Bot API: авторизация через заголовок Authorization, не через query
    token_val = getattr(bot, "token", "")
    auth_headers = {"Authorization": token_val, "Content-Type": "application/json"}
    base = (getattr(bot, "BASE_URL", None) or "https://botapi.max.ru").rstrip("/")
    url = f"{base}/uploads"
    resp = None
    for body, qparams in (
        ({"type": "image", "file_name": file_name, "file_size": file_size, "mime_type": mime_type}, {}),
        ({"type": "image", "file_name": file_name, "file_size": file_size, "mime_type": mime_type}, {"type": "image"}),
        ({"payload": {"type": "image", "file_name": file_name, "file_size": file_size, "mime_type": mime_type}}, {}),
    ):
        try:
            async with bot.session.post(url, headers=auth_headers, params=qparams, json=body) as r:
                if r.status == 200:
                    resp = await r.json()
                    break
                if r.status == 400:
                    err_body = await r.text()
                    logger.warning(
                        "MAX POST /uploads 400 (Authorization header, body keys %s): %s",
                        list(body.keys()),
                        err_body[:500] if err_body else "empty",
                    )
                else:
                    logger.warning("MAX get_upload_url: HTTP %s", r.status)
        except Exception as e:
            logger.debug("MAX get_upload_url (body %s): %s", list(body.keys()), e)
    if resp is None:
        return None
    if not isinstance(resp, dict):
        logger.warning("MAX get_upload_url: неожиданный ответ %s", type(resp))
        return None
    # Ответ может содержать token сразу или url для загрузки (после загрузки — token)
    token = resp.get("token") or resp.get("file_token") or resp.get("photo_id")
    upload_url = resp.get("url") or resp.get("upload_url")
    if upload_url:
        pre_token = token
        import aiohttp
        from aiohttp import FormData

        async def _parse_token(up_resp):
            ct = up_resp.content_type or ""
            body = await up_resp.text()
            # Проверяем заголовки (некоторые API отдают id в заголовке)
            for h in ("X-Photo-Id", "X-Photo-Token", "X-File-Token", "X-Token"):
                v = up_resp.headers.get(h)
                if v and str(v).strip():
                    return str(v).strip()
            if body.strip():
                try:
                    import json
                    data = json.loads(body)
                except Exception:
                    data = {}
            else:
                data = {}
            if isinstance(data, dict):
                t = (
                    data.get("token") or data.get("file_token") or data.get("photo_id")
                    or data.get("file_id") or data.get("id")
                )
                if t is not None:
                    return str(t)
                # MAX API: ответ загрузки фото — {"photos": {"<key>": {"token": "..."}}}
                photos = data.get("photos")
                if isinstance(photos, dict) and photos:
                    first = next(iter(photos.values()), None)
                    if isinstance(first, dict):
                        t = first.get("token") or first.get("file_token") or first.get("photo_id")
                        if t is not None:
                            return str(t)
                payload = data.get("payload") or data.get("result") or {}
                if isinstance(payload, dict):
                    t = payload.get("token") or payload.get("file_token") or payload.get("photo_id")
                    if t is not None:
                        return str(t)
                if data and data.get("error_code") is None:
                    logger.info("MAX upload response 200, body keys=%s, preview=%s", list(data.keys()), body[:200])
            elif body.strip():
                logger.info("MAX upload response 200, non-JSON body preview=%s", body[:200])
            return None

        try:
            async with aiohttp.ClientSession() as session:
                # PUT на upload_url даёт 405 — пробуем только POST. Retry при обрыве (Server disconnected).
                upload_timeout = aiohttp.ClientTimeout(total=30, sock_connect=10)
                for attempt in range(3):
                    try:
                        fd = FormData()
                        # По документации MAX файл передаётся полем "data".
                        fd.add_field("data", raw, filename=file_name, content_type=mime_type)
                        async with session.post(
                            upload_url,
                            data=fd,
                            headers={"Authorization": token_val},
                            timeout=upload_timeout,
                        ) as up_resp:
                            if up_resp.status >= 400:
                                logger.warning(
                                    "MAX upload file POST (multipart:data): HTTP %s %s",
                                    up_resp.status,
                                    (await up_resp.text())[:300],
                                )
                            else:
                                token = await _parse_token(up_resp)
                        if token:
                            break
                    except (aiohttp.ServerDisconnectedError, aiohttp.ClientError, ConnectionError, OSError) as e:
                        logger.warning("MAX upload file POST (multipart:data), попытка %s: %s", attempt + 1, e)
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))
        except Exception as e:
            logger.warning("MAX upload file: %s", e)
            token = None
        if token is None:
            token = pre_token
    if token:
        logger.info("MAX upload file: token получен после upload_url")
        return token
    logger.warning("MAX upload: в ответе нет token (get_upload_url keys: %s)", list(resp.keys()) if isinstance(resp, dict) else "?")
    return None


async def _upload_file_max(bot, file_path: str, mime_type: str = None) -> str | None:
    """
    Загрузка файла (документ, xlsx и т.д.) через MAX API.
    Аналогично _upload_image_max, но type="file". Возвращает token для вложения в сообщение.
    """
    if not file_path or not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        logger.warning("MAX: не удалось прочитать файл %s: %s", file_path, e)
        return None
    file_name = os.path.basename(file_path) or "file"
    file_size = len(raw)
    mime = mime_type or "application/octet-stream"
    if not mime_type and file_path.lower().endswith(".xlsx"):
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    token_val = getattr(bot, "token", "")
    auth_headers = {"Authorization": token_val, "Content-Type": "application/json"}
    base = (getattr(bot, "BASE_URL", None) or "https://botapi.max.ru").rstrip("/")
    url = f"{base}/uploads"
    resp = None
    # MAX API может требовать type в query или в payload
    for body, qparams in (
        ({"type": "file", "file_name": file_name, "file_size": file_size, "mime_type": mime}, {"type": "file"}),
        ({"type": "file", "file_name": file_name, "file_size": file_size, "mime_type": mime}, {}),
        ({"payload": {"type": "file", "file_name": file_name, "file_size": file_size, "mime_type": mime}}, {}),
    ):
        try:
            async with bot.session.post(url, headers=auth_headers, params=qparams, json=body) as r:
                if r.status == 200:
                    resp = await r.json()
                    break
                if r.status == 400:
                    logger.debug("MAX POST /uploads (file) 400, body keys=%s, params=%s: %s", list(body.keys()), qparams, (await r.text())[:200])
        except Exception as e:
            logger.debug("MAX POST /uploads (file): %s", e)
    if not resp or not isinstance(resp, dict):
        return None
    token = resp.get("token") or resp.get("file_token") or resp.get("document_id")
    upload_url = resp.get("url") or resp.get("upload_url")
    logger.info(
        "MAX upload file: init response keys=%s, has_upload_url=%s, has_token=%s",
        list(resp.keys()),
        bool(upload_url),
        bool(token),
    )
    if upload_url:
        pre_token = token
        import aiohttp
        from aiohttp import FormData
        async def _parse_token(up_resp):
            ct = up_resp.content_type or ""
            body_text = await up_resp.text()
            for h in ("X-File-Token", "X-Document-Id", "X-Token", "X-Photo-Token"):
                v = up_resp.headers.get(h)
                if v and str(v).strip():
                    return str(v).strip()
            if body_text.strip():
                try:
                    import json
                    data = json.loads(body_text)
                except Exception:
                    data = {}
            else:
                data = {}
            if isinstance(data, dict):
                t = data.get("token") or data.get("file_token") or data.get("document_id") or data.get("file_id") or data.get("id")
                if t is not None:
                    return str(t)
            return None
        token = None
        try:
            async with aiohttp.ClientSession() as session:
                upload_timeout = aiohttp.ClientTimeout(total=30, sock_connect=10)
                for attempt in range(3):
                    try:
                        fd = FormData()
                        # По документации MAX файл передаётся полем "data".
                        fd.add_field("data", raw, filename=file_name, content_type=mime)
                        async with session.post(
                            upload_url,
                            data=fd,
                            headers={"Authorization": token_val},
                            timeout=upload_timeout,
                        ) as up_resp:
                            if up_resp.status < 400:
                                token = await _parse_token(up_resp)
                            else:
                                logger.warning(
                                    "MAX upload file POST (multipart:data): HTTP %s %s",
                                    up_resp.status,
                                    (await up_resp.text())[:300],
                                )
                        if token:
                            break
                    except (aiohttp.ServerDisconnectedError, aiohttp.ClientError, ConnectionError, OSError) as e:
                        logger.warning("MAX upload file (multipart:data), попытка %s: %s", attempt + 1, e)
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))
        except Exception as e:
            logger.warning("MAX upload file: %s", e)
        if token is None:
            token = pre_token
    if token:
        return token
    logger.warning("MAX upload file: в ответе нет token (keys: %s)", list(resp.keys()) if isinstance(resp, dict) else "?")
    return None


async def _download_attachment_max(bot, att: dict) -> tuple[bytes, str] | None:
    """
    Скачивает вложение по payload.url (прямая ссылка CDN MAX). Авторизация не требуется.
    att должен содержать "url"; "filename" опционально. payload.token для скачивания не используется.
    Возвращает (content, filename) или None. Retry до 2 повторов при сетевой ошибке.
    """
    if not isinstance(att, dict):
        return None
    url = (att.get("url") or "").strip()
    if not url:
        if att.get("token"):
            logger.debug("MAX: вложение без url (есть только token); скачивание по токену не поддерживается")
        return None
    ext_by_type = {"image": ".jpg", "photo": ".jpg", "video": ".mp4", "audio": ".m4a", "file": ""}
    default_ext = ext_by_type.get(att.get("type") or "", ".bin")
    for attempt in range(3):
        try:
            async with bot.session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("MAX: GET вложение по url вернул %s (попытка %s)", resp.status, attempt + 1)
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                content = await resp.read()
                if not content:
                    return None
                name = (resp.headers.get("Content-Disposition") or "").split("filename=")[-1].strip(' "\n')
                if not name or "filename=" in name:
                    name = (att.get("filename") or "").strip() or f"attachment_{(url.split('/')[-1].split('?')[0][:16] or 'file')}{default_ext}"
                logger.info("MAX: вложение скачано по payload.url (размер %s)", len(content))
                return (content, name)
        except Exception as e:
            logger.warning("MAX: ошибка скачивания по url (попытка %s): %s", attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def _post_messages_and_log_error(
    bot,
    json_body: dict,
    query_params: dict = None,
    return_error_code: bool = False,
) -> dict | tuple[dict | None, str | None] | None:
    """POST /messages. query_params добавляются к access_token. При 400 логируем тело ответа."""
    url = f"{bot.BASE_URL}/messages"
    params = {"access_token": bot.token}
    headers = {"Authorization": bot.token, "Content-Type": "application/json"}
    if query_params:
        params.update(query_params)
    try:
        async with bot.session.post(url, params=params, headers=headers, json=json_body) as resp:
            body = await resp.text()
            if resp.status >= 400:
                err_code = None
                try:
                    import json as _json
                    parsed = _json.loads(body) if body.strip() else {}
                    if isinstance(parsed, dict):
                        err_code = (parsed.get("code") or "").strip() or None
                except Exception:
                    err_code = None
                logger.warning("MAX API %s (params=%s): %s", resp.status, params, body[:500])
                if return_error_code:
                    return None, err_code
                return None
            import json
            data = json.loads(body) if body.strip() else {}
            if return_error_code:
                return data, None
            return data
    except Exception as e:
        logger.debug("MAX _post_messages: %s", e)
        if return_error_code:
            return None, None
        return None


async def _send_message_max(
    bot,
    recipient_chat_id: str | None,
    recipient_user_id: int | None,
    text: str,
    attachments_max: list = None,
    parse_mode: str = None,
    log_source: str | None = None,
) -> str | None:
    """
    Отправка в MAX. Возвращает message_id отправленного сообщения или None.
    Перед отправкой в личке вызывающий должен удалить предыдущее сообщение бота (если есть).
    """
    body = {"text": text}
    if attachments_max:
        body["attachments"] = attachments_max
    if parse_mode:
        body["format"] = parse_mode  # maxapi использует "format"
    max_attachment_send_retries = int(os.getenv("MAX_ATTACHMENT_SEND_RETRIES", "30"))
    max_attachment_send_delay = float(os.getenv("MAX_ATTACHMENT_SEND_RETRY_DELAY_SECONDS", "1.0"))

    # 1) chat_id в query (часто работает; user_id даёт 403 "Invalid chatId: 0" у части ботов)
    if recipient_chat_id:
        attempts = 1 + (max_attachment_send_retries if attachments_max else 0)
        result = None
        last_err_code = None
        for attempt in range(attempts):
            resp = await _post_messages_and_log_error(
                bot,
                body,
                query_params={"chat_id": recipient_chat_id},
                return_error_code=True,
            )
            result, err_code = resp if isinstance(resp, tuple) else (resp, None)
            last_err_code = err_code
            if result is not None:
                break
            if (
                attachments_max
                and err_code == "attachment.not.ready"
                and attempt < attempts - 1
            ):
                await asyncio.sleep(max_attachment_send_delay)
                continue
            break
        if result is not None:
            mid = _message_id_from_send_response(result)
            if mid is None and isinstance(result, dict):
                logger.info("MAX: сообщение отправлено, но message_id не в ответе (ключи: %s)", list(result.keys()))
            preview = (text or "").replace("\n", " ").strip()
            if len(preview) > 120:
                preview = preview[:120] + "..."
            logger.info(
                "MAX: отправлено сообщение (chat_id=%s, source=%s, text=%r)",
                recipient_chat_id,
                log_source or "-",
                preview,
            )
            return mid
        # Если chat_id задан и вложение ещё не готово — не дублируем ту же ошибку fallback-ом в user_id.
        if attachments_max and last_err_code == "attachment.not.ready":
            return None

    # 2) user_id в query (личный чат)
    if recipient_user_id is not None:
        attempts = 1 + (max_attachment_send_retries if attachments_max else 0)
        result = None
        for attempt in range(attempts):
            resp = await _post_messages_and_log_error(
                bot,
                body,
                query_params={"user_id": recipient_user_id},
                return_error_code=True,
            )
            result, err_code = resp if isinstance(resp, tuple) else (resp, None)
            if result is not None:
                break
            if (
                attachments_max
                and err_code == "attachment.not.ready"
                and attempt < attempts - 1
            ):
                await asyncio.sleep(max_attachment_send_delay)
                continue
            break
        if result is not None:
            mid = _message_id_from_send_response(result)
            if mid is None and isinstance(result, dict):
                logger.info("MAX: сообщение отправлено, но message_id не в ответе (ключи: %s)", list(result.keys()))
            preview = (text or "").replace("\n", " ").strip()
            if len(preview) > 120:
                preview = preview[:120] + "..."
            logger.info(
                "MAX: отправлено сообщение (user_id=%s, source=%s, text=%r)",
                recipient_user_id,
                log_source or "-",
                preview,
            )
            return mid

    # 3) Fallback: MaxBotAPI (chat_id в body, без кнопок) — возвращает Message с message_id
    cid = recipient_chat_id or (str(recipient_user_id) if recipient_user_id is not None else None)
    if cid:
        try:
            msg_body = maxbotapi.NewMessageBody(chat_id=cid, text=text, inline_keyboard=None)
            sent = await bot.send_message(msg_body)
            preview = (text or "").replace("\n", " ").strip()
            if len(preview) > 120:
                preview = preview[:120] + "..."
            logger.info(
                "MAX: отправлено сообщение (NewMessageBody, source=%s, text=%r)",
                log_source or "-",
                preview,
            )
            return getattr(sent, "message_id", None) if sent else None
        except Exception as e:
            logger.debug("MAX send_message (NewMessageBody): %s", e)

    logger.warning("MAX send_message не удался. Проверьте лог выше (MAX API 400/403: ...).")
    return None


async def _get_updates_raw(bot, timeout: int = 25, limit: int = 10, offset: int | None = None) -> list:
    """
    Получить сырой список апдейтов (dict), без парсинга через Update.from_dict.
    API MAX может возвращать структуру без update_id — библиотека тогда падает с KeyError.
    """
    params = {"timeout": timeout, "limit": limit}
    # В MAX Bot API (как в Telegram) offset позволяет не получать уже обработанные апдейты повторно.
    if offset is not None:
        params["offset"] = int(offset)
    data = await bot._make_request("GET", "/updates", params=params)
    return data.get("updates") or []


def _extract_file_attachments_from_max_message(msg: dict) -> list[dict]:
    """
    Из входящего сообщения MAX (body.attachments) извлекает вложения для WMS.
    В MAX Bot API payload.url — прямая ссылка на CDN для скачивания; payload.token — только для пересылки.
    Приоритет: payload.url (для скачивания), иначе payload.token.
    Элемент: {"type": "...", "url": str} или {"type": "...", "token": str}, опционально "filename".
    """
    if not isinstance(msg, dict):
        return []
    out = []
    body = msg.get("body") or msg
    if isinstance(body, dict):
        attachments = body.get("attachments") or []
        for att in attachments:
            if not isinstance(att, dict):
                continue
            atype = (att.get("type") or "").strip().lower()
            if atype in ("contact",):
                continue
            if atype in ("image", "photo", "file", "document", "video", "audio"):
                payload = att.get("payload") or {}
                if isinstance(payload, str):
                    token = payload.strip()
                    url = ""
                    filename = ""
                else:
                    url = (payload.get("url") or "").strip()
                    token = (
                        payload.get("token") or payload.get("id")
                        or payload.get("file_id") or payload.get("photo_id") or payload.get("document_id")
                        or ""
                    )
                    if isinstance(token, str):
                        token = token.strip()
                    else:
                        token = str(token).strip() if token else ""
                    filename = (payload.get("filename") or "").strip()
                kind = "file" if atype in ("file", "document") else atype
                if url:
                    item = {"type": kind, "url": url}
                    if filename:
                        item["filename"] = filename
                    out.append(item)
                elif token:
                    out.append({"type": kind, "token": token})
    # Альтернативно: на верхнем уровне message — photo_id, document_id, video_token (только token)
    for key, kind in (("photo_id", "image"), ("document_id", "file"), ("video_token", "video")):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            out.append({"type": kind, "token": val.strip()})
    for key, kind in (("photo", "image"), ("image", "image"), ("document", "file"), ("video", "video")):
        val = msg.get(key)
        if isinstance(val, dict):
            u = (val.get("url") or "").strip()
            t = (val.get("token") or val.get("id") or val.get("file_id") or "")
            if not isinstance(t, str):
                t = str(t).strip() if t else ""
            else:
                t = t.strip()
            if u:
                item = {"type": kind, "url": u}
                if val.get("filename"):
                    item["filename"] = (val.get("filename") or "").strip()
                out.append(item)
            elif t:
                out.append({"type": kind, "token": t})
        elif isinstance(val, str) and val.strip():
            out.append({"type": kind, "token": val.strip()})
    if out:
        logger.debug("MAX: из сообщения извлечено вложений: %s", len(out))
    return out


def _extract_phone_from_contact_attachments(msg: dict) -> str | None:
    """
    Из вложений сообщения (body.attachments) извлекает номер телефона из контакта (type=contact).
    vcf_info — строка vCard; номер в поле TEL: или TEL;TYPE=...:
    """
    if not isinstance(msg, dict):
        return None
    body = msg.get("body") or msg
    attachments = body.get("attachments") if isinstance(body, dict) else None
    if not isinstance(attachments, list):
        return None
    for att in attachments:
        if not isinstance(att, dict):
            continue
        if att.get("type") != "contact":
            continue
        payload = att.get("payload") or {}
        vcf = (payload.get("vcf_info") or "").strip()
        if vcf:
            # vCard: TEL:+79991234567 или TEL;TYPE=CELL:+79991234567
            m = re.search(r"TEL[^:]*:([^\s\r\n]+)", vcf, re.IGNORECASE)
            if m:
                return re.sub(r"\D", "", m.group(1).strip()) or None
        # max_info может содержать данные пользователя MAX (телефон не всегда есть)
        max_info = payload.get("max_info") or {}
        if isinstance(max_info, dict):
            phone = (max_info.get("phone") or max_info.get("phone_number") or "").strip()
            if phone:
                return re.sub(r"\D", "", phone) or None
    return None


def _source_to_log_tag(source) -> str:
    """Короткая метка источника для логов отправки ответа."""
    if source == "start":
        return "source:start"
    if isinstance(source, tuple) and len(source) >= 2:
        kind = source[0]
        payload = source[1]
        if kind == "callback":
            p = (str(payload) if payload is not None else "").strip()
            if len(p) > 48:
                p = p[:48] + "..."
            return f"source:callback:{p or '-'}"
        if kind == "message":
            txt = (str(payload) if payload is not None else "").strip()
            if not txt:
                return "source:message:attachment_only"
            if len(txt) > 32:
                txt = txt[:32] + "..."
            return f"source:message:{txt}"
        if kind == "contact":
            return "source:contact"
        return f"source:{kind}"
    return "source:unknown"


def _get_message_text(msg: dict) -> str:
    """Текст сообщения: API может отдавать text/body/content (строка или dict с полем value/text)."""
    if not isinstance(msg, dict):
        return ""
    raw = msg.get("text") or msg.get("body") or msg.get("content")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return (raw.get("text") or raw.get("value") or raw.get("body") or "").strip()
    return str(raw).strip()


def _get_chat_id(msg: dict):
    """chat_id из message (для обратной совместимости)."""
    r = _get_recipient_ids(msg)
    return r[0] or r[1]  # chat_id или user_id


def _get_recipient_ids(msg: dict) -> tuple:
    """
    (chat_id, user_id) из message.recipient.
    В личном чате может быть только user_id; в группе — chat_id.
    Как в maxapi (platform-api): send_message принимает chat_id ИЛИ user_id в query.
    """
    if not isinstance(msg, dict):
        return None, None
    recipient = msg.get("recipient")
    if not isinstance(recipient, dict):
        cid = msg.get("chat_id")
        uid = msg.get("sender") or msg.get("from")
        uid = (uid.get("user_id") or uid.get("id")) if isinstance(uid, dict) else None
        return str(cid) if cid is not None else None, int(uid) if uid is not None else None
    cid = recipient.get("chat_id") or recipient.get("id")
    uid = recipient.get("user_id")
    if cid is not None:
        cid = str(cid)
    if uid is not None:
        uid = int(uid)
    # Если в recipient только id — может быть и chat_id, и user_id в зависимости от типа чата
    if cid is None and uid is None:
        rid = recipient.get("id")
        if rid is not None:
            uid = int(rid)
    return cid, uid


def _get_user_id(sender):
    """user_id из sender/from/user (dict или объект с атрибутами)."""
    if sender is None:
        return None
    if isinstance(sender, dict):
        uid = sender.get("user_id") or sender.get("id")
        return int(uid) if uid is not None else None
    uid = getattr(sender, "user_id", None) or getattr(sender, "id", None)
    return int(uid) if uid is not None else None


def _parse_update(raw: dict) -> tuple:
    """
    Из сырого апдейта извлекает (recipient_chat_id, recipient_user_id, sender_user_id, response_source).
    Поддерживает: 1) сообщение с /start, 2) callback (нажатие кнопки) — ключи callback + message.
    """
    payload = raw.get("payload") or raw

    # Нажатие кнопки: ключи callback, message (как в maxapi MessageCallback)
    if "callback" in payload or "callback" in raw:
        cb = payload.get("callback") or raw.get("callback")
        msg = payload.get("message") or raw.get("message")
        if isinstance(cb, dict) and isinstance(msg, dict):
            r_chat, r_user = _get_recipient_ids(msg)
            # user_id того, кто нажал — из callback.user (maxapi) или message.sender
            cb_user = cb.get("user") or cb.get("from") or {}
            sender_uid = _get_user_id(cb_user)
            if sender_uid is None:
                sender_uid = _get_user_id(msg.get("sender") or msg.get("from") or {})
            callback_payload = cb.get("payload") or cb.get("data") or cb.get("callback_id") or cb.get("id")
            if isinstance(callback_payload, str) and (r_chat or r_user) and sender_uid is not None:
                return r_chat, r_user, sender_uid, ("callback", callback_payload)
        elif isinstance(cb, str) and isinstance(msg, dict):
            # callback как строка (payload)
            r_chat, r_user = _get_recipient_ids(msg)
            sender = msg.get("sender") or msg.get("from") or {}
            sender_uid = _get_user_id(sender)
            if (r_chat or r_user) and sender_uid is not None:
                return r_chat, r_user, sender_uid, ("callback", cb)
    # Обычное сообщение (message / edited_message)
    for msg_key in ("message", "edited_message"):
        msg = payload.get(msg_key) if payload is not raw else raw.get(msg_key)
        if msg is None or not isinstance(msg, dict):
            continue
        r_chat, r_user = _get_recipient_ids(msg)
        sender = msg.get("sender") or msg.get("from") or msg.get("user") or {}
        sender_uid = _get_user_id(sender)
        text = _get_message_text(msg)
        if sender_uid is None or not (r_chat or r_user):
            return None, None, None, None
        if text.startswith("/start"):
            return r_chat, r_user, sender_uid, "start"
        # Вложение «контакт» (поделиться контактом) — приоритет над текстом
        phone = _extract_phone_from_contact_attachments(msg)
        if phone:
            return r_chat, r_user, sender_uid, ("contact", phone)
        # Текст (для ручного ввода номера или других сценариев)
        if text.strip():
            return r_chat, r_user, sender_uid, ("message", text.strip())
        # Сообщение без текста, но с вложениями (фото/документ) — обрабатываем как message с пустым текстом
        if _extract_file_attachments_from_max_message(msg):
            return r_chat, r_user, sender_uid, ("message", "")
        return None, None, None, None

    r_chat, r_user = _get_recipient_ids(payload)
    sender = payload.get("sender") or payload.get("from") or payload.get("user") or {}
    sender_uid = _get_user_id(sender)
    text = _get_message_text(payload)
    if text.startswith("/start") and sender_uid is not None and (r_chat or r_user):
        return r_chat, r_user, sender_uid, "start"

    if "callback_query" in payload:
        cq = payload["callback_query"]
        if isinstance(cq, dict):
            msg = cq.get("message")
            r_chat, r_user = _get_recipient_ids(msg) if isinstance(msg, dict) else (None, None)
            from_user = cq.get("from") or cq.get("user") or {}
            sender_uid = _get_user_id(from_user) if isinstance(from_user, dict) else None
            callback_id = cq.get("data") or cq.get("id")
            if (r_chat or r_user) and sender_uid is not None and callback_id:
                return r_chat, r_user, sender_uid, ("callback", str(callback_id))
    return None, None, None, None


async def _handle_open_issue_max(user_id: int, callback_id: str) -> dict | None:
    """
    Просмотр заявки в MAX: проверка владения, загрузка summary/status/комментариев из Jira.
    callback_id вида "open_issue:KEY". Возвращает dict с text, parse_mode, buttons или None.
    """
    from user_storage import is_user_registered
    from core.support.api import support_api
    from core.jira_aa import get_issue_info, get_issue_comments
    CHANNEL_ID = "max"
    back_btn = [{"id": "back_to_main", "label": "🔙 В главное меню"}]
    if not is_user_registered(user_id, CHANNEL_ID):
        return {"text": "Сначала пройдите регистрацию или привяжите аккаунт.", "parse_mode": "HTML", "buttons": back_btn}
    issue_key = (callback_id or "").split(":", 1)[-1].strip()
    if not issue_key or not support_api.user_owns_issue(CHANNEL_ID, user_id, issue_key):
        return {"text": "Заявка не найдена.", "parse_mode": "HTML", "buttons": back_btn}
    info = await get_issue_info(issue_key)
    comments = await get_issue_comments(issue_key)
    summary = (info or {}).get("summary") or "—"
    status = jira_status_display_ru((info or {}).get("status"))
    def _fmt(comments_list, max_len=200):
        out = []
        for c in reversed((comments_list or [])[-10:]):
            author = (c.get("author") or {}).get("displayName", "—")
            body = (c.get("body") or "").strip()
            if len(body) > max_len:
                body = body[:max_len] + "..."
            out.append(f"👤 {author}: {body}")
        return out
    lines = _fmt(comments)
    jira_url = support_api.get_jira_customer_request_url(issue_key)
    jira_line = f'\n🔗 <a href="{jira_url}">Открыть заявку в Jira</a>' if jira_url else ""
    text = (
        f"💬 <b>Заявка {issue_key}</b>\n"
        f"Тема: {summary}\nСтатус: {status}{jira_line}\n\n"
        + ("\n\n".join(lines) if lines else "Пока нет комментариев.")
    )
    buttons = [
        {"id": f"add_comment:{issue_key}", "label": "✏️ Добавить комментарий"},
        {"id": "my_tickets", "label": "🔙 К списку заявок"},
        {"id": "back_to_main", "label": "🔙 В главное меню"},
    ]
    return {"text": text, "parse_mode": "HTML", "buttons": buttons}


async def _handle_stc_open_issue_max(user_id: int, issue_key: str) -> dict:
    from core.stc_tasks import can_stc_user_access_issue, get_stc_assignee_tasks
    from core.jira_aa import get_issue_admin_details, get_issue_comments
    from core.support.api import support_api
    from config import is_stc_sa

    back_btn = [{"id": "back_to_main", "label": "🔙 В главное меню"}]
    if not is_stc_sa("max", user_id):
        return {"text": "Нет доступа.", "parse_mode": "HTML", "buttons": back_btn}
    if not await can_stc_user_access_issue("max", user_id, issue_key):
        return {
            "text": "❌ Заявка недоступна (возможно, вы больше не исполнитель).",
            "parse_mode": "HTML",
            "buttons": [
                {"id": "sa_stc_my_tasks", "label": "🔙 К моим задачам"},
                {"id": "back_to_main", "label": "🔙 В главное меню"},
            ],
        }
    info = await get_issue_admin_details(issue_key) or {}
    comments = await get_issue_comments(issue_key)
    creator = "—"
    req_label = "—"
    for t in await get_stc_assignee_tasks("max", user_id):
        if (t.get("issue_key") or "").upper() == issue_key.upper():
            creator = t.get("creator") or "—"
            req_label = t.get("request_type_label") or "—"
            break
    summary = info.get("summary") or "—"
    status = jira_status_display_ru(info.get("status"))
    desc = info.get("description") or "—"
    reporter = info.get("reporter_display") or "—"
    assignee = info.get("assignee_display") or "—"
    lines = []
    for c in reversed((comments or [])[-5:]):
        author = (c.get("author") or {}).get("displayName", "—")
        body = (c.get("body") or "").strip()
        if len(body) > 180:
            body = body[:180] + "..."
        lines.append(f"👤 {author}: {body}")
    jira_url = support_api.get_jira_browse_url(issue_key)
    jira_line = f'\n🔗 <a href="{jira_url}">Открыть в JIRA</a>' if jira_url else ""
    text = (
        f"🛠️ <b>Задача {issue_key}</b>\n\n"
        f"Тип: {req_label}\n"
        f"Автор: {creator}\n"
        f"Reporter: {reporter}\n"
        f"Assignee: {assignee}\n"
        f"Статус: {status}\n"
        f"Тема: {summary}{jira_line}\n\n"
        f"Описание:\n{desc}\n\n"
        + ("Последние комментарии:\n" + "\n\n".join(lines) if lines else "Комментариев пока нет.")
    )
    buttons = [
        {"id": f"stc_open_jira:{issue_key}", "label": "🔗 Открыть в JIRA"},
        {"id": f"stc_set_status:{issue_key}", "label": "🔄 Установить статус"},
        {"id": f"add_comment:{issue_key}", "label": "✏️ Добавить комментарий"},
        {"id": "sa_stc_my_tasks", "label": "🔙 К моим задачам"},
        {"id": "back_to_main", "label": "🔙 В главное меню"},
    ]
    return {"text": text, "parse_mode": "HTML", "buttons": buttons}


async def _handle_stc_callback_max(user_id: int, callback_id: str) -> dict:
    from config import is_stc_sa
    from core.stc_tasks import get_stc_assignee_tasks, can_stc_user_access_issue
    from core.jira_aa import get_issue_transitions, transition_issue
    from core.support.api import support_api

    back_btn = [{"id": "back_to_main", "label": "🔙 В главное меню"}]
    if not is_stc_sa("max", user_id):
        return {"text": "Нет доступа.", "parse_mode": "HTML", "buttons": back_btn}
    if callback_id == "sa_stc_menu":
        return {
            "text": "🛠️ <b>СА СТЦ</b>\n\nВыберите действие:",
            "parse_mode": "HTML",
            "buttons": [
                {"id": "sa_stc_my_tasks", "label": "📋 Мои задачи"},
                {"id": "back_to_main", "label": "🔙 В главное меню"},
            ],
        }
    if callback_id == "sa_stc_my_tasks":
        tasks = await get_stc_assignee_tasks("max", user_id)
        if not tasks:
            return {
                "text": "📋 <b>Мои задачи (СА СТЦ)</b>\n\nУ вас нет заявок, где вы текущий исполнитель.",
                "parse_mode": "HTML",
                "buttons": [
                    {"id": "sa_stc_menu", "label": "⬅️ Назад"},
                    {"id": "back_to_main", "label": "🔙 В главное меню"},
                ],
            }
        lines = [f"• {t['issue_key']} — {(t.get('request_type_label') or '—')}" for t in tasks]
        buttons = [{"id": f"stc_open_issue:{t['issue_key']}", "label": t["issue_key"]} for t in tasks]
        buttons += [
            {"id": "sa_stc_menu", "label": "⬅️ Назад"},
            {"id": "back_to_main", "label": "🔙 В главное меню"},
        ]
        return {"text": "📋 <b>Мои задачи (СА СТЦ)</b>\n\n" + "\n".join(lines), "parse_mode": "HTML", "buttons": buttons}
    if callback_id.startswith("stc_open_issue:"):
        issue_key = callback_id.split(":", 1)[-1].strip()
        return await _handle_stc_open_issue_max(user_id, issue_key)
    if callback_id.startswith("stc_open_jira:"):
        issue_key = callback_id.split(":", 1)[-1].strip()
        url = support_api.get_jira_browse_url(issue_key)
        return {
            "text": f'🔗 <a href="{url}">Открыть {issue_key} в Jira</a>' if url else "Ссылка недоступна.",
            "parse_mode": "HTML",
            "buttons": [
                {"id": f"stc_open_issue:{issue_key}", "label": "⬅️ Назад к задаче"},
                {"id": "back_to_main", "label": "🔙 В главное меню"},
            ],
        }
    if callback_id.startswith("stc_set_status:"):
        issue_key = callback_id.split(":", 1)[-1].strip()
        if not await can_stc_user_access_issue("max", user_id, issue_key):
            return {"text": "Заявка недоступна.", "parse_mode": "HTML", "buttons": back_btn}
        transitions = await get_issue_transitions(issue_key)
        if not transitions:
            return {
                "text": "Нет доступных переходов.",
                "parse_mode": "HTML",
                "buttons": [{"id": f"stc_open_issue:{issue_key}", "label": "⬅️ Назад к задаче"}],
            }
        buttons = []
        def _needs_timespent(t: dict) -> bool:
            name = ((t.get("name") or "") + " " + (t.get("to_name") or "")).strip().lower()
            markers = ("resolve", "resolved", "done", "close", "закры", "выполн", "реш")
            return any(m in name for m in markers)
        def _pick_transition(kind: str) -> dict | None:
            def norm(s: str) -> str:
                return (s or "").strip().lower()
            for t in transitions:
                to_name = norm(t.get("to_name") or "")
                name = norm(t.get("name") or "")
                blob = f"{to_name} {name}".strip()
                if kind == "in_progress":
                    if ("in progress" in blob) or ("в работе" in blob) or ("работ" in to_name and "в" in to_name):
                        return t
                if kind == "pause":
                    if any(x in blob for x in ("pause", "paused", "on hold", "hold", "пауза", "приост", "ожид")):
                        return t
                if kind == "done":
                    if any(x in blob for x in ("resolved", "resolve", "done", "close", "closed", "готов", "выполн", "закры", "решен", "решён")):
                        return t
            return None

        ordered = [
            ("in_progress", "🟢 В работе"),
            ("pause", "⏸ Пауза"),
            ("done", "✅ Готово"),
        ]
        for kind, label in ordered:
            t = _pick_transition(kind)
            if not t:
                continue
            tid = (t.get("id") or "").strip()
            if not tid:
                continue
            cb = f"stc_ask_timespent:{issue_key}:{tid}" if _needs_timespent(t) else f"stc_apply_status:{issue_key}:{tid}"
            buttons.append({"id": cb, "label": label})
        buttons.append({"id": f"stc_open_issue:{issue_key}", "label": "⬅️ Назад"})
        return {
            "text": f"🔄 <b>Установить статус</b>\n\nЗаявка: {issue_key}\nВыберите новый статус:",
            "parse_mode": "HTML",
            "buttons": buttons,
        }
    if callback_id.startswith("stc_ask_timespent:"):
        parts = callback_id.split(":")
        if len(parts) < 3:
            return {"text": "Некорректный переход.", "parse_mode": "HTML", "buttons": back_btn}
        issue_key = parts[1].strip()
        transition_id = parts[2].strip()
        return {
            "text": f"⏱ <b>Time Spent</b>\n\nЗаявка: {issue_key}\nВыберите затраченное время:",
            "parse_mode": "HTML",
            "buttons": [
                {"id": f"stc_apply_status_ts:{issue_key}:{transition_id}:5m", "label": "5m"},
                {"id": f"stc_apply_status_ts:{issue_key}:{transition_id}:15m", "label": "15m"},
                {"id": f"stc_apply_status_ts:{issue_key}:{transition_id}:30m", "label": "30m"},
                {"id": f"stc_apply_status_ts:{issue_key}:{transition_id}:1h", "label": "1h"},
                {"id": f"stc_set_status:{issue_key}", "label": "⬅️ Назад"},
            ],
        }
    if callback_id.startswith("stc_apply_status_ts:"):
        parts = callback_id.split(":")
        if len(parts) < 4:
            return {"text": "Некорректный переход.", "parse_mode": "HTML", "buttons": back_btn}
        issue_key = parts[1].strip()
        transition_id = parts[2].strip()
        time_spent = parts[3].strip()
        if not await can_stc_user_access_issue("max", user_id, issue_key):
            return {"text": "Заявка недоступна.", "parse_mode": "HTML", "buttons": back_btn}
        from user_storage import get_user_profile
        profile = get_user_profile(user_id, "max") or {}
        preserve_assignee = (profile.get("jira_username") or "").strip() or None
        ok, msg = await transition_issue(
            issue_key,
            transition_id,
            preserve_assignee_username=preserve_assignee,
            default_time_spent=time_spent or "5m",
        )
        if not ok:
            return {
                "text": f"❌ {msg}",
                "parse_mode": "HTML",
                "buttons": [{"id": f"stc_set_status:{issue_key}", "label": "⬅️ К статусам"}],
            }
        return await _handle_stc_open_issue_max(user_id, issue_key)
    if callback_id.startswith("stc_apply_status:"):
        parts = callback_id.split(":")
        if len(parts) < 3:
            return {"text": "Некорректный переход.", "parse_mode": "HTML", "buttons": back_btn}
        issue_key = parts[1].strip()
        transition_id = parts[2].strip()
        if not await can_stc_user_access_issue("max", user_id, issue_key):
            return {"text": "Заявка недоступна.", "parse_mode": "HTML", "buttons": back_btn}
        from user_storage import get_user_profile
        profile = get_user_profile(user_id, "max") or {}
        preserve_assignee = (profile.get("jira_username") or "").strip() or None
        ok, msg = await transition_issue(
            issue_key,
            transition_id,
            preserve_assignee_username=preserve_assignee,
        )
        if not ok:
            return {
                "text": f"❌ {msg}",
                "parse_mode": "HTML",
                "buttons": [{"id": f"stc_set_status:{issue_key}", "label": "⬅️ К статусам"}],
            }
        return await _handle_stc_open_issue_max(user_id, issue_key)
    return {"text": "Раздел недоступен.", "parse_mode": "HTML", "buttons": back_btn}


# Текущий экземпляр бота MAX (для доставки уведомлений из Core)
_current_max_bot = None


# Throttle исходящих уведомлений из Core (защита от "волн" сообщений).
# Ограничиваем частоту отправки одному user_id в MAX, чтобы исключить бурст при сбитых baseline/count.
_last_core_notification_ts_by_user: dict[int, float] = {}


async def send_notification_to_max_user(user_id: int, text: str, reply_markup=None) -> bool:
    """
    Отправка уведомления пользователю MAX (из Core delivery).
    reply_markup: список рядов кнопок, каждый ряд — список dict с "text", "callback_data".
    """
    global _current_max_bot
    if _current_max_bot is None:
        logger.debug("MAX: уведомление не отправлено (бот не запущен), user_id=%s", user_id)
        return False

    min_interval_seconds = float(os.getenv("MAX_CORE_NOTIFY_MIN_INTERVAL_SECONDS", "4.0"))
    now = time.monotonic()
    last_ts = _last_core_notification_ts_by_user.get(int(user_id))
    if last_ts is not None and min_interval_seconds > 0:
        if now - last_ts < min_interval_seconds:
            # Не спамим: если baseline/счётчики "разъехались", будет идти бурст.
            logger.debug(
                "MAX: уведомление подавлено throttling (user_id=%s, dt=%.2fs < %.2fs).",
                user_id,
                now - last_ts,
                min_interval_seconds,
            )
            return False
    _last_core_notification_ts_by_user[int(user_id)] = now

    buttons = []
    if reply_markup:
        for row in reply_markup or []:
            for b in row if isinstance(row, list) else []:
                if isinstance(b, dict) and b.get("callback_data"):
                    buttons.append({"id": b["callback_data"], "label": b.get("text", b["callback_data"])})
    attachments = _buttons_to_attachments_max(buttons)
    mid = await _send_message_max(
        _current_max_bot,
        None,
        user_id,
        text,
        attachments,
        "HTML",
        log_source="source:notify",
    )
    return mid is not None


# Ожидание ввода номера телефона для привязки (max_user_id -> True)
_pending_bind_max: dict[int, bool] = {}

# Ожидание текста комментария: user_id -> issue_key (кнопка «Написать комментарий»)
_pending_comment_max: dict[int, str] = {}
# Ожидание ввода пароля: user_id -> True (кнопка «Смена пароля»)
_pending_password_max: dict[int, bool] = {}
# Ожидание ввода логина/ID для удаления пользователя (только для админов MAX)
_pending_admin_delete_max: dict[int, bool] = {}
# Ожидание ввода части ФИО для поиска при удалении пользователя
_pending_admin_delete_search_max: dict[int, bool] = {}

# Ожидание подтверждения номера телефона (контакт) для мигрированных пользователей
_pending_verify_phone_max: dict[int, bool] = {}

# Регистрация из MAX: user_id -> {"step": "email" | "contact", "email": "..."}
_pending_registration_max: dict[int, dict] = {}

# user_id (MAX) -> {chat_id, user_id, mid} последнего сообщения бота (удаляем перед новым ответом, как в on_dute)
_last_bot_message_max: dict[int, dict] = {}

# Антиспам MAX: последнее время события по user_id
_throttle_max: dict[int, float] = {}


def _is_max_rate_limited(user_id: int, cooldown: float) -> bool:
    """True, если запрос от user_id пришёл раньше cooldown секунд."""
    if cooldown <= 0:
        return False
    now = time.monotonic()
    last = _throttle_max.get(user_id, 0.0)
    if now - last < cooldown:
        return True
    _throttle_max[user_id] = now
    return False


def _message_id_from_send_response(result: dict | None) -> str | None:
    """Из ответа POST /messages извлекает message_id (MAX API: result['message'] или message.body.mid)."""
    if not result or not isinstance(result, dict):
        return None
    for key in ("message_id", "mid", "id"):
        mid = result.get(key)
        if mid is not None:
            return str(mid)
    # Ответ MAX: { "message": { "body": { "mid": "..." }, ... } } или { "message": { "mid": "..." } }
    msg = result.get("message")
    if isinstance(msg, dict):
        for k in ("mid", "message_id", "id"):
            mid = msg.get(k)
            if mid is not None:
                return str(mid)
        body = msg.get("body")
        if isinstance(body, dict):
            mid = body.get("mid") or body.get("message_id") or body.get("id")
            if mid is not None:
                return str(mid)
    for key in ("body", "data", "result"):
        node = result.get(key)
        if isinstance(node, dict):
            for k in ("mid", "message_id", "id"):
                mid = node.get(k)
                if mid is not None:
                    return str(mid)
    body = result.get("body")
    if isinstance(body, dict):
        nested = body.get("message") or body.get("body")
        if isinstance(nested, dict):
            mid = nested.get("mid") or nested.get("message_id")
            if mid is not None:
                return str(mid)
    return None


async def _delete_message_max(
    bot,
    chat_id: str | None,
    user_id: int | None,
    message_id: str,
) -> bool:
    """
    Удаление сообщения в MAX (DELETE /messages или DELETE /messages/{id}).
    Пробуем варианты: query-параметры и path с message_id.
    """
    if not message_id:
        return False
    base = (getattr(bot, "BASE_URL", None) or "").rstrip("/")
    if not base or base == "":
        logger.debug("MAX delete_message: BASE_URL не задан")
        return False
    auth_headers = {"Authorization": bot.token, "Content-Type": "application/json"}

    # Вариант 1: DELETE /messages?chat_id=...&mid=... (или user_id)
    url_params = f"{base}/messages"
    for param_name in ("mid", "message_id"):
        params = {param_name: message_id}
        if chat_id:
            params["chat_id"] = chat_id
        elif user_id is not None:
            params["user_id"] = user_id
        else:
            continue
        try:
            async with bot.session.delete(url_params, params=params, headers=auth_headers) as resp:
                if resp.status in (200, 204):
                    logger.debug("MAX: предыдущее сообщение удалено (query)")
                    return True
                if resp.status not in (400, 404, 422):
                    logger.info("MAX delete_message ?%s: HTTP %s", param_name, resp.status)
        except Exception as e:
            logger.debug("MAX delete_message (?%s): %s", param_name, e)

    # Вариант 2: DELETE /messages/{message_id}?chat_id=... или ?user_id=...
    url_path = f"{base}/messages/{message_id}"
    params = {}
    if chat_id:
        params["chat_id"] = chat_id
    elif user_id is not None:
        params["user_id"] = user_id
    if params:
        try:
            async with bot.session.delete(url_path, params=params, headers=auth_headers) as resp:
                if resp.status in (200, 204):
                    logger.debug("MAX: предыдущее сообщение удалено (path)")
                    return True
                logger.debug("MAX delete_message path: HTTP %s", resp.status)
        except Exception as e:
            logger.debug("MAX delete_message path: %s", e)
    logger.info("MAX: не удалось удалить предыдущее сообщение (mid=%s)", (message_id[:24] + "…") if message_id and len(message_id) > 24 else (message_id or "?"))
    return False


async def run_max_bot() -> None:
    """
    Long polling MAX. Если установлен maxapi — запуск через него (/showracemenu с InputMedia).
    Иначе — сырой get_updates через MaxBotAPI.
    """
    token = _get_max_token().strip()
    if not token:
        logger.info("MAX: MAX_BOT_TOKEN не задан, бот в MAX не запускается")
        return
    max_cooldown = float(os.getenv("ANTISPAM_COOLDOWN", "1.5"))

    # Сначала MaxBotAPI — полные кнопки и все сценарии (WMS, Lupa, callback).
    if not HAS_MAX_SDK:
        try:
            import maxapi as _maxapi
            from adapters.max.run_maxapi import run_max_bot_maxapi
            await run_max_bot_maxapi()
            return
        except ImportError:
            pass
        logger.warning(
            "MAX: установите MaxBotAPI (pip install MaxBotAPI) или maxapi (pip install maxapi)"
        )
        return

    from adapters.max.handlers import handle_start, handle_callback, handle_main_menu
    from adapters.max import (
        wms_flow,
        lupa_flow,
        pc_flow,
        email_flow,
        email_forwarding_flow,
        email_groups_flow,
        orgtech_flow,
        peripheral_flow,
        network_flow,
        electronic_queue_flow,
    )
    from core.support.api import support_api
    from user_storage import bind_account_by_phone

    global _current_max_bot
    bot = maxbotapi.Bot(token)
    _current_max_bot = bot
    try:
        logger.info("MAX: бот запущен (long polling)")
        # offset для /updates: защищает от повторной доставки тех же апдейтов
        persisted_last_update_id = _get_max_last_update_id(token)
        next_offset: int | None = (persisted_last_update_id + 1) if persisted_last_update_id is not None else None
        # Дедуп на случай, если апдейт пришёл без update_id (встречается у некоторых реализаций MAX API)
        import time
        recent_noid: dict[str, float] = {}
        # Persistent дедуп — чтобы после рестарта не прогонять старые start/callback/message обновления.
        max_update_dedup_map: dict = _load_max_update_dedup_map()
        max_update_dedup_dirty = False
        ttl_seconds = int(os.getenv("MAX_UPDATE_DEDUP_TTL_SECONDS", "300"))
        dedup_initial_window_seconds = float(os.getenv("MAX_UPDATE_DEDUP_INITIAL_WINDOW_SECONDS", "60"))
        max_process_start_mono = time.monotonic()
        dedup_check_counter = 0
        require_fresh_command_after_restart = os.getenv(
            "MAX_REQUIRE_FRESH_COMMAND_AFTER_RESTART", "1"
        ).strip().lower() not in ("0", "false", "no", "off")
        max_unlocked_by_fresh_command = not require_fresh_command_after_restart
        fresh_command_wait_seconds = float(
            os.getenv("MAX_FRESH_COMMAND_WAIT_SECONDS", "30")
        )
        if require_fresh_command_after_restart:
            logger.info(
                "MAX: до первой новой команды после рестарта входящие события пропускаются "
                "(авто-разблокировка через %.0fs)",
                fresh_command_wait_seconds,
            )
        while True:
            try:
                raw_updates = await _get_updates_raw(bot, timeout=25, limit=10, offset=next_offset)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if isinstance(e, asyncio.TimeoutError):
                    logger.warning("MAX get_updates timeout: %s", e)
                else:
                    logger.exception("MAX get_updates: %s", e)
                await asyncio.sleep(5)
                continue

            # продвигаем offset по максимуму update_id в пачке
            max_update_id: int | None = None
            for u in raw_updates:
                if not isinstance(u, dict):
                    continue
                uid = u.get("update_id") or u.get("updateId") or u.get("id")
                if isinstance(uid, int):
                    max_update_id = uid if max_update_id is None else max(max_update_id, uid)
                elif isinstance(uid, str) and uid.isdigit():
                    iv = int(uid)
                    max_update_id = iv if max_update_id is None else max(max_update_id, iv)
            if max_update_id is not None:
                next_offset = max_update_id + 1
                # Пишем persist baseline после получения пачки,
                # чтобы после рестарта не переобрабатывать “старые” команды.
                _set_max_last_update_id(token, max_update_id)

            for raw in raw_updates:
                if not isinstance(raw, dict):
                    continue
                # если update_id нет — пытаемся отсеять повторы в коротком окне
                if (raw.get("update_id") is None) and (raw.get("updateId") is None) and (raw.get("id") is None):
                    payload = raw.get("payload") or raw
                    key = None
                    try:
                        # Стабильный отпечаток: тип события + sender + callback/text (чтобы дубли кнопок/сообщений уходили)
                        r_chat, r_user, user_id, source = _parse_update(raw)
                        if user_id is not None and source is not None:
                            key = f"{user_id}|{r_chat or ''}|{r_user or ''}|{source!r}"
                    except Exception:
                        key = None
                    if key:
                        now = time.time()
                        # чистим старые ключи
                        for k, ts in list(recent_noid.items()):
                            if now - ts > 3.0:
                                recent_noid.pop(k, None)
                        if key in recent_noid:
                            continue
                        recent_noid[key] = now
                try:
                    r_chat, r_user, user_id, source = _parse_update(raw)
                    if (r_chat is None and r_user is None) or user_id is None:
                        payload = raw.get("payload") or raw
                        msg = payload.get("message") or payload.get("edited_message")
                        if msg:
                            logger.info(
                                "MAX: апдейт не распознан (recipient, sender.user_id, text). "
                                "Ключи: %s, message: %s, text=%r",
                                list(payload.keys()),
                                list(msg.keys()) if isinstance(msg, dict) else type(msg).__name__,
                                _get_message_text(msg) if isinstance(msg, dict) else None,
                            )
                        continue

                    # Persistent дедуп по отпечатку апдейта (с TTL).
                    # Включаем только в первые N секунд после старта, чтобы “волна” ушла,
                    # но пользовательские клики позже не глушились дедупом.
                    update_id = raw.get("update_id") or raw.get("updateId") or raw.get("id")
                    msg_mid = _extract_message_mid_from_raw(raw)
                    fp = _fingerprint_max_update(
                        r_chat=r_chat,
                        r_user=r_user,
                        sender_uid=user_id,
                        source=source,
                        msg_mid=msg_mid,
                        update_id=update_id,
                    )
                    now_mono = time.monotonic()
                    if now_mono - max_process_start_mono <= dedup_initial_window_seconds:
                        last_seen = max_update_dedup_map.get(fp)
                        if last_seen is not None and (now_mono - float(last_seen)) <= ttl_seconds:
                            continue
                        max_update_dedup_map[fp] = now_mono
                        max_update_dedup_dirty = True
                        dedup_check_counter += 1
                        if (
                            max_update_dedup_dirty
                            and dedup_check_counter % _MAX_UPDATE_DEDUP_SAVE_EVERY_UPDATES == 0
                        ):
                            _save_max_update_dedup_map(max_update_dedup_map)
                            max_update_dedup_dirty = False

                    # Антиспам для MAX: ограничиваем частоту событий от одного user_id
                    if _is_max_rate_limited(user_id, max_cooldown):
                        continue

                    # После рестарта обрабатываем только новые команды пользователя (например /start),
                    # чтобы не отрабатывать "фантомные" callback-и из очереди.
                    if not max_unlocked_by_fresh_command:
                        if (time.monotonic() - max_process_start_mono) >= max(1.0, fresh_command_wait_seconds):
                            max_unlocked_by_fresh_command = True
                            logger.info(
                                "MAX: авто-разблокировка обработки апдейтов после рестарта (таймаут %.0fs)",
                                fresh_command_wait_seconds,
                            )
                        else:
                            is_fresh_command = False
                            if source == "start":
                                is_fresh_command = True
                            elif isinstance(source, tuple) and len(source) >= 2 and source[0] == "message":
                                txt = (source[1] or "").strip()
                                if txt.startswith("/"):
                                    is_fresh_command = True
                            if not is_fresh_command:
                                logger.debug(
                                    "MAX: пропуск апдейта до новой команды (user_id=%s, source=%s)",
                                    user_id,
                                    source,
                                )
                                continue
                            max_unlocked_by_fresh_command = True
                            logger.info("MAX: получена новая команда, обработка апдейтов включена")

                    if source == "start":
                        from user_storage import needs_phone_verification_channel
                        # Для мигрированных пользователей из Лупы просим подтвердить номер телефона
                        if needs_phone_verification_channel("max", user_id):
                            _pending_verify_phone_max[user_id] = True
                            response = {
                                "text": (
                                    "📱 <b>Подтверждение номера телефона</b>\n\n"
                                    "Нажмите кнопку ниже, чтобы поделиться контактом. "
                                    "Мы обновим ваш номер телефона в профиле Rubik."
                                ),
                                "parse_mode": "HTML",
                                "buttons": [
                                    {"type": "request_contact", "label": "📱 Поделиться контактом"},
                                    {"id": "back_to_main", "label": "◀️ Отмена"},
                                ],
                            }
                        else:
                            response = handle_start(user_id)
                        logger.debug("MAX: ответ на /start для user_id=%s", user_id)
                    elif isinstance(source, tuple) and source[0] == "callback":
                        callback_id = source[1]
                        if callback_id == "cancel":
                            _pending_password_max.pop(user_id, None)
                            _pending_comment_max.pop(user_id, None)
                        if callback_id == "back_to_main":
                            _pending_registration_max.pop(user_id, None)
                        if callback_id == "bind_account":
                            _pending_bind_max[user_id] = True
                        if callback_id == "start_registration":
                            _pending_registration_max[user_id] = {"step": "email"}
                            response = {
                                "text": (
                                    "📝 <b>Регистрация</b>\n\n"
                                    "Шаг 1/2: Введите вашу <b>рабочую почту</b> (@petrovich.ru или @petrovich.tech):"
                                ),
                                "parse_mode": "HTML",
                                "buttons": [{"id": "back_to_main", "label": "◀️ Отмена"}],
                            }
                        elif callback_id == "pc_issue_start":
                            response = await pc_flow.start_pc(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id == "orgtech_issue_start":
                            response = await orgtech_flow.start_orgtech(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id == "peripheral_issue_start":
                            response = await peripheral_flow.start_peripheral(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id == "network_issue_start":
                            response = await network_flow.start_network(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id == "electronic_queue_start":
                            response = await electronic_queue_flow.start_electronic_queue(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id == "tp_email_owa_outlook":
                            response = await email_flow.start_email_owa(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id == "tp_email_forwarding":
                            response = await email_forwarding_flow.start_email_forwarding(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id == "tp_email_groups":
                            response = await email_groups_flow.start_email_groups(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id in ("ticket_wms_issue", "tp_section_wms"):
                            response = await wms_flow.start_wms(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id in ("ticket_lupa_search", "tp_section_site"):
                            response = await lupa_flow.start_lupa(user_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id in ("sa_stc_menu", "sa_stc_my_tasks") or (
                            callback_id
                            and (
                                callback_id.startswith("stc_open_issue:")
                                or callback_id.startswith("stc_set_status:")
                                or callback_id.startswith("stc_apply_status:")
                                or callback_id.startswith("stc_ask_timespent:")
                                or callback_id.startswith("stc_apply_status_ts:")
                                or callback_id.startswith("stc_open_jira:")
                            )
                        ):
                            response = await _handle_stc_callback_max(user_id, callback_id)
                        elif callback_id and callback_id.startswith("open_issue:"):
                            response = await _handle_open_issue_max(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                        elif callback_id and callback_id.startswith("add_comment:"):
                            from core.support.api import support_api as _support_api
                            from user_storage import is_user_registered
                            issue_key = (callback_id or "").split(":", 1)[-1].strip()
                            allow = False
                            if issue_key and is_user_registered(user_id, "max"):
                                if _support_api.user_owns_issue("max", user_id, issue_key):
                                    allow = True
                                else:
                                    try:
                                        from core.stc_tasks import can_stc_user_access_issue
                                        allow = await can_stc_user_access_issue("max", user_id, issue_key)
                                    except Exception:
                                        allow = False
                            if allow:
                                _pending_comment_max[user_id] = issue_key
                                response = {
                                    "text": (
                                        f"✍️ Введите текст комментария к заявке <b>{issue_key}</b> "
                                        "(или нажмите Отмена).\n\n"
                                        "Можно в этом же сообщении приложить картинку/файл."
                                    ),
                                    "parse_mode": "HTML",
                                    "buttons": [{"id": "cancel", "label": "❌ Отмена"}],
                                }
                            else:
                                response = {"text": "Заявка не найдена или доступ запрещён.", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif callback_id == "ticket_rubik_password_change":
                            from user_storage import is_user_registered, get_user_profile as _get_profile_max
                            from core.ad_ldap import is_password_expired as _is_password_expired_max
                            import asyncio as _asyncio_max
                            if not is_user_registered(user_id, "max"):
                                response = {"text": "Сначала пройдите регистрацию или привяжите аккаунт.", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            else:
                                profile = _get_profile_max(user_id, "max") or {}
                                login = (profile.get("login") or "").strip()
                                if not login:
                                    response = {
                                        "text": "В профиле не указан рабочий логин. Обратитесь в поддержку для смены пароля.",
                                        "parse_mode": "HTML",
                                        "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                                    }
                                else:
                                    try:
                                        expired = await _asyncio_max.to_thread(_is_password_expired_max, login)
                                    except Exception:
                                        expired = None
                                    if expired is False:
                                        response = {
                                            "text": "Смена пароля через бота доступна только если срок действия вашего пароля истёк.",
                                            "parse_mode": "HTML",
                                            "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                                        }
                                    elif expired is None:
                                        response = {
                                            "text": "Не удалось проверить в AD, истёк ли ваш пароль. Обратитесь на первую линию поддержки.",
                                            "parse_mode": "HTML",
                                            "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                                        }
                                    else:
                                        _pending_password_max[user_id] = True
                                        response = {
                                            "text": "🔑 <b>Смена пароля</b>\n\nРубик поможет! Введите новый пароль (или нажмите Отмена):",
                                            "parse_mode": "HTML",
                                            "buttons": [{"id": "cancel", "label": "❌ Отмена"}],
                                        }
                        elif lupa_flow.is_in_lupa_flow(user_id) and (callback_id == "cancel" or callback_id.startswith("lupa_")):
                            response = lupa_flow.handle_lupa_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                ticket_type_id = ct.get("ticket_type_id") or "lupa_search"
                                success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data)
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif (
                            wms_flow.is_in_wms_flow(user_id)
                            and (
                                callback_id.startswith("wms_dept")
                                or callback_id.startswith("wms_process_")
                                or callback_id == "cancel"
                                or callback_id in ("wms_type_issue", "wms_type_settings", "wms_type_psi_user", "wms_type_back", "wms_show_subtype", "wms_skip_description", "wms_finish_ticket", "finish_wms_settings", "finish_psi_user", "skip_psi_attachment", "wms_service_topology", "wms_service_other")
                            )
                        ):
                            response = await wms_flow.handle_wms_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                attachment_tokens = ct.get("attachment_tokens") or []
                                ticket_type_id = ct.get("ticket_type_id") or "wms_issue"
                                import tempfile
                                import os as _os
                                temp_paths = []
                                try:
                                    for att in attachment_tokens[:10]:
                                        if not isinstance(att, dict) or not att.get("url"):
                                            continue
                                        downloaded = await _download_attachment_max(bot, att)
                                        if downloaded:
                                            content, name = downloaded
                                            ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                            f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="wms_")
                                            f.write(content)
                                            f.close()
                                            temp_paths.append(f.name)
                                    if ticket_type_id == "wms_settings":
                                        success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data, attachment_paths=temp_paths)
                                    else:
                                        success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data)
                                        if success and issue_key and temp_paths:
                                            from core.jira_wms import add_attachments_to_issue
                                            added, _ = await add_attachments_to_issue(issue_key, temp_paths)
                                            logger.info("MAX WMS: к заявке %s добавлено вложений: %s", issue_key, added)
                                    if ticket_type_id != "wms_settings" and attachment_tokens and not temp_paths:
                                        logger.warning("MAX WMS: вложений было %s, скачано 0", len(attachment_tokens))
                                finally:
                                    for p in temp_paths:
                                        try:
                                            _os.unlink(p)
                                        except Exception:
                                            pass
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif pc_flow.is_in_pc_flow(user_id) and (
                            callback_id == "cancel"
                            or callback_id.startswith("pc_kind_")
                            or callback_id in ("pc_skip_description", "pc_finish_ticket", "pc_skip_attachments")
                        ):
                            response = await pc_flow.handle_pc_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                attachment_tokens = ct.get("attachment_tokens") or []
                                import tempfile
                                import os as _os
                                temp_paths = []
                                try:
                                    for att in attachment_tokens[:10]:
                                        if not isinstance(att, dict) or not att.get("url"):
                                            continue
                                        downloaded = await _download_attachment_max(bot, att)
                                        if downloaded:
                                            content, name = downloaded
                                            ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                            f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="pc_")
                                            f.write(content)
                                            f.close()
                                            temp_paths.append(f.name)
                                    success, issue_key, user_msg = await support_api.create_ticket(
                                        "max", user_id, "pc_problem", form_data, attachment_paths=temp_paths
                                    )
                                finally:
                                    for p in temp_paths:
                                        try:
                                            _os.unlink(p)
                                        except Exception:
                                            pass
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif orgtech_flow.is_in_orgtech_flow(user_id) and (
                            callback_id == "cancel"
                            or callback_id.startswith("orgtech_kind_")
                            or callback_id in ("orgtech_skip_description", "orgtech_finish_ticket", "orgtech_skip_attachments")
                        ):
                            response = await orgtech_flow.handle_orgtech_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                attachment_tokens = ct.get("attachment_tokens") or []
                                import tempfile
                                import os as _os
                                temp_paths = []
                                try:
                                    for att in attachment_tokens[:10]:
                                        if not isinstance(att, dict) or not att.get("url"):
                                            continue
                                        downloaded = await _download_attachment_max(bot, att)
                                        if downloaded:
                                            content, name = downloaded
                                            ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                            f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="orgtech_")
                                            f.write(content)
                                            f.close()
                                            temp_paths.append(f.name)
                                    success, issue_key, user_msg = await support_api.create_ticket(
                                        "max", user_id, "orgtech_problem", form_data, attachment_paths=temp_paths
                                    )
                                finally:
                                    for p in temp_paths:
                                        try:
                                            _os.unlink(p)
                                        except Exception:
                                            pass
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif peripheral_flow.is_in_peripheral_flow(user_id) and (
                            callback_id == "cancel"
                            or callback_id.startswith("peripheral_kind_")
                            or callback_id in ("peripheral_skip_description", "peripheral_finish_ticket", "peripheral_skip_attachments")
                        ):
                            response = await peripheral_flow.handle_peripheral_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                attachment_tokens = ct.get("attachment_tokens") or []
                                import tempfile
                                import os as _os
                                temp_paths = []
                                try:
                                    for att in attachment_tokens[:10]:
                                        if not isinstance(att, dict) or not att.get("url"):
                                            continue
                                        downloaded = await _download_attachment_max(bot, att)
                                        if downloaded:
                                            content, name = downloaded
                                            ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                            f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="peripheral_")
                                            f.write(content)
                                            f.close()
                                            temp_paths.append(f.name)
                                    success, issue_key, user_msg = await support_api.create_ticket(
                                        "max", user_id, "peripheral_equipment", form_data, attachment_paths=temp_paths
                                    )
                                finally:
                                    for p in temp_paths:
                                        try:
                                            _os.unlink(p)
                                        except Exception:
                                            pass
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif network_flow.is_in_network_flow(user_id) and (
                            callback_id == "cancel"
                            or callback_id.startswith("network_type_")
                            or callback_id.startswith("network_wifi_owner_")
                            or callback_id.startswith("network_pc_type_")
                            or callback_id.startswith("network_provider_")
                            or callback_id in ("network_skip_rms", "network_skip_description", "network_finish_ticket", "network_skip_attachments")
                        ):
                            response = await network_flow.handle_network_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                attachment_tokens = ct.get("attachment_tokens") or []
                                import tempfile
                                import os as _os
                                temp_paths = []
                                try:
                                    for att in attachment_tokens[:10]:
                                        if not isinstance(att, dict) or not att.get("url"):
                                            continue
                                        downloaded = await _download_attachment_max(bot, att)
                                        if downloaded:
                                            content, name = downloaded
                                            ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                            f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="network_")
                                            f.write(content)
                                            f.close()
                                            temp_paths.append(f.name)
                                    success, issue_key, user_msg = await support_api.create_ticket(
                                        "max", user_id, "network_problem", form_data, attachment_paths=temp_paths
                                    )
                                finally:
                                    for p in temp_paths:
                                        try:
                                            _os.unlink(p)
                                        except Exception:
                                            pass
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif electronic_queue_flow.is_in_electronic_queue_flow(user_id) and (
                            callback_id == "cancel" or callback_id.startswith("eq_type_")
                        ):
                            response = await electronic_queue_flow.handle_electronic_queue_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                ticket_type_id = ct.get("ticket_type_id") or "electronic_queue"
                                success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data)
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif email_flow.is_in_email_owa_flow(user_id) and (
                            callback_id == "cancel"
                            or callback_id.startswith("email_owa_req_")
                            or callback_id in ("email_owa_skip_workplace", "email_owa_finish_ticket", "email_owa_skip_attachments")
                        ):
                            response = await email_flow.handle_email_owa_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                attachment_tokens = ct.get("attachment_tokens") or []
                                import tempfile
                                import os as _os
                                temp_paths = []
                                try:
                                    for att in attachment_tokens[:10]:
                                        if not isinstance(att, dict) or not att.get("url"):
                                            continue
                                        downloaded = await _download_attachment_max(bot, att)
                                        if downloaded:
                                            content, name = downloaded
                                            ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                            f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="email_owa_")
                                            f.write(content)
                                            f.close()
                                            temp_paths.append(f.name)
                                    success, issue_key, user_msg = await support_api.create_ticket(
                                        "max", user_id, "email_owa_outlook", form_data, attachment_paths=temp_paths
                                    )
                                finally:
                                    for p in temp_paths:
                                        try:
                                            _os.unlink(p)
                                        except Exception:
                                            pass
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif email_forwarding_flow.is_in_email_forwarding_flow(user_id) and (
                            callback_id == "cancel" or callback_id.startswith("email_fwd_onoff:")
                        ):
                            response = await email_forwarding_flow.handle_email_forwarding_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                success, issue_key, user_msg = await support_api.create_ticket("max", user_id, "email_forwarding", form_data)
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif email_groups_flow.is_in_email_groups_flow(user_id) and (
                            callback_id == "cancel" or callback_id.startswith("email_groups_do:")
                        ):
                            response = await email_groups_flow.handle_email_groups_callback(user_id, callback_id)
                            if response is None:
                                response = handle_start(user_id)
                            elif response.get("create_ticket"):
                                ct = response["create_ticket"]
                                form_data = ct.get("form_data", {})
                                success, issue_key, user_msg = await support_api.create_ticket("max", user_id, "email_groups", form_data)
                                msg_show = user_msg if success else issue_key
                                response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                        elif callback_id == "admin_detailed_report":
                            from config import is_channel_admin
                            from core.admin_ticket_report import build_admin_detailed_report
                            if is_channel_admin("max", user_id):
                                path = await build_admin_detailed_report()
                                if path and path.exists():
                                    token = await _upload_file_max(
                                        bot,
                                        str(path),
                                        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    )
                                    if token:
                                        await asyncio.sleep(2)
                                        file_att = _file_attachment_from_token(token, path.name)
                                        admin_btn = [{"id": "admin_panel", "label": "🔙 В админ-панель"}]
                                        response = {
                                            "text": "📥 Подробный отчёт по заявкам:",
                                            "parse_mode": "HTML",
                                            "buttons": admin_btn,
                                            "_attachments_max": file_att + _buttons_to_attachments_max(admin_btn),
                                        }
                                    else:
                                        response = {
                                            "text": "❌ Не удалось загрузить файл отчёта в MAX.",
                                            "parse_mode": "HTML",
                                            "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
                                        }
                                else:
                                    response = {
                                        "text": "❌ Не удалось сформировать отчёт. Проверьте доступ к Jira и наличие openpyxl.",
                                        "parse_mode": "HTML",
                                        "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
                                    }
                            else:
                                response = handle_main_menu(user_id)
                        elif callback_id == "admin_lupa_excel_report":
                            from config import is_lupa_report_allowed
                            from core.lupa_report import get_report_path
                            if is_lupa_report_allowed("max", user_id):
                                path = get_report_path()
                                if path and path.exists():
                                    token = await _upload_file_max(
                                        bot, str(path),
                                        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    )
                                    if token:
                                        # MAX обрабатывает файл асинхронно; без задержки приходит attachment.file.not.processed
                                        await asyncio.sleep(2)
                                        file_att = _file_attachment_from_token(token, path.name)
                                        admin_btn = [{"id": "admin_panel", "label": "🔙 В админ-панель"}]
                                        response = {
                                            "text": "📊 Отчёт по заявкам Лупа (Excel):",
                                            "parse_mode": "HTML",
                                            "buttons": admin_btn,
                                            "_attachments_max": file_att + _buttons_to_attachments_max(admin_btn),
                                        }
                                    else:
                                        response = handle_callback(callback_id, user_id)
                                else:
                                    response = {
                                        "text": "❌ Файл отчёта не найден. Заявки по поиску ещё не создавались.",
                                        "parse_mode": "HTML",
                                        "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}],
                                    }
                            else:
                                response = handle_main_menu(user_id)
                        else:
                            if callback_id == "my_tickets":
                                tickets = await support_api.get_my_tickets_filtered("max", user_id)
                                response = handle_callback(callback_id, user_id, my_tickets=tickets)
                            else:
                                response = handle_callback(callback_id, user_id)
                            if response is None:
                                response = handle_start(user_id)
                    elif isinstance(source, tuple) and source[0] == "contact":
                        phone = source[1]
                        if user_id in _pending_registration_max:
                            reg = _pending_registration_max.pop(user_id, None)
                            if reg and reg.get("step") == "contact":
                                from validators import validate_phone
                                from core.ad_ldap import search_user_by_phone
                                from core.registration import register_user_from_ad
                                from user_storage import get_user_profile, save_user_profile
                                from config import CONFIG
                                ok_phone, err_phone = validate_phone(phone or "")
                                if not ok_phone:
                                    _pending_registration_max[user_id] = reg
                                    response = {
                                        "text": f"❗ {err_phone}\n\nПоделитесь контактом снова.",
                                        "parse_mode": "HTML",
                                        "buttons": [
                                            {"type": "request_contact", "label": "📱 Поделиться контактом"},
                                            {"id": "back_to_main", "label": "◀️ Отмена"},
                                        ],
                                    }
                                else:
                                    profile = await asyncio.to_thread(search_user_by_phone, phone)
                                    if not profile:
                                        url = (CONFIG.get("SUPPORT_PORTAL_URL") or "").strip()
                                        response = {
                                            "text": f"По этому номеру сотрудник не найден в базе. Обратитесь в поддержку: {url}" if url else "По этому номеру сотрудник не найден. Обратитесь в поддержку.",
                                            "parse_mode": "HTML",
                                            "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                                        }
                                    else:
                                        email_entered = (reg.get("email") or "").strip().lower()
                                        if email_entered and profile.get("email") and (profile["email"].lower() != email_entered):
                                            _pending_registration_max[user_id] = reg
                                            response = {
                                                "text": "❌ Почта не совпадает с записью в базе по этому номеру. Проверьте почту или поделитесь контактом с правильного номера.",
                                                "parse_mode": "HTML",
                                                "buttons": [
                                                    {"type": "request_contact", "label": "📱 Поделиться контактом"},
                                                    {"id": "back_to_main", "label": "◀️ Отмена"},
                                                ],
                                            }
                                        else:
                                            success, msg = register_user_from_ad(user_id, profile)
                                            if not success:
                                                response = {"text": f"❌ {msg}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                                            else:
                                                try:
                                                    current = get_user_profile(user_id)
                                                    if current:
                                                        from core.registration import _enrich_profile_with_jira_username
                                                        enriched = await _enrich_profile_with_jira_username(dict(current))
                                                        save_user_profile(user_id, enriched)
                                                except Exception:
                                                    pass
                                                response = {
                                                    "text": "✅ Регистрация завершена!",
                                                    "parse_mode": "HTML",
                                                    "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                                                }
                            else:
                                response = handle_start(user_id)
                        elif user_id in _pending_bind_max:
                            del _pending_bind_max[user_id]
                            ok, msg = bind_account_by_phone(user_id, phone, "max")
                            response = {"text": f"✅ {msg}" if ok else f"❌ {msg}", "parse_mode": "HTML", "buttons": []}
                            if ok:
                                response["buttons"] = [{"id": "back_to_main", "label": "◀️ В главное меню"}]
                        elif user_id in _pending_verify_phone_max:
                            from user_storage import update_phone_and_mark_verified_channel
                            del _pending_verify_phone_max[user_id]
                            update_phone_and_mark_verified_channel("max", user_id, phone)
                            response = {
                                "text": "✅ Номер телефона обновлён.",
                                "parse_mode": "HTML",
                                "buttons": [{"id": "back_to_main", "label": "◀️ В главное меню"}],
                            }
                        else:
                            response = {"text": "Используйте /start и выберите «Привязать аккаунт».", "parse_mode": "HTML", "buttons": []}
                    elif isinstance(source, tuple) and source[0] == "message":
                        text = source[1]
                        from user_storage import is_user_registered as _is_user_registered_max
                        if user_id in _pending_registration_max:
                            reg = _pending_registration_max[user_id]
                            if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
                                _pending_registration_max.pop(user_id, None)
                                response = handle_start(user_id)
                            elif reg.get("step") == "email":
                                from validators import validate_corporate_email
                                ok, err = validate_corporate_email((text or "").strip())
                                if not ok:
                                    response = {
                                        "text": f"❗ {err}\n\nПопробуйте снова или нажмите Отмена.",
                                        "parse_mode": "HTML",
                                        "buttons": [{"id": "back_to_main", "label": "◀️ Отмена"}],
                                    }
                                else:
                                    _pending_registration_max[user_id] = {"step": "contact", "email": (text or "").strip().lower()}
                                    try:
                                        pl = raw.get("payload") or raw
                                        um = pl.get("message") or pl.get("edited_message")
                                        if isinstance(um, dict):
                                            user_mid = um.get("mid") or um.get("message_id") or um.get("id")
                                            body = um.get("body") or um
                                            if not user_mid and isinstance(body, dict):
                                                user_mid = body.get("mid") or body.get("message_id")
                                            if user_mid:
                                                await _delete_message_max(bot, r_chat, r_user, str(user_mid))
                                    except Exception:
                                        pass
                                    response = {
                                        "text": (
                                            "✅ Почта сохранена.\n\n"
                                            "Шаг 2/2: Поделитесь номером телефона — нажмите кнопку ниже (так мы проверим вас в базе сотрудников):"
                                        ),
                                        "parse_mode": "HTML",
                                        "buttons": [
                                            {"type": "request_contact", "label": "📱 Поделиться контактом"},
                                            {"id": "back_to_main", "label": "◀️ Отмена"},
                                        ],
                                    }
                            else:
                                response = {
                                    "text": "Поделитесь контактом по кнопке ниже.",
                                    "parse_mode": "HTML",
                                    "buttons": [
                                        {"type": "request_contact", "label": "📱 Поделиться контактом"},
                                        {"id": "back_to_main", "label": "◀️ Отмена"},
                                    ],
                                }
                        elif not _is_user_registered_max(user_id, "max"):
                            response = {
                                "text": "Привет! Для работы с ботом отправьте команду /start.",
                                "parse_mode": "HTML",
                                "buttons": [],
                            }
                        else:
                            payload = raw.get("payload") or raw
                            raw_msg = payload.get("message") or payload.get("edited_message")
                            attachment_list = _extract_file_attachments_from_max_message(raw_msg) if isinstance(raw_msg, dict) else None
                            if (text or "").strip().lower() == "/showracemenu":
                                pict_dir = Path(__file__).resolve().parents[2] / "Pict"
                                sent = False
                                if pict_dir.is_dir():
                                    exts = ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp")
                                    files = []
                                    for ext in exts:
                                        files.extend(pict_dir.glob(ext))
                                    if files:
                                        path = random.choice(files)
                                        token = await _upload_image_max(bot, str(path))
                                        if token:
                                            last = _last_bot_message_max.pop(user_id, None)
                                            if last and last.get("mid"):
                                                await _delete_message_max(
                                                    bot, last.get("chat_id"), last.get("user_id"), last["mid"]
                                                )
                                            att = _image_attachment_from_token(token)
                                            new_mid = await _send_message_max(
                                                bot, r_chat, r_user, "\u200b", att, None, "source:showracemenu"
                                            )
                                            if new_mid:
                                                _last_bot_message_max[user_id] = {
                                                    "chat_id": r_chat,
                                                    "user_id": r_user,
                                                    "mid": new_mid,
                                                }
                                                sent = True
                                if not sent:
                                    last = _last_bot_message_max.pop(user_id, None)
                                    if last and last.get("mid"):
                                        await _delete_message_max(
                                            bot, last.get("chat_id"), last.get("user_id"), last["mid"]
                                        )
                                    new_mid = await _send_message_max(
                                        bot, r_chat, r_user, "…", None, None, "source:showracemenu"
                                    )
                                    if new_mid:
                                        _last_bot_message_max[user_id] = {
                                            "chat_id": r_chat,
                                            "user_id": r_user,
                                            "mid": new_mid,
                                        }
                                continue
                            # Админ-удаление пользователя — отдельный режим ввода.
                            # Не должен перехватывать сообщения, если пользователь находится в любом другом сценарии (Lupa/WMS/формы).
                            in_any_flow = (
                                wms_flow.is_in_wms_flow(user_id)
                                or lupa_flow.is_in_lupa_flow(user_id)
                                or pc_flow.is_in_pc_flow(user_id)
                                or orgtech_flow.is_in_orgtech_flow(user_id)
                                or peripheral_flow.is_in_peripheral_flow(user_id)
                                or network_flow.is_in_network_flow(user_id)
                                or electronic_queue_flow.is_in_electronic_queue_flow(user_id)
                                or email_flow.is_in_email_owa_flow(user_id)
                                or email_forwarding_flow.is_in_email_forwarding_flow(user_id)
                                or email_groups_flow.is_in_email_groups_flow(user_id)
                            )
                            if (user_id in _pending_admin_delete_search_max) and (not in_any_flow):
                                _pending_admin_delete_search_max.pop(user_id, None)
                                from config import is_channel_admin
                                from user_storage import search_users_by_fio
                                if not is_channel_admin("max", user_id):
                                    response = handle_main_menu(user_id)
                                else:
                                    inp = (text or "").strip()
                                    if not inp:
                                        response = {"text": "Введите часть ФИО для поиска.", "parse_mode": "HTML", "buttons": [{"id": "admin_del_back_choice", "label": "🔙 К выбору способа"}], "_set_pending_admin_search": True}
                                    else:
                                        matches = search_users_by_fio(inp, limit=20)
                                        if not matches:
                                            response = {"text": f"По запросу «{inp}» никого не найдено. Введите другую часть ФИО или нажмите «К выбору способа».", "parse_mode": "HTML", "buttons": [{"id": "admin_del_back_choice", "label": "🔙 К выбору способа"}], "_set_pending_admin_search": True}
                                        else:
                                            buttons = []
                                            for uid, profile in matches:
                                                name = (profile.get("full_name") or "—").strip() or "—"
                                                login = (profile.get("login") or "").strip() or "—"
                                                label = f"{name} ({login})" if len(f"{name} ({login})") <= 40 else f"{name[:28]}… ({login})"
                                                buttons.append({"id": f"admin_del_uid_{uid}", "label": label})
                                            buttons.append({"id": "admin_del_back_choice", "label": "🔙 К выбору способа"})
                                            response = {"text": f"Найдено по «{inp}»: {len(matches)}. Выберите пользователя для удаления:", "parse_mode": "HTML", "buttons": buttons}
                            elif (user_id in _pending_admin_delete_max) and (not in_any_flow):
                                _pending_admin_delete_max.pop(user_id, None)
                                from config import is_channel_admin
                                from user_storage import delete_user, get_user_profile, find_by_login, resolve_channel_user_id
                                if not is_channel_admin("max", user_id):
                                    response = handle_main_menu(user_id)
                                elif not (text or "").strip():
                                    response = {"text": "Введите Telegram ID или логин пользователя для удаления.", "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}], "_set_pending_admin_delete": True}
                                elif (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
                                    response = handle_callback("admin_panel", user_id)
                                    if response is None:
                                        response = handle_main_menu(user_id)
                                else:
                                    inp = (text or "").strip()
                                    primary_id = None
                                    if inp.isdigit():
                                        uid = int(inp)
                                        profile = get_user_profile(uid, "max")
                                        if profile is not None:
                                            primary_id = resolve_channel_user_id("max", uid)
                                    else:
                                        primary_id = find_by_login(inp)
                                    if primary_id is None:
                                        response = {"text": "Пользователь не найден. Введите Telegram ID (число) или рабочий логин.", "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}], "_set_pending_admin_delete": True}
                                    else:
                                        profile = get_user_profile(primary_id)
                                        deleted = delete_user(primary_id)
                                        if deleted:
                                            response = {"text": f"✅ Пользователь удалён: {profile.get('full_name', '—')} ({profile.get('login', '—')}, ID {primary_id}).", "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}]}
                                            logger.info("MAX админ %s удалил пользователя %s (%s)", user_id, primary_id, profile.get("login"))
                                        else:
                                            response = {"text": "Не удалось удалить пользователя.", "parse_mode": "HTML", "buttons": [{"id": "admin_panel", "label": "🔙 В админ-панель"}]}
                            elif user_id in _pending_comment_max:
                                issue_key = _pending_comment_max.get(user_id)
                                text_clean = (text or "").strip()
                                if text_clean.lower() in ("отмена", "cancel", "/cancel"):
                                    _pending_comment_max.pop(user_id, None)
                                    response = handle_main_menu(user_id)
                                elif issue_key and (text_clean or attachment_list):
                                    _pending_comment_max.pop(user_id, None)
                                    from core.jira_aa import add_comment as jira_add_comment
                                    from user_storage import get_user_profile
                                    profile = get_user_profile(user_id, "max") or {}
                                    full_name = (profile.get("full_name") or "").strip() or "Пользователь"
                                    ok = True
                                    if text_clean:
                                        comment_body = f"[{full_name}] {text_clean}"
                                        ok = await jira_add_comment(issue_key, comment_body)

                                    # Вложения из сообщения (если есть): добавляем к заявке
                                    added_files = 0
                                    if ok and attachment_list:
                                        import tempfile
                                        import os as _os
                                        from core.jira_wms import add_attachments_to_issue

                                        temp_paths: list[str] = []
                                        try:
                                            for att in attachment_list[:10]:
                                                if not isinstance(att, dict) or not att.get("url"):
                                                    continue
                                                downloaded = await _download_attachment_max(bot, att)
                                                if not downloaded:
                                                    continue
                                                content, name = downloaded
                                                ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                                f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="comment_")
                                                f.write(content)
                                                f.close()
                                                temp_paths.append(f.name)
                                            if temp_paths:
                                                added_files, _ = await add_attachments_to_issue(issue_key, temp_paths)
                                                logger.info("MAX comment: к заявке %s добавлено вложений: %s", issue_key, added_files)
                                        finally:
                                            for p in temp_paths:
                                                try:
                                                    _os.unlink(p)
                                                except Exception:
                                                    pass

                                    suffix = f" (вложений: {added_files})" if ok and added_files else ""
                                    response = {
                                        "text": (
                                            (
                                                f"✅ Комментарий добавлен к заявке {issue_key}{suffix}."
                                                if text_clean
                                                else f"✅ Вложение добавлено к заявке {issue_key}{suffix}."
                                            )
                                            if ok
                                            else "❌ Не удалось добавить комментарий."
                                        ),
                                        "parse_mode": "HTML",
                                        "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}],
                                    }
                                else:
                                    response = {
                                        "text": (
                                            "Введите текст комментария (или подпись к картинке/файлу) "
                                            "или нажмите Отмена."
                                        ),
                                        "parse_mode": "HTML",
                                        "buttons": [{"id": "cancel", "label": "❌ Отмена"}],
                                    }
                            elif user_id in _pending_bind_max:
                                del _pending_bind_max[user_id]
                                ok, msg = bind_account_by_phone(user_id, text, "max")
                                response = {"text": f"✅ {msg}" if ok else f"❌ {msg}", "parse_mode": "HTML", "buttons": []}
                                if ok:
                                    response["buttons"] = [{"id": "back_to_main", "label": "◀️ В главное меню"}]
                            elif user_id in _pending_password_max:
                                _pending_password_max.pop(user_id, None)
                                if (text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
                                    response = handle_main_menu(user_id)
                                else:
                                    from core.password import request_password_change
                                    ok, msg = await request_password_change(user_id, (text or "").strip(), "max")
                                    response = {"text": f"✅ {msg}" if ok else f"❌ {msg}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif wms_flow.is_in_wms_flow(user_id):
                                state = getattr(wms_flow, "_flow", {}).get(user_id, {})
                                if state.get("step") == "attachments" and isinstance(raw_msg, dict):
                                    body = raw_msg.get("body") or raw_msg
                                    logger.info(
                                        "MAX WMS attachments: message keys=%s, body keys=%s, attachment_list len=%s",
                                        list(raw_msg.keys()),
                                        list(body.keys()) if isinstance(body, dict) else type(body).__name__,
                                        len(attachment_list) if attachment_list else 0,
                                    )
                                response = await wms_flow.handle_wms_message(user_id, text, attachment_list=attachment_list)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    attachment_tokens = ct.get("attachment_tokens") or []
                                    ticket_type_id = ct.get("ticket_type_id") or "wms_issue"
                                    import tempfile
                                    import os as _os
                                    temp_paths = []
                                    try:
                                        for att in attachment_tokens[:10]:
                                            if not isinstance(att, dict) or not att.get("url"):
                                                continue
                                            downloaded = await _download_attachment_max(bot, att)
                                            if downloaded:
                                                content, name = downloaded
                                                ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                                f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="wms_")
                                                f.write(content)
                                                f.close()
                                                temp_paths.append(f.name)
                                        if ticket_type_id == "wms_settings":
                                            success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data, attachment_paths=temp_paths)
                                        else:
                                            success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data)
                                            if success and issue_key and temp_paths:
                                                from core.jira_wms import add_attachments_to_issue
                                                added, _ = await add_attachments_to_issue(issue_key, temp_paths)
                                                logger.info("MAX WMS: к заявке %s добавлено вложений: %s", issue_key, added)
                                        if ticket_type_id != "wms_settings" and attachment_tokens and not temp_paths:
                                            logger.warning("MAX WMS: вложений было %s, скачано 0", len(attachment_tokens))
                                    finally:
                                        for p in temp_paths:
                                            try:
                                                _os.unlink(p)
                                            except Exception:
                                                pass
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif pc_flow.is_in_pc_flow(user_id):
                                response = await pc_flow.handle_pc_message(user_id, text, attachment_list=attachment_list)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    attachment_tokens = ct.get("attachment_tokens") or []
                                    import tempfile
                                    import os as _os
                                    temp_paths = []
                                    try:
                                        for att in attachment_tokens[:10]:
                                            if not isinstance(att, dict) or not att.get("url"):
                                                continue
                                            downloaded = await _download_attachment_max(bot, att)
                                            if downloaded:
                                                content, name = downloaded
                                                ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                                f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="pc_")
                                                f.write(content)
                                                f.close()
                                                temp_paths.append(f.name)
                                        success, issue_key, user_msg = await support_api.create_ticket(
                                            "max", user_id, "pc_problem", form_data, attachment_paths=temp_paths
                                        )
                                    finally:
                                        for p in temp_paths:
                                            try:
                                                _os.unlink(p)
                                            except Exception:
                                                pass
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif orgtech_flow.is_in_orgtech_flow(user_id):
                                response = await orgtech_flow.handle_orgtech_message(user_id, text, attachment_list=attachment_list)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    attachment_tokens = ct.get("attachment_tokens") or []
                                    import tempfile
                                    import os as _os
                                    temp_paths = []
                                    try:
                                        for att in attachment_tokens[:10]:
                                            if not isinstance(att, dict) or not att.get("url"):
                                                continue
                                            downloaded = await _download_attachment_max(bot, att)
                                            if downloaded:
                                                content, name = downloaded
                                                ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                                f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="orgtech_")
                                                f.write(content)
                                                f.close()
                                                temp_paths.append(f.name)
                                        success, issue_key, user_msg = await support_api.create_ticket(
                                            "max", user_id, "orgtech_problem", form_data, attachment_paths=temp_paths
                                        )
                                    finally:
                                        for p in temp_paths:
                                            try:
                                                _os.unlink(p)
                                            except Exception:
                                                pass
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif peripheral_flow.is_in_peripheral_flow(user_id):
                                response = await peripheral_flow.handle_peripheral_message(user_id, text, attachment_list=attachment_list)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    attachment_tokens = ct.get("attachment_tokens") or []
                                    import tempfile
                                    import os as _os
                                    temp_paths = []
                                    try:
                                        for att in attachment_tokens[:10]:
                                            if not isinstance(att, dict) or not att.get("url"):
                                                continue
                                            downloaded = await _download_attachment_max(bot, att)
                                            if downloaded:
                                                content, name = downloaded
                                                ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                                f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="peripheral_")
                                                f.write(content)
                                                f.close()
                                                temp_paths.append(f.name)
                                        success, issue_key, user_msg = await support_api.create_ticket(
                                            "max", user_id, "peripheral_equipment", form_data, attachment_paths=temp_paths
                                        )
                                    finally:
                                        for p in temp_paths:
                                            try:
                                                _os.unlink(p)
                                            except Exception:
                                                pass
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif network_flow.is_in_network_flow(user_id):
                                response = await network_flow.handle_network_message(user_id, text, attachment_list=attachment_list)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    attachment_tokens = ct.get("attachment_tokens") or []
                                    import tempfile
                                    import os as _os
                                    temp_paths = []
                                    try:
                                        for att in attachment_tokens[:10]:
                                            if not isinstance(att, dict) or not att.get("url"):
                                                continue
                                            downloaded = await _download_attachment_max(bot, att)
                                            if downloaded:
                                                content, name = downloaded
                                                ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                                f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="network_")
                                                f.write(content)
                                                f.close()
                                                temp_paths.append(f.name)
                                        success, issue_key, user_msg = await support_api.create_ticket(
                                            "max", user_id, "network_problem", form_data, attachment_paths=temp_paths
                                        )
                                    finally:
                                        for p in temp_paths:
                                            try:
                                                _os.unlink(p)
                                            except Exception:
                                                pass
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif electronic_queue_flow.is_in_electronic_queue_flow(user_id):
                                response = await electronic_queue_flow.handle_electronic_queue_message(user_id, text, attachment_list=attachment_list)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    ticket_type_id = ct.get("ticket_type_id") or "electronic_queue"
                                    success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data)
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif email_flow.is_in_email_owa_flow(user_id):
                                response = await email_flow.handle_email_owa_message(user_id, text, attachment_list=attachment_list)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    attachment_tokens = ct.get("attachment_tokens") or []
                                    import tempfile
                                    import os as _os
                                    temp_paths = []
                                    try:
                                        for att in attachment_tokens[:10]:
                                            if not isinstance(att, dict) or not att.get("url"):
                                                continue
                                            downloaded = await _download_attachment_max(bot, att)
                                            if downloaded:
                                                content, name = downloaded
                                                ext = _os.path.splitext(name)[1] if name and "." in name else ".bin"
                                                f = tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="email_owa_")
                                                f.write(content)
                                                f.close()
                                                temp_paths.append(f.name)
                                        success, issue_key, user_msg = await support_api.create_ticket(
                                            "max", user_id, "email_owa_outlook", form_data, attachment_paths=temp_paths
                                        )
                                    finally:
                                        for p in temp_paths:
                                            try:
                                                _os.unlink(p)
                                            except Exception:
                                                pass
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif email_forwarding_flow.is_in_email_forwarding_flow(user_id):
                                response = await email_forwarding_flow.handle_email_forwarding_message(user_id, text)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    success, issue_key, user_msg = await support_api.create_ticket("max", user_id, "email_forwarding", form_data)
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif email_groups_flow.is_in_email_groups_flow(user_id):
                                response = await email_groups_flow.handle_email_groups_message(user_id, text)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    success, issue_key, user_msg = await support_api.create_ticket("max", user_id, "email_groups", form_data)
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            elif lupa_flow.is_in_lupa_flow(user_id):
                                response = await lupa_flow.handle_lupa_message(user_id, text)
                                if response is None:
                                    response = {"text": "Используйте кнопки или /start.", "parse_mode": "HTML", "buttons": [{"id": "cancel", "label": "❌ Отмена"}]}
                                elif response.get("create_ticket"):
                                    ct = response["create_ticket"]
                                    form_data = ct.get("form_data", {})
                                    ticket_type_id = ct.get("ticket_type_id") or "lupa_search"
                                    success, issue_key, user_msg = await support_api.create_ticket("max", user_id, ticket_type_id, form_data)
                                    msg_show = user_msg if success else issue_key
                                    response = {"text": f"✅ {msg_show}" if success else f"❌ {msg_show}", "parse_mode": "HTML", "buttons": [{"id": "back_to_main", "label": "🔙 В главное меню"}]}
                            else:
                                response = {"text": "Используйте /start для начала.", "parse_mode": "HTML", "buttons": []}
                    else:
                        continue

                    if not response or not response.get("text"):
                        continue

                    if response.pop("_set_pending_admin_delete", False):
                        _pending_admin_delete_max[user_id] = True
                    if response.pop("_set_pending_admin_search", False):
                        _pending_admin_delete_search_max[user_id] = True

                    # Удаляем предыдущее сообщение бота и отправляем новое (как в the_bot_on_dute)
                    last = _last_bot_message_max.pop(user_id, None)
                    if last and last.get("mid"):
                        await _delete_message_max(
                            bot,
                            last.get("chat_id"),
                            last.get("user_id"),
                            last["mid"],
                        )

                    buttons = response.get("buttons") or []
                    attachments_max = response.get("_attachments_max")
                    if attachments_max is None:
                        attachments_max = _buttons_to_attachments_max(buttons)
                    parse_mode = response.get("parse_mode") or "HTML"
                    new_mid = await _send_message_max(
                        bot,
                        r_chat,
                        r_user,
                        response["text"],
                        attachments_max,
                        parse_mode,
                        _source_to_log_tag(source),
                    )
                    if new_mid:
                        _last_bot_message_max[user_id] = {
                            "chat_id": r_chat,
                            "user_id": r_user,
                            "mid": new_mid,
                        }
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("MAX обработка update: %s", e)
    finally:
        _current_max_bot = None
        await bot.close()


def main() -> None:
    """Точка входа при запуске модуля (python -m adapters.max.main_max)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    asyncio.run(run_max_bot())


if __name__ == "__main__":
    main()
