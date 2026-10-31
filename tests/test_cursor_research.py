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


# --- three passes, checklists and the miss review ---------------------------------------------------------

import miss_review  # noqa: E402

TASK = {"id": "q", "question": "Will the 10-year yield on Nov 6 be above 4.50%?", "criteria": "Treasury par curve.",
        "resolves_on": "2026-11-06", "topic": "Interest rates"}


def reply(sources: list[tuple[str, list[str]]], facts: list[tuple[str, int]], **fields: object) -> str:
    data = {"sources": [{"url": url, "title": url.split("/")[-1] or "page", "published": "2026-10-30", "quotes": quotes}
                        for url, quotes in sources],
            "facts": [{"fact": text, "source": index} for text, index in facts], **fields}
    return json.dumps(data)


OUTSIDE = reply([("https://home.treasury.gov/rates", ["The 10-year closed at 4.52."]),
                 ("https://fred.stlouisfed.org/series/DGS10", ["DGS10 4.52"])],
                [("The 10-year ended above its start in 31 of the last 60 five-day windows.", 0),
                 ("DGS10 was 4.52 on 2026-10-30.", 1)],
                base_rate="31 of the last 60 five-day windows ended higher.")
INSIDE = reply([("https://fred.stlouisfed.org/series/DGS10/", ["DGS10 4.52"]),   # same page as pass 1, trailing slash
                ("https://www.bls.gov/schedule/", ["The employment situation is released on November 6."])],
               [("DGS10 was 4.52 on 2026-10-30.", 0),  # repeats a fact of pass 1
                ("Payrolls are released on 2026-11-06, the resolution day.", 1)],
               different_now="Payrolls land on the resolution day.", uncertainties=["Payrolls surprise."])
AGAINST = reply([("https://www.federalreserve.gov/minutes", ["Several participants saw lower yields."])],
                [("Fed minutes of 2026-10-29 saw room for lower yields.", 0)],
                against="Yields could fall: the 2026-10-29 Fed minutes saw room for lower yields.",
                uncertainties=["Payrolls surprise.", "Auction demand."])


def staged_agent(prompts: list[str], *, inside: object = INSIDE, against: object = AGAINST,
                 outside: object = OUTSIDE) -> Any:
    """A fake agent that answers by which pass the prompt is for; an exception in place of a reply is raised."""
    def agent(text: str) -> str:
        prompts.append(text)
        which = outside if "Pass 1 of 3" in text else inside if "Pass 2 of 3" in text else against
        if isinstance(which, Exception):
            raise which
        return str(which)
    return agent


def rates_dir(tmp_path: Path, count: int = 1, topic: str = "Interest rates") -> Path:
    directory = tmp_path / "prospective_markets_20261031"
    directory.mkdir(parents=True)
    questions = [dict(MARKET, id=f"q{n}", question=f"Will the 10-year yield on Nov {n} be above 4.50%?", topic=topic)
                 for n in range(1, count + 1)]
    (directory / "forecasts.json").write_text(json.dumps({"questions": questions}))
    return directory


def test_three_passes_run_in_order_and_later_passes_get_the_earlier_findings(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path), staged_agent(prompts), "2026-10-31", parallel=1, budget=10, passes=3)
    assert result["runs"] == 3 and result["passes"] == 3
    assert [p.split("\n\n")[1].split(":")[0] for p in prompts] == ["Pass 1 of 3", "Pass 2 of 3", "Pass 3 of 3"]
    outside, inside, against = prompts
    assert "31 of the last 60" not in outside and "different this time" not in outside.lower()
    assert "31 of the last 60 five-day windows ended higher." in inside  # the base rate, so pass 2 skips it
    assert "- DGS10 was 4.52 on 2026-10-30." in inside and "https://home.treasury.gov/rates" in inside
    assert "Payrolls land on the resolution day." in against and "31 of the last 60" in against
    assert "Pass 3 of 3: the other side" in against and "give no probability" in against.lower()
    for text in prompts:  # the research rules hold in every pass
        assert "Do not forecast" in text and "Never use or mention prediction markets" in text
        assert "word for word" in text and "published before now" in text
    found = result["findings"]["q1"]
    assert found["passes_done"] == ["outside", "inside", "against"] and "passes_failed" not in found
    assert found["base_rate"].startswith("31 of the last 60") and found["different_now"].startswith("Payrolls land")
    assert found["against"].startswith("Yields could fall")
    assert found["uncertainties"] == ["Payrolls surprise.", "Auction demand."]  # pooled and de-duplicated
    assert set(found) >= {"facts", "base_rate", "different_now", "uncertainties", "against", "passes_done"}


