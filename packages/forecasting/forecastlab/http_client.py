from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from forecastlab.ssrf import UnsafeURLError, validate_url

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
DEFAULT_TIMEOUT = 20.0
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/json",
    "application/pdf",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


@dataclass
class SafeResponse:
    url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str
    truncated: bool = False
    bytes_read: int = 0


def _content_type_allowed(content_type: str) -> bool:
    lowered = (content_type or "").split(";", 1)[0].strip().lower()
    if not lowered:
        return True
    return any(lowered == allowed or lowered.startswith(allowed) for allowed in ALLOWED_CONTENT_TYPES)


def _absolute_location(current: str, location: str) -> str:
    if location.startswith("/"):
        parsed = urlparse(current)
        return f"{parsed.scheme}://{parsed.netloc}{location}"
    return location


def safe_get(
    url: str,
    *,
    allow_local_fixtures: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
    headers: dict[str, str] | None = None,
    expect_json: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> SafeResponse:
    current = validate_url(url, allow_local_fixtures=allow_local_fixtures)
    request_headers = {"User-Agent": "ForecastLab/0.1 research-fetch", **(headers or {})}
    client_kwargs: dict = {"timeout": timeout, "follow_redirects": False}
    if transport is not None:
        client_kwargs["transport"] = transport
    with httpx.Client(**client_kwargs) as client:
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current, headers=request_headers) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeURLError("Redirect missing Location")
                    current = validate_url(
                        _absolute_location(current, location),
                        allow_local_fixtures=allow_local_fixtures,
                    )
                    continue
                declared = response.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    raise UnsafeURLError("Declared content length exceeds limit")
                content_type = response.headers.get("content-type", "")
                if expect_json and "json" not in content_type and response.status_code < 400:
                    raise UnsafeURLError(f"Unsafe content type: {content_type or 'missing'}")
                if response.status_code < 400 and not _content_type_allowed(content_type):
                    raise UnsafeURLError(f"Unsafe content type: {content_type or 'missing'}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    next_total = total + len(chunk)
                    if next_total > max_bytes:
                        chunks.clear()
                        raise UnsafeURLError("Response exceeds byte limit")
                    chunks.append(chunk)
                    total = next_total
                return SafeResponse(
                    url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    content=b"".join(chunks),
                    content_type=content_type,
                    truncated=False,
                    bytes_read=total,
                )
    raise UnsafeURLError("Too many redirects")
