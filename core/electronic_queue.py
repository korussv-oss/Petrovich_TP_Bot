"""Константы сценария «Электронная очередь» (из forms_catalog)."""

from core.forms_catalog import get_form_definition


def _load_service_types() -> list[tuple[str, str]]:
    form = get_form_definition("electronic_queue") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get("service_type") or {}).get("options") or []
    out: list[tuple[str, str]] = []
    for o in options:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or "").strip()
        label = str(o.get("label") or "").strip()
        if oid and label:
            out.append((oid, label))
    if out:
        return out
    return [
        ("eq_problem", "Проблема с услугой"),
        ("eq_settings_change", "Изменение настроек"),
        ("eq_replace_equipment", "Замена оборудования"),
    ]


ELECTRONIC_QUEUE_SERVICE_TYPES = _load_service_types()
ELECTRONIC_QUEUE_SERVICE_TYPE_BY_ID = {k: v for k, v in ELECTRONIC_QUEUE_SERVICE_TYPES}
