from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.http import HttpRequest


LOGGER_NAME = "yealinkService.audit"
_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    log_path = Path(settings.LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(handler)
    logger.propagate = False
    _configured = True


def get_logger() -> logging.Logger:
    configure_logging()
    return logging.getLogger(LOGGER_NAME)


def new_request_id() -> str:
    return uuid4().hex


def source_ip_from_request(request: HttpRequest) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "-")


def log_audit(**fields: object) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    get_logger().info(json.dumps(payload, sort_keys=True, default=str))
