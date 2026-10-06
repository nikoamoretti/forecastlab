from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from tests.test_question_selection import NOW, bls_payload, releases

from forecastlab.errors import BudgetExceeded, PermanentProviderError, UnknownProviderResult
from forecastlab.macro import MacroSpec, fetch_latest_macro_snapshots
from forecastlab.macro_evidence import parse_first_release, validate_macro_packet
from forecastlab.providers.base import ChatResult
from forecastlab.question_selection import choose_questions
from forecastlab.schemas import ModelUsage
from forecastlab_api.autopilot import PolicyConfig
from forecastlab_api.autopilot_models import (
    AutopilotRun,
    FinalEstimateHold,
    InboxEvent,
)
from forecastlab_api.autopilot_store import budget_totals, claim_lease, release_lease, state, week_bounds, weekly_budget
from forecastlab_api.durable_execution import DurableModel, checkpoint_key, start_checkpoint
from forecastlab_api.usage_ledger import PersistentUsageLedger


@pytest.fixture(params=["sqlite", "postgres"] if os.environ.get("FORECASTLAB_TEST_POSTGRES_URL") else ["sqlite"])
def ready(client, monkeypatch, request):
    from forecastlab_api import autopilot
    from forecastlab_api.config import settings
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.question_suggestions import SelectionSources
    from forecastlab_api.secrets import save_secrets
    pg_engine = admin = None
    if request.param == "postgres":
        from forecastlab_api import db as db_module
        from forecastlab_api.migrate import apply_migrations
        admin = create_engine(os.environ["FORECASTLAB_TEST_POSTGRES_URL"], isolation_level="AUTOCOMMIT")
        database = "forecastlab_test_" + uuid.uuid4().hex
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
        url = admin.url.set(database=database)
        apply_migrations(url.render_as_string(hide_password=False))
        pg_engine = create_engine(url)
        SessionLocal = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
        monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(settings, "embedded_worker", False)
    monkeypatch.setattr(autopilot, "utcnow", lambda: NOW)
    monkeypatch.setattr("forecastlab_api.autopilot_store.utcnow", lambda: NOW)
    monkeypatch.setattr("forecastlab.macro.utcnow", lambda: NOW)
    save_secrets({"model_provider": "openai", "model_name": "gpt-5-mini", "model_api_key": "test-only",
                  "search_provider": "tavily", "search_api_key": "test-only", "max_cost_usd": 5})
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bls_payload()))) as http:
        snapshots = fetch_latest_macro_snapshots(client=http)
    sources = SelectionSources(checked_at=NOW, releases=releases(), snapshots=snapshots,
        documents={releases()[0].source_url: {"key": "sources/test", "sha256": "test"}})
    candidates, _ = choose_questions(sources.releases, snapshots, now=NOW)
    with SessionLocal() as session:
        policy = autopilot.approve_policy(session, PolicyConfig(), approved_by="146488758")
        current = state(session)
        current.enabled = True
        session.commit()
    yield autopilot, SessionLocal, sources, candidates, policy.id
    if pg_engine is not None:
        pg_engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database}"')
        admin.dispose()


def auto_run(ready):
    autopilot, sessions, sources, candidates, _ = ready
    with sessions() as session:
        candidate = next(c for c in candidates if c.macro.indicator == "cpi")
        run = autopilot.dispatch(session, candidate, sources, kind="initial")
        assert run is not None
        return run.id


def reserve(ledger, run_id, number, amount=1):
    return ledger.reserve(run_id=run_id, run_attempt_id=None, logical_call_id=str(number),
        physical_attempt_number=1, stage="research", provider_type="model", provider="openai", model="gpt-5-mini",
        reserved_input_tokens=100, reserved_output_tokens=100, reserved_cost_usd=amount)