def test_merge_dedupes_sources_by_url_remaps_facts_and_caps_the_total() -> None:
    outside, inside, against = (cr.validate(r, min_sources=1, min_facts=1, need_against=False)
                                for r in (OUTSIDE, INSIDE, AGAINST))
    merged = cr.merge({"against": against, "outside": outside, "inside": inside})  # order comes from the passes
    assert [s["url"] for s in merged["sources"]] == [
        "https://home.treasury.gov/rates", "https://fred.stlouisfed.org/series/DGS10",
        "https://www.bls.gov/schedule/", "https://www.federalreserve.gov/minutes"]
    assert merged["sources"][1]["quotes"] == ["DGS10 4.52"]  # the repeated page's identical quote is not doubled
    assert [(f["fact"][:20], f["source"]) for f in merged["facts"]] == [
        ("The 10-year ended ab", 0), ("DGS10 was 4.52 on 20", 1), ("Payrolls are release", 2),
        ("Fed minutes of 2026-", 3)]  # the repeated fact is dropped; each index points into the merged list
    assert merged["passes_done"] == ["outside", "inside", "against"]

    pooled = cr.merge({"outside": {**outside, "sources": [{**outside["sources"][1], "quotes": ["a", "b", "c"]}],
                                   "facts": [{"fact": "One.", "source": 0}]},
                       "inside": {**inside, "sources": [{**inside["sources"][0], "quotes": ["c", "d", "e", "f"]}],
                                  "facts": [{"fact": "Two.", "source": 0}]}})
    assert pooled["sources"][0]["quotes"] == ["a", "b", "c", "d", "e"] and [f["source"] for f in pooled["facts"]] == [0, 0]

    def many(prefix: str, count: int) -> dict[str, Any]:
        sources = [{"url": f"https://example.org/{prefix}{n}", "title": "t", "published": "", "quotes": []} for n in range(count)]
        return {**outside, "sources": sources, "facts": [{"fact": f"{prefix} fact {n}", "source": n} for n in range(count)],
                "uncertainties": []}

    capped = cr.merge({"outside": many("a", 8), "inside": many("b", 8), "against": many("c", 8)})
    urls = [s["url"] for s in capped["sources"]]
    assert len(urls) == cr.MAX_TOTAL_SOURCES == 14
    assert sum("/a" in u for u in urls) == 8 and sum("/b" in u for u in urls) == 3 and sum("/c" in u for u in urls) == 3
    assert all(f["source"] < 14 for f in capped["facts"]) and len(capped["facts"]) == 14  # facts of dropped sources go


def test_a_failed_later_pass_keeps_the_passes_that_succeeded(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path), staged_agent(prompts, inside=TimeoutError("took too long")),
                         "2026-10-31", parallel=1, budget=10, passes=3)
    found = result["findings"]["q1"]
    assert result["runs"] == 3 and not result["failed"]
    assert found["passes_done"] == ["outside", "against"] and found["passes_failed"] == {"inside": "TimeoutError: took too long"}
    assert found["different_now"] == "" and found["against"].startswith("Yields could fall")
    assert "Payrolls land" not in prompts[2]  # pass 3 worked from what existed

    prompts.clear()
    thin = cr.research(rates_dir(tmp_path / "thin"),
                       staged_agent(prompts, against=reply([("https://x.org/a", [])], [("A fact.", 0)])),
                       "2026-10-31", parallel=1, budget=10, passes=3)  # pass 3 gives no case for the other side
    assert thin["findings"]["q1"]["passes_done"] == ["outside", "inside"]
    assert "no case for the other outcome" in thin["findings"]["q1"]["passes_failed"]["against"]
    assert thin["findings"]["q1"]["against"] == ""


def test_a_failed_first_pass_fails_the_question_and_sends_nothing_more(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path, 2), staged_agent(prompts, outside="no json at all"), "2026-10-31",
                         parallel=1, budget=10, passes=3)
    assert len(prompts) == 2 and result["runs"] == 2  # one pass-1 run per question, nothing after the failure
    assert set(result["failed"]) == {"q1", "q2"} and "no JSON object" in result["failed"]["q1"] and not result["findings"]


