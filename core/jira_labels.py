"""Единая метка Jira для заявок, созданных ботом Rubik."""

from __future__ import annotations

from typing import Any, MutableMapping

JIRA_LABEL_CHATBOT = "чатбот"
JIRA_LABELS_CHATBOT: list[str] = [JIRA_LABEL_CHATBOT]


def merge_chatbot_into_labels(container: MutableMapping[str, Any]) -> None:
    """
    Добавляет метку «чатбот» в container['labels'] (JSM requestFieldValues или REST fields).
    """
    existing = container.get("labels")
    if isinstance(existing, list) and existing:
        merged: list[str] = []
        for x in existing:
            s = str(x).strip()
            if s and s not in merged:
                merged.append(s)
        if JIRA_LABEL_CHATBOT not in merged:
            merged.append(JIRA_LABEL_CHATBOT)
        container["labels"] = merged
    else:
        container["labels"] = list(JIRA_LABELS_CHATBOT)
