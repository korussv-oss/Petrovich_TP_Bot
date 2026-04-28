"""
TicketWizard: транспорт-независимая (Telegram/MAX) state-machine для создания тикетов.

Модуль предоставляет:
- WizardScreen       — DTO экрана (kind, text, departments, create_ticket_payload)
- WizardEvent        — транспортно-независимое событие от пользователя
- WizardSession      — сессия пользователя (ticket_type_id, step, data)
- wizard_step()      — главная async точка входа: (session, event) → (session, screen)
- screen_for_state() — диспетчер: FSM-состояние → экран (для рендеринга без перехода)
- save_wizard_session / load_wizard_session — helpers для TG FSM
- Фабричные функции экранов для всех 10 типов тикетов
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence


@dataclass(frozen=True)
class WizardScreen:
    """
    Экран, который должен отрендерить адаптер.

    kind:
      - department_wms / department_lupa / psi_department: выбрать подразделение (пагинация)
      - process_wms: выбрать процесс WMS (кнопки)
      - summary / description: ввод текста
      - create_ticket: flow завершён — `create_ticket_payload` содержит данные для создания

    Если `create_ticket_payload` не None — адаптер должен создать тикет
    и сбросить FSM-состояние.
    """

    kind: str
    text: str
    departments: Optional[Sequence[str]] = None
    create_ticket_payload: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# WizardEvent — транспортно-независимое событие от пользователя
# ---------------------------------------------------------------------------

@dataclass
class WizardEvent:
    """
    Событие пользователя, передаваемое в wizard_step().

    Примеры (TG-адаптер)::

        # Нажатие кнопки
        WizardEvent(kind="callback", callback_id=callback.data)

        # Текстовое сообщение
        WizardEvent(kind="text", text=message.text or "")

        # Вложение (фото, видео, документ)
        WizardEvent(kind="attachment", attachments=["file_id_1"])

    Примеры (MAX-адаптер)::

        WizardEvent(kind="callback", callback_id=callback_id)
        WizardEvent(kind="text", text=text)
    """

    kind: Literal["callback", "text", "attachment"]
    callback_id: str = ""
    text: str = ""
    attachments: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# WMS: wms_issue
# ---------------------------------------------------------------------------

def wms_issue_start_screen(*, has_department_wms: bool, departments: Optional[Sequence[str]]) -> WizardScreen:
    if has_department_wms:
        return WizardScreen(
            kind="process_wms",
            text="🚨 <b>Проблема в работе WMS</b>\n\nВыберите <b>сбойный процесс</b>:",
        )
    return WizardScreen(
        kind="department_wms",
        text="🚨 <b>Проблема в работе WMS</b>\n\nВыберите ваше подразделение (оно будет сохранено в профиль):",
        departments=departments or [],
    )


def wms_issue_process_screen() -> WizardScreen:
    return WizardScreen(
        kind="process_wms",
        text="🚨 <b>Проблема в работе WMS</b>\n\nВыберите <b>сбойный процесс</b>:",
    )


def wms_issue_summary_screen() -> WizardScreen:
    return WizardScreen(
        kind="summary",
        text="🚨 <b>Проблема в работе WMS</b>\n\nВведите <b>тему</b> проблемы (кратко):",
    )


def wms_issue_description_screen() -> WizardScreen:
    return WizardScreen(
        kind="description",
        text="🚨 <b>Проблема в работе WMS</b>\n\nВведите описание проблемы:\n\nМожно пропустить, нажав кнопку ниже.",
    )


def wms_issue_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="wms_issue_attachments",
        text=(
            "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
            f"Добавлено: {added_count} из 10.\n\n"
            "Или нажмите «✅ Завершить создание задачи»."
        ),
    )


# ---------------------------------------------------------------------------
# Lupa: lupa_search
# ---------------------------------------------------------------------------

def lupa_department_screen(departments: Sequence[str]) -> WizardScreen:
    return WizardScreen(
        kind="department_lupa",
        text="🔍 <b>Создание заявки о поиске (Lupa)</b>\n\nВыберите ваше подразделение (оно будет сохранено в профиль):",
        departments=departments,
    )


def lupa_service_screen() -> WizardScreen:
    return WizardScreen(
        kind="lupa_service",
        text="🔍 <b>Создание заявки о поиске</b>\n\nШаг 1/5: Выберите проблемный сервис:",
    )


def lupa_request_type_screen(*, service: str) -> WizardScreen:
    return WizardScreen(
        kind="lupa_request_type",
        text=f"🔍 <b>Создание заявки о поиске</b>\n\n✅ Сервис: {service}\n\nШаг 2/5: Выберите тип запроса:",
    )


def lupa_city_screen(*, request_type: str, subdivision: str) -> WizardScreen:
    subdiv = subdivision or "не указано"
    return WizardScreen(
        kind="lupa_city",
        text=(
            "🔍 <b>Создание заявки о поиске</b>\n\n"
            f"✅ Тип запроса: {request_type}\n"
            f"✅ Подразделение: {subdiv}\n\n"
            "Шаг 3/5: Укажите город:"
        ),
    )


def lupa_city_manual_screen() -> WizardScreen:
    return WizardScreen(
        kind="lupa_city_manual",
        text="🔍 <b>Создание заявки о поиске</b>\n\nШаг 3/5: Введите название города:",
    )


def lupa_description_screen(*, city: str) -> WizardScreen:
    return WizardScreen(
        kind="lupa_description",
        text=(
            f"🔍 <b>Создание заявки о поиске</b>\n\n✅ Город: {city}\n\n"
            "Шаг 4/5: Введите комментарий (описание проблемы):\n\nМожно пропустить, нажав кнопку ниже."
        ),
    )


# ---------------------------------------------------------------------------
# WMS: wms_settings
# ---------------------------------------------------------------------------

def wms_settings_department_screen(departments: Sequence[str]) -> WizardScreen:
    return WizardScreen(
        kind="department_wms_settings",
        text="⚙️ <b>Изменение настроек системы WMS</b>\n\nВыберите ваше подразделение:",
        departments=departments,
    )


def wms_settings_service_type_screen() -> WizardScreen:
    return WizardScreen(
        kind="wms_settings_service_type",
        text="⚙️ <b>Изменение настроек системы WMS</b>\n\nВыберите тип услуги:",
    )


def wms_settings_description_screen() -> WizardScreen:
    return WizardScreen(
        kind="wms_settings_description",
        text="⚙️ <b>Изменение настроек системы WMS</b>\n\n📝 Введите описание изменений (или «-» для пропуска):",
    )


def wms_settings_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="wms_settings_attachments",
        text=(
            "⚙️ <b>Изменение настроек системы WMS</b>\n\n"
            f"📎 Загрузите вложения (обязательно). Добавлено: {added_count}. "
            "Затем нажмите «✅ Завершить создание задачи»."
        ),
    )


def wms_wait_products_department_screen(departments: Sequence[str]) -> WizardScreen:
    return WizardScreen(
        kind="wms_wait_products_department",
        text="📦 <b>Товары в WAIT</b>\n\nВыберите ваше подразделение WMS:",
        departments=departments,
    )


def wms_wait_products_description_screen() -> WizardScreen:
    return WizardScreen(
        kind="wms_wait_products_description",
        text="📦 <b>Товары в WAIT</b>\n\n📝 Введите описание:",
    )


# ---------------------------------------------------------------------------
# WMS: wms_psi_user
# ---------------------------------------------------------------------------

def psi_title_screen() -> WizardScreen:
    return WizardScreen(
        kind="psi_title",
        text="👤 <b>Создать/изменить/удалить пользователя PSIwms</b>\n\nВведите тему задачи (не менее 3 символов):",
    )


def psi_full_name_screen() -> WizardScreen:
    return WizardScreen(
        kind="psi_full_name",
        text="👤 Введите ФИО полностью и должность пользователя, кому нужно внести корректировки или создать учетную запись",
    )


def psi_department_screen(departments: Sequence[str]) -> WizardScreen:
    return WizardScreen(
        kind="psi_department",
        text="👤 Выберите подразделение:",
        departments=departments,
    )


def psi_comment_screen() -> WizardScreen:
    return WizardScreen(
        kind="psi_comment",
        text="👤 Что нужно сделать?",
    )


def psi_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="psi_attachments",
        text=(
            "👤 <b>Создать/изменить/удалить пользователя PSIwms</b>\n\n"
            f"📎 Вложения (опционально). Добавлено: {added_count}. "
            "Нажмите «✅ Завершить создание задачи» или «⏭ Пропустить вложения»."
        ),
    )


# ---------------------------------------------------------------------------
# PC: pc_problem
# ---------------------------------------------------------------------------

def pc_kind_screen() -> WizardScreen:
    return WizardScreen(
        kind="pc_kind",
        text="🖥️ <b>Проблема в работе ПК</b>\n\nС чем наблюдаются проблемы?",
    )


def pc_description_screen(*, kind_label: str) -> WizardScreen:
    return WizardScreen(
        kind="pc_description",
        text=(
            "🖥️ <b>Проблема в работе ПК</b>\n\n"
            f"✅ Категория: {kind_label}\n\n"
            "Опишите проблему (поле Description) или нажмите «Пропустить»."
        ),
    )


def pc_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="pc_attachments",
        text=(
            "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
            f"Добавлено: {added_count}.\n\n"
            "Или нажмите «Создать заявку» / «Пропустить вложения»."
        ),
    )


# ---------------------------------------------------------------------------
# Orgtech: orgtech_problem
# ---------------------------------------------------------------------------

def orgtech_kind_screen() -> WizardScreen:
    return WizardScreen(kind="orgtech_kind", text="🖨️ <b>Оргтехника</b>\n\nУкажите тип оргтехники:")


def orgtech_location_screen(*, kind_label: str) -> WizardScreen:
    return WizardScreen(
        kind="orgtech_location",
        text="🖨️ <b>Оргтехника</b>\n\n" f"✅ Тип: {kind_label}\n\n" "Укажите местоположение (обязательно).",
    )


def orgtech_description_screen() -> WizardScreen:
    return WizardScreen(
        kind="orgtech_description",
        text="🖨️ <b>Оргтехника</b>\n\nОпишите проблему (или нажмите «Пропустить»).",
    )


def orgtech_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="orgtech_attachments",
        text=(
            "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
            f"Добавлено: {added_count}.\n\n"
            "Или нажмите «Создать заявку» / «Пропустить вложения»."
        ),
    )


# ---------------------------------------------------------------------------
# Peripheral: peripheral_equipment
# ---------------------------------------------------------------------------

def peripheral_kind_screen() -> WizardScreen:
    return WizardScreen(kind="peripheral_kind", text="🧩 <b>Периферийное оборудование</b>\n\nВыберите вид оборудования:")


def peripheral_ip_screen(*, kind_label: str) -> WizardScreen:
    return WizardScreen(
        kind="peripheral_ip",
        text="🧩 <b>Периферийное оборудование</b>\n\n"
        f"✅ Вид оборудования: {kind_label}\n\n"
        "Укажите IP адрес (если нет, напишите «нет»).",
    )


def peripheral_description_screen() -> WizardScreen:
    return WizardScreen(kind="peripheral_description", text="🧩 <b>Периферийное оборудование</b>\n\nОпишите проблему (или нажмите «Пропустить»).")


def peripheral_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="peripheral_attachments",
        text=(
            "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
            f"Добавлено: {added_count}.\n\n"
            "Или нажмите «Создать заявку» / «Пропустить вложения»."
        ),
    )


# ---------------------------------------------------------------------------
# Network: network_problem
# ---------------------------------------------------------------------------

def network_type_screen() -> WizardScreen:
    return WizardScreen(kind="network_type", text="🌐 <b>Проблемы в работе сети</b>\n\nВыберите тип проблемной сети:")


def network_wifi_owner_screen(*, network_type: str) -> WizardScreen:
    return WizardScreen(
        kind="network_wifi_owner",
        text="🌐 <b>Проблемы в работе сети</b>\n\n" f"✅ Тип сети: {network_type}\n\n" "Укажите, у кого проблемы:",
    )


def network_pc_type_screen(*, network_type: str) -> WizardScreen:
    return WizardScreen(
        kind="network_pc_type",
        text="🌐 <b>Проблемы в работе сети</b>\n\n" f"✅ Тип сети: {network_type}\n\n" "Выберите тип ПК:",
    )


def network_provider_screen(*, network_type: str) -> WizardScreen:
    return WizardScreen(
        kind="network_provider",
        text="🌐 <b>Проблемы в работе сети</b>\n\n" f"✅ Тип сети: {network_type}\n\n" "Выберите провайдера:",
    )


def network_provider_other_screen() -> WizardScreen:
    return WizardScreen(kind="network_provider_other", text="Укажите название поставщика услуг (поле Other):")


def network_rms_screen() -> WizardScreen:
    return WizardScreen(kind="network_rms", text="Укажите RMS Internet ID (опционально) или нажмите «Пропустить».")


def network_description_screen() -> WizardScreen:
    return WizardScreen(kind="network_description", text="Опишите проблему (или нажмите «Пропустить»).")


def network_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="network_attachments",
        text=(
            "📎 Приложите фото, видео или документы (до 10 файлов, до 10 МБ каждый).\n\n"
            f"Добавлено: {added_count}.\n\n"
            "Или нажмите «Создать заявку» / «Пропустить вложения»."
        ),
    )


# ---------------------------------------------------------------------------
# Electronic queue
# ---------------------------------------------------------------------------

def equeue_service_type_screen() -> WizardScreen:
    return WizardScreen(kind="equeue_service", text="🎫 <b>Электронная очередь</b>\n\nВыберите тип услуги:")


def equeue_description_screen() -> WizardScreen:
    return WizardScreen(kind="equeue_description", text="🎫 <b>Электронная очередь</b>\n\nОпишите проблему:")


# ---------------------------------------------------------------------------
# Email OWA/Outlook
# ---------------------------------------------------------------------------

def email_owa_request_kind_screen() -> WizardScreen:
    return WizardScreen(kind="email_owa_request_kind", text="📨 <b>Электронная почта (Owa\\Outlook)</b>\n\nВыберите ваш запрос:")


def email_owa_rms_or_ip_screen(*, request_kind: str) -> WizardScreen:
    return WizardScreen(
        kind="email_owa_rms_or_ip",
        text=f"📨 <b>Электронная почта (Owa\\Outlook)</b>\n\n✅ Запрос: {request_kind}\n\nУкажите RMS или IP:",
    )


def email_owa_workplace_screen() -> WizardScreen:
    return WizardScreen(kind="email_owa_workplace", text="Укажите номер или местоположение рабочего места (опционально) или нажмите «Пропустить».")


def email_owa_description_screen() -> WizardScreen:
    return WizardScreen(kind="email_owa_description", text="Введите подробное описание проблемы:")


def email_owa_attachments_screen(*, added_count: int = 0) -> WizardScreen:
    return WizardScreen(
        kind="email_owa_attachments",
        text=f"📎 Приложите фото/видео/документы (опционально). Добавлено: {added_count}. Или нажмите «Создать заявку».",
    )


# ===========================================================================
# WizardSession — транспортно-независимая сессия пользователя
# ===========================================================================

@dataclass
class WizardSession:
    """
    Транспортно-независимая сессия пользователя в TicketWizard.

    - TG-адаптер: сохраняй через save_wizard_session(), читай через load_wizard_session()
    - MAX-адаптер: используй напрямую вместо сырых dict'ов в _flow[user_id]
    """

    ticket_type_id: str                            # "wms_issue", "lupa_search", "pc_issue", …
    step: str                                       # имя TicketWizardStates (напр. "WMS_ISSUE_SUMMARY")
    data: Dict[str, Any] = field(default_factory=dict)  # собранные ответы пользователя


# Ключи для хранения метаданных сессии в aiogram FSMContext.data
_WZ_TYPE_KEY = "_wz_type"
_WZ_STEP_KEY = "_wz_step"


def save_wizard_session(session: WizardSession) -> Dict[str, Any]:
    """
    Возвращает dict для передачи в FSMContext.update_data().

    Пример::

        await state.update_data(**save_wizard_session(WizardSession("wms_issue", "WMS_ISSUE_SUMMARY")))
    """
    return {_WZ_TYPE_KEY: session.ticket_type_id, _WZ_STEP_KEY: session.step}


def load_wizard_session(fsm_data: Dict[str, Any]) -> Optional[WizardSession]:
    """
    Восстанавливает WizardSession из aiogram FSMContext.get_data().

    Возвращает None, если сессия ещё не была инициализирована.
    """
    ticket_type_id = fsm_data.get(_WZ_TYPE_KEY)
    step = fsm_data.get(_WZ_STEP_KEY)
    if not ticket_type_id or not step:
        return None
    user_data = {k: v for k, v in fsm_data.items() if k not in (_WZ_TYPE_KEY, _WZ_STEP_KEY)}
    return WizardSession(ticket_type_id=ticket_type_id, step=step, data=user_data)


# ===========================================================================
# Диспетчер: FSM-состояние → WizardScreen
# Ключ — строка имени состояния TicketWizardStates (e.g. "WMS_ISSUE_SUMMARY")
# Значение — функция (data: dict) → WizardScreen
# ===========================================================================

_STATE_SCREEN_MAP: Dict[str, Callable[[Dict[str, Any]], WizardScreen]] = {
    # --- WMS Issue ---
    "WMS_ISSUE_DEPARTMENT": lambda d: wms_issue_start_screen(
        has_department_wms=bool(d.get("department")),
        departments=d.get("departments"),
    ),
    "WMS_ISSUE_PROCESS": lambda _: wms_issue_process_screen(),
    "WMS_ISSUE_SUMMARY": lambda _: wms_issue_summary_screen(),
    "WMS_ISSUE_DESCRIPTION": lambda _: wms_issue_description_screen(),
    "WMS_ISSUE_ATTACHMENTS": lambda d: wms_issue_attachments_screen(
        added_count=len(d.get("wms_attachment_file_ids") or d.get("attachments") or []),
    ),

    # --- Lupa ---
    "LUPA_SERVICE": lambda _: lupa_service_screen(),
    "LUPA_REQUEST_TYPE": lambda d: lupa_request_type_screen(service=d.get("service", "")),
    "LUPA_CITY": lambda d: lupa_city_screen(
        request_type=d.get("request_type", ""),
        subdivision=d.get("subdivision", ""),
    ),
    "LUPA_CITY_MANUAL": lambda _: lupa_city_manual_screen(),
    "LUPA_DESCRIPTION": lambda d: lupa_description_screen(city=d.get("city", "")),
    "LUPA_DEPARTMENT": lambda d: lupa_department_screen(departments=d.get("departments") or []),

    # --- WMS Settings ---
    "WMS_SETTINGS_DEPARTMENT": lambda d: wms_settings_department_screen(
        departments=d.get("departments") or []
    ),
    "WMS_SETTINGS_SERVICE_TYPE": lambda _: wms_settings_service_type_screen(),
    "WMS_SETTINGS_DESCRIPTION": lambda _: wms_settings_description_screen(),
    "WMS_SETTINGS_ATTACHMENTS": lambda d: wms_settings_attachments_screen(
        added_count=len(d.get("attachments") or [])
    ),

    # --- WMS Товары в WAIT ---
    "WMS_WAIT_PRODUCTS_DEPARTMENT": lambda d: wms_wait_products_department_screen(
        departments=d.get("departments") or []
    ),
    "WMS_WAIT_PRODUCTS_DESCRIPTION": lambda _: wms_wait_products_description_screen(),

    # --- WMS PSI ---
    "PSI_TITLE": lambda _: psi_title_screen(),
    "PSI_FULL_NAME": lambda _: psi_full_name_screen(),
    "PSI_DEPARTMENT": lambda d: psi_department_screen(departments=d.get("departments") or []),
    "PSI_COMMENT": lambda _: psi_comment_screen(),
    "PSI_ATTACHMENTS": lambda d: psi_attachments_screen(
        added_count=len(d.get("attachments") or [])
    ),

    # --- PC Issue ---
    "PC_KIND": lambda _: pc_kind_screen(),
    "PC_DESCRIPTION": lambda d: pc_description_screen(kind_label=d.get("kind_label", "")),
    "PC_ATTACHMENTS": lambda d: pc_attachments_screen(
        added_count=len(d.get("attachments") or [])
    ),

    # --- Orgtech ---
    "ORGTECH_KIND": lambda _: orgtech_kind_screen(),
    "ORGTECH_LOCATION": lambda d: orgtech_location_screen(kind_label=d.get("kind_label", "")),
    "ORGTECH_DESCRIPTION": lambda _: orgtech_description_screen(),
    "ORGTECH_ATTACHMENTS": lambda d: orgtech_attachments_screen(
        added_count=len(d.get("attachments") or [])
    ),

    # --- Peripheral Equipment ---
    "PERIPHERAL_KIND": lambda _: peripheral_kind_screen(),
    "PERIPHERAL_IP": lambda d: peripheral_ip_screen(kind_label=d.get("kind_label", "")),
    "PERIPHERAL_DESCRIPTION": lambda _: peripheral_description_screen(),
    "PERIPHERAL_ATTACHMENTS": lambda d: peripheral_attachments_screen(
        added_count=len(d.get("attachments") or [])
    ),

    # --- Network ---
    "NETWORK_TYPE": lambda _: network_type_screen(),
    "NETWORK_WIFI_OWNER": lambda d: network_wifi_owner_screen(
        network_type=d.get("network_type", "")
    ),
    "NETWORK_PC_TYPE": lambda d: network_pc_type_screen(
        network_type=d.get("network_type", "")
    ),
    "NETWORK_PROVIDER": lambda d: network_provider_screen(
        network_type=d.get("network_type", "")
    ),
    "NETWORK_PROVIDER_OTHER": lambda _: network_provider_other_screen(),
    "NETWORK_RMS": lambda _: network_rms_screen(),
    "NETWORK_DESCRIPTION": lambda _: network_description_screen(),
    "NETWORK_ATTACHMENTS": lambda d: network_attachments_screen(
        added_count=len(d.get("attachments") or [])
    ),

    # --- Electronic Queue ---
    "EQUEUE_SERVICE_TYPE": lambda _: equeue_service_type_screen(),
    "EQUEUE_DESCRIPTION": lambda _: equeue_description_screen(),

    # --- Email OWA/Outlook ---
    "EMAIL_OWA_REQUEST_KIND": lambda _: email_owa_request_kind_screen(),
    "EMAIL_OWA_RMS_OR_IP": lambda d: email_owa_rms_or_ip_screen(
        request_kind=d.get("request_kind", "")
    ),
    "EMAIL_OWA_WORKPLACE": lambda _: email_owa_workplace_screen(),
    "EMAIL_OWA_DESCRIPTION": lambda _: email_owa_description_screen(),
    "EMAIL_OWA_ATTACHMENTS": lambda d: email_owa_attachments_screen(
        added_count=len(d.get("attachments") or [])
    ),
    # Email Forwarding и Email Groups пока не имеют фабрик экранов.
    # При добавлении — зарегистрировать здесь по аналогии.
}


def screen_for_state(
    state_key: str,
    data: Optional[Dict[str, Any]] = None,
) -> Optional[WizardScreen]:
    """
    Возвращает WizardScreen для текущего шага TicketWizard.

    :param state_key: строка-имя состояния TicketWizardStates
                      (напр. ``"WMS_ISSUE_SUMMARY"``, ``"PC_DESCRIPTION"``)
    :param data: словарь текущих данных сессии (из FSMContext.get_data() или WizardSession.data)
    :return: WizardScreen или None, если состояние не зарегистрировано в диспетчере

    Пример (TG-адаптер)::

        data = await state.get_data()
        screen = screen_for_state("WMS_ISSUE_SUMMARY", data)
        if screen:
            await message.answer(screen.text, parse_mode="HTML")

    Пример (MAX-адаптер)::

        session = _flow[user_id]        # WizardSession
        screen = screen_for_state(session.step, session.data)
        return {"text": screen.text, "parse_mode": "HTML", "buttons": [...]}
    """
    factory = _STATE_SCREEN_MAP.get(state_key)
    if factory is None:
        return None
    return factory(data or {})


# ===========================================================================
# wizard_step() — главная async точка входа
# ===========================================================================

async def wizard_step(
    session: WizardSession,
    event: WizardEvent,
    *,
    profile: Optional[Dict[str, Any]] = None,
) -> tuple[WizardSession, WizardScreen]:
    """
    Транспортно-независимая машина состояний TicketWizard.

    Получает текущую сессию + событие от пользователя.
    Возвращает новую сессию (с обновлённым step/data) + экран для рендеринга.

    Если ``screen.create_ticket_payload is not None`` — адаптер должен создать тикет,
    очистить FSM-контекст и показать пользователю подтверждение.

    Пример (TG-адаптер)::

        event = WizardEvent(kind="callback", callback_id=callback.data)
        new_session, screen = await wizard_step(session, event, profile=user_profile)
        if screen.create_ticket_payload:
            await create_jira_ticket(screen.create_ticket_payload)
            await state.clear()
        else:
            await state.set_state(TicketWizardStates[new_session.step])
            await state.update_data(**save_wizard_session(new_session))
            await message.edit_text(screen.text, ...)
    """
    _handlers: Dict[str, Any] = {
        "wms_issue":          _wstep_wms_issue,
        "wms_settings":       _wstep_wms_settings,
        "wms_psi_user":       _wstep_wms_psi,
        "lupa_search":        _wstep_lupa,
        "pc_problem":         _wstep_pc,
        "orgtech_problem":    _wstep_orgtech,
        "peripheral_equipment": _wstep_peripheral,
        "network_problem":    _wstep_network,
        "email_owa_outlook":  _wstep_email_owa,
        "equeue":             _wstep_equeue,
    }
    fn = _handlers.get(session.ticket_type_id)
    if fn is None:
        raise ValueError(f"wizard_step: unknown ticket_type_id={session.ticket_type_id!r}")
    return await fn(session, event, profile or {})


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _next(session: WizardSession, step: str, **extra_data: Any) -> WizardSession:
    """Создаёт новую сессию с обновлённым шагом и слиянием данных."""
    new_data = dict(session.data)
    new_data.update(extra_data)
    return WizardSession(ticket_type_id=session.ticket_type_id, step=step, data=new_data)


def _same(session: WizardSession, **extra_data: Any) -> WizardSession:
    """Создаёт новую сессию с тем же шагом (обновление данных без перехода)."""
    return _next(session, session.step, **extra_data)


def _done(session: WizardSession, payload: Dict[str, Any]) -> tuple[WizardSession, WizardScreen]:
    """Возвращает финальный результат — создать тикет и завершить flow."""
    finished = WizardSession(ticket_type_id=session.ticket_type_id, step="DONE", data={})
    return finished, WizardScreen(kind="create_ticket", text="", create_ticket_payload=payload)


# ---------------------------------------------------------------------------
# wms_issue
# ---------------------------------------------------------------------------

async def _wstep_wms_issue(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.wms_constants import WMS_PROCESSES
    step = session.step
    d = session.data

    if step == "WMS_ISSUE_DEPARTMENT":
        if event.kind == "callback":
            if event.callback_id.startswith("wms_dept_page_"):
                page = int(event.callback_id.replace("wms_dept_page_", ""))
                depts = d.get("departments") or []
                safe = max(0, min(page, (len(depts) - 1) // 8 if depts else 0))
                return _same(session, dept_page=safe), wms_issue_start_screen(
                    has_department_wms=False, departments=depts
                )
            if event.callback_id.startswith("wms_dept_"):
                idx = int(event.callback_id.replace("wms_dept_", ""))
                depts = d.get("departments") or []
                dept = depts[idx]
                ns = _next(session, "WMS_ISSUE_PROCESS", department=dept, department_wms=dept)
                return ns, wms_issue_process_screen()

    if step == "WMS_ISSUE_PROCESS":
        if event.kind == "callback" and event.callback_id.startswith("wms_process_"):
            key = event.callback_id.replace("wms_process_", "", 1)
            process = WMS_PROCESSES.get(key)
            if process:
                return _next(session, "WMS_ISSUE_SUMMARY", process=process), wms_issue_summary_screen()

    if step == "WMS_ISSUE_SUMMARY" and event.kind == "text" and event.text.strip():
        return _next(session, "WMS_ISSUE_DESCRIPTION", summary=event.text.strip()), wms_issue_description_screen()

    if step == "WMS_ISSUE_DESCRIPTION":
        skip = event.kind == "callback" and event.callback_id == "wms_skip_description"
        if skip or (event.kind == "text" and event.text.strip()):
            desc = "" if skip else event.text.strip()
            ns = _next(session, "WMS_ISSUE_ATTACHMENTS", description=desc, attachments=[])
            return ns, wms_issue_attachments_screen(added_count=0)

    if step == "WMS_ISSUE_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, wms_issue_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id == "wms_finish_ticket":
            dept = d.get("department") or d.get("department_wms") or profile.get("department_wms", "")
            return _done(session, {
                "ticket_type_id": "wms_issue",
                "form_data": {
                    "department": dept,
                    "process": d.get("process", ""),
                    "summary": d.get("summary", ""),
                    "description": d.get("description", ""),
                },
                "attachment_tokens": list(d.get("attachments") or []),
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# wms_settings
# ---------------------------------------------------------------------------

async def _wstep_wms_settings(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.wms_constants import WMS_SERVICE_TYPES
    step = session.step
    d = session.data

    if step == "WMS_SETTINGS_DEPARTMENT":
        if event.kind == "callback":
            if event.callback_id.startswith("wms_dept_page_"):
                page = int(event.callback_id.replace("wms_dept_page_", ""))
                depts = d.get("departments") or []
                return _same(session, dept_page=page), wms_settings_department_screen(depts)
            if event.callback_id.startswith("wms_dept_"):
                idx = int(event.callback_id.replace("wms_dept_", ""))
                depts = d.get("departments") or []
                dept = depts[idx]
                return _next(session, "WMS_SETTINGS_SERVICE_TYPE", department=dept), wms_settings_service_type_screen()

    if step == "WMS_SETTINGS_SERVICE_TYPE":
        if event.kind == "callback" and event.callback_id in ("wms_service_topology", "wms_service_other"):
            stype = WMS_SERVICE_TYPES.get(event.callback_id, "")
            return _next(session, "WMS_SETTINGS_DESCRIPTION", service_type=stype), wms_settings_description_screen()

    if step == "WMS_SETTINGS_DESCRIPTION" and event.kind == "text":
        desc = event.text.strip() if event.text.strip() != "—" else ""
        ns = _next(session, "WMS_SETTINGS_ATTACHMENTS", description=desc, attachments=[])
        return ns, wms_settings_attachments_screen(added_count=0)

    if step == "WMS_SETTINGS_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, wms_settings_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id == "finish_wms_settings":
            tokens = list(d.get("attachments") or [])
            if not tokens:
                return session, wms_settings_attachments_screen(added_count=0)
            dept = d.get("department") or profile.get("department_wms", "")
            return _done(session, {
                "ticket_type_id": "wms_settings",
                "form_data": {
                    "department": dept,
                    "service_type": d.get("service_type", ""),
                    "description": d.get("description", "") or "-",
                },
                "attachment_tokens": tokens,
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# wms_psi_user
# ---------------------------------------------------------------------------

async def _wstep_wms_psi(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    step = session.step
    d = session.data

    if step == "PSI_TITLE":
        if event.kind == "text":
            t = event.text.strip()
            if len(t) < 3:
                return session, WizardScreen(kind="psi_title", text="Тема не менее 3 символов. Введите тему:")
            return _next(session, "PSI_FULL_NAME", summary=t), psi_full_name_screen()

    if step == "PSI_FULL_NAME" and event.kind == "text":
        full_name = event.text.strip()
        dept_wms = profile.get("department_wms", "")
        if dept_wms:
            return _next(session, "PSI_COMMENT", full_name=full_name, department=dept_wms), psi_comment_screen()
        from core.jira_wms_departments import get_wms_departments_async
        depts = await get_wms_departments_async() or []
        ns = _next(session, "PSI_DEPARTMENT", full_name=full_name, departments=depts, dept_page=0)
        if not depts:
            return ns, WizardScreen(kind="error", text="Список подразделений недоступен. Попробуйте позже.")
        return ns, psi_department_screen(depts)

    if step == "PSI_DEPARTMENT":
        if event.kind == "callback":
            if event.callback_id.startswith("wms_dept_page_"):
                page = int(event.callback_id.replace("wms_dept_page_", ""))
                depts = d.get("departments") or []
                return _same(session, dept_page=page), psi_department_screen(depts)
            if event.callback_id.startswith("wms_dept_"):
                idx = int(event.callback_id.replace("wms_dept_", ""))
                depts = d.get("departments") or []
                dept = depts[idx]
                return _next(session, "PSI_COMMENT", department=dept), psi_comment_screen()

    if step == "PSI_COMMENT" and event.kind == "text":
        comment = event.text.strip() if event.text.strip() != "—" else ""
        ns = _next(session, "PSI_ATTACHMENTS", comment=comment, attachments=[])
        return ns, psi_attachments_screen(added_count=0)

    if step == "PSI_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, psi_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id in ("finish_psi_user", "skip_psi_attachment"):
            tokens = list(d.get("attachments") or []) if event.callback_id == "finish_psi_user" else []
            dept = d.get("department") or profile.get("department_wms", "") or profile.get("department", "")
            full_name = d.get("full_name", "")
            if not full_name:
                return session, WizardScreen(kind="error", text="Ошибка: не указаны ФИО и должность.")
            return _done(session, {
                "ticket_type_id": "wms_psi_user",
                "form_data": {
                    "summary": d.get("summary", ""),
                    "full_name": full_name,
                    "department": dept,
                    "comment": d.get("comment", ""),
                },
                "attachment_tokens": tokens,
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# lupa_search
# ---------------------------------------------------------------------------

async def _wstep_lupa(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.lupa_constants import LUPA_SERVICE_VALUES, LUPA_REQUEST_TYPE_VALUES  # noqa: PLC0415
    step = session.step
    d = session.data

    if step == "LUPA_DEPARTMENT":
        if event.kind == "callback":
            if event.callback_id.startswith("lupa_dept_page_"):
                page = int(event.callback_id.replace("lupa_dept_page_", ""))
                depts = d.get("departments") or []
                return _same(session, dept_page=page), lupa_department_screen(depts)
            if event.callback_id.startswith("lupa_dept_"):
                idx = int(event.callback_id.replace("lupa_dept_", ""))
                depts = d.get("departments") or []
                dept = depts[idx]
                return _next(session, "LUPA_SERVICE", subdivision=dept), lupa_service_screen()

    if step == "LUPA_SERVICE" and event.kind == "callback" and event.callback_id in LUPA_SERVICE_VALUES:
        service = LUPA_SERVICE_VALUES[event.callback_id]
        subdiv = d.get("subdivision") or profile.get("department", "")
        return _next(session, "LUPA_REQUEST_TYPE", problematic_service=service, subdivision=subdiv), \
               lupa_request_type_screen(service=service)

    if step == "LUPA_REQUEST_TYPE" and event.kind == "callback" and event.callback_id in LUPA_REQUEST_TYPE_VALUES:
        rtype = LUPA_REQUEST_TYPE_VALUES[event.callback_id]
        subdiv = d.get("subdivision") or profile.get("department", "")
        return _next(session, "LUPA_CITY", request_type=rtype, subdivision=subdiv), \
               lupa_city_screen(request_type=rtype, subdivision=subdiv)

    if step == "LUPA_CITY" and event.kind == "callback" and event.callback_id.startswith("lupa_city_"):
        if event.callback_id == "lupa_city_manual":
            return _next(session, "LUPA_CITY_MANUAL"), lupa_city_manual_screen()
        city = event.callback_id.replace("lupa_city_", "", 1).replace("_", " ")
        return _next(session, "LUPA_DESCRIPTION", city=city), lupa_description_screen(city=city)

    if step == "LUPA_CITY_MANUAL" and event.kind == "text" and event.text.strip():
        city = event.text.strip()
        return _next(session, "LUPA_DESCRIPTION", city=city), lupa_description_screen(city=city)

    if step == "LUPA_DESCRIPTION":
        skip = event.kind == "callback" and event.callback_id == "lupa_skip_comment"
        if skip or (event.kind == "text"):
            desc = "" if skip else event.text.strip()
            subdiv = d.get("subdivision") or profile.get("department", "")
            return _done(session, {
                "ticket_type_id": "lupa_search",
                "form_data": {
                    "description": desc,
                    "problematic_service": d.get("problematic_service", ""),
                    "request_type": d.get("request_type", ""),
                    "subdivision": subdiv,
                    "city": d.get("city", ""),
                },
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# pc_problem
# ---------------------------------------------------------------------------

async def _wstep_pc(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.pc_problem import PC_PROBLEM_KIND_BY_ID
    step = session.step
    d = session.data

    if step == "PC_KIND" and event.kind == "callback" and event.callback_id.startswith("pc_kind_"):
        kind_id = event.callback_id.replace("pc_kind_", "", 1).strip()
        label = PC_PROBLEM_KIND_BY_ID.get(kind_id)
        if label:
            return _next(session, "PC_DESCRIPTION", pc_problem_kind_id=kind_id, kind_label=label), \
                   pc_description_screen(kind_label=label)

    if step == "PC_DESCRIPTION":
        skip = event.kind == "callback" and event.callback_id == "pc_skip_description"
        if skip or (event.kind == "text"):
            desc = "" if skip else event.text.strip()
            ns = _next(session, "PC_ATTACHMENTS", description=desc, attachments=[])
            return ns, pc_attachments_screen(added_count=0)

    if step == "PC_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, pc_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id in ("pc_finish_ticket", "pc_skip_attachments"):
            tokens = list(d.get("attachments") or []) if event.callback_id == "pc_finish_ticket" else []
            return _done(session, {
                "ticket_type_id": "pc_problem",
                "form_data": {
                    "pc_problem_kind_id": d.get("pc_problem_kind_id", ""),
                    "description": d.get("description", ""),
                },
                "attachment_tokens": tokens,
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# orgtech_problem
# ---------------------------------------------------------------------------

async def _wstep_orgtech(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.orgtech import ORGTECH_KIND_BY_ID
    step = session.step
    d = session.data

    if step == "ORGTECH_KIND" and event.kind == "callback" and event.callback_id.startswith("orgtech_kind_"):
        kind_id = event.callback_id.replace("orgtech_kind_", "", 1).strip()
        label = ORGTECH_KIND_BY_ID.get(kind_id)
        if label:
            return _next(session, "ORGTECH_LOCATION", orgtech_kind=label, kind_label=label), \
                   orgtech_location_screen(kind_label=label)

    if step == "ORGTECH_LOCATION" and event.kind == "text" and event.text.strip():
        return _next(session, "ORGTECH_DESCRIPTION", location=event.text.strip()), orgtech_description_screen()

    if step == "ORGTECH_DESCRIPTION":
        skip = event.kind == "callback" and event.callback_id == "orgtech_skip_description"
        if skip or (event.kind == "text"):
            desc = "" if skip else event.text.strip()
            ns = _next(session, "ORGTECH_ATTACHMENTS", description=desc, attachments=[])
            return ns, orgtech_attachments_screen(added_count=0)

    if step == "ORGTECH_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, orgtech_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id in ("orgtech_finish_ticket", "orgtech_skip_attachments"):
            tokens = list(d.get("attachments") or []) if event.callback_id == "orgtech_finish_ticket" else []
            return _done(session, {
                "ticket_type_id": "orgtech_problem",
                "form_data": {
                    "orgtech_kind": d.get("orgtech_kind", ""),
                    "location": d.get("location", ""),
                    "description": d.get("description", ""),
                },
                "attachment_tokens": tokens,
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# peripheral_equipment
# ---------------------------------------------------------------------------

async def _wstep_peripheral(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.peripheral_equipment import PERIPHERAL_KIND_BY_ID
    step = session.step
    d = session.data

    if step == "PERIPHERAL_KIND" and event.kind == "callback" and event.callback_id.startswith("peripheral_kind_"):
        kind_id = event.callback_id.replace("peripheral_kind_", "", 1).strip()
        label = PERIPHERAL_KIND_BY_ID.get(kind_id)
        if label:
            return _next(session, "PERIPHERAL_IP", peripheral_kind=label, kind_label=label), \
                   peripheral_ip_screen(kind_label=label)

    if step == "PERIPHERAL_IP" and event.kind == "text" and event.text.strip():
        return _next(session, "PERIPHERAL_DESCRIPTION", ip_address=event.text.strip()), \
               peripheral_description_screen()

    if step == "PERIPHERAL_DESCRIPTION":
        skip = event.kind == "callback" and event.callback_id == "peripheral_skip_description"
        if skip or (event.kind == "text"):
            desc = "" if skip else event.text.strip()
            ns = _next(session, "PERIPHERAL_ATTACHMENTS", description=desc, attachments=[])
            return ns, peripheral_attachments_screen(added_count=0)

    if step == "PERIPHERAL_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, peripheral_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id in ("peripheral_finish_ticket", "peripheral_skip_attachments"):
            tokens = list(d.get("attachments") or []) if event.callback_id == "peripheral_finish_ticket" else []
            return _done(session, {
                "ticket_type_id": "peripheral_equipment",
                "form_data": {
                    "peripheral_kind": d.get("peripheral_kind", ""),
                    "ip_address": d.get("ip_address", ""),
                    "description": d.get("description", ""),
                },
                "attachment_tokens": tokens,
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# network_problem
# ---------------------------------------------------------------------------

async def _wstep_network(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.network_problem import (
        NETWORK_TYPE_BY_ID, NETWORK_WIFI_OWNER_BY_ID,
        NETWORK_PC_TYPE_BY_ID, NETWORK_PROVIDER_BY_ID,
    )
    step = session.step
    d = session.data

    if step == "NETWORK_TYPE" and event.kind == "callback" and event.callback_id.startswith("network_type_"):
        t_id = event.callback_id.replace("network_type_", "", 1).strip()
        t_label = NETWORK_TYPE_BY_ID.get(t_id)
        if t_label:
            base = {"network_type": t_label, "provider": "", "provider_other": "", "wifi_problem_owner": "", "pc_type": ""}
            if t_label == "Wi-Fi (беспроводная)":
                return _next(session, "NETWORK_WIFI_OWNER", **base), network_wifi_owner_screen(network_type=t_label)
            if t_label == "VPN":
                return _next(session, "NETWORK_PC_TYPE", **base), network_pc_type_screen(network_type=t_label)
            return _next(session, "NETWORK_PROVIDER", **base), network_provider_screen(network_type=t_label)

    if step == "NETWORK_WIFI_OWNER" and event.kind == "callback" and event.callback_id.startswith("network_wifi_owner_"):
        o_id = event.callback_id.replace("network_wifi_owner_", "", 1).strip()
        o_label = NETWORK_WIFI_OWNER_BY_ID.get(o_id)
        if o_label:
            return _next(session, "NETWORK_RMS", wifi_problem_owner=o_label), network_rms_screen()

    if step == "NETWORK_PC_TYPE" and event.kind == "callback" and event.callback_id.startswith("network_pc_type_"):
        p_id = event.callback_id.replace("network_pc_type_", "", 1).strip()
        p_label = NETWORK_PC_TYPE_BY_ID.get(p_id)
        if p_label:
            nt = d.get("network_type", "")
            return _next(session, "NETWORK_PROVIDER", pc_type=p_label), network_provider_screen(network_type=nt)

    if step == "NETWORK_PROVIDER" and event.kind == "callback" and event.callback_id.startswith("network_provider_"):
        pr_id = event.callback_id.replace("network_provider_", "", 1).strip()
        pr_label = NETWORK_PROVIDER_BY_ID.get(pr_id)
        if pr_label:
            if pr_label == "Другой":
                return _next(session, "NETWORK_PROVIDER_OTHER", provider=pr_label), network_provider_other_screen()
            return _next(session, "NETWORK_RMS", provider=pr_label, provider_other=""), network_rms_screen()

    if step == "NETWORK_PROVIDER_OTHER" and event.kind == "text" and event.text.strip():
        return _next(session, "NETWORK_RMS", provider_other=event.text.strip()), network_rms_screen()

    if step == "NETWORK_RMS":
        skip = event.kind == "callback" and event.callback_id == "network_skip_rms"
        if skip or (event.kind == "text"):
            rms = "нет" if skip else (event.text.strip() or "нет")
            return _next(session, "NETWORK_DESCRIPTION", rms_internet_id=rms), network_description_screen()

    if step == "NETWORK_DESCRIPTION":
        skip = event.kind == "callback" and event.callback_id == "network_skip_description"
        if skip or (event.kind == "text"):
            desc = "" if skip else event.text.strip()
            ns = _next(session, "NETWORK_ATTACHMENTS", description=desc, attachments=[])
            return ns, network_attachments_screen(added_count=0)

    if step == "NETWORK_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, network_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id in ("network_finish_ticket", "network_skip_attachments"):
            tokens = list(d.get("attachments") or []) if event.callback_id == "network_finish_ticket" else []
            return _done(session, {
                "ticket_type_id": "network_problem",
                "form_data": {
                    "network_type": d.get("network_type", ""),
                    "provider": d.get("provider", ""),
                    "provider_other": d.get("provider_other", ""),
                    "wifi_problem_owner": d.get("wifi_problem_owner", ""),
                    "pc_type": d.get("pc_type", ""),
                    "description": d.get("description", ""),
                    "rms_internet_id": d.get("rms_internet_id", "нет") or "нет",
                    "ip_address": "нет",
                    "preferred_contact_time": "нет",
                },
                "attachment_tokens": tokens,
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# email_owa_outlook
# ---------------------------------------------------------------------------

async def _wstep_email_owa(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    from core.email_owa import EMAIL_OWA_KIND_BY_ID
    step = session.step
    d = session.data

    if step == "EMAIL_OWA_REQUEST_KIND" and event.kind == "callback" and event.callback_id in EMAIL_OWA_KIND_BY_ID:
        kind_label = EMAIL_OWA_KIND_BY_ID[event.callback_id]
        return _next(session, "EMAIL_OWA_RMS_OR_IP", request_kind=kind_label), \
               email_owa_rms_or_ip_screen(request_kind=kind_label)

    if step == "EMAIL_OWA_RMS_OR_IP" and event.kind == "text" and event.text.strip():
        return _next(session, "EMAIL_OWA_WORKPLACE", rms_or_ip=event.text.strip()), email_owa_workplace_screen()

    if step == "EMAIL_OWA_WORKPLACE":
        skip = event.kind == "callback" and event.callback_id == "email_owa_skip_workplace"
        if skip or (event.kind == "text"):
            wp = "" if skip else event.text.strip()
            return _next(session, "EMAIL_OWA_DESCRIPTION", workplace=wp), email_owa_description_screen()

    if step == "EMAIL_OWA_DESCRIPTION" and event.kind == "text" and event.text.strip():
        desc = event.text.strip()
        ns = _next(session, "EMAIL_OWA_ATTACHMENTS", description=desc, attachments=[])
        return ns, email_owa_attachments_screen(added_count=0)

    if step == "EMAIL_OWA_ATTACHMENTS":
        if event.kind == "attachment":
            cur = list(d.get("attachments") or [])
            cur.extend(event.attachments)
            ns = _same(session, attachments=cur)
            return ns, email_owa_attachments_screen(added_count=len(cur))
        if event.kind == "callback" and event.callback_id in ("email_owa_finish_ticket", "email_owa_skip_attachments"):
            tokens = list(d.get("attachments") or []) if event.callback_id == "email_owa_finish_ticket" else []
            return _done(session, {
                "ticket_type_id": "email_owa_outlook",
                "form_data": {
                    "request_kind": d.get("request_kind", ""),
                    "rms_or_ip": d.get("rms_or_ip", ""),
                    "workplace": d.get("workplace", ""),
                    "description": d.get("description", ""),
                },
                "attachment_tokens": tokens,
            })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")


# ---------------------------------------------------------------------------
# equeue
# ---------------------------------------------------------------------------

async def _wstep_equeue(
    session: WizardSession, event: WizardEvent, profile: Dict[str, Any]
) -> tuple[WizardSession, WizardScreen]:
    step = session.step
    d = session.data

    if step == "EQUEUE_SERVICE_TYPE" and event.kind == "callback" and event.callback_id.startswith("equeue_service_"):
        stype = event.callback_id.replace("equeue_service_", "", 1)
        return _next(session, "EQUEUE_DESCRIPTION", service_type=stype), equeue_description_screen()

    if step == "EQUEUE_DESCRIPTION" and event.kind == "text" and event.text.strip():
        desc = event.text.strip()
        return _done(session, {
            "ticket_type_id": "equeue",
            "form_data": {
                "service_type": d.get("service_type", ""),
                "description": desc,
            },
        })

    return session, WizardScreen(kind="error", text="Неизвестное событие. Используйте кнопки.")