def test_the_usage_limit_stops_the_passes_and_the_remaining_questions(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path, 3),
                         staged_agent(prompts, inside=RuntimeError("cursor-agent exited 1: You've hit your weekly usage limit")),
                         "2026-10-31", parallel=1, budget=30, passes=3)
    assert len(prompts) == 2 and result["runs"] == 2  # q1 passes 1 and 2; pass 3 and q2, q3 are not sent
    found = result["findings"]["q1"]
    assert found["passes_done"] == ["outside"] and set(found["passes_failed"]) == {"inside", "against"}
    assert "usage limit" in found["passes_failed"]["inside"] and found["passes_failed"]["against"].startswith("skipped")
    assert all(result["failed"][q].startswith("skipped") for q in ("q2", "q3"))


def test_questions_are_started_only_when_all_their_passes_fit_in_the_budget(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path, 5), staged_agent(prompts), "2026-10-31", parallel=2, budget=8, passes=3)
    assert len(result["findings"]) == 2 and result["runs"] == 6 == len(prompts)  # 8 runs buy two 3-pass questions


def test_the_daily_cap_fits_thirty_three_pass_questions() -> None:
    settings = cr.load_settings(ROOT / cr.CONFIG_FILE)
    assert (settings.passes, settings.fallback_passes) == (3, 1)
    assert 30 * settings.passes == 90 <= settings.daily_run_cap and 5 * settings.daily_run_cap <= settings.weekly_run_cap


def test_two_passes_skip_the_other_side(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path), lambda text: (prompts.append(text), OUTSIDE if "Pass 1" in text else INSIDE)[1],
                         "2026-10-31", parallel=1, budget=10, passes=2)
    assert [p.split("\n\n")[1].split(":")[0] for p in prompts] == ["Pass 1 of 2", "Pass 2 of 2"]
    assert result["findings"]["q1"]["passes_done"] == ["outside", "inside"] and result["findings"]["q1"]["against"] == ""
    assert "Case for the other outcome" not in cr.digest(result, "q1")
    with pytest.raises(ValueError, match="passes must be"):
        path = tmp_path / "research.json"
        path.write_text(json.dumps({**json.loads((ROOT / cr.CONFIG_FILE).read_text()), "passes": 4}))
        cr.load_settings(path)


def test_the_fallback_researcher_uses_one_combined_pass(tmp_path: Path) -> None:
    prompts: list[str] = []
    combined = answer(different_now="Payrolls land on the resolution day.", against="Yields could fall on the minutes.")
    result = cr.research(rates_dir(tmp_path), lambda text: (prompts.append(text), combined)[1], "2026-10-31",
                         parallel=1, budget=1, by="claude-sonnet", passes=1,
                         checklists={"Interest rates": ["Check the official series."]})
    assert len(prompts) == 1 and result["runs"] == 1 and result["passes"] == 1
    assert "Pass 1 of 3" not in prompts[0] and "Check the official series." in prompts[0]
    assert '"against"' in prompts[0] and '"different_now"' in prompts[0] and '"base_rate"' in prompts[0]
    assert prompts[0].index("outside view") < prompts[0].index("inside view") < prompts[0].index("other side")
    found = result["findings"]["q1"]
    assert found["passes_done"] == ["combined"] and found["against"].startswith("Yields could fall")
    assert found["different_now"].startswith("Payrolls") and found["base_rate"]


def test_the_topic_checklist_goes_into_the_inside_pass_and_the_combined_pass_only(tmp_path: Path) -> None:
    items = ["Open the official series and list its last 10 values.", "List the scheduled releases before resolution."]
    prompts: list[str] = []
    cr.research(rates_dir(tmp_path), staged_agent(prompts), "2026-10-31", parallel=1, budget=3, passes=3,
                checklists={"interest rates": items, "Crypto": ["Never shown for this topic."]})  # topic match ignores case
    outside, inside, against = prompts
    assert "1. Open the official series" in inside and "2. List the scheduled releases" in inside
    assert items[0] not in outside and items[0] not in against and "Never shown for this topic." not in inside
    assert cr.checklist_for({"Interest rates": items}, "Jobs") == [] and cr.checklist_for({}, "") == []
    no_topic = {**TASK, "topic": ""}
    assert "Checklist" not in cr.inside_prompt(no_topic, "2026-10-31", {"facts": []}, [])


