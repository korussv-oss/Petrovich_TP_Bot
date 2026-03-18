"""Константы сценария «Группы рассылки» (ISR, request type 381)."""

from core.forms_catalog import get_form_definition


def _load_what_to_do() -> list[tuple[str, str]]:
    form = get_form_definition("email_groups") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get("what_to_do") or {}).get("options") or []
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
        ("13012", "Создать группу рассылки"),
        ("13013", "Удалить группу рассылки"),
        ("13014", "Добавить сотрудника в группу рассылки"),
        ("13015", "Удалить сотрудника из группы рассылки"),
    ]


EMAIL_GROUPS_WHAT_TO_DO = _load_what_to_do()
EMAIL_GROUPS_WHAT_TO_DO_BY_ID = {k: v for k, v in EMAIL_GROUPS_WHAT_TO_DO}
