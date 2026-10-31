from __future__ import annotations

import json
from pathlib import Path

from forecastlab_api.research_log import build_research, question_research


def test_each_question_gets_its_research_brief_and_judge_calls_without_page_text(tmp_path: Path) -> None:
    directory = tmp_path / "artifacts" / "prospective_markets_20261023"
    directory.mkdir(parents=True)
    (directory / "research.json").write_text(json.dumps({
        "researched_at": "2026-10-23T14:07:07+00:00", "downloaded_at": "2026-10-23T14:07:43+00:00", "model": "grok-4.7-low",
        "researched_by": {"q1": "claude-sonnet"}, "failed": {"q2": "cursor-agent timed out"},
        "seconds": {"q1": 48.2}, "cost_usd": {"q1": 0.21},
        "findings": {"q1": {"facts": [{"fact": "BTC was $81,600 on Oct 23.", "source": 0}], "base_rate": "Rare.",
                            "against": "A dip is plausible.", "passes_done": ["outside", "inside", "against"],
                            "uncertainties": ["Weekend moves"]}},
        "sources": {"q1": [{"title": "Price", "url": "https://example.com/p", "published": "2026-10-23", "verified": True,
                            "quotes": ["$81,600"], "text": "a very long downloaded page"}]}}))
    (directory / "briefs.json").write_text(json.dumps({"written_at": "2026-10-23T14:12:01+00:00", "briefs": {"q1": "Brief."}}))
    (directory / "judge_panel_log.json").write_text(json.dumps({"attempts": [
        {"id": "q1", "method": "judge_kimi_k26_v1", "model": "moonshotai/kimi-k2.6", "ok": False,
         "error": "no JSON object in the reply", "cost_usd": 0.01, "at": "2026-10-23T14:12:19+00:00", "reasoning": "x"}]}))
    (directory / "market_snapshot.json").write_text(json.dumps({"q1": {"price": 0.3}}))
    (tmp_path / "artifacts" / "prospective_empty_20261001").mkdir()

    assert build_research(tmp_path / "artifacts", tmp_path / "data" / "research") == 1
    saved = (tmp_path / "data" / "research" / "prospective_markets_20261023.json").read_text()
    assert "very long downloaded page" not in saved and "0.3" not in saved  # no page text, never market odds
    q1 = question_research(tmp_path / "data", "prospective_markets_20261023", "q1")
    assert q1 is not None
    assert (q1["researched_by"], q1["brief"], q1["base_rate"]) == ("Claude Sonnet", "Brief.", "Rare.")
    assert (q1["research_seconds"], q1["research_cost_usd"]) == (48.2, 0.21)
    assert (q1["against"], q1["passes_done"]) == ("A dip is plausible.", ["outside", "inside", "against"])
    assert q1["sources"] == [{"title": "Price", "url": "https://example.com/p", "published": "2026-10-23", "verified": True,
                              "quotes": ["$81,600"]}]
    assert q1["judges"][0] == {"method": "judge_kimi_k26_v1", "model": "moonshotai/kimi-k2.6", "ok": False,
                                     "error": "no JSON object in the reply", "cost_usd": 0.01, "at": "2026-10-23T14:12:19+00:00"}
    q2 = question_research(tmp_path / "data", "prospective_markets_20261023", "q2")
    assert q2 is not None and (q2["researched_by"], q2["research_failed"]) == ("Cursor (Grok 4.7)", "cursor-agent timed out")
    assert (q2["against"], q2["passes_done"]) == (None, None)
    assert question_research(tmp_path / "data", "../secrets", "q1") is None
    assert question_research(tmp_path / "data", "prospective_markets_20261023", "nope") is None


def test_research_endpoint_answers_404_for_unknown_questions(client) -> None:
    assert client.get("/api/research/prospective_markets_19990101/none").status_code == 404
    assert client.get("/api/research/..%2Fsecrets/none").status_code == 404
