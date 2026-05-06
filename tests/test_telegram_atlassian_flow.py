import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from states import TicketWizardStates


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestTelegramAtlassianFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Совместимость тестового окружения с декораторами в handlers/create_ticket.py.
        if not hasattr(TicketWizardStates, "any"):
            TicketWizardStates.any = classmethod(lambda _cls: "*")  # type: ignore[attr-defined]
        cls.module = importlib.import_module("handlers.create_ticket")

    def _callback(self, user_id: int, data: str) -> SimpleNamespace:
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=user_id),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

    def _message(self, user_id: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(
            text=text,
            from_user=SimpleNamespace(id=user_id),
            answer=AsyncMock(),
            reply=AsyncMock(),
        )

    def test_start_sets_state_and_shows_service_choices(self):
        callback = self._callback(501, "tp_atlassian_start")
        state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())
        with patch("handlers.create_ticket.is_user_registered", return_value=True):
            run(self.module.tp_atlassian_start(callback, state))

        state.clear.assert_awaited_once()
        state.set_state.assert_awaited_once_with(TicketWizardStates.ATLASSIAN_SERVICE)
        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("Техническая поддержка Atlassian", text)
        callback.answer.assert_awaited_once()

    def test_service_selection_transitions_to_description(self):
        callback = self._callback(502, "atlassian_service:jira")
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

        run(self.module.atlassian_service_selected(callback, state))

        state.update_data.assert_awaited_once_with(atlassian_service_name="Jira")
        state.set_state.assert_awaited_once_with(TicketWizardStates.ATLASSIAN_DESCRIPTION)
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("Сервис: Jira", text)

    def test_description_transitions_to_optional_attachments(self):
        message = self._message(503, "Нужны права на проект в Jira.")
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"atlassian_service_name": "Jira"}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )

        run(self.module.atlassian_description(message, state))

        state.update_data.assert_awaited_once()
        state.set_state.assert_awaited_once_with(TicketWizardStates.ATLASSIAN_ATTACHMENTS)
        payload = state.update_data.await_args.kwargs.get("atlassian_form_data") or {}
        self.assertEqual(payload["summary"], "Запрос созданный через Бот ТП")
        self.assertEqual(payload["service_name"], "Jira")
        self.assertEqual(payload["description"], "Нужны права на проект в Jira.")
        message.answer.assert_awaited_once()
