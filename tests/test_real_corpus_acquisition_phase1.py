from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from scripts.acquire_real_evaluation_candidates import (
    AcquisitionError,
    HttpPayload,
    Workspace,
    _archive_attempt,
    _candidate_from_market,
    _queues,
    _source_class,
    acquire,
    preflight,
    provisional_split,
    sha256_file,
    verify_workspace,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _source_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    tracked = {
        "uv.lock": "frozen\n",
        "pyproject.toml": "[project]\nname='acquisition-test'\n",
        "apps/web/package-lock.json": "{}\n",
        "packages/forecasting/forecastlab/version.py": '__version__ = "test"\n',
        "configs/forecast_profiles/graph_forecaster_v1.yaml": "id: graph_forecaster_v1\n",
        "prompts/forecast_graph.txt": "PROMPT_ID: forecast_graph:test\n",
    }
    for relative, content in tracked.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(source, "init")
    _git(source, "config", "user.name", "ForecastLab Test")
    _git(source, "config", "user.email", "forecastlab-test@example.invalid")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


def _market(index: int, *, question: str | None = None, resolution: str = "YES") -> dict[str, Any]:
    created = datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000 + index
    close = datetime(2024, 7, 1, tzinfo=UTC).timestamp() * 1000 + index
    resolved = datetime(2024, 7, 2, tzinfo=UTC).timestamp() * 1000 + index
    return {
        "id": f"market-{index}",
        "question": question or f"Will Example Company {index} release product {index} before July 2024?",
        "createdTime": int(created),
        "closeTime": int(close),
        "resolutionTime": int(resolved),
        "resolution": resolution,
        "outcomeType": "BINARY",
        "isResolved": True,
        "url": f"https://manifold.markets/example/market-{index}",
        "textDescription": (
            "Resolves YES if the named product is officially released before the stated deadline; "
            "otherwise resolves NO. Official company records determine resolution."
        ),
    }


class FixtureClient:
    def __init__(self, markets: list[dict[str, Any]]) -> None:
        self.markets = markets
        self.by_id = {item["id"]: item for item in markets}
        self.request_count = 0

    def get(self, url: str, *, accept: str = "application/json") -> HttpPayload:
        del accept
        self.request_count += 1
        if "search-markets" in url:
            value: Any = self.markets
        elif "/market/" in urlparse(url).path:
            value = self.by_id[urlparse(url).path.rsplit("/", 1)[-1]]
        elif "archive.org/wayback/available" in url:
            value = {"archived_snapshots": {}}
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return HttpPayload(
            body=json.dumps(value).encode(),
            final_url=url,
            status=200,
            content_type="application/json",
        )


class ArchiveFixtureClient:
    def __init__(self, *, rows: Any, final_url: str | None = None, body: bytes | None = None) -> None:
        self.rows = rows
        self.final_url = final_url
        self.body = body or b"<html><body>Historical eligible document with sufficient text.</body></html>"
        self.request_count = 0

    def get(self, url: str, *, accept: str = "application/json") -> HttpPayload:
        del accept
        self.request_count += 1
        if "archive.org/wayback/available" in url:
            return HttpPayload(
                body=json.dumps(self.rows).encode(),
                final_url=url,
                status=200,
                content_type="application/json",
            )
        return HttpPayload(
            body=self.body,
            final_url=self.final_url or url,
            status=200,
            content_type="text/html",
        )


@pytest.fixture(autouse=True)
def _no_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "FORECASTLAB_MODEL_API_KEY",
        "FORECASTLAB_SEARCH_API_KEY",
        "FORECASTLAB_MODEL_PROVIDER",
        "FORECASTLAB_SEARCH_PROVIDER",
        "FORECASTLAB_DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_preflight_enforces_workspace_source_lock_and_provider_guards(
    tmp_path: Path,
) -> None:
    source, source_sha = _source_repository(tmp_path)
    workspace = tmp_path / "private-workspace"
    result = preflight(source=source, source_sha=source_sha, workspace=workspace, environment={})
    assert result["source_sha"] == source_sha
    assert result["live_provider_configuration"] is False

    with pytest.raises(AcquisitionError, match="private_workspace_must_be_outside"):
        preflight(source=source, source_sha=source_sha, workspace=source / "private", environment={})
    with pytest.raises(AcquisitionError, match="source_sha_mismatch"):
        preflight(source=source, source_sha="0" * 40, workspace=workspace, environment={})
    with pytest.raises(AcquisitionError, match="credential_environment_present"):
        preflight(source=source, source_sha=source_sha, workspace=workspace, environment={"OPENAI_API_KEY": "set"})
    with pytest.raises(AcquisitionError, match="live_provider_configuration_refused"):
        preflight(
            source=source,
            source_sha=source_sha,
            workspace=workspace,
            environment={"FORECASTLAB_SEARCH_PROVIDER": "tavily"},
        )
    with pytest.raises(AcquisitionError, match="database_access_refused"):
        preflight(
            source=source,
            source_sha=source_sha,
            workspace=workspace,
            environment={"FORECASTLAB_DATABASE_URL": "sqlite:///live.db"},
        )

    (source / "uv.lock").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(AcquisitionError, match="tracked_source_worktree_is_dirty"):
        preflight(source=source, source_sha=source_sha, workspace=workspace, environment={})


