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


def test_the_research_model_cannot_judge() -> None:
    live = load_config(ROOT / "configs" / "judge_panel.json")
    panel_script.check_independent(live)  # Haiku researches, Sonnet judges
    with_haiku = PanelConfig(**{**live.model_dump(), "members": [*live.members, Member(method="judge_haiku", model="anthropic/claude-haiku-4.5")]})
    with pytest.raises(SystemExit, match="cannot judge"):
        panel_script.check_independent(with_haiku)
    assert any("claude" in m.model for m in live.members)  # a family rule would leave the panel empty


def test_with_cursor_the_whole_model_family_cannot_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(panel_script, "ROOT", tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "research.json").write_text(json.dumps(cursor_config()))
    live = load_config(ROOT / "configs" / "judge_panel.json")
    panel_script.check_independent(live)
    with_grok = PanelConfig(**{**live.model_dump(), "members": [*live.members, Member(method="judge_grok", model="x-ai/grok-4.7")]})
    with pytest.raises(SystemExit, match="cannot judge"):
        panel_script.check_independent(with_grok)


def cursor_config() -> dict[str, Any]:
    """The previous configs/research.json: Cursor (Grok) first, Claude Sonnet filling the gaps."""
    return {"mode": "live", "provider": "cursor", "model": "grok-4.7-low", "model_family": "grok", "passes": 5,
            "parallel": 3, "timeout_s": 420, "daily_run_cap": 120, "weekly_run_cap": 1000, "window_hours": [2, 8],
            "window_tz": "America/Los_Angeles", "base_branch": "grok/forecastlab-mvp",
            "fallback": {"provider": "claude", "model": "sonnet", "model_family": "claude", "timeout_s": 600, "passes": 1}}


def test_a_judge_sits_out_the_questions_its_own_family_researched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = market_dir(tmp_path)
    second = dict(MARKET, id="polymarket-2", question="Will payrolls rise?")
    (directory / "forecasts.json").write_text(json.dumps({"questions": [MARKET, second]}))
    research = cr.research(directory, lambda text: answer(), "2026-10-14", parallel=1, budget=5, by="grok-4.7-low")
    research["researched_by"]["polymarket-2"] = "claude-sonnet"
    (directory / "research.json").write_text(json.dumps(research))
    monkeypatch.setattr(panel_script, "ROOT", tmp_path)  # the Cursor-first config, where Claude only filled gaps
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "research.json").write_text(json.dumps(cursor_config()))
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
    assert settings.mode in ("dry_run", "live")
    assert (settings.provider, settings.model, settings.passes, settings.timeout_s, settings.parallel) == ("claude", "haiku", 5, 600, 3)
    assert settings.families == ("haiku",) and settings.barred == ("haiku",) and not settings.fallback_model
    old = write_config(cursor_config())
    cursor = cr.load_settings(old)
    assert cursor.provider == "cursor" and cursor.families == ("grok", "claude") and cursor.barred == ("grok",)


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



def test_merge_research_keeps_the_branchs_research_and_adds_ours_only_where_it_has_none() -> None:
    theirs = {"provider": "cursor", "runs": 2, "findings": {"a": {"facts": ["claude a"]}}, "failed": {"b": "timeout"},
              "sources": {"a": [{"url": "https://x/a"}]}, "researched_by": {"a": "claude-sonnet"}}
    ours = {"provider": "cursor", "runs": 9, "findings": {"a": {"facts": ["cursor a"]}, "b": {"facts": ["cursor b"]}},
            "failed": {"c": "no sources"}, "sources": {"a": [{"url": "https://y/a"}], "b": [{"url": "https://y/b"}]},
            "researched_by": {"a": "grok-4.7-low", "b": "grok-4.7-low"}}
    merged = research_script.merge_research(theirs, ours)
    assert merged["findings"] == {"a": {"facts": ["claude a"]}, "b": {"facts": ["cursor b"]}}
    assert merged["researched_by"] == {"a": "claude-sonnet", "b": "grok-4.7-low"}
    assert merged["sources"]["a"] == [{"url": "https://x/a"}]
    assert merged["failed"] == {"c": "no sources"}
    assert merged["runs"] == 11
    assert research_script.merge_research({}, ours) == ours


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                          capture_output=True, text=True).stdout


