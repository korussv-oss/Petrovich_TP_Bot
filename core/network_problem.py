"""Константы сценария «Проблемы в работе сети» (из forms_catalog)."""

from core.forms_catalog import get_form_definition


def _load_options(select_id: str, fallback: list[tuple[str, str]]) -> list[tuple[str, str]]:
    form = get_form_definition("network_problem") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get(select_id) or {}).get("options") or []
    out: list[tuple[str, str]] = []
    for o in options:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or "").strip()
        label = str(o.get("label") or "").strip()
        if oid and label:
            out.append((oid, label))
    return out or fallback


NETWORK_TYPES = _load_options(
    "network_type",
    [
        ("network_local", "Локальная сеть (проводная)"),
        ("network_wifi", "Wi-Fi (беспроводная)"),
        ("network_vpn", "VPN"),
    ],
)
NETWORK_TYPE_BY_ID = {k: v for k, v in NETWORK_TYPES}

NETWORK_PROVIDERS = _load_options(
    "provider",
    [("provider_petrovich", "Сеть Петрович"), ("provider_other", "Другой")],
)
NETWORK_PROVIDER_BY_ID = {k: v for k, v in NETWORK_PROVIDERS}

NETWORK_WIFI_OWNERS = _load_options(
    "wifi_problem_owner",
    [("wifi_owner_employee", "Сотрудник Петрович"), ("wifi_owner_client", "Клиент")],
)
NETWORK_WIFI_OWNER_BY_ID = {k: v for k, v in NETWORK_WIFI_OWNERS}

NETWORK_PC_TYPES = _load_options(
    "pc_type",
    [("pc_type_personal", "Личный ПК"), ("pc_type_corporate", "Корпоративный (офисный) ПК")],
)
NETWORK_PC_TYPE_BY_ID = {k: v for k, v in NETWORK_PC_TYPES}
