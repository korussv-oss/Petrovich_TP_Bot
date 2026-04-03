"""
Retry-декоратор для Jira HTTP-функций.

Все функции в jira_aa/jira_wms/... возвращают None при ошибке (не бросают исключение),
поэтому декоратор повторяет вызов при получении None с экспоненциальным backoff.

Применяется к функциям чтения данных из Jira: get_issue_info, get_issue_admin_details,
get_issue_status, get_issue_comments. Функции создания тикетов не декорируются,
чтобы исключить двойное создание при повторе.
"""
import asyncio
import functools
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def retry_jira(max_attempts: int = 3, base_delay: float = 1.0):
    """
    Декоратор: повторяет вызов async-функции при получении None (признак ошибки в jira_*.py).
    Использует экспоненциальный backoff: 1s, 2s, 4s...

    Применять только к функциям-читателям (get_*), не к create_*/add_comment,
    чтобы избежать дублирования действий при повторе.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                result = await func(*args, **kwargs)
                if result is not None:
                    return result
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.debug(
                        "%s: попытка %d/%d вернула None, повтор через %.1fs",
                        func.__name__, attempt, max_attempts, delay,
                    )
                    await asyncio.sleep(delay)
            return None
        return wrapper
    return decorator