def test_a_refused_research_push_is_merged_onto_the_branch_instead_of_failing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    origin, mac, cloud, work = tmp_path / "origin.git", tmp_path / "mac", tmp_path / "cloud", tmp_path / "work"
    branch, name = "claude/daily-forecasts-20261104", "artifacts/prospective_markets_20261104"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    for clone in (mac, cloud):
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    (cloud / name).mkdir(parents=True)
    (cloud / name / "questions.json").write_text("{}\n")
    _git(cloud, "add", "-A"), _git(cloud, "commit", "-q", "-m", "prepare"), _git(cloud, "push", "-q", "origin",
                                                                                 f"HEAD:refs/heads/{branch}")
    _git(mac, "fetch", "-q", "origin", branch)
    _git(mac, "worktree", "add", "-q", "--detach", str(work), f"origin/{branch}")
    # the routine's fill lands first, with Claude research for question a
    (cloud / name / "research.json").write_text(json.dumps({"findings": {"a": {"facts": ["claude"]}}, "failed": {},
                                                            "researched_by": {"a": "claude-sonnet"}}))
    _git(cloud, "add", "-A"), _git(cloud, "commit", "-q", "-m", "fill"), _git(cloud, "push", "-q", "origin",
                                                                              f"HEAD:refs/heads/{branch}")
    # Cursor, which started earlier, finishes a and b
    (work / name / "research.json").write_text(json.dumps({"findings": {"a": {"facts": ["cursor"]},
                                                                        "b": {"facts": ["cursor"]}}, "failed": {},
                                                           "researched_by": {"a": "grok", "b": "grok"}}))
    monkeypatch.setattr(research_script, "WORK", work)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"), monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t"), monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    monkeypatch.setattr(research_script.git, "__kwdefaults__", {"cwd": mac, "check": True})

    assert research_script.push_request(branch, "download", [name], "Research: cursor") == [name]
    _git(cloud, "pull", "-q", "origin", branch)
    saved = json.loads((cloud / name / "research.json").read_text())
    assert saved["findings"] == {"a": {"facts": ["claude"]}, "b": {"facts": ["cursor"]}}
    assert json.loads((cloud / "daily-run-request.json").read_text())["dirs"] == [name]

    # once the directory has been forecast, a late push changes nothing there
    (cloud / name / "briefs.json").write_text("{}\n")
    _git(cloud, "add", "-A"), _git(cloud, "commit", "-q", "-m", "forecast"), _git(cloud, "push", "-q", "origin",
                                                                                  f"HEAD:refs/heads/{branch}")
    (work / name / "research.json").write_text(json.dumps({"findings": {"c": {"facts": ["cursor"]}}, "failed": {}}))
    assert research_script.push_request(branch, "download", [name], "Research: late") == []

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
    assert len(urls) == cr.MAX_TOTAL_SOURCES == 20
    assert sum("/a" in u for u in urls) == 8 and sum("/b" in u for u in urls) == 8 and sum("/c" in u for u in urls) == 4
    assert all(f["source"] < 20 for f in capped["facts"]) and len(capped["facts"]) == 20  # facts of dropped sources go


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


def test_the_daily_cap_fits_twenty_five_pass_questions_with_the_primary_round() -> None:
    settings = cr.load_settings(ROOT / cr.CONFIG_FILE)
    assert (settings.passes, settings.fallback_passes) == (5, 1)
    assert settings.model == "haiku"
    each = cr.runs_per_question(settings.passes)
    assert each == 6  # five passes and, at most, the one extra primary-source round
    assert (cr.runs_per_question(3), cr.runs_per_question(1)) == (3, 1)  # the older plans are unchanged
    assert 10 * each == 60 <= settings.daily_run_cap and 20 * each <= settings.daily_run_cap  # ~10 a day, room for 20
    assert 5 * settings.daily_run_cap <= settings.weekly_run_cap


def test_two_passes_skip_the_other_side(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path), lambda text: (prompts.append(text), OUTSIDE if "Pass 1" in text else INSIDE)[1],
                         "2026-10-31", parallel=1, budget=10, passes=2)
    assert [p.split("\n\n")[1].split(":")[0] for p in prompts] == ["Pass 1 of 2", "Pass 2 of 2"]
    assert result["findings"]["q1"]["passes_done"] == ["outside", "inside"] and result["findings"]["q1"]["against"] == ""
    assert "Case for the other outcome" not in cr.digest(result, "q1")
    with pytest.raises(ValueError, match="passes must be"):
        path = tmp_path / "research.json"
        path.write_text(json.dumps({**json.loads((ROOT / cr.CONFIG_FILE).read_text()), "passes": 6}))
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


def _own(title: str, topic: str, resolves_on: str, p: float, made_at: str, status: str = "pending",
         outcome: int | None = None, qid: str = "") -> dict[str, Any]:
    return {"id": qid or title, "title": title, "topic": topic, "resolves_on": resolves_on, "status": status,
            "outcome": outcome,
            "forecasts": [{"method": "claude_code_forecaster_v1", "probability": p, "made_at": made_at},
                          {"method": "market_price_v1", "probability": 0.111, "made_at": made_at},
                          {"method": "judge_kimi_k26_v1", "probability": 0.222, "made_at": made_at}]}


