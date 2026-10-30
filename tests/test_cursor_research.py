from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from forecastlab import cursor_research as cr
from forecastlab.judge_panel import Member, PanelConfig, Price, load_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cursor_research as research_script  # noqa: E402
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


def test_research_asks_for_the_outside_view_then_the_inside_view_and_passes_both_on() -> None:
    task = {"id": "q", "question": "Will CPI rise?", "criteria": "BLS.", "resolves_on": "2026-10-15"}
    text = cr.prompt(task, "2026-10-14")
    assert text.index("outside view") < text.index("inside view") and '"different_now"' in text
    evidence = cr.validate(answer(different_now="Gasoline fell 8% in September, which pulls CPI down."))
    assert evidence["different_now"].startswith("Gasoline fell 8%")
    assert cr.validate(answer())["different_now"] == ""  # older replies without it still pass
    data = {"sources": {"q": []}, "findings": {"q": {"facts": [], "base_rate": "14 of 24 months.",
                                                    "different_now": evidence["different_now"],
                                                    "uncertainties": []}}}
    lines = cr.digest(data, "q").splitlines()
    assert lines == ["Base rate: 14 of 24 months.",
                     "Different this time: Gasoline fell 8% in September, which pulls CPI down."]


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
    assert set(again["seconds"]) == {"polymarket-1", "polymarket-2", "polymarket-3"}


def test_research_stops_asking_once_the_usage_limit_is_hit_and_records_cost(tmp_path: Path) -> None:
    directory = market_dir(tmp_path)
    questions = [dict(MARKET, id=f"polymarket-{n}", question=f"Question {n}?") for n in range(1, 5)]
    (directory / "forecasts.json").write_text(json.dumps({"questions": questions}))
    asked: list[str] = []

    def agent(text: str) -> tuple[str, float]:
        asked.append(text)
        if len(asked) > 1:
            raise RuntimeError("cursor-agent exited 1: You've hit your weekly usage limit")
        return answer(), 0.0123

    result = cr.research(directory, agent, "2026-10-22", parallel=1, budget=10)
    assert len(asked) == 2  # the first limit error stops the run; the rest are skipped, not sent
    assert set(result["findings"]) == {"polymarket-1"} and result["cost_usd"] == {"polymarket-1": 0.0123}
    assert "usage limit" in result["failed"]["polymarket-2"]
    assert all(result["failed"][f"polymarket-{n}"].startswith("skipped") for n in (3, 4))


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
    envelope = tmp_path / "envelope"
    envelope.write_text("#!/bin/sh\necho '{\"type\": \"result\", \"result\": \"{}\", \"total_cost_usd\": 0.21}'\n")
    envelope.chmod(0o755)
    assert cr.claude_agent("sonnet", 30, binary=str(envelope))("x") == ("{}", 0.21)


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
    assert any("claude" in m.model for m in live.members)  # Claude only fills gaps, so it sits out per question


