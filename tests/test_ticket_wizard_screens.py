"""
Tests for core/support/ticket_wizard.py

Coverage:
- Every screen function returns a WizardScreen with correct `kind` and non-empty `text`
- Text interpolation: context variables appear in the rendered text
- Attachment counter: added_count is reflected in the text
- Branching logic: wms_issue_start_screen with / without saved department
- departments field is populated when required
- WizardScreen is immutable (frozen dataclass)
"""
import unittest
from dataclasses import FrozenInstanceError

from core.support import ticket_wizard
from core.support.ticket_wizard import WizardScreen


class TestWizardScreenDataclass(unittest.TestCase):
    """WizardScreen structure and immutability."""

    def test_is_wizard_screen_instance(self):
        screen = ticket_wizard.wms_issue_summary_screen()
        self.assertIsInstance(screen, WizardScreen)

    def test_frozen_raises_on_mutation(self):
        screen = ticket_wizard.wms_issue_summary_screen()
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            screen.text = "hacked"

    def test_default_departments_is_none(self):
        screen = ticket_wizard.wms_issue_summary_screen()
        self.assertIsNone(screen.departments)


# ---------------------------------------------------------------------------
# WMS Issue
# ---------------------------------------------------------------------------

class TestWmsIssueScreens(unittest.TestCase):

    def test_start_with_department_returns_process_screen(self):
        screen = ticket_wizard.wms_issue_start_screen(has_department_wms=True, departments=None)
        self.assertEqual(screen.kind, "process_wms")
        self.assertIsNone(screen.departments)
        self.assertIn("WMS", screen.text)

    def test_start_without_department_returns_department_screen(self):
        deps = ["Склад А", "Склад Б"]
        screen = ticket_wizard.wms_issue_start_screen(has_department_wms=False, departments=deps)
        self.assertEqual(screen.kind, "department_wms")
        self.assertEqual(list(screen.departments), deps)
        self.assertIn("подразделение", screen.text)

    def test_start_without_department_empty_list_gives_empty_departments(self):
        screen = ticket_wizard.wms_issue_start_screen(has_department_wms=False, departments=None)
        self.assertEqual(screen.kind, "department_wms")
        self.assertEqual(list(screen.departments), [])

    def test_process_screen(self):
        screen = ticket_wizard.wms_issue_process_screen()
        self.assertEqual(screen.kind, "process_wms")
        self.assertIn("процесс", screen.text.lower())

    def test_summary_screen(self):
        screen = ticket_wizard.wms_issue_summary_screen()
        self.assertEqual(screen.kind, "summary")
        self.assertIn("тему", screen.text.lower())

    def test_description_screen(self):
        screen = ticket_wizard.wms_issue_description_screen()
        self.assertEqual(screen.kind, "description")
        self.assertIn("описание", screen.text.lower())


# ---------------------------------------------------------------------------
# WMS Settings
# ---------------------------------------------------------------------------

class TestWmsSettingsScreens(unittest.TestCase):

    def test_department_screen_has_departments(self):
        deps = ["ОП-1", "ОП-2"]
        screen = ticket_wizard.wms_settings_department_screen(deps)
        self.assertEqual(screen.kind, "department_wms_settings")
        self.assertEqual(list(screen.departments), deps)

    def test_service_type_screen(self):
        screen = ticket_wizard.wms_settings_service_type_screen()
        self.assertEqual(screen.kind, "wms_settings_service_type")
        self.assertTrue(screen.text)

    def test_description_screen(self):
        screen = ticket_wizard.wms_settings_description_screen()
        self.assertEqual(screen.kind, "wms_settings_description")
        self.assertIn("описание", screen.text.lower())

    def test_attachments_screen_zero(self):
        screen = ticket_wizard.wms_settings_attachments_screen(added_count=0)
        self.assertEqual(screen.kind, "wms_settings_attachments")
        self.assertIn("0", screen.text)

    def test_attachments_screen_nonzero(self):
        screen = ticket_wizard.wms_settings_attachments_screen(added_count=3)
        self.assertIn("3", screen.text)


class TestWmsWaitProductsScreens(unittest.TestCase):

    def test_department_screen(self):
        deps = ["Склад А", "Склад Б"]
        screen = ticket_wizard.wms_wait_products_department_screen(deps)
        self.assertEqual(screen.kind, "wms_wait_products_department")
        self.assertEqual(list(screen.departments), deps)

    def test_description_screen(self):
        screen = ticket_wizard.wms_wait_products_description_screen()
        self.assertEqual(screen.kind, "wms_wait_products_description")
        self.assertIn("описание", screen.text.lower())


# ---------------------------------------------------------------------------
# PSI User
# ---------------------------------------------------------------------------