def test_related_forecasts_list_only_claudes_own_calls_on_the_same_outcome() -> None:
    record = {"questions": [
        _own("Will the Republicans win the Ohio governor race in 2026?", "Politics", "2026-11-04", 0.57,
             "2026-10-28T11:01:48+00:00", "resolved", 1, "gov"),
        _own("Will the Democrats win the Ohio Senate race in 2026?", "Politics", "2026-11-04", 0.47,
             "2026-10-26T11:14:05+00:00", "pending", None, "sen"),
        _own("Will Vivek Ramaswamy be the next Governor of Ohio?", "Politics", "2026-11-04", 0.41,
             "2026-10-21T10:55:44+00:00", "pending", None, "viv"),
        _own("Will Ohio State win the Big Ten?", "Sports", "2026-11-30", 0.3, "2026-10-21T00:00:00+00:00"),
        _own("Will the Democrats win the Texas Senate race in 2026?", "Politics", "2026-11-04", 0.2,
             "2026-10-21T00:00:00+00:00"),
        _own("Will Ohio ban phones in schools?", "Politics", "2026-08-01", 0.5, "2026-07-01T00:00:00+00:00",
             "resolved", 0, "old"),
    ]}
    task = {"id": "viv", "question": "Will Vivek Ramaswamy be the next Governor of Ohio?", "topic": "Politics",
            "resolves_on": "2026-11-04"}
    other = {"id": "gov", "question": "Will the Republicans win the Ohio governor race in 2026?", "topic": "Politics",
             "resolves_on": "2026-11-04"}
    text = cr.related_forecasts(record, task, "2026-11-04")
    assert text.startswith("Your earlier forecasts on related questions:")
    assert "Republicans win the Ohio governor race in 2026? You said 57% yes on 2026-10-28; resolves 2026-11-04 (resolved yes)" in text
    assert "Ohio Senate race" in text and "(pending)" in text
    for left_out in ("Ramaswamy", "Big Ten", "Texas", "phones", "11%", "22%", "11.1", "22.2"):
        assert left_out not in text
    assert text.endswith("if the evidence has changed, say why in the rationale.")
    assert "Ramaswamy be the next Governor of Ohio? You said 41%" in cr.related_forecasts(record, other, "2026-11-04")
    assert "Ohio governor" not in cr.related_forecasts(record, {**task, "topic": "Sports"}, "2026-11-04")  # other topic


def test_related_forecasts_cap_order_and_nothing_to_show() -> None:
    record = {"questions": [_own(f"Will Bitcoin hit {n}?", "Crypto", f"2026-11-{n:02d}", 0.5, "2026-10-20T00:00:00+00:00")
                            for n in range(10, 20)]}
    task = {"id": "x", "question": "Will BTC be above $100k?", "topic": "Crypto", "resolves_on": "2026-11-12"}
    lines = cr.related_forecasts(record, task, "2026-11-04").splitlines()
    assert len(lines) == 8  # heading, 6 questions, coherence line
    assert [line.split("resolves ")[1][:10] for line in lines[1:7]] == [
        "2026-11-12", "2026-11-11", "2026-11-13", "2026-11-10", "2026-11-14", "2026-11-15"]
    assert cr.related_forecasts(record, {**task, "question": "Will the price be above 2026?"}, "2026-11-04") == ""
    assert cr.related_forecasts({"questions": []}, task, "2026-11-04") == ""


def test_cursor_agent_retries_a_timed_out_run_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        return subprocess.CompletedProcess(args[0], 0, stdout="found it", stderr="")

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    assert cr.cursor_agent("m", 5)("q") == "found it" and len(calls) == 2

    calls.clear()
    monkeypatch.setattr(cr.subprocess, "run", lambda *a, **k: (calls.append(1), (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 5)))[1])
    with pytest.raises(subprocess.TimeoutExpired):
        cr.cursor_agent("m", 5)("q")
    assert len(calls) == cr.CURSOR_TRIES


# ---- live tallies and the price-math line ----------------------------------------------------

def count_task(question: str = "Will commitments to the 1W public sale exceed $20M?", resolves_on: str = "2026-11-10") -> dict[str, str]:
    return {"id": "c1", "question": question, "criteria": "Resolves yes if the total committed on the sale page exceeds $20,000,000.",
            "resolves_on": resolves_on, "topic": "Crypto"}


@pytest.mark.parametrize("question,expected", [
    ("Will commitments to the 1W public sale exceed $20M?", True),
    ("Will Claude go down 5-7 times in October?", True),
    ("How many tweets will Elon Musk post this week?", True),
    ("Will Elon Musk post between 400 and 419 tweets from Nov 1 to Nov 8?", True),
    ("Will the Senate pass at least 3 bills?", False),
    ("Will CPI exceed 3.0%?", False),
    ("Will Bitcoin reach $120,000 in November?", False),
    ("Will the price of Ethereum be above $2,200 on November 8?", False),
    ("Will Democrats win the Senate?", False),
])
def test_count_questions_are_detected_and_asset_prices_are_not(question: str, expected: bool) -> None:
    assert cr.live_counts.is_count_question({"question": question, "criteria": ""}, "2026-11-05") is expected


