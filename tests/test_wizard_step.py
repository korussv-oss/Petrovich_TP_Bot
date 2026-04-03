"""
Тесты wizard_step() — переходы состояний и валидации.
Не требуют запущенного бота или Jira.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.support.ticket_wizard import (
    WizardEvent,
    WizardScreen,
    WizardSession,
    wizard_step,
)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestWizardStepPC(unittest.TestCase):
    """pc_problem: полный happy-path."""

    def _session(self, step, **data):
        return WizardSession("pc_problem", step, data)

    def test_kind_callback_transitions_to_description(self):
        session = self._session("PC_KIND")
        event = WizardEvent(kind="callback", callback_id="pc_kind_11733")  # "Не включается"
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "PC_DESCRIPTION")
        self.assertEqual(screen.kind, "pc_description")
        self.assertIsNone(screen.create_ticket_payload)

    def test_unknown_kind_stays_on_kind(self):
        session = self._session("PC_KIND")
        event = WizardEvent(kind="callback", callback_id="pc_kind_9999999")  # несуществующий ID
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(screen.kind, "error")

    def test_description_skip_transitions_to_attachments(self):
        session = self._session("PC_DESCRIPTION", pc_problem_kind_id="laptop", kind_label="Ноутбук")
        event = WizardEvent(kind="callback", callback_id="pc_skip_description")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "PC_ATTACHMENTS")
        self.assertEqual(screen.kind, "pc_attachments")

    def test_description_text_transitions_to_attachments(self):
        session = self._session("PC_DESCRIPTION", pc_problem_kind_id="laptop", kind_label="Ноутбук")
        event = WizardEvent(kind="text", text="Не включается")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "PC_ATTACHMENTS")
        self.assertEqual(new_s.data["description"], "Не включается")

    def test_attachment_accumulates(self):
        session = self._session("PC_ATTACHMENTS", description="Не включается", attachments=["file1"])
        event = WizardEvent(kind="attachment", attachments=["file2"])
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "PC_ATTACHMENTS")
        self.assertEqual(new_s.data["attachments"], ["file1", "file2"])
        self.assertIn("2", screen.text)

    def test_finish_creates_ticket_payload(self):
        session = self._session(
            "PC_ATTACHMENTS",
            pc_problem_kind_id="laptop",
            description="Не включается",
            attachments=["file1"],
        )
        event = WizardEvent(kind="callback", callback_id="pc_finish_ticket")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "DONE")
        self.assertIsNotNone(screen.create_ticket_payload)
        self.assertEqual(screen.create_ticket_payload["ticket_type_id"], "pc_problem")
        self.assertEqual(screen.create_ticket_payload["attachment_tokens"], ["file1"])

    def test_skip_attachments_creates_ticket_no_attachments(self):
        session = self._session("PC_ATTACHMENTS", pc_problem_kind_id="laptop", description="", attachments=[])
        event = WizardEvent(kind="callback", callback_id="pc_skip_attachments")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(screen.create_ticket_payload["attachment_tokens"], [])


class TestWizardStepOrgtech(unittest.TestCase):
    def _session(self, step, **data):
        return WizardSession("orgtech_problem", step, data)

    def test_kind_callback_transitions(self):
        session = self._session("ORGTECH_KIND")
        event = WizardEvent(kind="callback", callback_id="orgtech_kind_orgtech_printer")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "ORGTECH_LOCATION")
        self.assertEqual(screen.kind, "orgtech_location")

    def test_location_text_required(self):
        session = self._session("ORGTECH_LOCATION", orgtech_kind="Принтер", kind_label="Принтер")
        event = WizardEvent(kind="text", text="Офис 301")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "ORGTECH_DESCRIPTION")

    def test_finish_creates_payload(self):
        session = self._session(
            "ORGTECH_ATTACHMENTS",
            orgtech_kind="Принтер", location="301", description="", attachments=[]
        )
        event = WizardEvent(kind="callback", callback_id="orgtech_finish_ticket")
        new_s, screen = run(wizard_step(session, event))
        self.assertIsNotNone(screen.create_ticket_payload)
        self.assertEqual(screen.create_ticket_payload["form_data"]["location"], "301")


class TestWizardStepPeripheral(unittest.TestCase):
    def _session(self, step, **data):
        return WizardSession("peripheral_equipment", step, data)

    def test_kind_transitions_to_ip(self):
        session = self._session("PERIPHERAL_KIND")
        event = WizardEvent(kind="callback", callback_id="peripheral_kind_peripheral_keyboard")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "PERIPHERAL_IP")

    def test_ip_transitions_to_description(self):
        session = self._session("PERIPHERAL_IP", peripheral_kind="Сканер", kind_label="Сканер")
        event = WizardEvent(kind="text", text="192.168.1.100")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "PERIPHERAL_DESCRIPTION")
        self.assertEqual(new_s.data["ip_address"], "192.168.1.100")


class TestWizardStepEmailOWA(unittest.TestCase):
    def _session(self, step, **data):
        return WizardSession("email_owa_outlook", step, data)

    def test_request_kind_callback(self):
        session = self._session("EMAIL_OWA_REQUEST_KIND")
        event = WizardEvent(kind="callback", callback_id="email_owa_req_create")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "EMAIL_OWA_RMS_OR_IP")
        self.assertEqual(screen.kind, "email_owa_rms_or_ip")

    def test_finish_creates_payload(self):
        session = self._session(
            "EMAIL_OWA_ATTACHMENTS",
            request_kind="Создание почтового ящика",
            rms_or_ip="RMS001",
            workplace="",
            description="Нужна почта",
            attachments=[],
        )
        event = WizardEvent(kind="callback", callback_id="email_owa_finish_ticket")
        new_s, screen = run(wizard_step(session, event))
        self.assertIsNotNone(screen.create_ticket_payload)
        fd = screen.create_ticket_payload["form_data"]
        self.assertEqual(fd["rms_or_ip"], "RMS001")
        self.assertEqual(fd["description"], "Нужна почта")


class TestWizardStepNetwork(unittest.TestCase):
    def _session(self, step, **data):
        return WizardSession("network_problem", step, data)

    def test_wifi_type_leads_to_owner(self):
        session = self._session("NETWORK_TYPE")
        event = WizardEvent(kind="callback", callback_id="network_type_network_wifi")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "NETWORK_WIFI_OWNER")

    def test_unknown_type_returns_error(self):
        session = self._session("NETWORK_TYPE")
        event = WizardEvent(kind="callback", callback_id="network_type_zzz_unknown")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(screen.kind, "error")

    def test_rms_skip_leads_to_description(self):
        session = self._session("NETWORK_RMS", network_type="Wi-Fi (беспроводная)",
                                wifi_problem_owner="Мне одному")
        event = WizardEvent(kind="callback", callback_id="network_skip_rms")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "NETWORK_DESCRIPTION")
        self.assertEqual(new_s.data["rms_internet_id"], "нет")


class TestWizardStepEqueue(unittest.TestCase):
    def _session(self, step, **data):
        return WizardSession("equeue", step, data)

    def test_service_type_leads_to_description(self):
        session = self._session("EQUEUE_SERVICE_TYPE")
        event = WizardEvent(kind="callback", callback_id="equeue_service_cashier")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "EQUEUE_DESCRIPTION")

    def test_description_creates_payload(self):
        session = self._session("EQUEUE_DESCRIPTION", service_type="cashier")
        event = WizardEvent(kind="text", text="Очередь не работает")
        new_s, screen = run(wizard_step(session, event))
        self.assertIsNotNone(screen.create_ticket_payload)
        self.assertEqual(screen.create_ticket_payload["form_data"]["description"], "Очередь не работает")


class TestWizardStepPSI(unittest.TestCase):
    def _session(self, step, **data):
        return WizardSession("wms_psi_user", step, data)

    def test_title_too_short_stays(self):
        session = self._session("PSI_TITLE")
        event = WizardEvent(kind="text", text="ab")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "PSI_TITLE")
        self.assertEqual(screen.kind, "psi_title")

    def test_title_ok_transitions(self):
        session = self._session("PSI_TITLE")
        event = WizardEvent(kind="text", text="Создать пользователя Иванов")
        new_s, screen = run(wizard_step(session, event, profile={"department_wms": "МСК"}))
        self.assertEqual(new_s.step, "PSI_FULL_NAME")

    def test_full_name_with_dept_in_profile_skips_dept_step(self):
        session = self._session("PSI_FULL_NAME", summary="Создать")
        event = WizardEvent(kind="text", text="Иванов И.И., кладовщик")
        profile = {"department_wms": "МСК-Склад"}
        new_s, screen = run(wizard_step(session, event, profile=profile))
        self.assertEqual(new_s.step, "PSI_COMMENT")


class TestWizardStepWmsIssue(unittest.TestCase):
    def _session(self, step, **data):
        return WizardSession("wms_issue", step, data)

    def test_dept_select(self):
        depts = ["МСК-Склад", "СПБ-Склад"]
        session = self._session("WMS_ISSUE_DEPARTMENT", departments=depts, dept_page=0)
        event = WizardEvent(kind="callback", callback_id="wms_dept_0")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "WMS_ISSUE_PROCESS")
        self.assertEqual(new_s.data["department"], "МСК-Склад")

    def test_process_select_transitions_to_summary(self):
        from core.wms_constants import WMS_PROCESSES
        first_key = next(iter(WMS_PROCESSES))
        session = self._session("WMS_ISSUE_PROCESS", department="МСК")
        event = WizardEvent(kind="callback", callback_id=f"wms_process_{first_key}")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "WMS_ISSUE_SUMMARY")

    def test_summary_text_transitions(self):
        session = self._session("WMS_ISSUE_SUMMARY", department="МСК", process="Приёмка")
        event = WizardEvent(kind="text", text="Ошибка при сканировании")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "WMS_ISSUE_DESCRIPTION")
        self.assertEqual(new_s.data["summary"], "Ошибка при сканировании")

    def test_description_skip(self):
        session = self._session("WMS_ISSUE_DESCRIPTION", summary="Проблема")
        event = WizardEvent(kind="callback", callback_id="wms_skip_description")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "WMS_ISSUE_ATTACHMENTS")
        self.assertEqual(new_s.data["description"], "")

    def test_finish_creates_payload(self):
        session = self._session(
            "WMS_ISSUE_ATTACHMENTS",
            department="МСК", process="Приёмка",
            summary="Ошибка", description="", attachments=[]
        )
        event = WizardEvent(kind="callback", callback_id="wms_finish_ticket")
        new_s, screen = run(wizard_step(session, event))
        self.assertEqual(new_s.step, "DONE")
        self.assertIsNotNone(screen.create_ticket_payload)
        fd = screen.create_ticket_payload["form_data"]
        self.assertEqual(fd["department"], "МСК")
        self.assertEqual(fd["process"], "Приёмка")


if __name__ == "__main__":
    unittest.main()
