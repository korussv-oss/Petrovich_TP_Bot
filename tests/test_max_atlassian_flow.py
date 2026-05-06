import asyncio
import unittest
from unittest.mock import patch

from adapters.max import atlassian_flow


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestMaxAtlassianFlow(unittest.TestCase):
    def setUp(self):
        atlassian_flow._flow.clear()

    def test_start_requires_registration(self):
        with patch("adapters.max.atlassian_flow.is_user_registered", return_value=False):
            response = run(atlassian_flow.start_atlassian(1001))
        self.assertIsNone(response)

    def test_full_happy_path_returns_create_ticket_payload(self):
        user_id = 1002
        with patch("adapters.max.atlassian_flow.is_user_registered", return_value=True):
            start_resp = run(atlassian_flow.start_atlassian(user_id))

        self.assertIsNotNone(start_resp)
        self.assertIn("С каким сервисом проблема?", start_resp["text"])

        callback_resp = run(
            atlassian_flow.handle_atlassian_callback(user_id, "atlassian_service_jira")
        )
        self.assertIsNotNone(callback_resp)
        self.assertIn("✅ Сервис: Jira", callback_resp["text"])

        message_resp = run(
            atlassian_flow.handle_atlassian_message(
                user_id,
                "Нужна помощь с доступами в Jira.",
            )
        )
        self.assertIsNotNone(message_resp)
        self.assertIn("Прикрепите файлы", message_resp["text"])

        finish_resp = run(atlassian_flow.handle_atlassian_callback(user_id, "atlassian_finish_ticket"))
        payload = (finish_resp or {}).get("create_ticket") or {}
        self.assertEqual(payload.get("ticket_type_id"), "atlassian_support")
        self.assertEqual(payload.get("form_data", {}).get("summary"), "Запрос созданный через Бот ТП")
        self.assertEqual(payload.get("form_data", {}).get("service_name"), "Jira")
        self.assertEqual(
            payload.get("form_data", {}).get("description"),
            "Нужна помощь с доступами в Jira.",
        )

    def test_empty_description_rejected(self):
        user_id = 1003
        with patch("adapters.max.atlassian_flow.is_user_registered", return_value=True):
            run(atlassian_flow.start_atlassian(user_id))
        run(atlassian_flow.handle_atlassian_callback(user_id, "atlassian_service_confluence"))

        response = run(atlassian_flow.handle_atlassian_message(user_id, "   "))
        self.assertEqual(response["text"], "Описание не может быть пустым.")