def test_thresholds_come_from_the_question() -> None:
    thresholds = cr.live_counts.thresholds
    assert thresholds({"question": "Will commitments exceed $20M?"}) == [20e6]
    assert thresholds({"question": "Will Claude go down 5-7 times in October?"}) == [5.0, 7.0]
    assert thresholds({"question": "Will he post between 400 and 419 tweets?"}) == [400.0, 419.0]
    assert thresholds({"question": "How many tweets will he post Oct 24-31?", "criteria": ""}) == []


def test_count_prompts_ask_for_the_tally_in_every_pass_and_ordinary_ones_do_not() -> None:
    task = count_task()
    outside, combined = cr.outside_prompt(task, "2026-11-05"), cr.prompt(task, "2026-11-05")
    inside = cr.inside_prompt(task, "2026-11-05", {"facts": [], "sources": []})
    against = cr.against_prompt(task, "2026-11-05", {"facts": [], "sources": []})
    for text in (outside, combined, inside, against):
        assert "COUNT/TOTAL QUESTION" in text and '"tally"' in text and "earlier_value" in text
        assert not cr.MARKET_WORDS.search(text.replace("Never use or mention prediction markets, betting sites or odds "
                                                       "(Polymarket, Kalshi, Manifold, Metaculus, PredictIt, bookmakers).", ""))
    plain = cr.outside_prompt({**task, "question": "Will the Fed cut rates?", "criteria": "FOMC statement."}, "2026-11-05")
    assert "tally" not in plain and "spot" not in plain


def test_price_prompts_ask_for_the_spot_and_volatility() -> None:
    task = {"id": "p1", "question": "Will Bitcoin dip to $78,000 November 6-8?", "criteria": "Binance BTCUSDT 1m low.",
            "resolves_on": "2026-11-08", "topic": "Crypto"}
    text = cr.prompt(task, "2026-11-05")
    assert "ASSET-PRICE QUESTION" in text and '"spot"' in text and "daily_vol_30d" in text and "tally" not in text


READ = {"value": 12_000_000, "unit": "USD", "source_url": "https://sale.example.org/1w", "read_at": "2026-11-05T12:00 UTC",
        "earlier_value": 9_000_000, "earlier_at": "2026-11-04T12:00 UTC"}


def tally_reply(**extra: object) -> str:
    return answer(tally=READ, **extra)


def test_validate_keeps_a_usable_tally_and_drops_bad_ones() -> None:
    assert cr.validate(tally_reply())["tally"] == {**READ, "value": 12e6, "earlier_value": 9e6}
    for bad in ({**READ, "source_url": "https://polymarket.com/event/x"}, {**READ, "value": "lots"}, {**READ, "value": -1}, "12M", None):
        assert "tally" not in cr.validate(answer(tally=bad))
    no_earlier = cr.validate(answer(tally={**READ, "earlier_at": ""}))["tally"]
    assert "earlier_value" not in no_earlier


def test_the_tally_is_saved_and_the_latest_reading_wins_the_merge(tmp_path: Path) -> None:
    older = cr.validate(answer(tally={**READ, "value": 10_000_000, "read_at": "2026-11-05T09:00 UTC"}))
    newer = cr.validate(answer(tally=READ))
    assert cr.merge({"outside": newer, "inside": older})["tally"]["value"] == 12e6
    assert "tally" not in cr.merge({"outside": cr.validate(answer())})
    directory = tmp_path / "prospective_markets_20261105"
    directory.mkdir()
    (directory / "forecasts.json").write_text(json.dumps({"questions": [
        {**MARKET, "id": "c1", "question": "Will commitments to the 1W public sale exceed $20M?", "topic": "Crypto",
         "resolution_date": "2026-11-10"}]}))
    result = cr.research(directory, lambda text: tally_reply(), "2026-11-05", parallel=1, budget=3, passes=1)
    assert result["findings"]["c1"]["tally"]["value"] == 12e6


def test_digest_opens_a_count_question_with_the_tally_projection_and_needed_rate() -> None:
    data = {"sources": {"c1": []}, "findings": {"c1": {"facts": [], "uncertainties": [], "tally": cr.validate(tally_reply())["tally"]}}}
    lines = cr.digest(data, "c1", count_task(), "2026-11-05").splitlines()
    assert lines[0].startswith("LIVE TALLY: 12.0M USD, read 2026-11-05T12:00 UTC from https://sale.example.org/1w")
    text = "\n".join(lines)
    assert "rate 3.0M USD per day over 1.0 days" in text
    assert "Deadline 2026-11-10: 5.5 days left" in text
    assert "Projection at the current rate" in text and "28.5M USD by the deadline" in text  # 12M + 3M x 5.5
    assert "Threshold 20.0M: needs 8.0M more, i.e. 1.5M USD per day (current rate 3.0M/day)" in text