def test_cohort_questions_take_their_topic_from_the_series_and_the_checklists_file_is_valid(tmp_path: Path) -> None:
    from forecastlab.plain_questions import TOPICS

    assert cr.COHORT_TOPICS == TOPICS
    directory = tmp_path / "prospective_daily_20261014"
    directory.mkdir()
    (directory / "frozen_manifest.json").write_text("{}")
    (directory / "questions.json").write_text(json.dumps([{**COHORT, "macro": {"indicator": "treasury_10y"}}, COHORT]))
    assert [t["topic"] for t in cr.tasks(directory)] == ["Interest rates", "US economic data"]

    checklists = cr.load_checklists(ROOT / cr.CHECKLIST_FILE)
    known = {"Economy", "Jobs", "Inflation", "Interest rates", "Politics", "Crypto", "Tech", "World"}
    assert checklists["Interest rates"] and set(checklists) <= known
    assert all(1 <= len(items) <= cr.MAX_CHECKLIST_ITEMS for items in checklists.values())
    assert all(not cr.MARKET_WORDS.search(i) for items in checklists.values() for i in items)
    assert cr.load_checklists(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    for body in ({"topics": {"Jobs": ["x"] * 7}}, {"topics": {"Jobs": "text"}}, {"topics": {"Jobs": ["Check the Polymarket price."]}}):
        bad.write_text(json.dumps(body))
        with pytest.raises(ValueError):  # noqa: PT011
            cr.load_checklists(bad)


def test_validation_stays_strict_in_every_pass() -> None:
    market = reply([("https://polymarket.com/event/x", ["Traders say 42%."]), ("https://www.bls.gov/a", ["Fact."])],
                   [("Odds are 42%.", 0), ("A real fact.", 1)],
                   against="Polymarket bettors favour yes. The data point the other way.")
    evidence = cr.validate(market, min_sources=1, min_facts=1, need_against=True)
    assert [s["url"] for s in evidence["sources"]] == ["https://www.bls.gov/a"] and evidence["facts"] == [
        {"fact": "A real fact.", "source": 0}]
    assert evidence["against"] == "The data point the other way."
    with pytest.raises(ValueError, match="no case for the other outcome"):
        cr.validate(OUTSIDE, min_sources=1, min_facts=1, need_against=True)
    with pytest.raises(ValueError, match="usable sources"):
        cr.validate(reply([("https://kalshi.com/x", ["q"])], [("F.", 0)]), min_sources=1, min_facts=1)
    with pytest.raises(ValueError, match="usable facts"):  # the first pass still needs 2 sources and 2 facts
        cr.validate(reply([("https://a.org/1", []), ("https://a.org/2", [])], [("One.", 0)]))


def test_digest_shows_the_case_for_the_other_outcome_after_different_this_time() -> None:
    base = {"facts": [{"fact": "A fact.", "source": 0}], "base_rate": "7 of 10.", "different_now": "Payrolls land.",
            "uncertainties": ["An unknown."]}
    sources = [{"title": "T", "url": "https://a.org/t", "published": "2026-10-30", "verified": True}]
    with_against = cr.digest({"sources": {"q": sources}, "findings": {"q": {**base, "against": "Yields could fall."}}}, "q")
    lines = with_against.splitlines()
    assert lines[:5] == ["- A fact. [1]", "Base rate: 7 of 10.", "Different this time: Payrolls land.",
                         "Case for the other outcome: Yields could fall.", "Unknown: An unknown."]
    assert lines[5].startswith("[1] T (https://a.org/t, 2026-10-30; quotes checked)")
    without = cr.digest({"sources": {"q": sources}, "findings": {"q": base}}, "q")
    assert without == with_against.replace("Case for the other outcome: Yields could fall.\n", "")  # all else identical


def test_merged_research_still_goes_through_the_quote_check(tmp_path: Path) -> None:
    directory = rates_dir(tmp_path)
    saved = cr.research(directory, staged_agent([]), "2026-10-31", parallel=1, budget=3, passes=3)
    (directory / "research.json").write_text(json.dumps(saved))
    page = ("<html><body><article><p>" + "Treasury yields background. " * 30 + "</p><p>The 10-year closed at 4.52. "
            "DGS10 4.52. Several participants saw lower yields.</p></article></body></html>").encode()
    research_download.run([directory], lambda url: ("text/html", page))
    data = json.loads((directory / "research.json").read_text())
    checked = {s["url"]: s["verified"] for s in data["sources"]["q1"]}
    assert checked["https://home.treasury.gov/rates"] is True and checked["https://www.federalreserve.gov/minutes"] is True
    assert checked["https://www.bls.gov/schedule/"] is False  # its quote is not on that page
    assert data["findings"]["q1"]["against"] and data["passes"] == 3 and data["downloaded_at"]
    assert cr.digest(data, "q1").count("Case for the other outcome:") == 1


# --- the miss review --------------------------------------------------------------------------------------

CLAUDE = "claude_code_forecaster_v1"


def tr_question(qid: str, p: float, outcome: int, topic: str, day: str, *, source: str = "prospective_markets_20261020",
                **extra: object) -> dict[str, Any]:
    return {"id": qid, "title": f"Question {qid}?", "status": "resolved", "outcome": outcome, "topic": topic,
            "resolves_on": day, "source": source, "scored": True, "actual": "Polymarket: Yes" if outcome else "Polymarket: No",
            "detail": "Resolves yes if the rate rises. This Polymarket page shows 0.61 for yes.",
            "source_url": f"https://polymarket.com/event/{qid}",
            "forecasts": [{"method": "market_price_v1", "probability": 0.61},
                          {"method": CLAUDE, "probability": p, "rationale": f"Why {qid}. Polymarket prices it at 42%."}], **extra}


def review_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "configs").mkdir()
    (root / "data" / "track_record.json").write_text(json.dumps({"questions": [
        tr_question("wrong", 0.7, 0, "Interest rates", "2026-10-20"),             # a wrong call
        tr_question("bold", 0.95, 1, "Economy", "2026-10-21"),                    # right, squared error 0.0025
        tr_question("big", 0.35, 1, "Economy", "2026-10-22", source="prospective_daily_20261022"),  # wrong
        tr_question("toss", 0.5, 1, "Jobs", "2026-10-23"),                        # a toss-up is not a miss
        tr_question("right", 0.8, 1, "Jobs", "2026-10-24"),
        tr_question("unscored", 0.9, 0, "Jobs", "2026-10-25", scored=False),
        tr_question("late", 0.2, 1, "Crypto", "2026-10-28"),
        {**tr_question("open", 0.9, 0, "Jobs", "2026-11-05"), "status": "pending", "outcome": None},
        {"id": "nomine", "status": "resolved", "outcome": 1, "topic": "Jobs", "resolves_on": "2026-10-26", "title": "T",
         "forecasts": [{"method": "market_price_v1", "probability": 0.1}]}]}))
    (root / "configs" / "research_checklists.json").write_text(json.dumps({"version": 1, "topics": {
        "Economy": ["Open the statistics office's release calendar."]}}))
    directory = root / "artifacts" / "prospective_daily_20261022"
    directory.mkdir(parents=True)
    (directory / "research.json").write_text(json.dumps({
        "provider": "cursor", "model": "grok-4.7-low", "researched_by": {"big": "claude-sonnet"},
        "findings": {"big": {"facts": [{"fact": "GDP rose 0.2% in the advance estimate.", "source": 0}],
                             "base_rate": "2 of 6.", "different_now": "Retail rose.", "uncertainties": ["Revisions."],
                             "against": "The advance can be revised.", "passes_done": ["outside", "inside", "against"]}},
        "sources": {"big": [{"url": "https://a.org/x", "verified": True}, {"url": "https://a.org/y", "verified": None}]}}))
    (directory / "market_snapshot.json").write_text(json.dumps({"big": {"price": 0.4242}}))  # must never be read
    return root


