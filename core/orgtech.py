"""Константы сценария «Оргтехника» (из forms_catalog)."""

from core.forms_catalog import get_form_definition


def _load_orgtech_kinds() -> list[tuple[str, str]]:
    form = get_form_definition("orgtech_problem") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get("orgtech_kind") or {}).get("options") or []
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
        ("orgtech_printer", "Принтер"),
        ("orgtech_mfu", "МФУ"),
        ("orgtech_scanner", "Сканер"),
        ("orgtech_bad_cartridge", "Плохо заправлен картридж"),
        ("orgtech_other", "Другое"),
    ]


ORGTECH_KINDS = _load_orgtech_kinds()
ORGTECH_KIND_BY_ID = {key: label for key, label in ORGTECH_KINDS}
