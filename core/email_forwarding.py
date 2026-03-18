"""Константы сценария «Настройка переадресации» (ISR, request type 394)."""

from core.forms_catalog import get_form_definition


def _load_on_off() -> list[tuple[str, str]]:
    form = get_form_definition("email_forwarding") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get("on_off") or {}).get("options") or []
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
    return [("email_fwd_on", "Включить"), ("email_fwd_off", "Выключить")]


EMAIL_FORWARDING_ON_OFF = _load_on_off()
EMAIL_FORWARDING_ON_OFF_BY_ID = {k: v for k, v in EMAIL_FORWARDING_ON_OFF}