def test_digest_handles_a_reached_threshold_one_reading_and_an_old_reading() -> None:
    one = {**READ, "value": 25e6}
    one.pop("earlier_value"), one.pop("earlier_at")
    data = {"sources": {"c1": []}, "findings": {"c1": {"facts": [], "uncertainties": [], "tally": one}}}
    text = cr.digest(data, "c1", count_task(), "2026-11-05")
    assert "already reached" in text and "No earlier point reported, so no rate." in text and "Projection" not in text
    old = cr.digest(data, "c1", count_task(), "2026-11-12")
    assert "WARNING: this reading is 6 days old" in old


def test_digest_warns_when_a_count_question_has_no_tally_and_is_unchanged_for_other_questions() -> None:
    data = {"sources": {"c1": []}, "findings": {"c1": {"facts": [], "uncertainties": []}}}
    assert cr.digest(data, "c1", count_task(), "2026-11-05").startswith("WARNING: no running tally found")
    assert cr.digest(data, "c1") == "" or "WARNING" not in cr.digest(data, "c1")
    plain = {**count_task(), "question": "Will the Fed cut rates in December?", "criteria": "FOMC statement."}
    assert cr.digest(data, "c1", plain, "2026-11-05") == cr.digest(data, "c1")


PRICE_TASK = {"id": "p1", "question": "Will Bitcoin dip to $78,000 November 6-8?", "criteria": "Binance BTCUSDT 1m low.",
              "resolves_on": "2026-11-08", "topic": "Crypto"}
SPOT = {"asset": "BTC", "price": 85_000, "source_url": "https://www.coinbase.com/price/bitcoin",
        "read_at": "2026-11-05T12:00 UTC", "daily_vol_30d": 0.03}


def test_validate_keeps_the_spot_and_reads_a_percent_volatility() -> None:
    spot = cr.validate(answer(spot=SPOT))["spot"]
    assert spot["price"] == 85000 and spot["daily_vol"] == 0.03
    assert cr.validate(answer(spot={**SPOT, "daily_vol_30d": 3.0}))["spot"]["daily_vol"] == 0.03
    assert "daily_vol" not in cr.validate(answer(spot={**SPOT, "daily_vol_30d": None}))["spot"]
    assert "spot" not in cr.validate(answer(spot={**SPOT, "price": 0}))


def test_digest_adds_one_price_math_reference_line_when_it_can() -> None:
    from forecastlab.price_model import parse_question, price_model_probability
    spot = cr.validate(answer(spot=SPOT))["spot"]
    data = {"sources": {"p1": []}, "findings": {"p1": {"facts": [], "uncertainties": [], "spot": spot}}}
    lines = cr.digest(data, "p1", PRICE_TASK, "2026-11-05").splitlines()
    expected = price_model_probability(parse_question(PRICE_TASK["question"], PRICE_TASK["criteria"], datetime(2026, 11, 5).date()),
                                       85000, datetime(2026, 11, 5).date(), 0.03)
    assert lines[0].startswith(f"Price math (thin-tailed, tends to underrate sudden dips): {expected:.0%}.")
    assert "Reference only, not the call" in lines[0]
    assert sum("Price math" in line for line in lines) == 1
    no_vol = {**data, "findings": {"p1": {"facts": [], "uncertainties": [], "spot": {**spot, "daily_vol": None}}}}
    assert "Price math" not in cr.digest(no_vol, "p1", PRICE_TASK, "2026-11-05")
    assert "Price math" not in cr.digest(data, "p1", {**PRICE_TASK, "question": "Will Bitcoin be popular?"}, "2026-11-05")


def test_politics_checklist_asks_for_polling_averages_model_forecasts_and_ranges() -> None:
    items = cr.load_checklists(ROOT / cr.CHECKLIST_FILE)["Politics"]
    joined = " ".join(items).lower()
    assert len(items) <= cr.MAX_CHECKLIST_ITEMS
    for wanted in ("polling average", "number of pollsters", "cook political report", "sabato", "silver bulletin", "the economist",
                   "distribution or range"):
        assert wanted in joined
    assert not cr.MARKET_WORDS.search(joined)


# --- five passes: rules first, gap last, the primary-source gate ---

