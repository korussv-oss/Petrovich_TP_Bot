"""Константы сценария «Электронная почта (Owa\\Outlook)» (из forms_catalog)."""

from core.forms_catalog import get_form_definition


def _load_email_owa_kinds() -> list[tuple[str, str]]:
    form = get_form_definition("email_owa_outlook") or {}
    options = (((form.get("ui") or {}).get("selects") or {}).get("request_kind") or {}).get("options") or []
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
        ("email_owa_req_create", "Создание почтового ящика"),
        ("email_owa_req_delete", "Удаление почтового ящика"),
        ("email_owa_req_delivery_error", "Ошибка при отправке/доставки"),
        ("email_owa_req_consult", "Консультация"),
    ]


EMAIL_OWA_REQUEST_KINDS = _load_email_owa_kinds()

EMAIL_OWA_KIND_BY_ID = {key: label for key, label in EMAIL_OWA_REQUEST_KINDS}
