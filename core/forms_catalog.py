"""Загрузка каталога форм из config/forms_catalog.yaml."""
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_CATALOG: Optional[Dict[str, Any]] = None
_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "forms_catalog.yaml"


def load_forms_catalog() -> Dict[str, Any]:
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    if not _CATALOG_PATH.exists():
        logger.warning("Каталог форм не найден: %s", _CATALOG_PATH)
        _CATALOG = {}
        return _CATALOG
    try:
        import yaml
        with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
            _CATALOG = yaml.safe_load(f) or {}
        if not isinstance(_CATALOG, dict):
            _CATALOG = {}
    except Exception as e:
        logger.exception("Ошибка загрузки forms_catalog: %s", e)
        _CATALOG = {}
    return _CATALOG


def get_forms_catalog() -> Dict[str, Any]:
    return load_forms_catalog()


def get_form_definition(form_id: str) -> Optional[Dict[str, Any]]:
    form = get_forms_catalog().get((form_id or "").strip())
    return form if isinstance(form, dict) else None


def form_requires_profile_department(form_id: str) -> bool:
    """
    True, если в Jira-маппинге формы есть обязательное поле с источником profile.department
    (JSM customfield_11406 «Подразделение» и аналоги).
    """
    form = get_form_definition(form_id)
    if not form:
        return False
    jira = form.get("jira") or {}
    fields = jira.get("fields") or {}
    if not isinstance(fields, dict):
        return False
    for _fid, rule in fields.items():
        if not isinstance(rule, dict):
            continue
        if (rule.get("source") or "").strip() != "profile.department":
            continue
        if bool(rule.get("required")):
            return True
    return False