RULES = reply([("https://www.federalreserve.gov/rules", ["The series is the H.15 daily release."])],
              [("The settling series is H.15 DGS10.", 0)],
              resolution={"settling_source": "Federal Reserve H.15 (https://www.federalreserve.gov/releases/h15/)",
                          "criteria": "First published daily 10-year par yield for Nov 6.",
                          "current_status": "4.52 on 2026-10-30.",
                          "edge_cases": ["Revisions do not count.", "A tie at 4.50 resolves No."]})
GAP = reply([("https://www.bls.gov/news.release/empsit.htm", ["The next release is on November 6."])],
            [("Payrolls release 2026-11-06 at 8:30 ET.", 0)], uncertainties=["Revision to the October print."])
EXTRA = reply([("https://home.treasury.gov/data", ["Daily Treasury Par Yield Curve Rates"])], [])


def five_agent(prompts: list[str], *, rules: object = RULES, outside: object = OUTSIDE, extra: object = EXTRA,
               gap: object = GAP) -> Any:
    """Answers by pass; the extra round is recognized by its header."""
    def agent(text: str) -> str:
        prompts.append(text)
        which = (extra if "Extra round: primary sources only" in text else rules if "Pass 1 of 5" in text
                 else outside if "Pass 2 of 5" in text else inside_for(text) if "Pass 3 of 5" in text
                 else AGAINST if "Pass 4 of 5" in text else gap)
        if isinstance(which, Exception):
            raise which
        return str(which)
    return agent


def inside_for(text: str) -> str:
    return INSIDE


def test_five_passes_run_in_order_and_the_rules_summary_is_merged(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path), five_agent(prompts), "2026-10-31", parallel=1, budget=10, passes=5)
    assert [p.split("\n\n")[1].split(":")[0] for p in prompts] == [f"Pass {n} of 5" for n in range(1, 6)]
    assert "the rules" in prompts[0] and "time zones" in prompts[0] and "settling_source" in prompts[0]
    assert "the outside view" in prompts[1] and "the inside view" in prompts[2]
    assert "the other side" in prompts[3] and "the gaps" in prompts[4]
    assert "Settling source: Federal Reserve H.15" in prompts[1 + 1]  # later passes get the rules
    assert result["runs"] == 5 and result["passes"] == 5  # two primary sources (federalreserve.gov, bls.gov): no extra round
    found = result["findings"]["q1"]
    assert found["passes_done"] == ["rules", "outside", "inside", "against", "gap"]
    assert found["resolution"]["settling_source"].startswith("Federal Reserve H.15")
    assert found["resolution"]["edge_cases"] == ["Revisions do not count.", "A tie at 4.50 resolves No."]
    assert "warnings" not in found and found["primary_sources"] >= 2
    text = cr.digest(result, "q1")
    assert "Resolution rules:" in text and "Settled by: Federal Reserve H.15" in text and "Edge cases: Revisions" in text
    assert text.index("Resolution rules:") < text.index("- The settling series")
    for prompt in prompts:
        assert "Never use or mention prediction markets" in prompt and "published before now" in prompt


def test_the_gap_pass_gets_everything_found_so_far(tmp_path: Path) -> None:
    prompts: list[str] = []
    cr.research(rates_dir(tmp_path), five_agent(prompts), "2026-10-31", parallel=1, budget=10, passes=5)
    gap = prompts[4]
    for expected in ("A tie at 4.50 resolves No.", "31 of the last 60 five-day windows ended higher.",
                     "Payrolls land on the resolution day.", "Yields could fall", "Still unknown: Auction demand.",
                     "https://www.federalreserve.gov/minutes", "- DGS10 was 4.52 on 2026-10-30."):
        assert expected in gap
    assert "2 or 3 open unknowns" in gap and "primary sources first" in gap


def test_lower_pass_settings_keep_the_older_plans(tmp_path: Path) -> None:
    assert [cr.plan_for(n) for n in (1, 2, 3)] == [("combined",), ("outside", "inside"), ("outside", "inside", "against")]
    assert cr.plan_for(4) == ("rules", "outside", "inside", "against") and cr.plan_for(9) == cr.plan_for(5)
    prompts: list[str] = []
    replies = [RULES, OUTSIDE, INSIDE, AGAINST]
    result = cr.research(rates_dir(tmp_path), lambda text: (prompts.append(text), replies[len(prompts) - 1])[1],
                         "2026-10-31", parallel=1, budget=10, passes=4)
    assert "Pass 4 of 4" in prompts[3] and result["runs"] == 4 and len(prompts) == 4  # no gap pass
    assert result["findings"]["q1"]["passes_done"] == ["rules", "outside", "inside", "against"]


def test_a_failed_rules_pass_does_not_fail_the_question(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path), five_agent(prompts, rules=TimeoutError("slow")), "2026-10-31",
                         parallel=1, budget=10, passes=5)
    found = result["findings"]["q1"]
    assert "rules" not in found["passes_done"] and "TimeoutError" in found["passes_failed"]["rules"]
    assert "resolution" not in found and "Resolution rules" not in cr.digest(result, "q1")


