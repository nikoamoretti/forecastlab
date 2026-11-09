"""The ForecastLab research job on Nico's Mac (see forecastlab.cursor_research).

    cursor_research.py daily [--dry-run] [--branch claude/daily-forecasts-YYYYMMDD[-suffix]]
    cursor_research.py fill [--branch claude/daily-forecasts-YYYYMMDD]
    cursor_research.py digest artifacts/<dir> [...]

Since 2026-11-10 (configs/research.json provider "claude") ``fill`` is the research step, run by the cloud
routine with Claude Haiku, and ``daily`` only logs "research runs in the cloud routine now" and exits 0.
The text below describes ``daily`` with provider "cursor".

``daily`` is what the LaunchAgent ai.forecastlab.research runs every 30 minutes through
scripts/mac_research.sh. On weekdays inside the configured window (Pacific time), once per day, it:

1. creates today's branch and has Actions prepare the questions, if that has not happened yet;
2. researches each new question with cursor-agent, in up to five passes (rules, outside view, inside view with the topic's
   checklist from configs/research_checklists.json, the other side, gaps) plus a primary-source check, and saves research.json next to it;
3. pushes that with a request for the Actions ``download`` step, which checks the quotes against the pages.

In ``dry_run`` mode (configs/research.json) the branch carries a ``-dryrun`` suffix and nothing is
forecast from it. Agent runs (one per pass) are counted against daily and weekly caps, so the job stops at the cap
instead of using up Nico's Cursor allowance; the CLI cannot read Cursor's own usage meter.

``--dry-run`` is a hand test: at most 3 questions from an existing ``--branch``, nothing pushed.
``fill`` researches with Claude every question that still has no research on a daily branch: with provider
"claude" using the main model, passes and timeout of configs/research.json (it also creates and prepares the
branch if needed); with provider "cursor" using ``fallback`` (one combined pass unless that says otherwise).
The daily routine runs it.
``digest`` prints the forecaster's own record so far (calibration, topics, latest misses), then each
question's evidence the way the forecaster reads it, followed by the forecaster's own earlier calls on related
questions (same topic, a shared name or word; pending or decided in the last 30 days) so answers stay coherent.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.cursor_research import (  # noqa: E402
    CHECKLIST_FILE,
    CONFIG_FILE,
    claude_agent,
    cursor_agent,
    digest,
    load_checklists,
    load_settings,
    record_notes,
    related_forecasts,
    research,
    runs_per_question,
    tasks,
)
from forecastlab.research import RESEARCH_FILE  # noqa: E402

UTC = timezone.utc  # noqa: UP017 -- datetime.UTC needs Python 3.11; the Mac may run 3.9
STATE = Path.home() / ".forecastlab"
WORK = STATE / "work"  # a git worktree of the daily branch, so the job never touches another checkout
LEDGER = STATE / "ledger.json"
REQUEST = "daily-run-request.json"
PREPARE_WAIT_S = 15 * 60
PUSH_TRIES = 3  # a refused push is redone on top of the branch at most this many times
HAND_TEST_QUESTIONS = 3  # a hand test researches at most this many questions, whatever their passes


def log(message: str) -> None:
    print(f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S}Z {message}", flush=True)


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()[-300:]}")
    return done


def on_origin(branch: str) -> bool:
    return git("ls-remote", "--exit-code", "--heads", "origin", branch, check=False).returncode == 0


def checkout(ref: str) -> Path:
    """A fresh detached worktree of ``ref`` at WORK."""
    git("worktree", "remove", "--force", str(WORK), check=False)
    git("worktree", "prune")
    if WORK.exists():  # left by another clone (each cloud session clones afresh); only this script writes there
        shutil.rmtree(WORK)
    git("worktree", "add", "--detach", str(WORK), ref)
    return WORK


def push_request(branch: str, step: str, dirs: list[str], message: str) -> list[str]:
    """Commit ``dirs`` with a request for ``step`` and push; returns the directories actually pushed.

    Someone else may push to the branch first (the routine's ``fill`` while Cursor is still researching, or
    the other way round). A rejected push is then redone on top of what is there instead of failing the run:
    a ``prepare`` is dropped (the branch exists), and research is merged in, keeping research the branch
    already has and adding ours only for questions still without it (``merge_research``); a directory that
    has been forecast meanwhile is left alone.
    """
    for _ in range(PUSH_TRIES):
        pending = _read_json(WORK / REQUEST)
        wanted = sorted(set(dirs) | set(pending.get("dirs", []))) if pending.get("step") == step else dirs
        (WORK / REQUEST).write_text(json.dumps({"step": step, "dirs": wanted,
                                                "requested_at": datetime.now(UTC).isoformat()}) + "\n")
        git("add", "-A", REQUEST, *dirs, cwd=WORK)
        git("commit", "-q", "-m", message, cwd=WORK)
        pushed = git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=WORK, check=False)
        if not pushed.returncode:
            return dirs
        log(f"{branch}: push refused ({pushed.stderr.strip()[-200:]}); redoing it on top of the branch")
        if step != "download":
            return []  # the branch exists now; whoever created it asked for the same step
        ours = {name: _read_json(WORK / name / RESEARCH_FILE) for name in dirs}
        git("fetch", "-q", "origin", branch, cwd=WORK)
        git("reset", "-q", "--hard", f"origin/{branch}", cwd=WORK)
        dirs = rebase_research(ours)
        if not dirs:
            log(f"{branch}: the branch already has research for every question; nothing to push")
            return []
    raise RuntimeError(f"{branch}: push refused {PUSH_TRIES} times")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


PER_QUESTION = ("findings", "sources", "researched_by", "seconds", "cost_usd")


def merge_research(theirs: dict[str, Any], ours: dict[str, Any]) -> dict[str, Any]:
    """``theirs`` (already on the branch) with ``ours`` added for the questions ``theirs`` has no findings for."""
    if not theirs.get("findings") and not theirs.get("failed"):
        return ours
    have = set(theirs.get("findings", {}))
    merged = dict(theirs)
    for key in PER_QUESTION:
        part = dict(theirs.get(key, {}))
        part.update({qid: value for qid, value in ours.get(key, {}).items() if qid not in have})
        merged[key] = part
    failed = {qid: reason for qid, reason in theirs.get("failed", {}).items() if qid not in merged["findings"]}
    failed.update({qid: reason for qid, reason in ours.get("failed", {}).items()
                   if qid not in merged["findings"] and qid not in failed})
    merged["failed"] = failed
    merged["runs"] = int(theirs.get("runs", 0)) + int(ours.get("runs", 0))
    return merged


def rebase_research(ours: dict[str, dict[str, Any]]) -> list[str]:
    """Write ``ours`` into the freshly reset worktree, merged with what the branch has; the directories changed."""
    changed = []
    for name, research_data in ours.items():
        directory = WORK / name
        if (directory / "briefs.json").exists():
            log(f"{name}: already forecast from the research on the branch; leaving it")
            continue
        path = directory / RESEARCH_FILE
        theirs = _read_json(path)
        merged = merge_research(theirs, research_data)
        if merged.get("findings") == theirs.get("findings") and merged.get("failed") == theirs.get("failed"):
            continue
        path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
        changed.append(name)
    return changed


def prepared(branch: str) -> bool:
    """Whether Actions has run the prepare step: the branch exists and its request file is gone."""
    if not on_origin(branch):
        return False
    git("fetch", "-q", "origin", branch)
    return git("cat-file", "-e", f"origin/{branch}:{REQUEST}", check=False).returncode != 0


def week_of(day: datetime) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def ledger() -> dict[str, int]:
    try:
        return json.loads(LEDGER.read_text())
    except (OSError, ValueError):
        return {}


def count_runs(now: datetime, runs: int) -> None:
    used = ledger()
    for key in (f"{now:%Y-%m-%d}", week_of(now)):
        used[key] = used.get(key, 0) + runs
    LEDGER.write_text(json.dumps(used, indent=2, sort_keys=True) + "\n")


def daily(args: argparse.Namespace) -> None:
    settings = load_settings(ROOT / CONFIG_FILE)
    if settings.provider == "claude":
        # The LaunchAgent keeps firing; it must exit 0 and touch nothing (no branch, no cursor-agent, no ledger).
        log("research runs in the cloud routine now (configs/research.json provider is claude): nothing to do on the Mac")
        return
    now = datetime.now(UTC)
    STATE.mkdir(parents=True, exist_ok=True)
    if args.branch:
        branch = args.branch
    else:
        if not settings.in_window(now):
            return  # the LaunchAgent fires every 30 minutes; outside the window there is nothing to do
        branch = f"claude/daily-forecasts-{now:%Y%m%d}" + ("-dryrun" if settings.mode == "dry_run" else "")
    found = re.search(r"claude/daily-forecasts-(\d{8})", branch)
    if not found:
        raise SystemExit(f"{branch} is not a daily branch")
    marker = STATE / f"done2-{branch.replace('/', '_')}"  # done2: markers from the first version are ignored
    if marker.exists() and not args.dry_run:
        return
    used = ledger()
    budget = min(settings.daily_run_cap - used.get(f"{now:%Y-%m-%d}", 0),
                 settings.weekly_run_cap - used.get(week_of(now), 0))
    if args.dry_run:
        budget = min(budget, HAND_TEST_QUESTIONS * runs_per_question(settings.passes))
    if budget <= 0:
        log(f"{branch}: the research cap is reached ({json.dumps(used)}); skipping")
        return

    dirs = question_dirs(branch, found.group(1), settings.base_branch, create=not args.dry_run)
    if not dirs:
        return
    agent = cursor_agent(settings.model, settings.timeout_s)
    checklists = load_checklists(ROOT / CHECKLIST_FILE)
    runs, changed = 0, []
    for directory in dirs:
        result = research(directory, agent, f"{now:%Y-%m-%d}", parallel=settings.parallel, budget=budget - runs,
                          by=settings.model, passes=settings.passes, checklists=checklists)
        runs += result["runs"]
        if not result["runs"]:
            continue
        result.update(model=settings.model, model_family=settings.model_family, mode=settings.mode)
        (directory / RESEARCH_FILE).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        changed.append(f"artifacts/{directory.name}")
        log(f"{directory.name}: {len(result['findings'])} researched, {len(result['failed'])} failed "
            f"({result['runs']} runs)")
        for qid, reason in sorted(result["failed"].items()):
            log(f"  {qid}: {reason}")
    count_runs(now, runs)
    if args.dry_run:
        for name in changed:
            data = json.loads((WORK / name / RESEARCH_FILE).read_text())
            for qid in data["findings"]:
                print(f"\n{qid}\n{digest(data, qid)}")
        log(f"hand test done: {runs} runs, nothing pushed")
        return
    if changed:
        pushed = push_request(branch, "download", changed, f"Research: cursor-agent evidence ({runs} runs)")
        if pushed:
            log(f"{branch}: pushed research for {', '.join(pushed)}; Actions now downloads the cited pages")
    marker.touch()


def question_dirs(branch: str, day: str, base_branch: str, *, create: bool) -> list[Path]:
    """The branch's question directories for ``day`` in a fresh worktree, creating and preparing it if needed."""
    git("fetch", "-q", "origin", base_branch)
    if not on_origin(branch):
        if not create:
            raise SystemExit(f"{branch} does not exist on origin")
        log(f"{branch}: creating it and asking Actions to prepare the questions")
        checkout(f"origin/{base_branch}")
        push_request(branch, "prepare", [], "Daily run: request prepare")
    deadline = time.monotonic() + PREPARE_WAIT_S
    while not prepared(branch):
        if time.monotonic() > deadline:
            log(f"{branch}: Actions has not prepared the questions yet; trying again next run")
            return []
        time.sleep(30)
    work = checkout(f"origin/{branch}")
    dirs = [d for d in sorted((work / "artifacts").glob(f"prospective_*_{day}"))
            if (d / "questions.json").exists() or (d / "forecasts.json").exists()]
    if not dirs:
        log(f"{branch}: no questions to research yet; trying again next run")
    return dirs


