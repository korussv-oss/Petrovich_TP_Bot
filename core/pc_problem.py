"""Общие константы для заявки «Проблема в работе ПК» (из forms_catalog)."""

from core.forms_catalog import get_form_definition


def _load_pc_problem_kinds() -> list[tuple[str, str]]:
    form = get_form_definition("pc_problem") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get("pc_problem_kind_id") or {}).get("options") or []
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
        ("11732", "Зависание"),
        ("11733", "Не включается"),
        ("11734", "Самопроизвольно выключается"),
        ("11735", "Медленно работает"),
        ("11736", "Не печатает"),
        ("10803", "Не нашёл подходящую категорию"),
        ("10800", "Другая проблема с ПК"),
    ]


PC_PROBLEM_KINDS = _load_pc_problem_kinds()

PC_PROBLEM_KIND_BY_ID = {kind_id: label for kind_id, label in PC_PROBLEM_KINDS}