def test_export_lists_wrong_calls_and_big_errors_with_the_research_and_no_market_price(tmp_path: Path) -> None:
    root = review_root(tmp_path)
    packet = miss_review.export(root, today="2026-10-31")
    assert [m["id"] for m in packet["misses"]] == ["wrong", "big", "late"]
    text = json.dumps(packet)
    assert "0.61" not in text and "0.4242" not in text and "polymarket" not in text.lower().replace("polymarket-", "")  # no price, no market name
    assert packet["causes"] == list(miss_review.CAUSES) and packet["since"] is None
    assert packet["current_checklists"] == {"Economy": ["Open the statistics office's release calendar."]}
    big = packet["misses"][1]
    assert (big["date"], big["topic"], big["question"], big["probability"], big["outcome"]) == (
        "2026-10-22", "Economy", "Question big?", 0.35, "yes")
    assert big["squared_error"] == 0.423 and big["actual"] == "Yes" and big["rationale"] == "Why big."
    assert big["resolution_rule"] == "Resolves yes if the rate rises."
    assert big["research"] == {"researched_by": "claude-sonnet", "passes_ran": ["outside", "inside", "against"],
                               "base_rate": "2 of 6.", "different_now": "Retail rose.",
                               "against": "The advance can be revised.", "facts": ["GDP rose 0.2% in the advance estimate."],
                               "unknowns": ["Revisions."], "sources_checked": "1 of 2 sources with every quote found"}
    assert packet["misses"][0]["research"] is None  # no research was saved for it
    assert miss_review.export(root, since="2026-10-22")["misses"][0]["id"] == "big"


