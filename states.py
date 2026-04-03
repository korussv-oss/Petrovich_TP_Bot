"""
FSM-состояния для бота: регистрация, смена пароля, смена учётных данных, админ.
"""
from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    """Регистрация: ФИО, логин, почта, подразделение, телефон."""
    WAITING_FOR_FULL_NAME = State()
    WAITING_FOR_LOGIN = State()
    WAITING_FOR_EMAIL = State()
    WAITING_FOR_DEPARTMENT = State()
    WAITING_FOR_PHONE = State()


class ChangePasswordStates(StatesGroup):
    """Смена пароля: ввод нового пароля."""
    WAITING_FOR_NEW_PASSWORD = State()


class ChangeCredentialsStates(StatesGroup):
    """Смена учётных данных: те же поля, что при регистрации (включая подразделение)."""
    WAITING_FOR_FULL_NAME = State()
    WAITING_FOR_LOGIN = State()
    WAITING_FOR_EMAIL = State()
    WAITING_FOR_DEPARTMENT = State()
    WAITING_FOR_PHONE = State()


class CommentStates(StatesGroup):
    """Комментарии к заявке на смену пароля."""
    WAITING_FOR_COMMENT = State()


class AdminStates(StatesGroup):
    """Админ: удаление пользователя (список, поиск по ФИО, логин/ID)."""
    WAITING_FOR_USER_ID_OR_LOGIN = State()
    WAITING_FOR_FIO_SEARCH = State()


class WmsTicketStates(StatesGroup):
    """Заявка WMS (как the_bot_wms): подтип → подразделение → процесс → тема → описание (можно пропустить) → вложения (до 10 файлов, 10 МБ) → завершить."""
    WAITING_WMS_SUBTYPE = State()
    WAITING_FOR_DEPARTMENT = State()
    WAITING_FOR_PROCESS = State()
    WAITING_FOR_SUMMARY = State()
    WAITING_FOR_DESCRIPTION = State()
    WAITING_FOR_ATTACHMENTS = State()


class CabinetEditStates(StatesGroup):
    """Редактирование одного поля в личном кабинете."""
    WAITING_VALUE = State()


class BindAccountStates(StatesGroup):
    """Привязка аккаунта по контакту (телефон)."""
    WAITING_FOR_CONTACT = State()


class AdRegistrationStates(StatesGroup):
    """Регистрация через AD: рабочая почта → контакт (телефон) → поиск в AD по телефону."""
    WAITING_FOR_EMAIL = State()
    WAITING_FOR_CONTACT = State()


class TpSectionStates(StatesGroup):
    """Выбор раздела «Создать заявку в ТП»: запрос department_wms или employee_id при необходимости."""
    WAITING_WMS_DEPARTMENT = State()
    WAITING_EMPLOYEE_ID = State()


class TicketWizardStates(StatesGroup):
    """TicketWizard (transport-agnostic): этапы первого мигрируемого сценария wms_issue."""
    WMS_ISSUE_DEPARTMENT = State()
    WMS_ISSUE_PROCESS = State()
    WMS_ISSUE_SUMMARY = State()
    WMS_ISSUE_DESCRIPTION = State()

    # Lupa (lupa_search)
    LUPA_DEPARTMENT = State()
    LUPA_SERVICE = State()
    LUPA_REQUEST_TYPE = State()
    LUPA_CITY = State()
    LUPA_CITY_MANUAL = State()
    LUPA_DESCRIPTION = State()

    # WMS settings (wms_settings)
    WMS_SETTINGS_DEPARTMENT = State()
    WMS_SETTINGS_SERVICE_TYPE = State()
    WMS_SETTINGS_DESCRIPTION = State()
    WMS_SETTINGS_ATTACHMENTS = State()

    # PSI user (wms_psi_user)
    PSI_TITLE = State()
    PSI_FULL_NAME = State()
    PSI_DEPARTMENT = State()
    PSI_COMMENT = State()
    PSI_ATTACHMENTS = State()

    # PC problem (pc_problem)
    PC_KIND = State()
    PC_DESCRIPTION = State()
    PC_ATTACHMENTS = State()

    # Orgtech (orgtech_problem)
    ORGTECH_KIND = State()
    ORGTECH_LOCATION = State()
    ORGTECH_DESCRIPTION = State()
    ORGTECH_ATTACHMENTS = State()

    # Peripheral (peripheral_equipment)
    PERIPHERAL_KIND = State()
    PERIPHERAL_IP = State()
    PERIPHERAL_DESCRIPTION = State()
    PERIPHERAL_ATTACHMENTS = State()

    # Network (network_problem)
    NETWORK_TYPE = State()
    NETWORK_WIFI_OWNER = State()
    NETWORK_PC_TYPE = State()
    NETWORK_PROVIDER = State()
    NETWORK_PROVIDER_OTHER = State()
    NETWORK_RMS = State()
    NETWORK_DESCRIPTION = State()
    NETWORK_ATTACHMENTS = State()

    # Electronic queue
    EQUEUE_SERVICE_TYPE = State()
    EQUEUE_DESCRIPTION = State()

    # Email OWA
    EMAIL_OWA_REQUEST_KIND = State()
    EMAIL_OWA_RMS_OR_IP = State()
    EMAIL_OWA_WORKPLACE = State()
    EMAIL_OWA_DESCRIPTION = State()
    EMAIL_OWA_ATTACHMENTS = State()

    # Email forwarding
    EMAIL_FORWARDING_ON_OFF = State()
    EMAIL_FORWARDING_FROM = State()
    EMAIL_FORWARDING_TO = State()
    EMAIL_FORWARDING_DATE = State()

    # Email groups
    EMAIL_GROUPS_WHAT_TO_DO = State()
    EMAIL_GROUPS_GROUP_NAME = State()
    EMAIL_GROUPS_OWNER = State()
    EMAIL_GROUPS_MEMBERSHIP = State()
    EMAIL_GROUPS_GROUP_EMAIL = State()
    EMAIL_GROUPS_AD_LOGIN = State()
    EMAIL_GROUPS_DESCRIPTION = State()