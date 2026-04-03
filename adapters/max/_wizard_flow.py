"""
Вспомогательный модуль для хранения WizardSession в MAX-адаптерах.

Каждый MAX flow файл держит сессии пользователей в module-level dict.
Этот модуль предоставляет тонкую обёртку, которая:
- хранит WizardSession вместо сырых dict'ов
- предоставляет screen_for_state() как shortcut
- позволяет единообразно читать/записывать/очищать сессии

Пример использования в flow-файле::

    from adapters.max._wizard_flow import WizardFlowStore
    from core.support.ticket_wizard import screen_for_state

    _store = WizardFlowStore()

    async def start_flow(user_id: int) -> dict:
        session = _store.create(user_id, ticket_type_id="wms_issue", step="WMS_ISSUE_PROCESS")
        screen = screen_for_state(session.step)
        return {"text": screen.text, "parse_mode": "HTML", "buttons": [...]}

    async def handle_callback(user_id: int, callback_id: str) -> Optional[dict]:
        session = _store.get(user_id)
        if session is None:
            return None
        # обновляем шаг
        session = _store.set_step(user_id, "WMS_ISSUE_SUMMARY", data={"process": callback_id})
        screen = screen_for_state(session.step, session.data)
        return {"text": screen.text, "parse_mode": "HTML", "buttons": [...]}
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.support.ticket_wizard import WizardSession, screen_for_state as _screen_for_state  # noqa: F401

# screen_for_state реэкспортируется для удобства импорта в flow-файлах
screen_for_state = _screen_for_state


class WizardFlowStore:
    """
    Хранилище WizardSession на основе in-memory dict (аналог _flow в каждом flow-файле).

    Потокобезопасность: не гарантируется — аналогично существующим _flow dict'ам.
    Для продакшн-окружения с несколькими воркерами следует заменить на Redis или БД.
    """

    def __init__(self) -> None:
        self._store: Dict[int, WizardSession] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        user_id: int,
        *,
        ticket_type_id: str,
        step: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> WizardSession:
        """Создаёт новую сессию и возвращает её."""
        session = WizardSession(
            ticket_type_id=ticket_type_id,
            step=step,
            data=dict(data or {}),
        )
        self._store[user_id] = session
        return session

    def get(self, user_id: int) -> Optional[WizardSession]:
        """Возвращает текущую сессию или None."""
        return self._store.get(user_id)

    def set_step(
        self,
        user_id: int,
        step: str,
        *,
        data: Optional[Dict[str, Any]] = None,
        merge: bool = True,
    ) -> Optional[WizardSession]:
        """
        Обновляет шаг (и опционально данные) существующей сессии.

        :param merge: если True — обновляет data (dict.update); если False — заменяет.
        :return: обновлённую сессию или None, если сессии нет
        """
        session = self._store.get(user_id)
        if session is None:
            return None
        new_data = dict(session.data)
        if data:
            if merge:
                new_data.update(data)
            else:
                new_data = dict(data)
        new_session = WizardSession(
            ticket_type_id=session.ticket_type_id,
            step=step,
            data=new_data,
        )
        self._store[user_id] = new_session
        return new_session

    def update_data(self, user_id: int, **kwargs: Any) -> Optional[WizardSession]:
        """Добавляет/перезаписывает поля data без изменения шага."""
        session = self._store.get(user_id)
        if session is None:
            return None
        new_data = {**session.data, **kwargs}
        new_session = WizardSession(
            ticket_type_id=session.ticket_type_id,
            step=session.step,
            data=new_data,
        )
        self._store[user_id] = new_session
        return new_session

    def clear(self, user_id: int) -> None:
        """Удаляет сессию пользователя."""
        self._store.pop(user_id, None)

    def has(self, user_id: int) -> bool:
        """Проверяет, есть ли активная сессия."""
        return user_id in self._store

    # ------------------------------------------------------------------
    # Screen shortcut
    # ------------------------------------------------------------------

    def current_screen(self, user_id: int) -> Optional[Any]:
        """
        Возвращает WizardScreen для текущего шага сессии.

        Эквивалентно::

            session = store.get(user_id)
            screen_for_state(session.step, session.data)
        """
        session = self._store.get(user_id)
        if session is None:
            return None
        return _screen_for_state(session.step, session.data)