class TestPsiUserScreens(unittest.TestCase):

    def test_title_screen(self):
        screen = ticket_wizard.psi_title_screen()
        self.assertEqual(screen.kind, "psi_title")
        self.assertIn("PSI", screen.text)

    def test_full_name_screen(self):
        screen = ticket_wizard.psi_full_name_screen()
        self.assertEqual(screen.kind, "psi_full_name")
        self.assertIn("ФИО", screen.text)

    def test_department_screen_has_departments(self):
        deps = ["Логистика", "Финансы"]
        screen = ticket_wizard.psi_department_screen(deps)
        self.assertEqual(screen.kind, "psi_department")
        self.assertEqual(list(screen.departments), deps)

    def test_comment_screen(self):
        screen = ticket_wizard.psi_comment_screen()
        self.assertEqual(screen.kind, "psi_comment")
        self.assertTrue(screen.text)

    def test_attachments_screen_count_reflected(self):
        screen = ticket_wizard.psi_attachments_screen(added_count=5)
        self.assertEqual(screen.kind, "psi_attachments")
        self.assertIn("5", screen.text)

    def test_attachments_screen_default_count(self):
        screen = ticket_wizard.psi_attachments_screen()
        self.assertIn("0", screen.text)


# ---------------------------------------------------------------------------
# Lupa
# ---------------------------------------------------------------------------

class TestLupaScreens(unittest.TestCase):

    def test_department_screen(self):
        deps = ["Регион 1"]
        screen = ticket_wizard.lupa_department_screen(deps)
        self.assertEqual(screen.kind, "department_lupa")
        self.assertEqual(list(screen.departments), deps)

    def test_service_screen(self):
        screen = ticket_wizard.lupa_service_screen()
        self.assertEqual(screen.kind, "lupa_service")
        self.assertIn("сервис", screen.text.lower())

    def test_request_type_screen_interpolates_service(self):
        screen = ticket_wizard.lupa_request_type_screen(service="ПСМ")
        self.assertEqual(screen.kind, "lupa_request_type")
        self.assertIn("ПСМ", screen.text)

    def test_city_screen_interpolates_request_type_and_subdivision(self):
        screen = ticket_wizard.lupa_city_screen(request_type="Поиск", subdivision="МСК")
        self.assertEqual(screen.kind, "lupa_city")
        self.assertIn("Поиск", screen.text)
        self.assertIn("МСК", screen.text)

    def test_city_screen_fallback_when_subdivision_empty(self):
        screen = ticket_wizard.lupa_city_screen(request_type="Поиск", subdivision="")
        self.assertIn("не указано", screen.text)

    def test_city_manual_screen(self):
        screen = ticket_wizard.lupa_city_manual_screen()
        self.assertEqual(screen.kind, "lupa_city_manual")
        self.assertIn("город", screen.text.lower())

    def test_description_screen_interpolates_city(self):
        screen = ticket_wizard.lupa_description_screen(city="Москва")
        self.assertEqual(screen.kind, "lupa_description")
        self.assertIn("Москва", screen.text)


# ---------------------------------------------------------------------------
# PC Problem
# ---------------------------------------------------------------------------

class TestPcScreens(unittest.TestCase):

    def test_kind_screen(self):
        screen = ticket_wizard.pc_kind_screen()
        self.assertEqual(screen.kind, "pc_kind")
        self.assertIn("ПК", screen.text)

    def test_description_screen_interpolates_kind(self):
        screen = ticket_wizard.pc_description_screen(kind_label="Монитор")
        self.assertEqual(screen.kind, "pc_description")
        self.assertIn("Монитор", screen.text)

    def test_attachments_screen_count(self):
        screen = ticket_wizard.pc_attachments_screen(added_count=2)
        self.assertEqual(screen.kind, "pc_attachments")
        self.assertIn("2", screen.text)


# ---------------------------------------------------------------------------
# Orgtech
# ---------------------------------------------------------------------------

class TestOrgtechScreens(unittest.TestCase):

    def test_kind_screen(self):
        screen = ticket_wizard.orgtech_kind_screen()
        self.assertEqual(screen.kind, "orgtech_kind")
        self.assertTrue(screen.text)

    def test_location_screen_interpolates_kind(self):
        screen = ticket_wizard.orgtech_location_screen(kind_label="Принтер")
        self.assertEqual(screen.kind, "orgtech_location")
        self.assertIn("Принтер", screen.text)

    def test_description_screen(self):
        screen = ticket_wizard.orgtech_description_screen()
        self.assertEqual(screen.kind, "orgtech_description")
        self.assertTrue(screen.text)

    def test_attachments_screen_count(self):
        screen = ticket_wizard.orgtech_attachments_screen(added_count=1)
        self.assertEqual(screen.kind, "orgtech_attachments")
        self.assertIn("1", screen.text)


# ---------------------------------------------------------------------------
# Peripheral Equipment
# ---------------------------------------------------------------------------