def test_primary_round_runs_when_short_and_not_when_enough(tmp_path: Path) -> None:
    def secondary(url: str) -> str:
        return reply([(url, ["A line copied from the page."])], [("A dated fact.", 0)], base_rate="x")

    news = reply([("https://www.example-news.com/a", ["Q."]), ("https://www.example-news.com/b", ["Q."])],
                 [("A dated fact.", 0), ("Another fact.", 1)], base_rate="x")
    prompts: list[str] = []
    short = {"rules": secondary("https://www.example-news.com/r"), "outside": news,
             "gap": secondary("https://blog.example.org/g")}
    plain = reply([("https://blog.example.org/x", ["Q."])], [("A fact.", 0)], different_now="d", uncertainties=["u"])
    against = reply([("https://blog.example.org/y", ["Q."])], [("A fact two.", 0)], against="a")

    def agent(text: str) -> str:
        prompts.append(text)
        if "Extra round: primary sources only" in text:
            return EXTRA
        for n, key in enumerate(("rules", "outside"), 1):
            if f"Pass {n} of 5" in text:
                return short[key]
        return plain if "Pass 3 of 5" in text else against if "Pass 4 of 5" in text else short["gap"]

    result = cr.research(rates_dir(tmp_path), agent, "2026-10-31", parallel=1, budget=10, passes=5)
    assert len(prompts) == 6 and result["runs"] == 6 and "Extra round: primary sources only" in prompts[5]
    assert "Do not list news articles" in prompts[5] and "https://blog.example.org/y" in prompts[5]
    found = result["findings"]["q1"]
    assert found["passes_done"][-1] == "primary" and found["primary_sources"] == 1  # home.treasury.gov is .gov; one primary
    assert "warnings" in found  # still short of two primary sources
    assert "WARNING: only 1 primary source(s)" in cr.digest(result, "q1")

    enough: list[str] = []
    done = cr.research(rates_dir(tmp_path / "ok"), five_agent(enough), "2026-10-31", parallel=1, budget=10, passes=5)
    assert done["runs"] == 5 and all("Extra round" not in p for p in enough)


def test_the_primary_round_that_finds_enough_clears_the_warning(tmp_path: Path) -> None:
    secondary = reply([("https://blog.example.org/a", ["Q."]), ("https://blog.example.org/b", ["Q."])],
                      [("A fact.", 0), ("Another fact.", 1)], base_rate="x", different_now="d", against="a",
                      uncertainties=["u"])
    two = reply([("https://home.treasury.gov/data", ["Rates."]), ("https://www.bls.gov/cpi/", ["CPI."])], [])

    def agent(text: str) -> str:
        return two if "Extra round" in text else secondary

    result = cr.research(rates_dir(tmp_path), agent, "2026-10-31", parallel=1, budget=10, passes=5)
    found = result["findings"]["q1"]
    assert found["primary_sources"] == 2 and "warnings" not in found and result["runs"] == 6


def test_is_primary_uses_the_flag_the_host_the_settling_source_and_needs_quotes() -> None:
    quote = ["q"]
    assert cr.is_primary({"url": "https://www.bls.gov/cpi/", "quotes": quote})
    assert cr.is_primary({"url": "https://www.ecb.europa.eu/x", "quotes": quote})
    assert cr.is_primary({"url": "https://www.ons.gov.uk/x", "quotes": quote})
    assert cr.is_primary({"url": "https://fred.stlouisfed.org/series/DGS10", "quotes": quote})
    assert cr.is_primary({"url": "https://news.example.com/a", "quotes": quote, "primary": True})
    assert cr.is_primary({"url": "https://www.acme-corp.com/ir", "quotes": quote}, "Acme investor relations, acme-corp.com/ir")
    assert not cr.is_primary({"url": "https://news.example.com/a", "quotes": quote})
    assert not cr.is_primary({"url": "https://www.bls.gov/cpi/", "quotes": []})  # nothing was read from the page
    assert not cr.is_primary({"url": "https://notgov.com/a", "quotes": quote})


def test_five_passes_budget_reserves_the_extra_round(tmp_path: Path) -> None:
    prompts: list[str] = []
    result = cr.research(rates_dir(tmp_path, 5), five_agent(prompts), "2026-10-31", parallel=1, budget=13, passes=5)
    assert len(result["findings"]) == 2 and result["runs"] == len(prompts) == 10  # 13 runs buy two questions of up to 6


CONFIG_DIR: list[Path] = []


@pytest.fixture(autouse=True)
def _config_dir(tmp_path: Path) -> None:
    CONFIG_DIR[:] = [tmp_path]


def write_config(data: dict[str, Any]) -> Path:
    path = CONFIG_DIR[0] / "research_test.json"
    path.write_text(json.dumps(data))
    return path


