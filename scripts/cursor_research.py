"""The ForecastLab research job on Nico's Mac (see forecastlab.cursor_research).

    cursor_research.py daily [--dry-run] [--branch claude/daily-forecasts-YYYYMMDD[-suffix]]
    cursor_research.py fill [--branch claude/daily-forecasts-YYYYMMDD]
    cursor_research.py digest artifacts/<dir> [...]

``daily`` is what the LaunchAgent ai.forecastlab.research runs every 30 minutes through
scripts/mac_research.sh. On weekdays inside the configured UTC window, once per day, it:

1. creates today's branch and has Actions prepare the questions, if that has not happened yet;
2. researches each new question with cursor-agent and saves research.json next to it;
3. pushes that with a request for the Actions ``download`` step, which checks the quotes against the pages.

In ``dry_run`` mode (configs/research.json) the branch carries a ``-dryrun`` suffix and nothing is
forecast from it. Runs are counted against daily and weekly caps, so the job stops at the cap
instead of using up Nico's Cursor allowance; the CLI cannot read Cursor's own usage meter.

``--dry-run`` is a hand test: at most 3 questions from an existing ``--branch``, nothing pushed.
``fill`` researches with Claude (``fallback`` in configs/research.json) every question that still has no
research on a daily branch, e.g. when Cursor's weekly limit is used up; the daily routine runs it.
``digest`` prints the forecaster's own record so far (calibration, topics, latest misses), then each
question's evidence the way the forecaster reads it.
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
    CONFIG_FILE,
    claude_agent,
    cursor_agent,
    digest,
    load_settings,
    record_notes,
    research,
    tasks,
)
from forecastlab.research import RESEARCH_FILE  # noqa: E402

UTC = timezone.utc  # noqa: UP017 -- datetime.UTC needs Python 3.11; the Mac may run 3.9
STATE = Path.home() / ".forecastlab"
WORK = STATE / "work"  # a git worktree of the daily branch, so the job never touches another checkout
LEDGER = STATE / "ledger.json"
REQUEST = "daily-run-request.json"
PREPARE_WAIT_S = 15 * 60
HAND_TEST_QUESTIONS = 3


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


def push_request(branch: str, step: str, dirs: list[str], message: str) -> None:
    (WORK / REQUEST).write_text(json.dumps({"step": step, "dirs": dirs,
                                            "requested_at": datetime.now(UTC).isoformat()}) + "\n")
    git("add", "-A", REQUEST, *dirs, cwd=WORK)
    git("commit", "-q", "-m", message, cwd=WORK)
    git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=WORK)


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
    now = datetime.now(UTC)
    STATE.mkdir(parents=True, exist_ok=True)
    if args.branch:
        branch = args.branch
    else:
        start, end = settings.window_utc
        if now.weekday() >= 5 or not start <= now.hour < end:
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
        budget = min(budget, HAND_TEST_QUESTIONS)
    if budget <= 0:
        log(f"{branch}: the research cap is reached ({json.dumps(used)}); skipping")
        return

    dirs = question_dirs(branch, found.group(1), settings.base_branch, create=not args.dry_run)
    if not dirs:
        return
    agent = cursor_agent(settings.model, settings.timeout_s)
    runs, changed = 0, []
    for directory in dirs:
        result = research(directory, agent, f"{now:%Y-%m-%d}", parallel=settings.parallel, budget=budget - runs,
                          by=settings.model)
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
        push_request(branch, "download", changed, f"Research: cursor-agent evidence ({runs} runs)")
        log(f"{branch}: pushed research for {', '.join(changed)}; Actions now downloads the cited pages")
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


def fill(args: argparse.Namespace) -> None:
    """Research with Claude every question on a daily branch that Cursor left without research."""
    settings = load_settings(ROOT / CONFIG_FILE)
    if not settings.fallback_model:
        raise SystemExit("configs/research.json has no fallback")
    now = datetime.now(UTC)
    branch = args.branch or f"claude/daily-forecasts-{now:%Y%m%d}" + ("-dryrun" if settings.mode == "dry_run" else "")
    found = re.search(r"claude/daily-forecasts-(\d{8})", branch)
    if not found:
        raise SystemExit(f"{branch} is not a daily branch")
    dirs = question_dirs(branch, found.group(1), settings.base_branch, create=True)
    agent = claude_agent(settings.fallback_model, settings.fallback_timeout_s)
    changed = []
    for directory in dirs:
        path = directory / RESEARCH_FILE
        saved = json.loads(path.read_text()) if path.exists() else {}
        result = research(directory, agent, f"{now:%Y-%m-%d}", parallel=settings.parallel,
                          budget=len(tasks(directory)), by=f"claude-{settings.fallback_model}")
        if not result["runs"]:
            log(f"{directory.name}: every question already has research")
            continue
        result.update(model=saved.get("model", settings.model), model_family=saved.get("model_family", settings.model_family),
                      mode=settings.mode)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        changed.append(f"artifacts/{directory.name}")
        log(f"{directory.name}: {len(result['findings'])} researched, {len(result['failed'])} failed "
            f"({result['runs']} Claude runs)")
        for qid, reason in sorted(result["failed"].items()):
            log(f"  {qid}: {reason}")
    if changed:
        push_request(branch, "download", changed, f"Research: Claude {settings.fallback_model} fills the gaps")
        log(f"{branch}: pushed research for {', '.join(changed)}; Actions now downloads the cited pages")


def show_digest(directories: list[str]) -> None:
    record = ROOT / "data" / "track_record.json"
    if record.exists():
        print(f"{record_notes(json.loads(record.read_text()))}\n")
    for name in directories:
        directory = ROOT / name
        data: dict[str, Any] = json.loads((directory / RESEARCH_FILE).read_text())
        for task in tasks(directory):
            print(f"## {task['id']}: {task['question']}\n{digest(data, task['id'])}\n")


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