class TestPeripheralScreens(unittest.TestCase):

    def test_kind_screen(self):
        screen = ticket_wizard.peripheral_kind_screen()
        self.assertEqual(screen.kind, "peripheral_kind")
        self.assertTrue(screen.text)

    def test_ip_screen_interpolates_kind(self):
        screen = ticket_wizard.peripheral_ip_screen(kind_label="Мышь")
        self.assertEqual(screen.kind, "peripheral_ip")
        self.assertIn("Мышь", screen.text)

    def test_description_screen(self):
        screen = ticket_wizard.peripheral_description_screen()
        self.assertEqual(screen.kind, "peripheral_description")
        self.assertTrue(screen.text)

    def test_attachments_screen_count(self):
        screen = ticket_wizard.peripheral_attachments_screen(added_count=4)
        self.assertEqual(screen.kind, "peripheral_attachments")
        self.assertIn("4", screen.text)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

class TestNetworkScreens(unittest.TestCase):

    def test_type_screen(self):
        screen = ticket_wizard.network_type_screen()
        self.assertEqual(screen.kind, "network_type")
        self.assertIn("сет", screen.text.lower())  # "сети" / "сеть" — общий корень

    def test_wifi_owner_screen_interpolates_type(self):
        screen = ticket_wizard.network_wifi_owner_screen(network_type="Wi-Fi")
        self.assertEqual(screen.kind, "network_wifi_owner")
        self.assertIn("Wi-Fi", screen.text)

    def test_pc_type_screen_interpolates_type(self):
        screen = ticket_wizard.network_pc_type_screen(network_type="LAN")
        self.assertEqual(screen.kind, "network_pc_type")
        self.assertIn("LAN", screen.text)

    def test_provider_screen_interpolates_type(self):
        screen = ticket_wizard.network_provider_screen(network_type="WAN")
        self.assertEqual(screen.kind, "network_provider")
        self.assertIn("WAN", screen.text)

    def test_provider_other_screen(self):
        screen = ticket_wizard.network_provider_other_screen()
        self.assertEqual(screen.kind, "network_provider_other")
        self.assertTrue(screen.text)

    def test_rms_screen(self):
        screen = ticket_wizard.network_rms_screen()
        self.assertEqual(screen.kind, "network_rms")
        self.assertIn("RMS", screen.text)

    def test_description_screen(self):
        screen = ticket_wizard.network_description_screen()
        self.assertEqual(screen.kind, "network_description")
        self.assertTrue(screen.text)

    def test_attachments_screen_count(self):
        screen = ticket_wizard.network_attachments_screen(added_count=7)
        self.assertEqual(screen.kind, "network_attachments")
        self.assertIn("7", screen.text)


# ---------------------------------------------------------------------------
# Electronic Queue
# ---------------------------------------------------------------------------

class TestEqueueScreens(unittest.TestCase):

    def test_service_type_screen(self):
        screen = ticket_wizard.equeue_service_type_screen()
        self.assertEqual(screen.kind, "equeue_service")
        self.assertIn("очередь", screen.text.lower())

    def test_description_screen(self):
        screen = ticket_wizard.equeue_description_screen()
        self.assertEqual(screen.kind, "equeue_description")
        self.assertTrue(screen.text)


# ---------------------------------------------------------------------------
# Email OWA/Outlook
# ---------------------------------------------------------------------------

class TestEmailOwaScreens(unittest.TestCase):

    def test_request_kind_screen(self):
        screen = ticket_wizard.email_owa_request_kind_screen()
        self.assertEqual(screen.kind, "email_owa_request_kind")
        self.assertIn("почта", screen.text.lower())

    def test_rms_or_ip_screen_interpolates_request_kind(self):
        screen = ticket_wizard.email_owa_rms_or_ip_screen(request_kind="Настройка")
        self.assertEqual(screen.kind, "email_owa_rms_or_ip")
        self.assertIn("RMS", screen.text)
        self.assertIn("Настройка", screen.text)

    def test_workplace_screen(self):
        screen = ticket_wizard.email_owa_workplace_screen()
        self.assertEqual(screen.kind, "email_owa_workplace")
        self.assertTrue(screen.text)

    def test_description_screen(self):
        screen = ticket_wizard.email_owa_description_screen()
        self.assertEqual(screen.kind, "email_owa_description")
        self.assertTrue(screen.text)

    def test_attachments_screen_zero(self):
        screen = ticket_wizard.email_owa_attachments_screen(added_count=0)
        self.assertEqual(screen.kind, "email_owa_attachments")
        self.assertIn("0", screen.text)

    def test_attachments_screen_nonzero(self):
        screen = ticket_wizard.email_owa_attachments_screen(added_count=2)
        self.assertIn("2", screen.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
