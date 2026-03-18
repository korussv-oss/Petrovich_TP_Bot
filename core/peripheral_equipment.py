"""Константы сценария «Периферийное оборудование» (из forms_catalog)."""

from core.forms_catalog import get_form_definition


def _load_peripheral_kinds() -> list[tuple[str, str]]:
    form = get_form_definition("peripheral_equipment") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get("peripheral_kind") or {}).get("options") or []
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
        ("peripheral_keyboard", "Клавиатура"),
        ("peripheral_mouse", "Мышка"),
        ("peripheral_monitor", "Монитор"),
        ("peripheral_phone", "Телефонный аппарат"),
        ("peripheral_ups", "ИБП"),
        ("peripheral_headset", "Гарнитура/наушники"),
        ("peripheral_barcode_scanner", "Сканер штрих-кода"),
        ("peripheral_tsd", "ТСД"),
        ("peripheral_other", "Другое"),
    ]


PERIPHERAL_KINDS = _load_peripheral_kinds()
PERIPHERAL_KIND_BY_ID = {key: label for key, label in PERIPHERAL_KINDS}
