#!/usr/bin/env python3
"""Read an immutable H017 workspace and emit a sanitized V2 readiness receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from forecastlab.private_internal_evaluation_v2 import (
    build_private_internal_evaluation_v2_preflight,
)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    repository = args.repository.resolve()
    output = args.output.resolve()
    if repository == workspace or repository in workspace.parents:
        raise SystemExit("workspace_must_be_outside_repository")
    if repository == output or repository in output.parents:
        raise SystemExit("output_must_be_outside_repository")
    required = (
        workspace / "summary.json",
        workspace / "records/blinded_candidates.jsonl",
        workspace / "records/evidence_packets.jsonl",
        workspace / "records/evidence_documents.jsonl",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"workspace_receipts_missing:{','.join(missing)}")
    receipt = build_private_internal_evaluation_v2_preflight(
        summary=json.loads((workspace / "summary.json").read_text(encoding="utf-8")),
        candidates=_rows(workspace / "records/blinded_candidates.jsonl"),
        packets=_rows(workspace / "records/evidence_packets.jsonl"),
        documents=_rows(workspace / "records/evidence_documents.jsonl"),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"preflight_hash": receipt["preflight_hash"], "release_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