def test_candidate_identity_contract_and_blinded_outcome_separation() -> None:
    market = _market(1)
    receipt = {
        "content_sha256": "a" * 64,
        "private_locator": "raw/downloads/" + ("a" * 64),
    }
    first, sealed = _candidate_from_market(market, source_receipt=receipt)
    second, _ = _candidate_from_market(market, source_receipt=receipt)
    assert first["candidate_id"] == second["candidate_id"]
    assert first["contract_hash"] == second["contract_hash"]
    assert first["forecast_date"] < first["resolution_date"]
    assert first["origin_proof"]["type"] == "platform_timestamped_market_record"
    assert "provisional_observed_outcome" not in first
    assert sealed["provisional_observed_outcome"] == 1
    assert sealed["independently_adjudicated"] is False


def test_content_addressing_deduplicates_bytes(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "private")
    first = workspace.content("blobs", b"same bytes")
    second = workspace.content("blobs", b"same bytes")
    assert first == second
    assert len(list((workspace.root / "blobs").iterdir())) == 1
    assert first[1] == sha256_file(workspace.root / first[0])


def test_source_classification_uses_deterministic_official_domains() -> None:
    assert _source_class("https://www.bls.gov/news.release/empsit.htm") == "primary"
    assert _source_class("https://subdomain.gov.uk/statistics") == "primary"
    assert _source_class("https://example.com/report") == "secondary"


def test_archive_temporal_and_original_url_validation(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "private")
    cutoff = datetime(2024, 6, 1, tzinfo=UTC)
    original = "https://example.com/policy"
    rows = {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "timestamp": "20240501000000",
                "status": "200",
                "url": f"https://web.archive.org/web/20240501000000id_/{original}",
            }
        }
    }
    client = ArchiveFixtureClient(
        rows=rows,
        final_url=f"https://web.archive.org/web/20240501000000id_/{original}",
    )
    attempt, document = _archive_attempt(
        client=client,
        workspace=workspace,
        candidate_id="candidate",
        requested_url=original,
        cutoff=cutoff,
        rank=1,
    )
    assert attempt["status"] == "accepted"
    assert document is not None
    assert document["temporal_basis"] == "snapshot_date"
    assert document["cutoff_verified"] is True
    assert document["source_class"] == "secondary"

    other_attempt, other_document = _archive_attempt(
        client=ArchiveFixtureClient(
            rows=rows,
            final_url=f"https://web.archive.org/web/20240501000000id_/{original}",
        ),
        workspace=workspace,
        candidate_id="other-candidate",
        requested_url=original,
        cutoff=cutoff,
        rank=1,
    )
    assert other_attempt["status"] == "accepted"
    assert other_document is not None
    assert other_document["document_id"] != document["document_id"]
    assert other_document["content_sha256"] == document["content_sha256"]

    after = ArchiveFixtureClient(
        rows={
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "timestamp": "20240701000000",
                    "status": "200",
                    "url": f"https://web.archive.org/web/20240701000000id_/{original}",
                }
            }
        }
    )
    rejected, document = _archive_attempt(
        client=after,
        workspace=workspace,
        candidate_id="candidate",
        requested_url=original,
        cutoff=cutoff,
        rank=2,
    )
    assert rejected["rejection_reason"] == "snapshot_after_cutoff"
    assert document is None

    mismatch = ArchiveFixtureClient(
        rows={
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "timestamp": "20240501000000",
                    "status": "200",
                    "url": "https://web.archive.org/web/20240501000000id_/https://other.example/a",
                }
            }
        }
    )
    rejected, document = _archive_attempt(
        client=mismatch,
        workspace=workspace,
        candidate_id="candidate",
        requested_url=original,
        cutoff=cutoff,
        rank=3,
    )
    assert rejected["rejection_reason"] == "archived_original_mismatch"
    assert document is None

    escaped = ArchiveFixtureClient(
        rows=rows,
        final_url="https://example.com/current-page",
    )
    rejected, document = _archive_attempt(
        client=escaped,
        workspace=workspace,
        candidate_id="candidate",
        requested_url=original,
        cutoff=cutoff,
        rank=4,
    )
    assert rejected["rejection_reason"] == "archive_redirected_to_live_content"
    assert document is None


