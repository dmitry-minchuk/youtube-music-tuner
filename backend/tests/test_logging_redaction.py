"""Secrets must never reach the logs (docs/10 section 4, NFR-004)."""

from __future__ import annotations

import json
import logging

from app.logging_config import JsonFormatter, redact_text


def _format(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def test_secret_keys_in_message_are_redacted() -> None:
    text = 'authorization: Bearer abc123 refresh_token="zzz" visitorData=Cgt0'
    redacted = redact_text(text)
    assert "abc123" not in redacted
    assert "zzz" not in redacted
    assert "Cgt0" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_redaction_is_case_insensitive() -> None:
    assert "secret-value" not in redact_text("Client_Secret: secret-value")


def test_secret_extras_are_redacted() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="external call",
        args=(),
        exc_info=None,
    )
    record.access_token = "top-secret"  # type: ignore[attr-defined]
    record.payload = {"cookie": "sid=1", "operation": "get_playlist"}  # type: ignore[attr-defined]

    payload = _format(record)
    assert payload["access_token"] == "[REDACTED]"
    assert payload["payload"]["cookie"] == "[REDACTED]"
    assert payload["payload"]["operation"] == "get_playlist"


def test_regular_fields_survive() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "sync done", (), None)
    record.job_id = "job-1"  # type: ignore[attr-defined]
    record.duration_ms = 42  # type: ignore[attr-defined]
    payload = _format(record)
    assert payload["job_id"] == "job-1"
    assert payload["duration_ms"] == 42
    assert payload["message"] == "sync done"
