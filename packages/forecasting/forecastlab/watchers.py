from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def content_fingerprint(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def extract_json_path(payload: Any, path: str | None) -> Any:
    if not path:
        return payload
    text = path.strip()
    if text.startswith("$"):
        text = text[1:]
    if text.startswith("."):
        text = text[1:]
    current = payload
    token_re = re.compile(r"([A-Za-z0-9_]+)|\[(\d+)\]")
    pos = 0
    while pos < len(text):
        if text[pos] == ".":
            pos += 1
            continue
        match = token_re.match(text, pos)
        if not match:
            raise ValueError(f"Unsupported JSON path segment at: {text[pos:]}")
        pos = match.end()
        if match.group(1) is not None:
            key = match.group(1)
            if not isinstance(current, dict) or key not in current:
                raise KeyError(key)
            current = current[key]
        else:
            idx = int(match.group(2))
            if not isinstance(current, list) or idx >= len(current):
                raise KeyError(idx)
            current = current[idx]
    return current


def canonical_watch_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if value is None:
        return ""
    return str(value)


def watch_changed(previous: str | None, current: str) -> bool:
    if previous is None:
        return False
    return previous != current
