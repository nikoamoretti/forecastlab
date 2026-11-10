"""Check what the blind forecaster opened (see forecastlab.forecaster_audit).

    forecaster_audit.py <transcript.jsonl or the subagent's id> --folder <the forecaster's folder>

Prints one line per tool call that reached outside the folder, with what the call asked for (never what came
back). Exit 0: every call stayed inside. Exit 1: at least one call is flagged. Exit 2: the transcript could
not be read, which is not a clean result. Standard library only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.forecaster_audit import AuditError, audit, find_transcript  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript", help="the forecaster's transcript file, or its subagent id")
    parser.add_argument("--folder", required=True, type=Path, help="the only folder the forecaster may open")
    args = parser.parse_args(argv)
    try:
        result = audit(find_transcript(args.transcript), args.folder)
    except AuditError as error:
        print(f"forecaster audit: could not run: {error}")
        return 2
    if result.clean:
        print(f"forecaster audit: clean ({result.calls} tool calls, all inside {args.folder})")
        return 0
    print(f"forecaster audit: {len(result.flags)} of {result.calls} tool calls reached outside {args.folder}")
    for flag in result.flags:
        print("  " + flag.line())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