def test_a_judge_sits_out_the_questions_its_own_family_researched(tmp_path: Path) -> None:
    directory = market_dir(tmp_path)
    second = dict(MARKET, id="polymarket-2", question="Will payrolls rise?")
    (directory / "forecasts.json").write_text(json.dumps({"questions": [MARKET, second]}))
    research = cr.research(directory, lambda text: answer(), "2026-10-14", parallel=1, budget=5, by="grok-4.7-low")
    research["researched_by"]["polymarket-2"] = "claude-sonnet"
    (directory / "research.json").write_text(json.dumps(research))
    assert panel_script.researched_by(directory) == {"polymarket-1": "grok", "polymarket-2": "claude"}
    (directory / "briefs.json").write_text(json.dumps({"briefs": {
        "polymarket-1": "CPI rose 2.9% through August.", "polymarket-2": "Payrolls rose 50,000 in August."}}))
    config = PanelConfig(version="t", prompt_version="t", daily_budget_usd=1.0, max_tokens=500, members=[
        Member(method="judge_a", model="open/a"), Member(method="judge_claude", model="anthropic/claude-sonnet-5.5")])
    asked: list[str] = []

    def post(body: dict[str, Any]) -> dict[str, Any]:
        asked.append(body["model"])
        return {"model": body["model"], "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                "choices": [{"message": {"content": '{"probability": 0.4, "reasoning": "Base rate."}'}}]}

    summary = panel_script.run([directory], post=post, price_list={m.model: Price(0.0, 0.0) for m in config.members},
                               now=datetime(2026, 10, 14, 12, tzinfo=UTC), config=config, artifacts=tmp_path)
    assert sorted(asked) == ["anthropic/claude-sonnet-5.5", "open/a", "open/a"]
    assert summary["skipped_own_research"] == 1


def test_research_settings_are_valid() -> None:
    settings = cr.load_settings(ROOT / cr.CONFIG_FILE)
    assert settings.model_family in settings.model and settings.mode in ("dry_run", "live")
    assert settings.families == (settings.model_family, "claude") and settings.fallback_model


@pytest.mark.parametrize(("when", "inside"), [
    (datetime(2026, 10, 30, 9, 30, tzinfo=UTC), True),     # Friday, daylight time: 02:30 Pacific
    (datetime(2026, 10, 30, 8, 59, tzinfo=UTC), False),    # 01:59 Pacific
    (datetime(2026, 10, 30, 15, 0, tzinfo=UTC), False),    # 08:00 Pacific
    (datetime(2026, 10, 31, 10, 30, tzinfo=UTC), False),   # Saturday
    (datetime(2026, 11, 2, 9, 30, tzinfo=UTC), False),     # Monday after the clocks change: 01:30 Pacific
    (datetime(2026, 11, 2, 10, 30, tzinfo=UTC), True),     # 02:30 Pacific
    (datetime(2026, 11, 2, 15, 59, tzinfo=UTC), True),     # 07:59 Pacific
    (datetime(2026, 11, 2, 16, 0, tzinfo=UTC), False),     # 08:00 Pacific
])
def test_research_window_follows_pacific_time_through_the_daylight_saving_change(when: datetime, inside: bool) -> None:
    settings = cr.load_settings(ROOT / cr.CONFIG_FILE)
    assert settings.window_tz == "America/Los_Angeles"
    assert settings.in_window(when) is inside


def test_the_old_window_utc_key_still_reads_as_utc(tmp_path: Path) -> None:
    data = json.loads((ROOT / cr.CONFIG_FILE).read_text())
    data.pop("window_hours"), data.pop("window_tz")
    data["window_utc"] = [9, 15]
    path = tmp_path / "research.json"
    path.write_text(json.dumps(data))
    settings = cr.load_settings(path)
    assert settings.window_tz == "UTC"
    assert settings.in_window(datetime(2026, 11, 2, 9, 30, tzinfo=UTC))
    assert not settings.in_window(datetime(2026, 11, 2, 15, 0, tzinfo=UTC))


def test_a_mistyped_window_zone_fails_when_the_settings_load(tmp_path: Path) -> None:
    data = json.loads((ROOT / cr.CONFIG_FILE).read_text())
    data["window_tz"] = "America/Los_Angelez"
    path = tmp_path / "research.json"
    path.write_text(json.dumps(data))
    with pytest.raises(Exception):  # noqa: B017, PT011 -- ZoneInfoNotFoundError
        cr.load_settings(path)


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


def test_refused_pages_are_asked_as_a_browser_then_read_from_the_archive() -> None:
    import httpx

    page = b"<html><body><article><p>" + b"Ballot measures on the November ballot. " * 20 + b"</p></article></body></html>"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host} {request.headers['user-agent'][:7]}")
        if request.url.host == "archive.org":
            return httpx.Response(200, json={"archived_snapshots": {"closest": {
                "available": True, "status": "200", "timestamp": "20261020120000"}}})
        if request.url.host == "web.archive.org":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=page)
        if "browser-ok" in request.url.path and request.headers["user-agent"].startswith("Mozilla"):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=page)
        return httpx.Response(403)

    with httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": research_download.AGENT}) as client:
        fetch = research_download.http_fetch(client)
        assert fetch("https://example.org/browser-ok")[1] == page and len(fetch("https://example.org/browser-ok")) == 2
        seen.clear()
        got = fetch("https://ballotpedia.org/x")
    assert got[2] == "https://web.archive.org/web/20261020120000id_/https://ballotpedia.org/x"
    assert [s.split()[0] for s in seen] == ["ballotpedia.org", "ballotpedia.org", "archive.org", "web.archive.org"]
    source = {"url": "https://ballotpedia.org/x", "quotes": ["ballot measures on the November ballot"]}
    checked = research_download.check_source(source, lambda url: got)
    assert checked["verified"] is True and checked["checked_via"] == "archive"
    unquoted = research_download.check_source({"url": "https://ballotpedia.org/x", "quotes": []}, lambda url: got)
    assert unquoted["verified"] is None and "November ballot" in unquoted["text"]  # nothing to check, page still read
    stale = research_download.check_source({**source, "published": "2026-10-22"}, lambda url: got)
    assert stale["verified"] is None and "predates" in stale["download_error"]  # the Oct 20 copy cannot hold it


def test_checkout_replaces_a_worktree_left_by_another_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, stale = tmp_path / "repo", tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
                    "-m", "x"], check=True)
    stale.mkdir()  # a worktree from yesterday's clone, which this repo doesn't know about
    (stale / ".git").write_text("gitdir: /gone/.git/worktrees/work\n")
    monkeypatch.setattr(research_script, "WORK", stale)
    monkeypatch.setattr(research_script.git, "__kwdefaults__", {"cwd": repo, "check": True})
    assert research_script.checkout("HEAD") == stale
    assert "/gone/" not in (stale / ".git").read_text()