def test_policy_disabled_until_restore_and_three_qualifications(client):
    data = client.get("/api/autopilot").json()
    assert data["enabled"] is False and data["policy"]["weekly_usd"] == 25
    assert client.put("/api/autopilot/policy", json=PolicyConfig().model_dump(mode="json")).status_code == 200
    response = client.post("/api/autopilot/enable", json={"enabled": True})
    assert response.status_code == 409
    assert "restore" in response.text.lower()
    assert client.put("/api/autopilot/policy", json={"weekly_usd": 26}).status_code == 422
    assert client.post("/internal/process").status_code == 401
    assert client.get("/internal/cron").status_code == 401


def test_dispatch_freezes_contract_and_deduplicates_changed_threshold(ready):
    autopilot, sessions, sources, candidates, policy_id = ready
    run_id = auto_run(ready)
    candidate = next(c for c in candidates if c.macro.indicator == "cpi")
    changed = candidate.model_copy(update={"macro": candidate.macro.model_copy(update={"threshold": 99})})
    with sessions() as session:
        assert autopilot.dispatch(session, changed, sources, kind="initial") is None
        auto = session.get(AutopilotRun, run_id)
        assert auto.policy_id == policy_id
        assert json.loads(auto.approval_json)["contract_hash"]
        auto.cutoff = NOW - timedelta(days=1)
        with pytest.raises(ValueError, match="append_only"):
            session.commit()


def test_concurrent_dispatch_and_weekly_run_budget(ready):
    autopilot, sessions, sources, candidates, _ = ready
    candidate = next(c for c in candidates if c.macro.indicator == "cpi")
    def submit(_):
        with sessions() as session:
            result = autopilot.dispatch(session, candidate, sources, kind="initial")
            return result.id if result else None
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(submit, range(3)))
    assert sum(i is not None for i in ids) == 1
    run_id = next(i for i in ids if i)
    ledger = PersistentUsageLedger(sessions, max_cost_usd=5)
    def spend(i):
        try:
            return reserve(ledger, run_id, i, 2).id
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(spend, range(4)))
    assert sum(r is not None for r in results) == 2
    with sessions() as session:
        totals = budget_totals(session, weekly_budget(session).id)
        assert totals["reserved_usd"] == 4
        assert totals["remaining_usd"] == 21


def test_pause_final_estimate_holds_and_failed_attempt_charges(ready):
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    with sessions() as session:
        session.add(FinalEstimateHold(run_id=run_id, role="base_rate", cost_usd=2, tokens=100))
        session.commit()
    ledger = PersistentUsageLedger(sessions, max_cost_usd=5)
    with pytest.raises(BudgetExceeded):
        reserve(ledger, run_id, "too-big", 4)
    entry = reserve(ledger, run_id, "paid", 2)
    ledger.fail(entry.id, error_category="TransientProviderError", error_message="Provider unavailable")
    assert ledger.totals(run_id).total_cost_usd == 2
    with sessions() as session:
        state(session).enabled = False
        session.commit()
    with pytest.raises(BudgetExceeded, match="autopilot_paused"):
        reserve(ledger, run_id, "paused", .1)


def test_week_rollover_does_not_reset_run_ceiling(ready, monkeypatch):
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    ledger = PersistentUsageLedger(sessions, max_cost_usd=5)
    reserve(ledger, run_id, 1, 4)
    monkeypatch.setattr("forecastlab_api.autopilot_store.utcnow", lambda: NOW + timedelta(days=3))
    with pytest.raises(BudgetExceeded):
        reserve(ledger, run_id, 2, 2)
    reserve(ledger, run_id, 3, 1)
    with sessions() as session:
        assert budget_totals(session, weekly_budget(session).id)["reserved_usd"] == 1
    assert ledger.totals(run_id).total_cost_usd == 5
    assert week_bounds(datetime(2026, 3, 8, 20, tzinfo=UTC))[2].hour == 7  # DST uses local Monday.


def test_three_physical_provider_failures_pause_once(ready):
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    ledger = PersistentUsageLedger(sessions, max_cost_usd=5)
    for i in range(3):
        entry = reserve(ledger, run_id, i, .1)
        ledger.fail(entry.id, error_category="TransientProviderError", error_message="HTTP 503")
    with sessions() as session:
        assert not state(session).enabled
        assert state(session).provider_failures == 3
        incidents = list(session.scalars(select(InboxEvent).where(InboxEvent.id.like("provider-pause:%"))))
        assert len(incidents) == 1


