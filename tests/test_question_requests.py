from __future__ import annotations


def test_owner_asks_withdraws_and_claude_answers(client) -> None:
    assert client.post("/api/question-requests", json={"text": "too short"}).status_code == 422
    first = client.post("/api/question-requests", json={"text": "  Will the Fed cut rates\n   in December 2026?  "})
    assert first.status_code == 201
    asked = first.json()
    assert (asked["status"], asked["text"]) == ("waiting", "Will the Fed cut rates in December 2026?")
    second = client.post("/api/question-requests", json={"text": "Will Bitcoin trade above $150,000 on Dec 31, 2026?"}).json()

    listed = client.get("/api/question-requests").json()
    assert [item["id"] for item in listed] == [second["id"], asked["id"]]

    assert client.delete(f"/api/question-requests/{second['id']}").json() == {"ok": True}
    answered = client.post(f"/api/question-requests/{asked['id']}/answered").json()
    assert answered["status"] == "answered" and "answered_at" in answered
    assert client.delete(f"/api/question-requests/{asked['id']}").status_code == 409
    assert client.post("/api/question-requests/missing/answered").status_code == 404
    assert [item["status"] for item in client.get("/api/question-requests").json()] == ["answered"]


def _answers(tmp_path, **changes):
    answer = {"id": "req-1", "question": "Will the Fed cut rates in December 2026?", "topic": "Interest rates",
              "resolution_criteria": "YES if the FOMC lowers the target range at its December 2026 meeting.",
              "resolution_date": "2026-12-10", "resolution_source": "https://www.federalreserve.gov/monetarypolicy.htm",
              "probability": 0.3, "rationale": "Markets price a hold.", "sources": ["https://www.cmegroup.com/"]} | changes
    path = tmp_path / "answers.json"
    import json
    path.write_text(json.dumps({"forecast_made_at": "2026-10-08T11:00:00Z", "answers": [answer]}))
    return path


def test_claude_answers_are_recorded_and_appear_in_the_track_record(tmp_path, monkeypatch) -> None:
    import sys
    from datetime import date
    from pathlib import Path

    import pytest
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import question_requests as script

    from forecastlab_api.track_record import build_track_record

    monkeypatch.setattr(script, "ROOT", tmp_path)
    target = script.record(_answers(tmp_path), today=date(2026, 10, 8))
    script.record(_answers(tmp_path), today=date(2026, 10, 8))  # recording twice keeps one copy
    record = build_track_record(tmp_path / "artifacts", today=date(2026, 10, 8))
    [asked] = record["questions"]
    assert (asked["id"], asked["topic"], asked["status"]) == ("req-1", "Interest rates", "pending")
    assert asked["call"] == {"method": "combined_median_v1", "probability": 0.3, "members": 1}
    assert asked["forecasts"][0]["rationale"] == "Markets price a hold."

    with pytest.raises(SystemExit, match="outside"):
        script.record(_answers(tmp_path, probability=0.99), today=date(2026, 10, 8))
    with pytest.raises(SystemExit, match="missing"):
        script.record(_answers(tmp_path, rationale=""), today=date(2026, 10, 8))
    with pytest.raises(SystemExit, match="resolves on"):
        script.set_outcome(target.parent, "req-1", "no", note="Held at 4.5%", source="https://www.federalreserve.gov/")
    monkeypatch.setattr(script, "date", type("FrozenDate", (date,), {"today": classmethod(lambda cls: date(2026, 12, 11))}))
    script.set_outcome(target.parent, "req-1", "no", note="Held at 4.5%", source="https://www.federalreserve.gov/")
    resolved = build_track_record(tmp_path / "artifacts", today=date(2026, 12, 11))["questions"][0]
    assert (resolved["status"], resolved["actual"], resolved["verdict"]) == ("resolved", "Held at 4.5%", "right")
