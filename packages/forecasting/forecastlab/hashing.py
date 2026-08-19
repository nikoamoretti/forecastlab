from __future__ import annotations

import hashlib
import json
import re
from typing import Any

API_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|authorization|bearer|token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-.]{8,})")
LONG_SECRET_PATTERN = re.compile(r"\b(?:sk-|xai-|tvly-|Bearer )[A-Za-z0-9_\-.]{8,}\b")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return sha256_text(normalized)


def import_hash(fields: dict[str, Any]) -> str:
    return sha256_text(canonical_json(fields))


def redact_secrets(value: str) -> str:
    redacted = API_KEY_PATTERN.sub(r"\1=[REDACTED]", value)
    return LONG_SECRET_PATTERN.sub("[REDACTED]", redacted)


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    return "[REDACTED]"
