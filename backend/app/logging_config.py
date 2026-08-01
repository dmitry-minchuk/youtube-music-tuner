"""Structured JSON logging with secret redaction (docs/10 section 4).

Keys such as ``authorization``, ``cookie``, ``client_secret``,
``access_token``, ``refresh_token`` and ``visitorData`` are redacted
case-insensitively, wherever they appear in the message or the extras.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

REDACTED = "[REDACTED]"
SECRET_KEYS = (
    "authorization",
    "cookie",
    "client_secret",
    "access_token",
    "refresh_token",
    "visitordata",
)

_KEY_ALTERNATION = "|".join(re.escape(key) for key in SECRET_KEYS)
# Value = optional auth scheme + everything up to the next secret key, a
# structural separator or end of line. Lazy, so one line may hold several.
_SECRET_PATTERN = re.compile(
    rf"(?i)\b({_KEY_ALTERNATION})\b\s*[=:]\s*"
    r"\"?(?:(?:Bearer|Basic|Token)\s+)?"
    rf".+?(?=\"?(?:\s+\b(?:{_KEY_ALTERNATION})\b\s*[=:]|[,;}}]|$))"
)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def redact_text(value: str) -> str:
    return _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}={REDACTED}", value)


def redact_value(key: str, value: Any) -> Any:
    if key.lower() in SECRET_KEYS:
        return REDACTED
    if isinstance(value, dict):
        return {k: redact_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = redact_value(key, value)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install redacting JSON handlers, replacing uvicorn's plain output.

    Called both at import time and from the lifespan hook, because uvicorn
    installs its own handlers after the application module is imported.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
