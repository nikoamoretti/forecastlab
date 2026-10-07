from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_forecasts.py"


def _module():
    spec = importlib.util.spec_from_file_location("daily_forecasts", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(directory: Path, cutoff: str = "2026-10-06T16:50:00+00:00") -> None:
    directory.mkdir()
    entries = [{"entry_id": "a1", "cutoff": cutoff, "release_event": "dgs10-2026-10-13",
                "macro": {"indicator": "treasury_10y", "observation_period": "2026-10-13", "threshold": 5.24,
                          "comparison": "gt", "release_at": "2026-10-14T21:00:00Z", "revision_policy": "first_release"}}]
    (directory / "frozen_manifest.json").write_text(json.dumps({"manifest": {"methods": ["statistical_baseline_v1"],
                                                                            "entries": entries}, "manifest_hash": "h"}))
    (directory / "cohort_report.json").write_text(json.dumps({"id": directory.name, "manifest_hash": "h", "questions": [
        {"id": "a1", "release_event": "dgs10-2026-10-13", "cells": []}]}))


def _forecasts(path: Path, made_at: str, probability: float = 0.6, entry: str = "a1") -> Path:
    path.write_text(json.dumps({"method": "claude_code_forecaster_v1", "forecast_made_at": made_at,
                                "forecasts": [{"entry_id": entry, "probability": probability, "rationale": "r"}]}))
    return path


def test_record_writes_supplement_before_cutoff(tmp_path: Path) -> None:
    module = _module()
    _artifact(tmp_path / "day")
    module.record(tmp_path / "day", _forecasts(tmp_path / "f.json", "2026-10-06T11:30:00Z"))
    supplement = json.loads((tmp_path / "day" / "claude_code_supplement.json").read_text())
    assert supplement["method"] == "claude_code_forecaster_v1"
    assert supplement["cells"][0]["probability"] == 0.6


@pytest.mark.parametrize(("made_at", "probability", "entry"), [
    ("2026-10-06T16:50:00Z", 0.6, "a1"),   # at the cutoff: too late
    ("2026-10-06T11:30:00Z", 0.995, "a1"),  # outside [0.02, 0.98]
    ("2026-10-06T11:30:00Z", 0.6, "zz"),    # does not cover the entries
])
def test_record_rejects_late_extreme_or_mismatched_forecasts(tmp_path: Path, made_at, probability, entry) -> None:
    module = _module()
    _artifact(tmp_path / "day")
    with pytest.raises(SystemExit):
        module.record(tmp_path / "day", _forecasts(tmp_path / "f.json", made_at, probability, entry))
    assert not (tmp_path / "day" / "claude_code_supplement.json").exists()


def test_existing_targets_are_not_proposed_again(tmp_path: Path) -> None:
    module = _module()
    _artifact(tmp_path / "prospective_daily_20261006")
    assert ("treasury_10y", "2026-10-13") in module.existing_targets(tmp_path)
    assert module.entry_id({"indicator": "treasury_10y", "observation_period": "2026-10-13", "threshold": 5.24,
                            "comparison": "gt"}) == module.entry_id({"indicator": "treasury_10y",
                                                                     "observation_period": "2026-10-13",
                                                                     "threshold": 5.24, "comparison": "gt"})


def test_indicators_with_unreleased_questions_are_open(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    module = _module()
    _artifact(tmp_path / "prospective_daily_20261006")
    assert module.open_indicators(tmp_path, datetime(2026, 10, 14, 20, tzinfo=UTC)) == {"treasury_10y"}
    assert module.open_indicators(tmp_path, datetime(2026, 10, 14, 21, tzinfo=UTC)) == set()
