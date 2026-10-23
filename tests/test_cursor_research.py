from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from forecastlab import cursor_research as cr
from forecastlab.judge_panel import Member, PanelConfig, load_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import judge_panel as panel_script  # noqa: E402
import research_download  # noqa: E402

MARKET = {
    "id": "polymarket-1", "market_id": "1", "outcome": None, "topic": "Economy",
    "question": "Will US CPI inflation for September be above 3.0%?",
    "resolution_criteria": "Resolves Yes if BLS reports year-over-year CPI above 3.0%. "
                           "This Polymarket market uses the first release.",
    "resolution_date": "2026-10-15",
    "resolution_source": "https://polymarket.com/event/cpi-september",
    "forecasts": [{"method": "market_price_v1", "probability": 0.42}],
}
COHORT = {"entry_id": "abc123", "question": "Will the first published 10-year yield for 2026-10-15 be above 5.31 percent?",
          "release_at": "2026-10-16T21:00:00Z", "cutoff": "2026-10-14T12:00:00Z"}


def answer(**extra: object) -> str:
    data = {
        "sources": [
            {"url": "https://www.bls.gov/cpi/", "title": "CPI Home", "published": "2026-09-11",
             "quotes": ["The all items index rose 2.9 percent for the 12 months ending August."]},
            {"url": "https://polymarket.com/event/cpi-september", "title": "CPI market", "published": "",
             "quotes": ["Traders put it at 42%."]},
            {"url": "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting", "title": "Nowcast",
             "published": "October 2026", "quotes": ["The nowcast for September CPI is 3.0 percent. "
                                                     "Kalshi traders disagree."]},
        ],
        "facts": [
            {"fact": "CPI rose 2.9% year over year through August.", "source": 0},
            {"fact": "The market implied probability is 42%.", "source": 1},
            {"fact": "The Cleveland Fed nowcast for September is 3.0%.", "source": 2},
        ],
        "base_rate": "Year-over-year CPI moved by 0.1 points or more in 14 of the last 24 months.",
        "uncertainties": ["Energy prices in September."],
    }
    data.update(extra)
    return "Here is the evidence:\n```json\n" + json.dumps(data) + "\n```"


def market_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "prospective_markets_20261014"
    directory.mkdir()
    (directory / "forecasts.json").write_text(json.dumps({"questions": [MARKET]}))
    return directory


def test_tasks_carry_no_market_names_links_or_prices(tmp_path: Path) -> None:
    task = cr.tasks(market_dir(tmp_path))[0]
    text = json.dumps(task) + cr.prompt(task, "2026-10-14")
    assert task["id"] == "polymarket-1"
    assert "polymarket.com" not in text and "0.42" not in text
    assert "This Polymarket market" not in text
    assert "Never use or mention prediction markets" in text  # the instruction names them; the task does not


def test_prepared_market_set_without_forecasts_yields_tasks_without_market_fields(tmp_path: Path) -> None:
    directory = tmp_path / "prospective_markets_20261014"
    directory.mkdir()
    (directory / "questions.json").write_text(json.dumps({"selected_at": "2026-10-14T09:25:00Z", "selection": {}, "questions": [{
        "id": "polymarket-9", "question": "Will US CPI inflation for September be above 3.0%?",
        "resolution_criteria": "Resolves Yes if BLS reports year-over-year CPI above 3.0%.",
        "closes_at": "2026-10-15T12:00:00Z", "topic": "Economy",
        "market_url": "https://polymarket.com/event/cpi-september"}]}))
    (directory / "market_snapshot.json").write_text("{}")  # never read
    (task,) = cr.tasks(directory)
    assert task == {"id": "polymarket-9", "question": "Will US CPI inflation for September be above 3.0%?",
                    "criteria": "Resolves Yes if BLS reports year-over-year CPI above 3.0%.",
                    "resolves_on": "2026-10-15", "topic": "Economy"}
    assert "polymarket.com" not in cr.prompt(task, "2026-10-14")


