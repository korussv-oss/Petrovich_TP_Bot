import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))

from core.support.api import create_ticket


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAtlassianSupportCreateTicket(unittest.TestCase):
    def test_create_atlassian_support_success(self):
        create_mock = AsyncMock(return_value=(True, "ISR-123", "ISR"))
        fake_engine = types.SimpleNamespace(create_issue_from_form=create_mock)
        with (
            patch("user_storage.get_user_profile", return_value={"jira_username": "i.ivanov"}) as profile_mock,
            patch("core.support.issue_binding_registry.add_binding") as add_binding_mock,
            patch.dict(sys.modules, {"core.jira_form_engine": fake_engine}),
        ):
            ok, issue_key, msg = run(
                create_ticket(
                    "telegram",
                    42,
                    "atlassian_support",
                    {
                        "service_name": "Jira",
                        "description": "Нужна помощь с правами в проекте.",
                    },
                )
            )

        self.assertTrue(ok)
        self.assertEqual(issue_key, "ISR-123")
        self.assertIn("ISR-123", msg or "")
        profile_mock.assert_called_once_with(42, "telegram")
        add_binding_mock.assert_called_once_with("telegram", 42, "ISR-123", "ISR", "atlassian_support")

        args, kwargs = create_mock.call_args
        self.assertEqual(args[0], "atlassian_support")
        self.assertEqual(kwargs["form_data"]["summary"], "Запрос созданный через Бот ТП")
        self.assertEqual(kwargs["form_data"]["service_name"], "Jira")
        self.assertEqual(
            kwargs["form_data"]["description"],
            "Нужна помощь с правами в проекте.",
        )

    def test_create_atlassian_support_propagates_engine_error(self):
        create_mock = AsyncMock(return_value=(False, "Ошибка Jira", None))
        fake_engine = types.SimpleNamespace(create_issue_from_form=create_mock)
        with (
            patch("user_storage.get_user_profile", return_value={}),
            patch("core.support.issue_binding_registry.add_binding") as add_binding_mock,
            patch.dict(sys.modules, {"core.jira_form_engine": fake_engine}),
        ):
            ok, err, issue_key = run(
                create_ticket(
                    "max",
                    7,
                    "atlassian_support",
                    {"service_name": "Confluence", "description": "Не открывается space."},
                )
            )

        self.assertFalse(ok)
        self.assertEqual(err, "Ошибка Jira")
        self.assertIsNone(issue_key)
        add_binding_mock.assert_not_called()
