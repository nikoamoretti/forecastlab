from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from scripts.acquire_native_provenance_candidates_v3 import (
    KALSHI_NATIVE_SERIES,
    NativeAcquisitionError,
    acquire,
    preflight,
    verify_workspace,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, text=True, capture_output=True
    ).stdout.strip()


def _source_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    files = {
        "uv.lock": "frozen\n",
        "pyproject.toml": "[project]\nname='native-acquisition-test'\n",
        "apps/web/package-lock.json": "{}\n",
        "packages/forecasting/forecastlab/version.py": '__version__ = "test"\n',
        "configs/forecast_profiles/graph_forecaster_v1.yaml": "id: graph_forecaster_v1\n",
        "prompts/forecast_graph.txt": "PROMPT_ID: forecast_graph:test\n",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(source, "init")
    _git(source, "config", "user.name", "ForecastLab Test")
    _git(source, "config", "user.email", "forecastlab-test@example.invalid")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


def _market(index: int, *, series: str, category: str) -> dict[str, object]:
    created = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index)
    settled = created + timedelta(days=30)
    return {
        "ticker": f"{series}-{index}",
        "event_ticker": f"{series}-EVENT-{index}",
        "series_ticker": series,
        "market_type": "binary",
        "result": "yes" if index % 2 else "no",
        "title": f"Will the official {category} indicator {index} exceed its published threshold?",
        "rules_primary": (
            "This market resolves Yes when the named official indicator exceeds the published "
            "threshold at settlement. It resolves No in every other case under these rules."
        ),
        "created_time": created.isoformat(),
        "settlement_ts": settled.isoformat(),
    }


class FixtureNativeClient:
    def __init__(self, markets: list[dict[str, object]]) -> None:
        self.markets = markets
        self.by_ticker = {str(row["ticker"]): row for row in markets}
        self.request_count = 0

    def get_json(self, url: str) -> tuple[dict[str, Any], str, bytes]:
        self.request_count += 1
        parsed = urlparse(url)
        path = parsed.path
        if path.endswith("/events"):
            categories = sorted({str(row["category"]) for row in self.markets if "category" in row})
            payload: dict[str, Any] = {
                "events": [
                    {
                        "event_ticker": row["event_ticker"],
                        "series_ticker": row["series_ticker"],
                        "category": row["category"],
                    }
                    for row in self.markets
                ]
            }
            assert categories
        elif path.endswith("/historical/markets"):
            series = parse_qs(parsed.query)["series_ticker"][0]
            payload = {
                "markets": [
                    {
                        "ticker": row["ticker"],
                        "event_ticker": row["event_ticker"],
                    }
                    for row in self.markets
                    if row["series_ticker"] == series
                ]
            }
        elif "/historical/markets/" in path:
            payload = {"market": self.by_ticker[path.rsplit("/", 1)[-1]]}
        else:  # pragma: no cover - explicit fixture guard
            raise AssertionError(f"unexpected URL: {url}")
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        return payload, url, raw


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


def test_preflight_refuses_unsafe_workspace_provider_and_database_access(tmp_path: Path) -> None:
    source, source_sha = _source_repository(tmp_path)
    workspace = tmp_path / "private-workspace"
    result = preflight(source=source, source_sha=source_sha, workspace=workspace, environment={})
    assert result["provider_calls"] == 0

    with pytest.raises(NativeAcquisitionError, match="native_workspace_must_be_outside"):
        preflight(source=source, source_sha=source_sha, workspace=source / "private", environment={})
    with pytest.raises(NativeAcquisitionError, match="native_provider_credential_environment_present"):
        preflight(
            source=source,
            source_sha=source_sha,
            workspace=workspace,
            environment={"OPENAI_API_KEY": "set"},
        )
    with pytest.raises(NativeAcquisitionError, match="native_database_access_refused"):
        preflight(
            source=source,
            source_sha=source_sha,
            workspace=workspace,
            environment={"FORECASTLAB_DATABASE_URL": "sqlite:///live.db"},
        )


def test_fixture_acquisition_is_resumable_blinded_and_provisionally_split(tmp_path: Path) -> None:
    source, source_sha = _source_repository(tmp_path)
    markets: list[dict[str, object]] = []
    for index in range(260):
        category, series = KALSHI_NATIVE_SERIES[index % len(KALSHI_NATIVE_SERIES)]
        row = _market(index, series=series, category=category)
        row["category"] = category
        markets.append(row)
    client = FixtureNativeClient(markets)
    workspace = tmp_path / "private-workspace"

    result = acquire(
        source=source,
        source_sha=source_sha,
        workspace_path=workspace,
        target=260,
        client=client,  # type: ignore[arg-type]
        max_series_pages=1,
        max_series_requests=20,
        environment={},
    )

    assert result["ready_candidate_count"] == 260
    assert result["native_ready_threshold_met"] is True
    assert result["split"]["counts"] == {"development": 60, "validation": 40, "test": 100}
    assert len(result["split"]["reserve_candidate_ids"]) == 60
    assert result["sealed_input_ready"] is True
    assert result["review_artifacts_created"] is False
    blinded = (workspace / "records/blinded_candidates.jsonl").read_text(encoding="utf-8")
    assert "provisional_observed_outcome" not in blinded
    assert (workspace / "sealed/provisional_outcomes.jsonl").is_file()
    first_requests = client.request_count

    resumed = acquire(
        source=source,
        source_sha=source_sha,
        workspace_path=workspace,
        target=260,
        client=client,  # type: ignore[arg-type]
        max_series_pages=1,
        max_series_requests=20,
        environment={},
    )
    assert resumed == result
    assert client.request_count == first_requests
    verified = verify_workspace(
        source=source, source_sha=source_sha, workspace_path=workspace, environment={}
    )
    assert verified["verified"] is True
    assert verified["network_requests"] == 0


def test_invalid_records_are_retained_as_failures_not_ready_candidates(tmp_path: Path) -> None:
    source, source_sha = _source_repository(tmp_path)
    invalid = _market(1, series="KXJOBLESSCLAIMS", category="Economics")
    invalid["rules_primary"] = "short"
    invalid["category"] = "Economics"
    client = FixtureNativeClient([invalid])
    workspace = tmp_path / "private-workspace"

    result = acquire(
        source=source,
        source_sha=source_sha,
        workspace_path=workspace,
        target=200,
        client=client,  # type: ignore[arg-type]
        max_series_pages=1,
        max_series_requests=600,
        environment={},
    )

    assert result["ready_candidate_count"] == 0
    assert result["provenance_failure_count"] == 1
    assert result["native_ready_threshold_met"] is False
