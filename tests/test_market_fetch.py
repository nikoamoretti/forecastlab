"""How the market APIs are read (forecastlab.market_questions.fetch_json): gzip, retries, and what a refusal says."""

from __future__ import annotations

import gzip
import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from forecastlab.market_questions import FETCH_TRIES, fetch_json, refusal_text

URL = "https://gamma-api.polymarket.com/events?limit=100"
GZIP_REQUIRED = b'{"error":"gzip_required",\n "hint":"send the header Accept-Encoding: gzip."}'


def _headers(encoding: str | None) -> Message:
    headers = Message()
    if encoding:
        headers["Content-Encoding"] = encoding
    return headers


class _Answer:
    def __init__(self, body: bytes, encoding: str | None = None) -> None:
        self._body, self.headers = body, _headers(encoding)

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _refusal(code: int, body: bytes = b"", encoding: str | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "Not Acceptable" if code == 406 else "Unavailable", _headers(encoding), io.BytesIO(body))


class _Server:
    """Answers each request with the next item of ``script``: an exception is raised, anything else is returned."""

    def __init__(self, *script: Any) -> None:
        self.script, self.requests, self.waits = list(script), [], []

    def open(self, request: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(request)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def fetch(self) -> Any:
        return fetch_json(URL, opener=self.open, sleep=self.waits.append)


def test_every_request_accepts_gzip_and_a_compressed_answer_is_read() -> None:
    server = _Server(_Answer(gzip.compress(json.dumps([{"id": "e1"}]).encode()), "gzip"))
    assert server.fetch() == [{"id": "e1"}]
    assert server.requests[0].get_header("Accept-encoding") == "gzip"
    assert server.requests[0].get_header("Accept") == "application/json"
    assert server.waits == []


def test_an_answer_that_is_not_compressed_is_read_as_before() -> None:
    assert _Server(_Answer(b'{"markets": []}')).fetch() == {"markets": []}


@pytest.mark.parametrize("encoding", [None, "gzip"])
def test_a_refusal_is_raised_at_once_with_what_the_server_said(encoding: str | None) -> None:
    body = gzip.compress(GZIP_REQUIRED) if encoding else GZIP_REQUIRED
    server = _Server(_refusal(406, body, encoding))
    with pytest.raises(urllib.error.HTTPError) as caught:
        server.fetch()
    assert caught.value.code == 406
    assert str(caught.value) == 'HTTP Error 406: Not Acceptable: {"error":"gzip_required", "hint":"send the header Accept-Encoding: gzip."}'
    assert len(server.requests) == 1 and server.waits == []


def test_a_refusal_without_text_keeps_its_plain_reason() -> None:
    with pytest.raises(urllib.error.HTTPError) as caught:
        _Server(_refusal(406)).fetch()
    assert str(caught.value) == "HTTP Error 406: Not Acceptable"
    assert refusal_text(_refusal(406, b"x" * 1000), limit=10) == "x" * 10


def test_a_busy_server_and_a_dropped_connection_are_tried_again() -> None:
    server = _Server(_refusal(503), urllib.error.URLError("connection reset"), _Answer(b"[1]"))
    assert server.fetch() == [1]
    assert len(server.requests) == 3 and server.waits == [3, 6]


def test_it_gives_up_after_the_last_try() -> None:
    server = _Server(*[TimeoutError("timed out")] * FETCH_TRIES)
    with pytest.raises(TimeoutError):
        server.fetch()
    assert len(server.requests) == FETCH_TRIES and len(server.waits) == FETCH_TRIES - 1

    busy = _Server(*[_refusal(503, b"try later") for _ in range(FETCH_TRIES)])
    with pytest.raises(urllib.error.HTTPError) as caught:
        busy.fetch()
    assert caught.value.code == 503 and "try later" in str(caught.value) and len(busy.requests) == FETCH_TRIES