def test_acquisition_resume_verify_and_no_evidence_packet_are_deterministic(
    tmp_path: Path,
) -> None:
    source, source_sha = _source_repository(tmp_path)
    workspace = tmp_path / "private-workspace"
    client = FixtureClient([_market(index) for index in range(3)])
    first = acquire(
        source=source,
        source_sha=source_sha,
        workspace_path=workspace,
        target=3,
        max_pages=1,
        max_evidence_urls=1,
        client=client,
        environment={},
    )
    assert first["counts"]["acquired_candidates"] == 3
    assert first["counts"]["machine_complete"] == 3
    assert first["counts"]["no_eligible_evidence_packets"] == 3
    assert first["human_review"]["reviewer_assigned"] is False
    assert first["human_review"]["independent_adjudicator_assigned"] is False
    assert first["human_review"]["release_frozen"] is False
    assert first["execution"] == {
        "forecast_runs": 0,
        "experiments": 0,
        "scores": 0,
        "calibration": None,
        "openai_calls": 0,
        "tavily_calls": 0,
        "provider_spend_usd": 0.0,
    }
    blinded = (workspace / "records/blinded_candidates.jsonl").read_text(encoding="utf-8")
    sealed = (workspace / "sealed/provisional_outcomes.jsonl").read_text(encoding="utf-8")
    assert "provisional_observed_outcome" not in blinded
    assert "provisional_observed_outcome" in sealed

    resume_client = FixtureClient([])
    resumed = acquire(
        source=source,
        source_sha=source_sha,
        workspace_path=workspace,
        target=3,
        max_pages=1,
        max_evidence_urls=1,
        client=resume_client,
        environment={},
    )
    assert resumed["summary_hash"] == first["summary_hash"]
    assert resume_client.request_count == 0
    verified = verify_workspace(
        source=source,
        source_sha=source_sha,
        workspace_path=workspace,
        environment={},
    )
    assert verified["verified"] is True
    assert verified["network_requests"] == 0
    assert verified["suspected_secret_count"] == 0

    summary_path = workspace / "summary.json"
    summary_path.write_text(summary_path.read_text(encoding="utf-8").replace('"machine_complete":3', '"machine_complete":4'), encoding="utf-8")
    with pytest.raises(AcquisitionError, match="workspace_summary_hash_mismatch"):
        verify_workspace(
            source=source,
            source_sha=source_sha,
            workspace_path=workspace,
            environment={},
        )


def test_split_is_outcome_independent_group_safe_and_reserve_backed() -> None:
    candidates = [
        {
            "candidate_id": f"candidate-{index:03d}",
            "leakage_group_id": f"group-{index:03d}",
        }
        for index in range(220)
    ]
    first = provisional_split(candidates)
    second = provisional_split(list(reversed(candidates)))
    assert first == second
    assert first["complete"] is True
    assert first["counts"] == {"development": 60, "validation": 40, "test": 100}
    assert len(first["reserve_candidate_ids"]) == 20
    assert first["outcome_used"] is False
    group_splits: dict[str, set[str]] = {}
    for item in first["assignments"]:
        group_splits.setdefault(item["leakage_group_id"], set()).add(item["split"])
    assert all(len(values) == 1 for values in group_splits.values())


def test_review_queues_keep_human_identities_blank_and_outcomes_sealed() -> None:
    candidate, sealed = _candidate_from_market(
        _market(9),
        source_receipt={
            "content_sha256": "b" * 64,
            "private_locator": "raw/downloads/" + ("b" * 64),
        },
    )
    queues = _queues([candidate], {candidate["candidate_id"]: sealed})
    assert set(queues) == {
        "question_review",
        "independent_outcome_adjudication",
        "licensing",
        "event_family",
        "leakage",
    }
    assert queues["question_review"][0]["reviewer_id"] is None
    assert queues["independent_outcome_adjudication"][0]["outcome_adjudicator_id"] is None
    assert queues["licensing"][0]["source_use_status"] == "unknown_pending_human_review"
    assert "provisional_observed_outcome" not in queues["question_review"][0]


def test_exact_duplicate_is_rejected_without_outcome_based_selection(tmp_path: Path) -> None:
    source, source_sha = _source_repository(tmp_path)
    duplicate_question = "Will Example Company publish audited revenue before July 2024?"
    markets = [
        _market(1, question=duplicate_question, resolution="YES"),
        _market(2, question=duplicate_question, resolution="NO"),
        _market(3),
    ]
    summary = acquire(
        source=source,
        source_sha=source_sha,
        workspace_path=tmp_path / "private-workspace",
        target=2,
        max_pages=1,
        max_evidence_urls=1,
        client=FixtureClient(markets),
        environment={},
    )
    assert summary["counts"]["machine_complete"] == 2
    assert summary["counts"]["rejected_before_acquisition"] >= 1
    assert summary["split"]["outcome_used"] is False
