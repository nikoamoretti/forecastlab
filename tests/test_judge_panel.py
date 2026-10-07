from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forecastlab.judge_panel import Budget, Price, load_config, parse_answer
from forecastlab_api.track_record import FORECASTERS, build_track_record

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import judge_panel as script  # noqa: E402

CONFIG = load_config(ROOT / "configs" / "judge_panel.json")
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
CHEAP = {m.model: Price(0.3e-6, 1.2e-6) for m in CONFIG.members}
ANSWERS = {"judge_nemotron_ultra_v1": 0.6, "judge_nemotron_super_v1": 0.7, "judge_gemma_v1": 1.0}
BY_MODEL = {m.model: ANSWERS[m.method] for m in CONFIG.members}


def fake_post(calls: list[dict]):
    def post(body: dict) -> dict:
        calls.append(body)
        assert "Polymarket" not in body["messages"][1]["content"]
        content = f'```json\n{{"probability": {BY_MODEL[body["model"]]}, "reasoning": "Base rate, then the brief."}}\n```'
        return {"model": body["model"], "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 300, "cost": 0.002}}
    return post


def _general(directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / "forecasts.json").write_text(json.dumps({"schema": "owner_requests_v1", "questions": [
        {"id": "req-1", "question": "Will it rain in Paris on Oct 20?", "topic": "Weather",
         "resolution_criteria": "Yes if Météo-France records rain at Paris-Montsouris on 2026-10-20.",
         "resolution_date": "2026-10-21", "resolution_source": "https://meteofrance.com", "outcome": None,
         "forecasts": [{"method": "claude_code_forecaster_v1", "probability": 0.4, "rationale": "Climatology."}]}]}))
    (directory / "briefs.json").write_text(json.dumps({"written_at": NOW.isoformat(), "briefs": {
        "req-1": "Paris sees measurable rain on about 40% of October days (Météo-France normals, retrieved 2026-10-07)."}}))
    return directory


def test_every_panel_member_has_a_label_on_the_track_record() -> None:
    assert {m.method for m in CONFIG.members} <= set(FORECASTERS)
    assert CONFIG.daily_budget_usd == 1.0


@pytest.mark.parametrize("text", ['{"probability": 0.3, "reasoning": "x"}', 'Sure.\n{"probability": 0.3, "reasoning": "x"} done'])
def test_reply_parsing_tolerates_text_around_the_json(text: str) -> None:
    assert parse_answer(text).probability == 0.3


@pytest.mark.parametrize("text", ["no json", '{"probability": 30, "reasoning": "x"}', '{"probability": 0.3}'])
def test_unusable_replies_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_answer(text)


def test_budget_allows_a_call_only_if_its_worst_case_fits() -> None:
    budget = Budget(1.0, 0.95)
    assert budget.allows(0.05) and not budget.allows(0.06)


def test_panel_answers_once_records_cost_and_joins_the_median(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    directory = _general(artifacts / "prospective_requests_20261007")
    calls: list[dict] = []
    summary = script.run([directory], post=fake_post(calls), price_list=CHEAP, now=NOW, config=CONFIG, artifacts=artifacts)
    assert (summary["asked"], summary["answered"]) == (3, 3)
    assert summary["spent_today_usd"] == pytest.approx(0.006)
    assert all(call["temperature"] == 0 and call["usage"] == {"include": True} for call in calls)

    log = json.loads((directory / script.LOG).read_text())
    assert {a["prompt_version"] for a in log["attempts"]} == {"judge_prompt_v1"}
    forecasts = json.loads((directory / "forecasts.json").read_text())["questions"][0]["forecasts"]
    assert [f["method"] for f in forecasts] == ["claude_code_forecaster_v1", *ANSWERS]
    assert forecasts[-1]["probability"] == 0.98  # a judge's 100% is clamped like every recorded forecast

    again = script.run([directory], post=fake_post(calls), price_list=CHEAP, now=NOW, config=CONFIG, artifacts=artifacts)
    assert again["asked"] == 0 and len(calls) == 3  # reruns never ask or record twice
    assert len(json.loads((directory / "forecasts.json").read_text())["questions"][0]["forecasts"]) == 4

    data = json.loads((directory / "forecasts.json").read_text())
    data["questions"][0]["outcome"] = 1
    (directory / "forecasts.json").write_text(json.dumps(data))
    record = build_track_record(artifacts, today=NOW.date())
    question = record["questions"][0]
    assert question["call"]["probability"] == pytest.approx(0.65) and question["call"]["members"] == 4
    assert question["verdict"] == "right"
    assert next(f for f in record["forecasters"] if f["method"] == "judge_nemotron_super_v1")["label"] == "Nemotron Super (judge)"


def test_daily_cap_stops_new_calls(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    directory = _general(artifacts / "prospective_requests_20261007")
    (artifacts / "prospective_markets_20261007").mkdir()
    (artifacts / "prospective_markets_20261007" / script.LOG).write_text(json.dumps({"attempts": [
        {"id": "x", "method": "judge_nemotron_super_v1", "at": "2026-10-07T09:00:00+00:00", "ok": True, "cost_usd": 0.998}]}))
    calls: list[dict] = []
    summary = script.run([directory], post=fake_post(calls), price_list=CHEAP, now=NOW, config=CONFIG, artifacts=artifacts)
    assert calls == [] and summary["skipped_budget"] == 3


def test_briefs_must_not_carry_market_odds(tmp_path: Path) -> None:
    directory = _general(tmp_path / "prospective_requests_20261007")
    (directory / "briefs.json").write_text(json.dumps({"briefs": {"req-1": "Polymarket has it at 40%."}}))
    with pytest.raises(SystemExit, match="prediction market"):
        script.run([directory], post=fake_post([]), price_list=CHEAP, now=NOW, config=CONFIG, artifacts=tmp_path)


def test_failed_calls_are_logged_and_cost_nothing_extra(tmp_path: Path) -> None:
    directory = _general(tmp_path / "prospective_requests_20261007")

    def down(_body: dict) -> dict:
        raise RuntimeError("OpenRouter returned HTTP 503")

    summary = script.run([directory], post=down, price_list=CHEAP, now=NOW, config=CONFIG, artifacts=tmp_path)
    assert (summary["asked"], summary["answered"]) == (3, 0)
    attempts = json.loads((directory / script.LOG).read_text())["attempts"]
    assert all(not a["ok"] and "503" in a["error"] for a in attempts)
    assert len(json.loads((directory / "forecasts.json").read_text())["questions"][0]["forecasts"]) == 1
    # Same day: not retried. A later day: retried.
    assert script.run([directory], post=down, price_list=CHEAP, now=NOW, config=CONFIG, artifacts=tmp_path)["asked"] == 0
    later = datetime(2026, 10, 8, 12, tzinfo=UTC)
    assert script.run([directory], post=fake_post([]), price_list=CHEAP, now=later, config=CONFIG,
                      artifacts=tmp_path)["answered"] == 3


def test_cohort_judges_become_scored_supplements_before_the_cutoff(tmp_path: Path) -> None:
    directory = tmp_path / "prospective_daily_20261007"
    shutil.copytree(ROOT / "artifacts" / "prospective_daily_20261007", directory)
    for path in directory.glob("judge_*"):
        path.unlink()
    entry = json.loads((directory / "questions.json").read_text())[0]
    (directory / "briefs.json").write_text(json.dumps({"briefs": {entry["entry_id"]: "DGS10 closed at 5.27% on 2026-10-06."}}))

    late = datetime(2026, 10, 7, 17, tzinfo=UTC)  # after the 16:52 cutoff
    assert script.run([directory], post=fake_post([]), price_list=CHEAP, now=late, config=CONFIG, artifacts=tmp_path)["asked"] == 0

    calls: list[dict] = []
    script.run([directory], post=fake_post(calls), price_list=CHEAP, now=NOW, config=CONFIG, artifacts=tmp_path)
    assert "first published value" in calls[0]["messages"][1]["content"]
    claude = json.loads((directory / "claude_code_supplement.json").read_text())
    judge = json.loads((directory / "judge_gemma_v1_supplement.json").read_text())
    assert judge["manifest_hash"] == claude["manifest_hash"] and judge["method"] == "judge_gemma_v1"
    assert set(claude["cells"][0]) - {"sources"} <= set(judge["cells"][0])
    assert judge["cells"][0]["probability"] == 0.98