def test_leases_are_exclusive_fenced_and_expiring(client):
    from forecastlab_api.autopilot_models import ExecutionLease
    from forecastlab_api.db import SessionLocal
    first = claim_lease("test-lease", seconds=10)
    assert first is not None and claim_lease("test-lease") is None
    with SessionLocal() as session:
        session.get(ExecutionLease, "test-lease").expires_at = datetime(2020, 1, 1, tzinfo=UTC)
        session.commit()
    second = claim_lease("test-lease")
    assert second[1] > first[1]
    release_lease("test-lease", first)
    assert claim_lease("test-lease") is None
    release_lease("test-lease", second)
    assert claim_lease("test-lease") is not None


def test_provider_response_checkpoint_replays_without_a_call(ready):
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    class Provider:
        name = model = "stub"
        calls = 0
        def complete_json(self, **_):
            self.calls += 1
            return ChatResult(content='{"ok":true}', parsed={"ok": True}, usage=ModelUsage(cost_usd=.1))
    provider = Provider()
    kwargs = {"schema_name": "research", "system": "s", "user": "u"}
    first = DurableModel(provider, run_id).complete_json(**kwargs)
    second = DurableModel(provider, run_id).complete_json(**kwargs)
    assert first == second and provider.calls == 1
    key = checkpoint_key(run_id, "model", {**kwargs, "user": "interrupted"})
    start_checkpoint(key, run_id, "research")
    with pytest.raises(PermanentProviderError, match="requires_reconciliation"):
        DurableModel(provider, run_id).complete_json(**{**kwargs, "user": "interrupted"})
    assert provider.calls == 1


def test_ambiguous_read_timeout_never_replays_automatically(ready):
    from forecastlab.errors import TransientProviderError
    from forecastlab.physical import run_physical_attempts
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    ledger = PersistentUsageLedger(sessions, max_cost_usd=5)
    ledger.reconcile_ambiguous = True
    calls = []
    def send(number):
        calls.append(number)
        try:
            raise httpx.ReadTimeout("no response")
        except httpx.ReadTimeout as exc:
            raise TransientProviderError("timeout") from exc
    with pytest.raises(UnknownProviderResult):
        run_physical_attempts(ledger=ledger, run_id=run_id, run_attempt_id=None, logical_call_id="ambiguous",
            stage="research", provider_type="model", provider="openai", model="gpt-5-mini", reserved_input_tokens=1,
            reserved_output_tokens=1, reserved_cost_usd=.5, send=send, sleep=lambda _: None)
    assert calls == [1] and ledger.totals(run_id).total_cost_usd == .5


RELEASE = '''<pre>Transmission of material in this news release is embargoed until
8:30 a.m. (ET) Friday, September 4, 2026
THE EMPLOYMENT SITUATION - AUGUST 2026
Total nonfarm payroll employment increased by 162,000 in August, and the unemployment rate was unchanged at 4.1 percent.</pre>'''


@pytest.mark.parametrize("indicator,value", [("unemployment", 4.1), ("payrolls", 162000)])
def test_first_release_measurement_exact_period_units_time(indicator, value):
    spec = MacroSpec(indicator=indicator, observation_period="2026-08", threshold=5, release_at="2026-09-04T12:30:00Z")
    args = {"source_url": "https://www.bls.gov/news.release/empsit.nr0.htm", "retrieved_at": NOW}
    result = parse_first_release(RELEASE, spec, **args)
    assert result["value"] == value
    for bad in [RELEASE.replace("AUGUST 2026", "JULY 2026"), RELEASE.replace("8:30", "9:30"), RELEASE.replace("162,000", "162 thousand").replace("4.1 percent", "4.1 points")]:
        with pytest.raises(ValueError):
            parse_first_release(bad, spec, **args)
    with pytest.raises(ValueError):
        parse_first_release(RELEASE, spec, **{**args, "retrieved_at": NOW - timedelta(days=1)})


