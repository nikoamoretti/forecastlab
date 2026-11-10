from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from forecastlab import forecaster_audit as fa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import forecaster_audit as audit_script  # noqa: E402


def row(tool: str, **asked: Any) -> str:
    return json.dumps({"type": "assistant", "timestamp": "2026-11-11T12:40:07.120Z",
                       "message": {"content": [{"type": "text", "text": "thinking"},
                                               {"type": "tool_use", "name": tool, "input": asked}]}})


def result(text: str) -> str:
    return json.dumps({"type": "user", "timestamp": "2026-11-11T12:40:08.000Z",
                       "message": {"content": [{"type": "tool_result", "content": text}]}})


def transcript(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "agent-abc123.jsonl"
    path.write_text("\n".join(["not json", json.dumps({"type": "attachment"}), *rows]) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    made = tmp_path / "daily-20261111" / "forecaster"
    made.mkdir(parents=True)
    return made


def test_a_forecaster_that_stays_in_its_folder_is_clean(tmp_path: Path, folder: Path) -> None:
    write_files = (f"cd {folder} && python3 - <<'E'\nimport json\n"
                   'out = {"forecasts": [{"id": "polymarket-1", "probability": 0.4, "rationale": "Up 3/4 of the '
                   'time and/or flat; $3.90/gallon ... see https://www.eia.gov/petroleum/gasdiesel/ (2026-11-10)"}]}\n'
                   f"json.dump(out, open('{folder}/prospective_markets_20261111.forecasts.json', 'w'))\nE")
    path = transcript(
        tmp_path,
        row("Read", file_path=f"{folder}/prospective_markets_20261111.digest.txt", limit=400),
        result("market price 0.62 of an open question"),
        row("Bash", command="date -u +%Y-%m-%dT%H:%M:%S+00:00"),
        row("Glob", pattern="*.digest.txt", path=str(folder)),
        row("Glob", pattern=f"{folder}/*.txt"),
        row("Grep", pattern="Resolution rules", path=str(folder)),
        row("Bash", command=write_files),
        row("Write", file_path=f"{folder}/prospective_markets_20261111.briefs.json",
            content="see artifacts/prospective_markets_20261110/forecasts.json and https://polymarket.com/x"),
        row("Bash", command=f"/usr/bin/python3 -m json.tool {folder}/prospective_markets_20261111.forecasts.json > /dev/null"),
        row("SubagentHandback", message="done"),
    )

    audited = fa.audit(path, folder)

    assert audited.clean and audited.calls == 9


@pytest.mark.parametrize(("tool", "asked", "reason"), [
    # 2026-11-10: the forecaster, working in the clone, printed an earlier day's forecasts.json.
    ("Bash", {"command": "cd /tmp/s/daily-20261110/forecastlab && python3 -c \"import json;"
                         "print(json.load(open('artifacts/prospective_markets_20261109/forecasts.json')))\""},
     "path outside the folder: /tmp/s/daily-20261110/forecastlab"),
    ("Bash", {"command": "cat forecastlab/artifacts/prospective_markets_20261109/market_snapshot.json"},
     "names a place that holds prices"),
    ("Bash", {"command": "cd FOLDER && cat ../second-look/prospective_markets_20261111.packet.json"}, 'with ".."'),
    ("Bash", {"command": "ls ~/ && cat $HOME/notes.txt"}, "names the home folder"),
    ("Bash", {"command": "grep -r price /"}, "reads from the top of the disk"),
    ("Bash", {"command": "curl -s https://gamma-api.polymarket.com/markets/1"}, "reaches the network or the repository: curl"),
    ("Bash", {"command": "cd FOLDER && python3 -c 'import urllib.request'"}, "reaches the network or the repository: urllib"),
    ("Bash", {"command": "cd FOLDER && git log -3"}, "reaches the network or the repository: git log"),
    ("Read", {"file_path": "/tmp/s/daily-20261110/forecastlab/docs/DAILY_CLAUDE_FORECASTS.md"}, "path outside the folder"),
    ("Read", {"file_path": "FOLDER/../forecastlab/data/track_record.json"}, "path outside the folder"),
    ("Read", {"file_path": "forecastlab/artifacts/x/forecasts.json"}, "path not spelled from the root"),
    ("Grep", {"pattern": "price"}, "searched without naming the forecaster's folder"),
    ("Glob", {"pattern": "**/forecasts.json"}, "searched without naming the forecaster's folder"),
    ("Glob", {"pattern": "/tmp/s/**/forecasts.json"}, "pattern outside the folder"),
    ("Glob", {"pattern": "/tmp/s/**/forecasts.json", "path": "FOLDER"}, "pattern outside the folder"),
    ("Glob", {"pattern": "FOLDER*/*.json"}, "pattern outside the folder"),
    ("Glob", {"pattern": "../*/forecasts.json", "path": "FOLDER"}, 'pattern climbs out with ".."'),
    ("WebFetch", {"url": "https://polymarket.com/event/x"}, "a tool the forecaster has no use for"),
    ("Agent", {"prompt": "look up the price"}, "a tool the forecaster has no use for"),
])
def test_a_call_that_reaches_outside_the_folder_is_flagged(
    tmp_path: Path, folder: Path, tool: str, asked: dict[str, str], reason: str,
) -> None:
    asked = {key: value.replace("FOLDER", str(folder)) for key, value in asked.items()}
    path = transcript(tmp_path, row("Read", file_path=f"{folder}/a.digest.txt"), row(tool, **asked),
                      result("SECRET market price 0.62"))

    audited = fa.audit(path, folder)

    assert [flag.tool for flag in audited.flags] == [tool]
    assert reason in audited.flags[0].reason
    assert audited.flags[0].time == "12:40:07"
    assert "SECRET" not in audited.flags[0].line()


def test_a_transcript_without_tool_calls_is_an_error_not_a_clean_result(tmp_path: Path, folder: Path) -> None:
    with pytest.raises(fa.AuditError, match="no tool calls"):
        fa.audit(transcript(tmp_path, result("hello")), folder)
    with pytest.raises(fa.AuditError, match="cannot read"):
        fa.audit(tmp_path / "missing.jsonl", folder)


def test_the_transcript_is_found_by_the_subagents_id(tmp_path: Path) -> None:
    saved = tmp_path / ".claude" / "projects" / "-home-claude" / "session-1" / "subagents"
    saved.mkdir(parents=True)
    (saved / "agent-a6af.jsonl").write_text(row("Bash", command="date -u"), encoding="utf-8")

    assert fa.find_transcript("a6af", home=tmp_path) == saved / "agent-a6af.jsonl"
    assert fa.find_transcript(str(saved / "agent-a6af.jsonl")) == saved / "agent-a6af.jsonl"
    with pytest.raises(fa.AuditError, match="no transcript found for a999"):
        fa.find_transcript("a999", home=tmp_path)


def test_the_script_exits_0_clean_1_flagged_2_when_it_could_not_run(
    tmp_path: Path, folder: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    clean = transcript(tmp_path, row("Read", file_path=f"{folder}/a.digest.txt"))
    assert audit_script.main([str(clean), "--folder", str(folder)]) == 0
    assert "forecaster audit: clean (1 tool calls, all inside" in capsys.readouterr().out

    flagged = transcript(tmp_path, row("Read", file_path=f"{folder}/a.digest.txt"),
                         row("Read", file_path="/tmp/s/forecastlab/artifacts/prospective_markets_20261109/forecasts.json"),
                         result("SECRET 0.62"))
    assert audit_script.main([str(flagged), "--folder", str(folder)]) == 1
    printed = capsys.readouterr().out
    assert "forecaster audit: 1 of 2 tool calls reached outside" in printed
    assert "12:40:07 Read: path outside the folder" in printed and "SECRET" not in printed

    assert audit_script.main([str(tmp_path / "missing.jsonl"), "--folder", str(folder)]) == 2
    assert "forecaster audit: could not run" in capsys.readouterr().out
