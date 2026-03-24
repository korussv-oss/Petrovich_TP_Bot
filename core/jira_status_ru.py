"""
Человекочитаемые подписи статусов Jira для пользователя (RU).
Имена из REST API часто на английском; сравнение логики (resolved, silent и т.д.) по-прежнему на сырых строках из Jira.
"""

from __future__ import annotations

from typing import Optional

_KEYS: dict[str, str] = {
    "open": "Открыта",
    "opened": "Открыта",
    "new": "Новая",
    "reopened": "Открыта повторно",
    "to do": "К выполнению",
    "todo": "К выполнению",
    "backlog": "Бэклог",
    "selected for development": "Выбрано для разработки",
    "in progress": "В работе",
    "in development": "В разработке",
    "work in progress": "В работе",
    "processing": "В обработке",
    "waiting for support": "Ожидание поддержки",
    "waiting for customer": "Ожидание ответа клиента",
    "pending": "В ожидании",
    "on hold": "Отложено",
    "hold": "На удержании",
    "blocked": "Заблокировано",
    "paused": "Приостановлено",
    "in review": "На проверке",
    "code review": "Ревью кода",
    "review": "На проверке",
    "testing": "Тестирование",
    "qa": "Тестирование",
    "ready for testing": "Готово к тестированию",
    "ready for qa": "Готово к тестированию",
    "done": "Готово",
    "completed": "Выполнено",
    "complete": "Выполнено",
    "resolved": "Решено",
    "fixed": "Исправлено",
    "closed": "Закрыто",
    "cancelled": "Отменено",
    "canceled": "Отменено",
    "declined": "Отклонено",
    "rejected": "Отклонено",
    "approved": "Согласовано",
    "waiting for approval": "Ожидание согласования",
    "in analysis": "Анализ",
    "analysis": "Анализ",
    "estimation": "Оценка",
    "scheduled": "Запланировано",
    "deployed": "Развёрнуто",
    "live": "В эксплуатации",
    "archived": "Архив",
}


def _has_cyrillic(s: str) -> bool:
    return any("\u0400" <= c <= "\u04ff" for c in s)


def jira_status_display_ru(status: Optional[str]) -> str:
    """
    Строка для показа пользователю. Уже кириллические имена из Jira не меняем;
    для типичных английских — подставляем русские формулировки.
    «—» и пустое — как «нет данных».
    """
    if status is None:
        return "—"
    s = str(status).strip()
    if not s or s == "—":
        return "—"
    if _has_cyrillic(s):
        return s
    key = " ".join(s.lower().split())
    return _KEYS.get(key, s)
