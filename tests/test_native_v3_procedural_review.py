from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.run_native_v3_procedural_review import (
    POLICY_VERSION,
    _instruction,
    invoke_cursor_role,
)

from forecastlab.native_provenance_corpus import native_candidate_from_market, native_review_manifests
from forecastlab.procedural_ai_review import rubric_for


def _manifests() -> tuple[object, object]:
    created = datetime(2024, 1, 1, tzinfo=UTC)
    candidate, outcome, _ = native_candidate_from_market(
        market={
            "ticker": "KXTEST-1",
            "event_ticker": "KXEVENT-1",
            "series_ticker": "KXTEST",
            "market_type": "binary",
            "result": "yes",
            "title": "Will the official test series exceed the published threshold?",
            "rules_primary": (
                "This market resolves Yes if the official test series exceeds the published "
                "threshold on the stated release date. It resolves No otherwise."
            ),
            "created_time": created.isoformat(),
            "settlement_ts": (created + timedelta(days=21)).isoformat(),
        },
        category="Economics",
        retrieved_at=datetime(2026, 9, 1, tzinfo=UTC),
        raw_record_hash="a" * 64,
    )
    return native_review_manifests(candidate=candidate, sealed_outcome=outcome)


def _output(role: str) -> dict[str, object]:
    findings = [
        {
            "code": code,
            "status": "pass",
            "conclusion": "The supplied source record supports this required review finding.",
            "source_ids": ["kalshi-market-KXTEST-1"],
        }
        for code in rubric_for(POLICY_VERSION, role).required_finding_codes
    ]
    if role == "question_review":
        return {
            "schema_version": 1,
            "output_type": "question_review_output",
            "decision": "accepted",
            "findings": findings,
            "uncertainties": [],
            "conflicts": [],
        }
    return {
        "schema_version": 1,
        "output_type": "outcome_adjudication_output",
        "decision": "confirmed",
        "adjudicated_outcome": 1,
        "findings": findings,
        "uncertainties": [],
        "conflicts": [],
    }


def test_native_manifests_are_structurally_role_blind() -> None:
    question, outcome = _manifests()
    question_payload = question.model_dump(mode="json")
    outcome_payload = outcome.model_dump(mode="json")

    assert "candidate_outcome" not in question_payload
    assert "outcome_known_at" not in question_payload
    assert "question" not in outcome_payload
    assert "event_family_id" not in outcome_payload
    assert question_payload["sources"][0]["temporal_basis"] == "immutable_version"
    assert outcome_payload["sources"][0]["temporal_basis"] == "immutable_version"


def test_cursor_transport_records_only_validated_artifact(monkeypatch, tmp_path: Path) -> None:
    question, _ = _manifests()
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202 - subprocess seam
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_output("question_review")),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    record = invoke_cursor_role(
        role="question_review",
        manifest=question,
        root=tmp_path,
        model_id="gpt-5.3-codex-low-fast",
        cli_version="test-cli",
        timeout_seconds=1,
    )

    assert record["record_type"] == "procedural_review_artifact"
    assert record["gate_status"] == "passed"
    assert record["raw_response_persisted"] is False
    assert "candidate_outcome" not in record["artifact"]["input_manifest"]
    assert len(list((tmp_path / "transport").rglob("manifest.json"))) == 1
    command = commands[0]
    workspace = Path(command[command.index("--workspace") + 1])
    assert command[command.index("--trust")] == "--trust"
    assert "--yolo" not in command
    assert "-f" not in command
    assert sorted(path.name for path in workspace.iterdir()) == ["manifest.json"]


def test_cursor_transport_fails_closed_on_non_json_output(monkeypatch, tmp_path: Path) -> None:
    question, _ = _manifests()

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202 - subprocess seam
        del kwargs
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    record = invoke_cursor_role(
        role="question_review",
        manifest=question,
        root=tmp_path,
        model_id="gpt-5.3-codex-low-fast",
        cli_version="test-cli",
        timeout_seconds=1,
    )

    assert record["record_type"] == "procedural_review_terminal_failure"
    assert record["error_code"] == "review_cursor_output_invalid_json"
    assert record["raw_response_persisted"] is False


def test_substantive_review_failure_is_not_a_transport_failure(monkeypatch, tmp_path: Path) -> None:
    question, _ = _manifests()
    incomplete = _output("question_review")
    incomplete["findings"] = incomplete["findings"][:-1]

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202 - subprocess seam
        del kwargs
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(incomplete), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    record = invoke_cursor_role(
        role="question_review",
        manifest=question,
        root=tmp_path,
        model_id="gpt-5.3-codex-low-fast",
        cli_version="test-cli",
        timeout_seconds=1,
    )

    assert record["record_type"] == "procedural_review_artifact"
    assert record["gate_status"] == "failed"
    assert "required_finding_missing:temporal_proof" in record["gate_reasons"]


def test_role_instruction_does_not_cross_the_typed_boundary() -> None:
    question, outcome = _manifests()
    question_instruction = _instruction(role="question_review", manifest=question)
    outcome_instruction = _instruction(role="outcome_adjudication", manifest=outcome)

    assert "candidate_outcome" not in question_instruction
    assert "outcome_known_at" not in question_instruction
    assert '"question"' not in outcome_instruction
    assert "event_family_id" not in outcome_instruction