def test_provider_claude_is_accepted_and_cursor_still_loads() -> None:
    live = json.loads((ROOT / cr.CONFIG_FILE).read_text())
    assert cr.load_settings(write_config({**live, "provider": "claude", "model": "sonnet"})).model == "sonnet"
    assert cr.load_settings(write_config({**live, "provider": "cursor", "model": "grok-4.7-low", "model_family": "grok"})).provider == "cursor"
    with pytest.raises(ValueError, match="provider must be"):
        cr.load_settings(write_config({**live, "provider": "grok"}))
    with pytest.raises(ValueError, match="needs no fallback"):
        cr.load_settings(write_config({**live, "fallback": {"provider": "claude", "model": "sonnet"}}))


def test_the_mac_daily_command_does_nothing_and_exits_cleanly_when_claude_researches(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the Mac job must not touch git, cursor-agent or its state")

    for name in ("git", "question_dirs", "cursor_agent", "push_request", "count_runs"):
        monkeypatch.setattr(research_script, name, forbidden)
    monkeypatch.setattr(research_script, "STATE", CONFIG_DIR[0] / "state")
    research_script.main(["daily"])  # no SystemExit: the LaunchAgent sees exit code 0
    research_script.main(["daily", "--dry-run", "--branch", "claude/daily-forecasts-20261109"])
    assert "research runs in the cloud routine now" in capsys.readouterr().out
    assert not (CONFIG_DIR[0] / "state").exists()


def fill_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> tuple[list[dict[str, Any]], Path]:
    """Run ``fill`` on a daily directory with a stub agent factory; returns the claude_agent calls and the directory."""
    directory = rates_dir(tmp_path)
    calls: list[dict[str, Any]] = []
    prompts: list[str] = []

    def fake_claude(model: str, timeout_s: int) -> Any:
        calls.append({"model": model, "timeout_s": timeout_s, "prompts": prompts})
        return five_agent(prompts)

    monkeypatch.setattr(research_script, "claude_agent", fake_claude)
    monkeypatch.setattr(research_script, "load_settings", lambda path: cr.load_settings(write_config(config)))
    monkeypatch.setattr(research_script, "question_dirs", lambda *a, **k: [directory])
    monkeypatch.setattr(research_script, "push_request", lambda *a, **k: ["x"])
    return calls, directory


def test_fill_uses_the_main_model_five_passes_and_the_timeout_when_claude_researches(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = json.loads((ROOT / cr.CONFIG_FILE).read_text())
    calls, directory = fill_setup(tmp_path, monkeypatch, live)
    research_script.fill(type("Args", (), {"branch": "claude/daily-forecasts-20261031"})())
    assert calls[0]["model"] == "haiku" and calls[0]["timeout_s"] == 600
    data = json.loads((directory / "research.json").read_text())
    assert data["researched_by"]["q1"] == "claude-haiku" and data["passes"] == 5
    assert data["findings"]["q1"]["passes_done"][:2] == ["rules", "outside"]
    assert "primary_sources" in data["findings"]["q1"]  # the primary-source gate ran


def test_fill_with_cursor_still_uses_the_sonnet_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, directory = fill_setup(tmp_path, monkeypatch, cursor_config())
    research_script.fill(type("Args", (), {"branch": "claude/daily-forecasts-20261031"})())
    assert calls[0]["model"] == "sonnet" and json.loads((directory / "research.json").read_text())["passes"] == 1


def test_the_claude_agent_tells_the_model_how_to_get_exact_quotes_from_webfetch(tmp_path: Path) -> None:
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$(dirname \"$0\")/args\"\ncat\n")
    fake.chmod(0o755)
    cr.claude_agent("haiku", 30, binary=str(fake))("x")
    args = (tmp_path / "args").read_bytes().decode().split("\0")
    assert args[args.index("--append-system-prompt") + 1] == cr.CLAUDE_TOOL_NOTES
    assert "Copy word for word" in cr.CLAUDE_TOOL_NOTES and "never invent" in cr.CLAUDE_TOOL_NOTES


def test_a_result_without_any_quote_carries_a_warning_and_one_with_quotes_does_not(tmp_path: Path) -> None:
    bare = json.loads(OUTSIDE)
    for source in bare["sources"]:
        source["quotes"] = []
    result = cr.research(rates_dir(tmp_path), lambda text: json.dumps(bare), "2026-10-31", parallel=1, budget=1, passes=1)
    assert "no source has a quote" in result["findings"]["q1"]["warnings"][0]
    quoted = cr.research(rates_dir(tmp_path / "b"), lambda text: OUTSIDE, "2026-10-31", parallel=1, budget=1, passes=1)
    assert "warnings" not in quoted["findings"]["q1"]