def test_cpi_is_unadjusted_12_month_change():
    text = '''<pre>8:30 a.m. (ET) Wednesday, August 12, 2026
CONSUMER PRICE INDEX - JULY 2026
The CPI-U increased 0.1 percent on a seasonally adjusted basis in July. Over the last 12 months, the all items index increased 3.4 percent before seasonal adjustment.</pre>'''
    spec = MacroSpec(indicator="cpi", observation_period="2026-07", threshold=3, release_at="2026-08-12T12:30:00Z")
    args = {"source_url": "https://www.bls.gov/news.release/cpi.nr0.htm", "retrieved_at": NOW}
    result = parse_first_release(text, spec, **args)
    assert result["value"] == 3.4 and result["outcome"] == 1
    with pytest.raises(ValueError):
        parse_first_release(text.replace("before seasonal adjustment", "after seasonal adjustment"), spec, **args)


def test_macro_evidence_requires_measurements_and_cutoff():
    spec = MacroSpec(indicator="unemployment", observation_period="2026-08", threshold=5, release_at="2026-09-04T12:30:00Z")
    base = {"usable": True, "classification": "supporting", "source_available_at": "2026-08-01T00:00:00Z",
        "required_sections": ["resolution", "reference_class", "current_conditions"]}
    packet = [dict(base, quote="Release schedule for September 2026"),
        dict(base, quote="The unemployment rate was 4.1 percent in July 2026"),
        dict(base, quote="The unemployment rate was 4.1 percent in July 2026", source_available_at="2026-09-04T20:00:00Z")]
    a, b, c = validate_macro_packet(packet, spec, cutoff="2026-09-01T00:00:00+00:00")
    assert not a["usable"] and a["required_sections"] == ["resolution"]
    assert b["usable"] and "current_conditions" in b["required_sections"]
    assert not c["usable"]


def test_owner_auth_protects_direct_api_sessions_and_csrf(client, monkeypatch):
    from forecastlab_api.auth import hashed
    from forecastlab_api.autopilot_models import AuthSession
    from forecastlab_api.config import settings
    from forecastlab_api.db import SessionLocal
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "owner_github_id", "146488758")
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "internal_secret", "test-internal-secret")
    assert client.get("/api/settings").status_code == 401
    headers = {"x-forecastlab-internal": "test-internal-secret"}
    assert client.get("/api/settings", headers=headers).status_code == 401
    with SessionLocal() as session:
        session.add(AuthSession(token_hash=hashed("owner-session"), owner_id="146488758", csrf_hash=hashed("csrf"), expires_at=NOW + timedelta(days=1000)))
        session.commit()
    client.cookies.set("forecastlab_session", "owner-session")
    assert client.get("/api/auth/session", headers=headers).json()["owner_id"] == "146488758"
    assert client.post("/api/autopilot/enable", json={"enabled": False}, headers=headers).status_code == 403
    headers.update({"origin": settings.web_origin, "x-csrf-token": "csrf"})
    assert client.post("/api/autopilot/enable", json={"enabled": False}, headers=headers).status_code == 200
    assert client.post("/internal/process", headers={"authorization": "Bearer wrong"}).status_code == 401


def test_oauth_rejects_wrong_owner_and_failed_state(client, monkeypatch):
    from forecastlab_api import auth
    from forecastlab_api.config import settings
    monkeypatch.setattr(settings, "owner_github_id", "146488758")
    class GitHub:
        async def authorize_access_token(self, request):
            return {"access_token": "test-only"}
        async def get(self, path, **_):
            return httpx.Response(200, json={"id": 999}, request=httpx.Request("GET", "https://api.github.com/user"))
    monkeypatch.setattr(auth, "github", lambda: GitHub())
    response = client.get("/api/auth/github/callback")
    assert response.status_code == 403
    assert "forecastlab_session" not in response.cookies
    class InvalidState(GitHub):
        async def authorize_access_token(self, request):
            raise ValueError("state mismatch")
    monkeypatch.setattr(auth, "github", lambda: InvalidState())
    assert client.get("/api/auth/github/callback").status_code == 401