def test_export_with_a_squared_error_of_at_least_036_includes_rights_and_wrongs_only_by_that_rule(tmp_path: Path) -> None:
    root = review_root(tmp_path)
    track = json.loads((root / "data" / "track_record.json").read_text())
    track["questions"].append(tr_question("edge", 0.4, 1, "Jobs", "2026-10-27"))  # wrong at 0.4
    track["questions"].append(tr_question("near", 0.45, 1, "Jobs", "2026-10-27"))  # wrong at 0.45
    assert [q["id"] for q, _ in miss_review.misses(track)] == ["wrong", "big", "edge", "near", "late"]
    assert all(((m["probability"] > 0.5) != (m["outcome"] == "yes")) or m["squared_error"] >= 0.36
               for m in miss_review.export(root)["misses"])


def test_export_starts_after_the_newest_review_and_skips_misses_already_reviewed(tmp_path: Path) -> None:
    root = review_root(tmp_path)
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"reviewed_on": "2026-10-22", "misses": [
        {"id": "wrong", "cause": "stale_data", "note": "Used a headline level."},
        {"id": "big", "cause": "unlucky", "note": "A late revision."}]}))
    saved = miss_review.apply(root, review, today="2026-10-31")
    assert saved["path"] == "data/miss_reviews/2026-10-22.json"
    assert [m["id"] for m in miss_review.export(root)["misses"]] == ["late"]
    assert miss_review.export(root)["since"] == "2026-10-22"
    again = miss_review.export(root, since="2026-10-01")  # an explicit date lists everything from then
    assert [m["id"] for m in again["misses"]] == ["wrong", "big", "late"]


def test_apply_validates_the_review(tmp_path: Path) -> None:
    root = review_root(tmp_path)

    def attempt(body: object, match: str) -> None:
        path = tmp_path / "r.json"
        path.write_text(json.dumps(body))
        with pytest.raises(ValueError, match=match):
            miss_review.apply(root, path, today="2026-10-31")

    ok = {"id": "wrong", "cause": "overconfident", "note": "Too sure."}
    attempt([], "JSON object")
    attempt({"misses": [{**ok, "cause": "bad_luck"}]}, "cause must be one of")
    attempt({"misses": [{**ok, "note": ""}]}, "note must be a non-empty string")
    attempt({"misses": [{**ok, "note": "x" * 301}]}, "longer than 300")
    attempt({"misses": [ok, ok]}, "listed twice")
    attempt({"misses": [{**ok, "id": "ghost"}]}, "not a resolved question")
    attempt({"misses": [{**ok, "id": "open"}]}, "not a resolved question")
    attempt({"reviewed_on": "31/10/2026", "misses": [ok]}, "YYYY-MM-DD")
    attempt({"misses": [], "checklist_items": {}}, "no misses and no checklist items")
    attempt({"misses": [ok], "checklist_items": {"Jobs": ["Check Polymarket for the price."]}}, "market or odds")
    attempt({"misses": [ok], "checklist_items": {"Jobs": ["Look at the betting odds."]}}, "market or odds")
    attempt({"misses": [ok], "checklist_items": {"Jobs": ["Short."]}}, "too short")
    attempt({"misses": [ok], "checklist_items": {"Jobs": "Not a list of items."}}, "must be a list")
    assert not (root / "data" / "miss_reviews").exists()  # nothing is saved for a rejected review
    assert json.loads((root / "configs" / "research_checklists.json").read_text())["version"] == 1


