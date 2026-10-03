"""Single-owner sessions; authorization applies at the API, including direct URLs."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.autopilot_models import AuthSession
from forecastlab_api.config import settings

router = APIRouter()
COOKIE = "forecastlab_session"
CSRF_COOKIE = "forecastlab_csrf"
# scrypt hash of the owner access code; the code itself is never committed.
ACCESS_CODE_FILE: Path | None = None  # override for tests; default is configs/owner_access_code.json


def hashed(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def matches(value: str | None, expected: str | None) -> bool:
    return bool(value and expected and secrets.compare_digest(value, expected))


def owner(request: Request) -> str:
    return getattr(request.state, "owner_id", "local" if not settings.cloud else "")


def install_auth(app) -> None:
    # OAuth state contains no access token or provider secret. A separate opaque,
    # database-backed cookie authenticates application requests.
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or secrets.token_hex(32),
                       session_cookie="forecastlab_oauth", https_only=settings.cloud,
                       same_site="lax", max_age=600)

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        path = request.url.path
        if path == "/health":
            return await call_next(request)
        if path.startswith("/internal/"):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            expected = os.environ.get("CRON_SECRET") if path == "/internal/cron" else settings.internal_secret
            if not matches(token, expected):
                return JSONResponse({"detail": "Internal authentication required"}, status_code=401)
            return await call_next(request)
        if not settings.cloud:
            request.state.owner_id = "local"
            return await call_next(request)
        if not settings.owner_github_id or not settings.session_secret or not settings.internal_secret:
            return JSONResponse({"detail": "Owner authentication is not configured"}, status_code=503)
        if not matches(request.headers.get("x-forecastlab-internal"), settings.internal_secret):
            return JSONResponse({"detail": "Use the authenticated application"}, status_code=401)
        if path in {"/api/auth/github", "/api/auth/github/callback", "/api/auth/code"}:
            return await call_next(request)
        from forecastlab_api.db import SessionLocal
        with SessionLocal() as session:
            row = session.get(AuthSession, hashed(request.cookies.get(COOKIE, "")))
            if row is None or row.owner_id != settings.owner_github_id or as_utc(row.expires_at) <= utcnow():
                return JSONResponse({"detail": "Sign in to ForecastLab", "code": "authentication_required"}, status_code=401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                csrf = request.headers.get("x-csrf-token", "")
                if (not matches(hashed(csrf), row.csrf_hash) or
                        request.headers.get("origin") != settings.web_origin.rstrip("/")):
                    return JSONResponse({"detail": "Request verification failed"}, status_code=403)
            request.state.owner_id = row.owner_id
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        return response


def github():
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(503, "Configure the GitHub OAuth application before signing in")
    oauth = OAuth()
    return oauth.register("github", client_id=settings.github_client_id, client_secret=settings.github_client_secret,
        authorize_url="https://github.com/login/oauth/authorize", access_token_url="https://github.com/login/oauth/access_token",
        api_base_url="https://api.github.com/", client_kwargs={"scope": "read:user", "code_challenge_method": "S256"})


@router.get("/api/auth/github")
async def login(request: Request):
    return await github().authorize_redirect(request, settings.web_origin.rstrip("/") + "/api/auth/github/callback")


@router.get("/api/auth/github/callback")
async def callback(request: Request):
    client = github()
    try:
        token = await client.authorize_access_token(request)
        user = await client.get("user", token=token)
        user.raise_for_status()
        identity = str(user.json().get("id", ""))
    except Exception as exc:
        raise HTTPException(401, "GitHub sign-in verification failed") from exc
    if not settings.owner_github_id or identity != settings.owner_github_id:
        raise HTTPException(403, "This application is restricted to its owner")
    request.session.clear()
    return _start_session(identity, RedirectResponse(settings.web_origin.rstrip("/") + "/autopilot", status_code=303))


def _start_session(identity: str, response):
    """Create an opaque, database-backed owner session and set its cookies on ``response``."""
    from forecastlab_api.db import SessionLocal
    session_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with SessionLocal() as session:
        session.add(AuthSession(token_hash=hashed(session_token), owner_id=identity, csrf_hash=hashed(csrf),
                                expires_at=utcnow() + timedelta(days=7)))
        session.commit()
    response.set_cookie(COOKIE, session_token, httponly=True, secure=settings.cloud, samesite="lax", max_age=604800)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=settings.cloud, samesite="lax", max_age=604800)
    return response


def access_code_matches(code: str) -> bool:
    """Compare a submitted owner access code with the committed scrypt hash (never the code itself)."""
    from forecastlab.paths import project_root
    path = ACCESS_CODE_FILE or project_root() / "configs" / "owner_access_code.json"
    if not path.exists() or not code or len(code) > 200:
        return False
    spec = json.loads(path.read_text())
    if spec.get("algorithm") != "scrypt":
        return False
    digest = hashlib.scrypt(code.encode(), salt=bytes.fromhex(spec["salt"]), n=int(spec["n"]), r=int(spec["r"]),
                            p=int(spec["p"]), maxmem=64 * 1024 * 1024, dklen=int(spec["dklen"]))
    return secrets.compare_digest(digest.hex(), spec["hash"])


class AccessCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=200)


@router.post("/api/auth/code")
def code_login(request: Request, body: AccessCodeIn):
    """Owner sign-in with a private access code, as an alternative to GitHub OAuth."""
    if settings.cloud and request.headers.get("origin") != settings.web_origin.rstrip("/"):
        raise HTTPException(403, "Request verification failed")
    if not settings.owner_github_id:
        raise HTTPException(503, "Owner authentication is not configured")
    if not access_code_matches(body.code):
        time.sleep(1)  # The code is high-entropy; the delay only makes guessing slower still.
        raise HTTPException(401, "That access code is not valid")
    return _start_session(settings.owner_github_id, JSONResponse({"ok": True}))


@router.get("/api/auth/session")
def get_session(request: Request):
    return {"owner_id": owner(request), "cloud": settings.cloud}


@router.post("/api/auth/logout")
def logout(request: Request):
    from forecastlab_api.db import SessionLocal
    with SessionLocal() as session:
        row = session.get(AuthSession, hashed(request.cookies.get(COOKIE, "")))
        if row:
            session.delete(row)
            session.commit()
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return response
