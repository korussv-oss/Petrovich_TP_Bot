"""
Бот Rubik: регистрация, смена пароля (задача в Jira AA), смена учётных данных, админ (удаление пользователей).
Вся логика в core для последующего подключения из MAX (идентификация по номеру телефона).
"""
import asyncio
import logging
import os
from pathlib import Path

from config import CONFIG
from core.support.delivery import set_delivery
from core.notifications import run_registry_status_loop, run_registry_comments_loop


def _env_strip_inline_comment(raw: str | None) -> str | None:
    """
    Docker --env-file и часть окружений отдают значение целиком, без вырезания комментария после #.
    Пример: PASSWORD_STATUS_CHECK_INTERVAL=15 # по умолчанию 90
    """
    if raw is None:
        return None
    return raw.split("#", 1)[0].strip() or None


os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=getattr(
        logging,
        (_env_strip_inline_comment(os.getenv("LOG_LEVEL")) or "INFO").upper(),
        logging.INFO,
    ),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("data/bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

_singleton_lock_fp = None
_telegram_dp = None  # создаём один раз, чтобы не повторять dp.include_router на рестартах


def _env_int(name: str, default: int) -> int:
    raw = _env_strip_inline_comment(os.getenv(name))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Некорректное целое для %s=%r, используем %s",
            name,
            os.getenv(name),
            default,
        )
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env_strip_inline_comment(os.getenv(name))
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Некорректное число для %s=%r, используем %s",
            name,
            os.getenv(name),
            default,
        )
        return default


def _env_flag_enabled(name: str, default: bool = True) -> bool:
    raw = _env_strip_inline_comment(os.getenv(name))
    if raw is None:
        return default
    v = raw.lower()
    return v not in ("0", "false", "no", "off")


def _acquire_singleton_lock() -> bool:
    """
    Защита от запуска нескольких экземпляров бота одновременно.
    Иначе будут дубли уведомлений и TelegramConflictError (несколько getUpdates).
    """
    global _singleton_lock_fp
    lock_path = Path("data") / "rubik_singleton.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lock_path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            # 1 байт блокировки достаточно; режим non-blocking.
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # type: ignore

            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.seek(0)
        fp.truncate()
        fp.write(str(os.getpid()))
        fp.flush()
        _singleton_lock_fp = fp
        return True
    except Exception:
        try:
            fp.close()
        except Exception:
            pass
        return False


async def _supervise(name: str, runner, *, restart_delay_seconds: float = 5.0) -> None:
    """
    Запускает runner() в бесконечном цикле. Падение одного «сервиса» не влияет на другие.
    Если runner() завершился исключением — логируем и перезапускаем через задержку.
    Если runner() завершился штатно — тоже перезапускаем через задержку (на случай временной остановки polling).
    """
    while True:
        try:
            await runner()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s: сервис упал, перезапуск через %.1fs", name, restart_delay_seconds)
            await asyncio.sleep(restart_delay_seconds)
        else:
            logger.warning("%s: сервис завершился, перезапуск через %.1fs", name, restart_delay_seconds)
            await asyncio.sleep(restart_delay_seconds)


async def _run_telegram_bot() -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.fsm.storage.memory import MemoryStorage

    from handlers.start import router as start_router
    from handlers.registration import router as registration_router
    from handlers.password import router as password_router
    from handlers.admin import router as admin_router
    from handlers.comments import router as comments_router
    from handlers.my_tickets import router as my_tickets_router
    from handlers.create_ticket import router as create_ticket_router
    from handlers.menu_extra import router as menu_extra_router
    from middlewares.antispam import AntispamMiddleware

    token = CONFIG.get("TELEGRAM", {}).get("TOKEN", "").strip()
    if not token:
        logger.info("TELEGRAM: TELEGRAM_TOKEN не задан, бот в Telegram не запускается")
        return

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    global _telegram_dp
    if _telegram_dp is None:
        dp = Dispatcher(storage=MemoryStorage())
        cooldown = _env_float("ANTISPAM_COOLDOWN", 1.5)
        dp.update.outer_middleware(AntispamMiddleware(cooldown=cooldown))

        # Роутеры импортируются как singletons, поэтому include_router делаем один раз.
        dp.include_router(start_router)
        dp.include_router(registration_router)
        dp.include_router(password_router)
        dp.include_router(admin_router)
        dp.include_router(comments_router)
        dp.include_router(my_tickets_router)
        dp.include_router(create_ticket_router)
        dp.include_router(menu_extra_router)
        _telegram_dp = dp
    else:
        dp = _telegram_dp

    logger.info("TELEGRAM: бот запущен (polling)")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


async def _run_max_bot() -> None:
    max_token = (CONFIG.get("MAX") or {}).get("BOT_TOKEN", "").strip()
    if not max_token:
        logger.info("MAX: MAX_BOT_TOKEN не задан, бот в MAX не запускается")
        return

    from adapters.max.main_max import run_max_bot

    logger.info("MAX: сервис запускается")
    await run_max_bot()