def test_apply_saves_the_review_and_merges_checklist_items(tmp_path: Path) -> None:
    root = review_root(tmp_path)
    existing = [f"Existing instruction number {n} for the economy topic." for n in range(1, 6)]
    (root / "configs" / "research_checklists.json").write_text(json.dumps({"version": 3, "topics": {
        "Economy": existing, "Jobs": ["Look at the first print of the claims release."]}}, indent=2))
    review = tmp_path / "review.json"
    review.write_text(json.dumps({
        "reviewed_on": "2026-10-31", "summary": "Two stale numbers.",
        "misses": [{"id": "wrong", "cause": "stale_data", "note": "Used a  headline\nlevel."},
                   {"id": "late", "cause": "missed_scheduled_event", "note": "A court date."}],
        "checklist_items": {"economy": ["Existing instruction number 2 for the economy topic.",   # a duplicate
                                        "NEW instruction A for the economy topic.",
                                        "New instruction b for the economy topic."],
                            "Jobs": ["Look at the FIRST print of the claims release."],         # a duplicate, other case
                            "Crypto": ["Find the exchange named in the rules and its price window."]}}))
    saved = miss_review.apply(root, review, today="2026-10-31")
    assert saved["misses"][0] == {"id": "wrong", "cause": "stale_data", "note": "Used a headline level.",
                                  "topic": "Interest rates", "resolves_on": "2026-10-20", "probability": 0.7, "outcome": 0}
    assert saved["checklist_added"] == {"Economy": ["NEW instruction A for the economy topic.",
                                                    "New instruction b for the economy topic."],
                                        "Crypto": ["Find the exchange named in the rules and its price window."]}
    assert saved["checklist_dropped"] == {"Economy": [existing[0]]}  # six at most; the oldest goes first
    checklists = json.loads((root / "configs" / "research_checklists.json").read_text())
    assert checklists["version"] == 4 and checklists["updated"] == "2026-10-31"
    assert checklists["topics"]["Economy"] == [*existing[1:], "NEW instruction A for the economy topic.",
                                              "New instruction b for the economy topic."][-6:]
    assert len(checklists["topics"]["Economy"]) == 6 and len(checklists["topics"]["Jobs"]) == 1
    assert list(checklists["topics"]) == ["Economy", "Jobs", "Crypto"]
    on_disk = json.loads((root / "data" / "miss_reviews" / "2026-10-31.json").read_text())
    assert on_disk["summary"] == "Two stale numbers." and on_disk["checklist_version"] == 4
    assert cr.load_checklists(root / "configs" / "research_checklists.json")["Economy"][-1].startswith("New instruction b")
    second = miss_review.apply(root, review, today="2026-10-31")  # the same day again keeps the first file
    assert second["path"] == "data/miss_reviews/2026-10-31-2.json" and not second["checklist_added"]
    assert json.loads((root / "configs" / "research_checklists.json").read_text())["version"] == 4  # nothing new, no bump


def test_miss_review_cli_exports_to_a_file_and_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    root = review_root(tmp_path)
    monkeypatch.setattr(miss_review, "ROOT", root)
    target = tmp_path / "packet.json"
    miss_review.main(["export", "--out", str(target)])
    assert [m["id"] for m in json.loads(target.read_text())["misses"]] == ["wrong", "big", "late"]
    miss_review.main(["export", "--since", "2026-10-28"])
    assert [m["id"] for m in json.loads(capsys.readouterr().out.split("\n", 1)[1])["misses"]] == ["late"]
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"misses": [{"id": "late", "cause": "other", "note": "Unclear."}]}))
    miss_review.main(["apply", str(review)])
    assert "1 misses (other 1)" in capsys.readouterr().out
    review.write_text(json.dumps({"misses": [{"id": "late", "cause": "nope", "note": "x"}]}))
    with pytest.raises(SystemExit, match="cause must be one of"):
        miss_review.main(["apply", str(review)])
    with pytest.raises(SystemExit):
        miss_review.main(["export", "--since", "yesterday"])