def test_cohort_tasks(tmp_path: Path) -> None:
    directory = tmp_path / "prospective_daily_20261014"
    directory.mkdir()
    (directory / "frozen_manifest.json").write_text("{}")
    (directory / "questions.json").write_text(json.dumps([COHORT]))
    task = cr.tasks(directory)[0]
    assert task["id"] == "abc123" and task["resolves_on"] == "2026-10-16"


def test_validate_drops_market_sources_and_odds_and_reindexes_facts() -> None:
    evidence = cr.validate(answer())
    assert [s["url"] for s in evidence["sources"]] == [
        "https://www.bls.gov/cpi/", "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"]
    assert evidence["sources"][1]["quotes"] == ["The nowcast for September CPI is 3.0 percent."]
    assert evidence["sources"][1]["published"] == ""  # not a date
    assert [f["source"] for f in evidence["facts"]] == [0, 1]
    assert "42" not in json.dumps(evidence)


@pytest.mark.parametrize("reply, reason", [
    ("no json here", "no JSON object"),
    ("{not json}", "not valid JSON"),
    (answer(sources=[{"url": "https://www.bls.gov/cpi/", "title": "CPI", "quotes": []}]), "usable sources"),
    (answer(facts=[{"fact": "One fact.", "source": 0}]), "usable facts"),
])
def test_validate_rejects_unusable_replies(reply: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        cr.validate(reply)


def test_research_saves_evidence_skips_failures_and_respects_the_budget(tmp_path: Path) -> None:
    directory = market_dir(tmp_path)
    second = dict(MARKET, id="polymarket-2", question="Will payrolls rise?")
    third = dict(MARKET, id="polymarket-3", question="Will the Fed cut?")
    (directory / "forecasts.json").write_text(json.dumps({"questions": [MARKET, second, third]}))
    prompts: list[str] = []

    def agent(text: str) -> str:
        prompts.append(text)
        if "payrolls" in text:
            raise TimeoutError("took too long")
        return answer()

    result = cr.research(directory, agent, "2026-10-14", parallel=2, budget=2)
    assert result["runs"] == 2 and len(prompts) == 2
    assert set(result["findings"]) == {"polymarket-1"}
    assert result["failed"] == {"polymarket-2": "TimeoutError: took too long"}
    source = result["sources"]["polymarket-1"][0]
    assert source["text"] == source["quotes"][0] and source["verified"] is None
    assert "CPI rose 2.9%" in cr.digest(result, "polymarket-1")

    (directory / "research.json").write_text(json.dumps(result))
    again = cr.research(directory, lambda text: answer(), "2026-10-14", parallel=1, budget=10, by="claude-sonnet")
    assert again["runs"] == 2  # only the failed and the unresearched questions are asked again
    assert set(again["findings"]) == {"polymarket-1", "polymarket-2", "polymarket-3"} and not again["failed"]
    assert again["researched_by"] == {"polymarket-2": "claude-sonnet", "polymarket-3": "claude-sonnet"}


def test_claude_agent_reads_the_prompt_on_stdin_with_only_web_tools(tmp_path: Path) -> None:
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$(dirname \"$0\")/args\"\ncat\n")
    fake.chmod(0o755)
    assert cr.claude_agent("sonnet", 30, binary=str(fake))("evidence please") == "evidence please"
    args = (tmp_path / "args").read_text().split()
    assert args[:3] == ["-p", "--model", "sonnet"] and args[-3:] == ["--allowedTools", "WebSearch", "WebFetch"]
    failing = tmp_path / "broken"
    failing.write_text("#!/bin/sh\necho 'usage limit' >&2\nexit 1\n")
    failing.chmod(0o755)
    with pytest.raises(RuntimeError, match="usage limit"):
        cr.claude_agent("sonnet", 30, binary=str(failing))("x")


def test_download_checks_quotes_against_the_page(tmp_path: Path) -> None:
    directory = market_dir(tmp_path)
    evidence = cr.research(directory, lambda text: answer(), "2026-10-14", parallel=1, budget=5)
    (directory / "research.json").write_text(json.dumps(evidence))
    page = ("<html><body><article><p>" + "Background on prices. " * 60 + "</p><p>The all items index rose 2.9 percent "
            "for the 12 months ending August. Polymarket bettors expect more.</p></article></body></html>").encode()

    def fetch(url: str) -> tuple[str, bytes]:
        if "clevelandfed" in url:
            raise ConnectionError("blocked")
        return "text/html", page

    research_download.run([directory], fetch)
    data = json.loads((directory / "research.json").read_text())
    bls, nowcast = data["sources"]["polymarket-1"]
    assert bls["verified"] is True and "12 months ending August" in bls["text"]
    assert "Polymarket" not in bls["text"]
    assert nowcast["verified"] is None and "ConnectionError" in nowcast["download_error"]
    assert data["downloaded_at"]


def test_judges_read_cursor_research(tmp_path: Path) -> None:
    directory = market_dir(tmp_path)
    (directory / "research.json").write_text(json.dumps(
        cr.research(directory, lambda text: answer(), "2026-10-14", parallel=1, budget=5)))
    (directory / "briefs.json").write_text(json.dumps({"briefs": {"polymarket-1": "CPI rose 2.9% through August."}}))
    question = panel_script.load_questions(directory)[0]
    assert "The all items index rose 2.9 percent" in question.sources


def test_the_research_model_family_cannot_judge() -> None:
    live = load_config(ROOT / "configs" / "judge_panel.json")
    panel_script.check_independent(live)
    with_grok = PanelConfig(**{**live.model_dump(), "members": [*live.members, Member(method="judge_grok", model="x-ai/grok-4.7")]})
    with pytest.raises(SystemExit, match="cannot judge"):
        panel_script.check_independent(with_grok)
    with_claude = PanelConfig(**{**live.model_dump(),
                                 "members": [*live.members, Member(method="judge_claude", model="anthropic/claude-haiku-5.5")]})
    with pytest.raises(SystemExit, match="cannot judge"):  # Claude fills research gaps, so it may not judge either
        panel_script.check_independent(with_claude)


def test_research_settings_are_valid() -> None:
    settings = cr.load_settings(ROOT / cr.CONFIG_FILE)
    assert settings.model_family in settings.model and settings.mode in ("dry_run", "live")
    assert settings.families == (settings.model_family, "claude") and settings.fallback_model


def test_record_notes_show_calibration_topics_and_latest_misses() -> None:
    from forecastlab.cursor_research import record_notes

    def question(qid: str, p: float, outcome: int, topic: str, day: str, **extra: object) -> dict:
        return {"id": qid, "title": f"Question {qid}?", "status": "resolved", "outcome": outcome, "topic": topic,
                "resolves_on": day, "forecasts": [{"method": "claude_code_forecaster_v1", "probability": p,
                                                   "rationale": f"Reason for {qid}. More detail."}], **extra}

    notes = record_notes({"questions": [
        question("a", 0.55, 0, "Rates", "2026-10-10", actual="5.1%"), question("b", 0.56, 0, "Rates", "2026-10-11"),
        question("c", 0.8, 1, "Crypto", "2026-10-12"), question("d", 0.3, 0, "Crypto", "2026-10-13"),
        question("e", 0.9, 0, "Crypto", "2026-10-14", scored=False),
        {"id": "open", "status": "pending", "forecasts": [{"method": "claude_code_forecaster_v1", "probability": 0.7}]}]})
    assert notes.splitlines()[0].startswith("Your record so far: 2 right of 4 decided calls")
    assert "When 50-60% sure: said 56% on average, right 0% of 2 (overconfident)." in notes
    assert "By topic: Crypto 2 of 2; Rates 0 of 2." in notes
    misses = notes.split("Latest misses")[1]
    assert misses.index("Question b?") < misses.index("Question a?")  # newest first
    assert "You said 55% yes; it was no (5.1%). Your reason: Reason for a." in misses
    assert "Question e?" not in notes  # unscored questions teach nothing
    assert record_notes({"questions": []}) == "Your record so far: nothing decided yet."
