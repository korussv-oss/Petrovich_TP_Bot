"""Shared utilities for MAX adapter flow modules."""
from typing import Optional


def collect_attachments(
    existing: list,
    attachment_list: Optional[list],
    max_count: int = 10,
) -> list:
    """
    Accumulate attachment tokens from an incoming MAX attachment list.

    Each entry in attachment_list is a dict with at least one of:
      - "url"   (str) + optional "type", "filename"
      - "token" (str) + optional "type"

    Returns a new list (does not mutate *existing*).
    """
    tokens = list(existing)
    for att in (attachment_list or []):
        if not isinstance(att, dict) or len(tokens) >= max_count:
            continue
        if att.get("url"):
            item: dict = {"type": att.get("type") or "file", "url": att["url"]}
            if att.get("filename"):
                item["filename"] = att["filename"]
            tokens.append(item)
        elif att.get("token"):
            tokens.append({"type": att.get("type") or "file", "token": att["token"]})
    return tokens
