"""Content-addressed artifacts; cloud storage is private and never a local disk."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath

from forecastlab_api.config import settings


def safe_key(key: str) -> str:
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts or not key or ":" in key:
        raise ValueError("invalid_artifact_key")
    return key


def put_bytes(data: bytes, *, content_type="application/octet-stream", prefix="sources") -> dict:
    sha = hashlib.sha256(data).hexdigest()
    key = safe_key(prefix + "/" + sha)
    if settings.cloud:
        from vercel.blob import put
        result = put(key, data, access="private", token=settings.blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN"),
                     content_type=content_type, add_random_suffix=False, overwrite=True)
        url = result.url
    else:
        target = settings.data_dir / "local" / "artifacts" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        url = key
    return {"key": key, "url": url, "sha256": sha, "byte_length": len(data), "content_type": content_type}


def get_bytes(artifact: dict) -> bytes:
    key = safe_key(artifact["key"])
    if settings.cloud:
        from vercel.blob import get
        # Resolve our own pathname within this store; never forward credentials
        # to a URL supplied in an artifact manifest.
        result = get(key, access="private", token=settings.blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN"))
        if result.status_code != 200:
            raise FileNotFoundError(key)
        data = result.content
    else:
        data = (settings.data_dir / "local" / "artifacts" / key).read_bytes()
    if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
        raise ValueError("artifact_hash_mismatch")
    return data


def retain_export(session, identifier: str, data: bytes, content_type: str) -> None:
    if not settings.cloud:
        return
    from forecastlab_api.autopilot_models import AppSetting
    from forecastlab_api.autopilot_store import insert_once
    manifest = put_bytes(data, prefix="exports", content_type=content_type)
    insert_once(session, AppSetting, {"key": "export:" + hashlib.sha256((identifier + manifest["sha256"]).encode()).hexdigest(),
        "value_json": json.dumps({"artifact_id": identifier, "artifact": manifest})})
