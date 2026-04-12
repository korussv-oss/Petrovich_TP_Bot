"""
HTTP error classification helpers (Jira/MAX).

Goal: keep consistent log levels and allow callers to react (cleanup, backoff).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HttpErrorInfo:
    status: int
    kind: str  # e.g. "not_found", "unauthorized", "rate_limited", "server_error", "other"
    log_level: str  # "debug" | "info" | "warning" | "error"
    mapped_status: int | None = None  # optional semantic code for higher-level logic


def classify_http_error(*, status: int, body_text: str = "") -> HttpErrorInfo:
    """
    Classify an HTTP response status + optional body snippet.

    mapped_status is used to surface semantic events:
    - 410: resource definitely does not exist (safe to cleanup registry)
    """
    body = body_text or ""

    if status == 401:
        return HttpErrorInfo(status=401, kind="unauthorized", log_level="warning")
    if status == 403:
        return HttpErrorInfo(status=403, kind="forbidden", log_level="warning")
    if status == 404:
        if "Issue Does Not Exist" in body:
            return HttpErrorInfo(status=404, kind="not_found", log_level="info", mapped_status=410)
        return HttpErrorInfo(status=404, kind="not_found", log_level="warning")
    if status == 429:
        return HttpErrorInfo(status=429, kind="rate_limited", log_level="warning")
    if 500 <= int(status) <= 599:
        return HttpErrorInfo(status=status, kind="server_error", log_level="warning")

    # other 3xx/4xx
    if 400 <= int(status) <= 499:
        return HttpErrorInfo(status=status, kind="client_error", log_level="warning")

    return HttpErrorInfo(status=status, kind="other", log_level="warning")