def fill_plan(settings: Any) -> tuple[Any, str, int, str]:
    """(agent, researched_by, passes, label) for ``fill``: the main provider when it is Claude, else the Cursor fallback."""
    if settings.provider == "claude":
        return (claude_agent(settings.model, settings.timeout_s), f"claude-{settings.model}", settings.passes,
                settings.model)
    if not settings.fallback_model:
        raise SystemExit("configs/research.json has no fallback")
    return (claude_agent(settings.fallback_model, settings.fallback_timeout_s), f"claude-{settings.fallback_model}",
            settings.fallback_passes, settings.fallback_model)


def fill(args: argparse.Namespace) -> None:
    """Research with Claude every question on a daily branch that has no research yet.

    This is the daily research step when ``provider`` is claude (the cloud routine runs it; it also creates the
    branch and asks Actions to prepare the questions if nobody has). With provider cursor it fills what the Mac left.
    """
    settings = load_settings(ROOT / CONFIG_FILE)
    agent, by, passes, label = fill_plan(settings)
    now = datetime.now(UTC)
    branch = args.branch or f"claude/daily-forecasts-{now:%Y%m%d}" + ("-dryrun" if settings.mode == "dry_run" else "")
    found = re.search(r"claude/daily-forecasts-(\d{8})", branch)
    if not found:
        raise SystemExit(f"{branch} is not a daily branch")
    dirs = question_dirs(branch, found.group(1), settings.base_branch, create=True)
    checklists = load_checklists(ROOT / CHECKLIST_FILE)
    each = runs_per_question(passes)
    left = settings.daily_run_cap if settings.provider == "claude" else None  # the cloud has no ledger: the cap is per fill
    changed = []
    for directory in dirs:
        path = directory / RESEARCH_FILE
        saved = json.loads(path.read_text()) if path.exists() else {}
        budget = len(tasks(directory)) * each
        if left is not None:
            budget = min(budget, left)
        result = research(directory, agent, f"{now:%Y-%m-%d}", parallel=settings.parallel, budget=budget,
                          by=by, passes=passes, checklists=checklists)
        if left is not None:
            left -= result["runs"]
        if not result["runs"]:
            log(f"{directory.name}: every question already has research (or the run cap is reached)")
            continue
        result.update(model=saved.get("model", settings.model), model_family=saved.get("model_family", settings.model_family),
                      mode=settings.mode)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        changed.append(f"artifacts/{directory.name}")
        log(f"{directory.name}: {len(result['findings'])} researched, {len(result['failed'])} failed "
            f"({result['runs']} Claude runs, ${sum(result['cost_usd'].values()):.2f})")
        for qid, reason in sorted(result["failed"].items()):
            log(f"  {qid}: {reason}")
        bare = sorted(qid for qid, items in result["sources"].items() if not any(s["quotes"] for s in items))
        if bare:
            log(f"WARNING: no source has a quote for {', '.join(bare)}: WebFetch could not open the pages "
                "(network policy of this environment?)")
    if changed:
        pushed = push_request(branch, "download", changed, f"Research: Claude {label} ({passes} passes)")
        if pushed:
            log(f"{branch}: pushed research for {', '.join(pushed)}; Actions now downloads the cited pages")


def show_digest(directories: list[str]) -> None:
    record = ROOT / "data" / "track_record.json"
    history: dict[str, Any] = json.loads(record.read_text()) if record.exists() else {}
    if history:
        print(f"{record_notes(history)}\n")
    today = f"{datetime.now(UTC):%Y-%m-%d}"
    for name in directories:
        directory = ROOT / name
        data: dict[str, Any] = json.loads((directory / RESEARCH_FILE).read_text())
        for task in tasks(directory):
            text = digest(data, task["id"], task, today)
            related = related_forecasts(history, task, today) if task["id"] in data.get("findings", {}) else ""
            print(f"## {task['id']}: {task['question']}\n{text}\n" + (f"{related}\n" if related else ""))


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("daily")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--branch")
    gaps = sub.add_parser("fill")
    gaps.add_argument("--branch")
    show = sub.add_parser("digest")
    show.add_argument("dirs", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "daily":
        if args.dry_run and not args.branch:
            parser.error("--dry-run needs --branch")
        daily(args)
    elif args.command == "fill":
        fill(args)
    else:
        show_digest(args.dirs)


if __name__ == "__main__":
    main(sys.argv[1:])
