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


class WmsSettingsStates(StatesGroup):
    """Изменение настроек системы WMS: подразделение → тип услуги (топология/другие) → описание → вложения (обязательно) → завершить."""
    WAITING_DEPARTMENT = State()
    WAITING_SERVICE_TYPE = State()
    WAITING_DESCRIPTION = State()
    WAITING_ATTACHMENTS = State()


class PsiUserStates(StatesGroup):
    """Создать/изменить/удалить пользователя PSIwms: тема → ФИО+должность → подразделение → комментарий → вложения (опционально) → завершить."""
    WAITING_TITLE = State()
    WAITING_FULL_NAME = State()
    WAITING_DEPARTMENT = State()
    WAITING_COMMENT = State()
    WAITING_ATTACHMENTS = State()


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


class LupaTicketStates(StatesGroup):
    """Заявка Lupa (как the_bot_lupa): подразделение (если нет в профиле) → сервис → тип запроса → город → комментарий."""
    WAITING_FOR_DEPARTMENT = State()  # запрос подразделения из Jira, если в профиле пусто
    SELECT_PROBLEMATIC_SERVICE = State()
    SELECT_REQUEST_TYPE = State()
    ENTER_CITY = State()
    ENTER_CITY_MANUAL = State()  # ввод города текстом после «Ввести вручную»
    WAITING_FOR_DESCRIPTION = State()  # комментарий (можно пропустить)


class PcIssueStates(StatesGroup):
    """Заявка «Проблема в работе ПК»: категория проблемы → описание (можно пропустить) → вложения (можно пропустить)."""
    WAITING_FOR_KIND = State()
    WAITING_FOR_DESCRIPTION = State()
    WAITING_FOR_ATTACHMENTS = State()


class EmailOwaStates(StatesGroup):
    """Заявка «Электронная почта (Owa\\Outlook)»: запрос → RMS/IP → рабочее место (опц.) → описание → вложения (опц.)."""
    WAITING_FOR_REQUEST_KIND = State()
    WAITING_FOR_RMS_OR_IP = State()
    WAITING_FOR_WORKPLACE = State()
    WAITING_FOR_DESCRIPTION = State()
    WAITING_FOR_ATTACHMENTS = State()


class OrgtechIssueStates(StatesGroup):
    """Заявка «Оргтехника»: тип → местоположение → описание (опц.) → вложения (опц.)."""
    WAITING_FOR_KIND = State()
    WAITING_FOR_LOCATION = State()
    WAITING_FOR_DESCRIPTION = State()
    WAITING_FOR_ATTACHMENTS = State()


class PeripheralEquipmentStates(StatesGroup):
    """Заявка «Периферийное оборудование»: вид → IP (обяз.) → описание (опц.) → вложения (опц.)."""
    WAITING_FOR_KIND = State()
    WAITING_FOR_IP = State()
    WAITING_FOR_DESCRIPTION = State()
    WAITING_FOR_ATTACHMENTS = State()


class NetworkIssueStates(StatesGroup):
    """Заявка «Проблемы в работе сети» с ветвлением по типу сети."""
    WAITING_FOR_NETWORK_TYPE = State()
    WAITING_FOR_PROVIDER = State()
    WAITING_FOR_PROVIDER_OTHER = State()
    WAITING_FOR_WIFI_OWNER = State()
    WAITING_FOR_PC_TYPE = State()
    WAITING_FOR_RMS = State()
    WAITING_FOR_DESCRIPTION = State()
    WAITING_FOR_ATTACHMENTS = State()


class ElectronicQueueStates(StatesGroup):
    """Заявка «Электронная очередь»: тип услуги -> описание."""
    WAITING_FOR_SERVICE_TYPE = State()
    WAITING_FOR_DESCRIPTION = State()


class EmailForwardingStates(StatesGroup):
    """Переадресация: on/off -> from -> to -> date."""
    WAITING_FOR_ON_OFF = State()
    WAITING_FOR_EMAIL_FROM = State()
    WAITING_FOR_EMAIL_TO = State()
    WAITING_FOR_DATE = State()


class EmailGroupsStates(StatesGroup):
    """Группы рассылки: what_to_do -> ветвление по сценарию."""
    WAITING_FOR_WHAT_TO_DO = State()
    WAITING_FOR_GROUP_NAME = State()
    WAITING_FOR_GROUP_OWNER = State()
    WAITING_FOR_GROUP_MEMBERSHIP = State()
    WAITING_FOR_GROUP_EMAIL = State()
    WAITING_FOR_AD_LOGIN = State()
    WAITING_FOR_DESCRIPTION = State()