async def main():
    os.makedirs("data", exist_ok=True)
    if not _acquire_singleton_lock():
        logger.critical("Rubik уже запущен (singleton lock). Остановите второй экземпляр.")
        return

    telegram_token_present = bool((CONFIG.get("TELEGRAM", {}) or {}).get("TOKEN", "").strip())
    # Совместимость с the_bot_on_dute: USED_TELEGRAMM=0 принудительно отключает Telegram.
    telegram_enabled = telegram_token_present and _env_flag_enabled("USED_TELEGRAMM", True)
    max_enabled = bool((CONFIG.get("MAX", {}) or {}).get("BOT_TOKEN", "").strip())

    if not telegram_enabled and not max_enabled:
        logger.critical(
            "Telegram отключён/не настроен и MAX_BOT_TOKEN не задан — нечего запускать"
        )
        return

    # Delivery не должен связывать жизненный цикл сервисов: если Telegram не запущен,
    # доставка в Telegram просто логируется и пропускается, MAX продолжает работать.
    import time as _time
    telegram_delivery_timeout_seconds = _env_float("TELEGRAM_DELIVERY_TIMEOUT_SECONDS", 3.0)
    telegram_delivery_cooldown_seconds = _env_float("TELEGRAM_DELIVERY_COOLDOWN_SECONDS", 30.0)
    telegram_send_disabled_until = 0.0
    async def deliver_to_channel(channel_id: str, channel_user_id: int, text: str, reply_markup=None):
        nonlocal telegram_send_disabled_until
        if (channel_id or "").strip().lower() == "telegram":
            if not telegram_enabled:
                logger.warning("Доставка в Telegram пропущена (бот выключен): user_id=%s", channel_user_id)
                return
            # Если Telegram уже “падает”, не трогаем сеть слишком часто.
            now = _time.monotonic()
            if now < telegram_send_disabled_until:
                logger.debug("Доставка в Telegram пропущена (cooldown): user_id=%s", channel_user_id)
                return
            from aiogram import Bot
            from aiogram.client.default import DefaultBotProperties
            from aiogram.enums import ParseMode
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            token = (CONFIG.get("TELEGRAM", {}) or {}).get("TOKEN", "").strip()
            bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            try:
                markup = None
                if reply_markup:
                    rows = [
                        [InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"]) for b in row]
                        for row in reply_markup
                    ]
                    markup = InlineKeyboardMarkup(inline_keyboard=rows)
                # Не блокируем общий event-loop на долгие сетевые таймауты.
                await asyncio.wait_for(
                    bot.send_message(channel_user_id, text, reply_markup=markup),
                    timeout=telegram_delivery_timeout_seconds,
                )
            except Exception as e:
                # При проблемах с сетью/TG временно отключаем доставку, чтобы не тормозить MAX.
                telegram_send_disabled_until = _time.monotonic() + telegram_delivery_cooldown_seconds
                logger.warning(
                    "Доставка в Telegram отключена на %.0fs (timeout/ошибка): user_id=%s: %s",
                    telegram_delivery_cooldown_seconds,
                    channel_user_id,
                    e,
                )
                return
            finally:
                await bot.session.close()
            return

        if (channel_id or "").strip().lower() == "max":
            if not max_enabled:
                logger.warning("Доставка в MAX пропущена (бот выключен): user_id=%s", channel_user_id)
                return
            try:
                from adapters.max.main_max import send_notification_to_max_user
                await send_notification_to_max_user(channel_user_id, text, reply_markup)
            except Exception as e:
                logger.warning("Доставка в MAX user_id=%s: %s", channel_user_id, e)
            return

        logger.warning("Доставка: неизвестный канал %r (user_id=%s)", channel_id, channel_user_id)

    set_delivery(deliver_to_channel)

    logger.info("Rubik: сервисы запускаются независимо (MAX и Telegram не ждут друг друга)")
    status_interval = _env_int("PASSWORD_STATUS_CHECK_INTERVAL", 90)
    comments_interval = _env_int("COMMENTS_CHECK_INTERVAL", 30)
    status_task = asyncio.create_task(
        _supervise(
            "REGISTRY_STATUS",
            lambda: run_registry_status_loop(interval_seconds=status_interval),
            restart_delay_seconds=5.0,
        )
    )
    comments_task = asyncio.create_task(
        _supervise(
            "REGISTRY_COMMENTS",
            lambda: run_registry_comments_loop(interval_seconds=comments_interval),
            restart_delay_seconds=5.0,
        )
    )

    service_tasks = []
    if telegram_enabled:
        telegram_task = asyncio.create_task(_supervise("TELEGRAM", _run_telegram_bot, restart_delay_seconds=5.0))
    if max_enabled:
        max_task = asyncio.create_task(_supervise("MAX", _run_max_bot, restart_delay_seconds=5.0))

    try:
        # Telegram не ждём: если Telegram падает/перезапускается — это не должно ломать MAX/уведомления.
        await asyncio.gather(
            status_task,
            comments_task,
            *( [max_task] if max_enabled else [] ),
        )
    finally:
        status_task.cancel()
        comments_task.cancel()
        if telegram_enabled:
            telegram_task.cancel()
        if max_enabled:
            max_task.cancel()
        for t in (status_task, comments_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