def test_preview_refuses_provider_reservations(ready, monkeypatch):
    from forecastlab_api.config import settings
    _, sessions, *_ = ready
    run_id = auto_run(ready)
    monkeypatch.setattr(settings, "env", "preview")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    with pytest.raises(BudgetExceeded, match="preview_provider_calls_disabled"):
        reserve(PersistentUsageLedger(sessions, max_cost_usd=5), run_id, "preview", .1)


def test_owner_access_code_creates_session_and_rejects_wrong_codes(client, monkeypatch, tmp_path):
    import hashlib
    import json as _json

    from forecastlab_api import auth
    from forecastlab_api.config import settings
    salt = b"0123456789abcdef"
    digest = hashlib.scrypt(b"right-code", salt=salt, n=2**14, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    path = tmp_path / "owner_access_code.json"
    path.write_text(_json.dumps({"algorithm": "scrypt", "n": 2**14, "r": 8, "p": 1, "dklen": 32,
                                 "salt": salt.hex(), "hash": digest.hex()}))
    monkeypatch.setattr(auth, "ACCESS_CODE_FILE", path)
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "owner_github_id", "146488758")
    monkeypatch.setattr(settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "internal_secret", "test-internal-secret")
    headers = {"x-forecastlab-internal": "test-internal-secret", "origin": settings.web_origin}

    assert client.post("/api/auth/code", json={"code": "wrong-code"}, headers=headers).status_code == 401
    wrong_origin = {**headers, "origin": "https://evil.example"}
    assert client.post("/api/auth/code", json={"code": "right-code"}, headers=wrong_origin).status_code == 403
    # The browser path still requires the web proxy's internal header.
    assert client.post("/api/auth/code", json={"code": "right-code"}, headers={"origin": settings.web_origin}).status_code == 401

    response = client.post("/api/auth/code", json={"code": "right-code"}, headers=headers)
    assert response.status_code == 200
    assert "forecastlab_session" in response.cookies and "forecastlab_csrf" in response.cookies
    client.cookies.set("forecastlab_session", response.cookies["forecastlab_session"])
    assert client.get("/api/auth/session", headers=headers).json()["owner_id"] == "146488758"


def test_committed_owner_access_code_is_a_hash_not_a_code():
    import json as _json

    from forecastlab.paths import project_root
    spec = _json.loads((project_root() / "configs" / "owner_access_code.json").read_text())
    assert spec["algorithm"] == "scrypt" and spec["n"] >= 2**15
    assert len(bytes.fromhex(spec["hash"])) == 32 and len(bytes.fromhex(spec["salt"])) >= 16
    assert set(spec) <= {"algorithm", "n", "r", "p", "dklen", "salt", "hash", "normalize", "created", "note"}


def test_passphrase_matches_regardless_of_case_spacing_and_separators(monkeypatch, tmp_path):
    import hashlib
    import json as _json

    from forecastlab_api import auth
    salt = b"fedcba9876543210"
    digest = hashlib.scrypt(b"apple-river-stone-cloud-maple", salt=salt, n=2**14, r=8, p=1,
                            maxmem=64 * 1024 * 1024, dklen=32)
    path = tmp_path / "owner_access_code.json"
    path.write_text(_json.dumps({"algorithm": "scrypt", "n": 2**14, "r": 8, "p": 1, "dklen": 32,
                                 "salt": salt.hex(), "hash": digest.hex(), "normalize": "passphrase_v1"}))
    monkeypatch.setattr(auth, "ACCESS_CODE_FILE", path)
    for typed in ("apple-river-stone-cloud-maple", " Apple River Stone Cloud Maple \n",
                  "apple river  stone_cloud.maple", "APPLE-RIVER-STONE-CLOUD-MAPLE"):
        assert auth.access_code_matches(typed), typed
    for wrong in ("apple-river-stone-cloud", "applerivertonecloudmaple", "apple-river-stone-cloud-maples"):
        assert not auth.access_code_matches(wrong), wrong
