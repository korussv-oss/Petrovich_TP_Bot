"""Константы для заявки «Техническая поддержка Atlassian»."""

ATLASSIAN_SERVICE_TYPES = (
    ("jira", "Jira"),
    ("confluence", "Confluence"),
)

ATLASSIAN_SERVICE_BY_ID = {sid: label for sid, label in ATLASSIAN_SERVICE_TYPES}
