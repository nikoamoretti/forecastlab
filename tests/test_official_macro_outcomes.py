"""Offline regressions for deterministic official macro outcome adjudication."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from forecastlab.http_client import SafeResponse
from forecastlab.macro import MacroSpec
from forecastlab.root_event import contract_hash
from forecastlab_api.models import ProspectiveCohort, ProspectiveEntry, ProspectiveOutcome, Question

RELEASE_AT = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 9, 12, tzinfo=UTC)


def cpi_html(*, value: str = "3.4", period: str = "AUGUST 2026", qualifier: str = "before seasonal adjustment") -> bytes:
    return f"""<pre>8:30 a.m. (ET) Friday, September 11, 2026
CONSUMER PRICE INDEX - {period}
Over the last 12 months, the all items index increased {value} percent {qualifier}.</pre>""".encode()


def _entry(session, *, entry_id: str, threshold: float):
    from forecastlab_api.models import Question

    question_id = "question-" + entry_id
    spec = MacroSpec(indicator="cpi", observation_period="2026-08", threshold=threshold,
                     comparison="gt", release_at=RELEASE_AT)
    contract = spec.template(question_id, "contract-" + entry_id).model_copy(update={"status": "approved"})
    entry = ProspectiveEntry(id=entry_id, cohort_id="cohort", question_id=question_id,
        contract_json=contract.model_dump_json(), macro_json=spec.model_dump_json(), release_event="cpi-2026-09-11",
        cutoff=datetime(2026, 9, 5, 6, tzinfo=UTC))
    session.add(Question(id=question_id, original_text=contract.original_question, normalized_text=contract.normalized_question,
        requested_mode="live", requested_profile_id="root_event_ensemble_v1"))
    session.flush()
    session.add(entry)
    return entry, contract


def _cohort(session):
    cohort = ProspectiveCohort(id="cohort", name="September 4 macro pilot", status="draft", budget_usd=1)
    session.add(cohort)
    session.flush([cohort])
    return cohort


def _freeze_cohort(cohort, entries: list[tuple[ProspectiveEntry, object]]):
    manifest = {"schema_version": "prospective_v1", "entries": [
        {"entry_id": entry.id, "contract": contract.model_dump(mode="json")} for entry, contract in entries
    ]}
    cohort.status = "awaiting_resolution"
    cohort.manifest_json = json.dumps(manifest, sort_keys=True)
    cohort.manifest_hash = "frozen-manifest"


def _stub(monkeypatch, content: bytes):
    from forecastlab_api import official_macro_outcomes
    monkeypatch.setattr(official_macro_outcomes, "utcnow", lambda: RETRIEVED_AT)
    monkeypatch.setattr(official_macro_outcomes, "safe_get", lambda url, **_: SafeResponse(
        url=url, final_url=url, status_code=200, content=content, content_type="text/html", bytes_read=len(content)))


def test_august_cpi_frozen_cohort_auto_adjudicates_strict_thresholds(client, monkeypatch):
    """3.4 is YES for >3.2 and NO for >3.4; neither requires a human identity."""
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import SYSTEM_ATTRIBUTION, process_prospective_entry

    _stub(monkeypatch, cpi_html())
    with SessionLocal() as session:
        cohort = _cohort(session)
        low = _entry(session, entry_id="entry-low", threshold=3.2)
        high = _entry(session, entry_id="entry-high", threshold=3.4)
        _freeze_cohort(cohort, [low, high])
        session.commit()
        first = process_prospective_entry(session, "entry-low")
        second = process_prospective_entry(session, "entry-high")
        assert (first.status, first.outcome, second.status, second.outcome) == ("ready", 1, "ready", 0)
        assert first.outcome_known_at == RELEASE_AT and first.retrieved_at == RETRIEVED_AT
        assert json.loads(first.measurement_json)["value_decimal"] == "3.4"
        outcomes = list(session.scalars(select(ProspectiveOutcome).order_by(ProspectiveOutcome.entry_id)).all())
        assert [row.outcome for row in outcomes] == [0, 1]
        assert {row.confirmed_by for row in outcomes} == {SYSTEM_ATTRIBUTION}
        assert all(json.loads(row.evidence)["amendment_id"] for row in outcomes)
        # Reprocessing the same retained bytes is idempotent, including scoring input.
        assert process_prospective_entry(session, "entry-low").id == first.id
        assert len(list(session.scalars(select(ProspectiveOutcome).where(ProspectiveOutcome.entry_id == "entry-low")))) == 1
        assert first.contract_hash == contract_hash(low[1])


def test_selected_cohort_processing_does_not_adjudicate_other_cohorts(client, monkeypatch):
    from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_due_prospective_entries

    _stub(monkeypatch, cpi_html())
    with SessionLocal() as session:
        selected = _cohort(session)
        selected_entry = _entry(session, entry_id="selected", threshold=3.2)
        _freeze_cohort(selected, [selected_entry])
        other = ProspectiveCohort(id="other-cohort", name="Other pilot", status="draft", budget_usd=1)
        session.add(other)
        session.flush([other])
        other_entry = _entry(session, entry_id="other", threshold=3.4)
        other_entry[0].cohort_id = other.id
        _freeze_cohort(other, [other_entry])
        session.commit()

        processed = process_due_prospective_entries(session, cohort_id=selected.id)
        assert len(processed) == 1
        assert processed[0].prospective_entry_id == selected_entry[0].id
        assert session.scalars(select(OfficialMacroOutcomeAmendment)).all() == processed
        assert session.scalars(select(ProspectiveOutcome)).all()[0].entry_id == selected_entry[0].id


@pytest.mark.parametrize("content, reason", [
    (cpi_html(period="JULY 2026"), "release_observation_period_mismatch"),
    (cpi_html(qualifier="after seasonal adjustment"), "headline_measurement_unverified"),
    (b"<pre>8:30 a.m. (ET) Friday, September 11, 2026 CORRECTION TO CONSUMER PRICE INDEX - AUGUST 2026</pre>", "first_release_unavailable_or_corrected"),
])
def test_bad_or_revised_official_evidence_is_retained_as_exception(client, monkeypatch, content, reason):
    from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    _stub(monkeypatch, content)
    with SessionLocal() as session:
        cohort = _cohort(session)
        row, contract = _entry(session, entry_id="entry", threshold=3.2)
        _freeze_cohort(cohort, [(row, contract)])
        session.commit()
        amendment = process_prospective_entry(session, "entry")
        assert amendment.status == "exception"
        assert amendment.exception_code == reason
        assert session.scalars(select(ProspectiveOutcome)).all() == []
        assert process_prospective_entry(session, "entry").id == amendment.id
        assert len(session.scalars(select(OfficialMacroOutcomeAmendment)).all()) == 1


def test_conflicting_cpi_headlines_fail_closed_without_outcome_or_score(client, monkeypatch):
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_prospective_entry
    from forecastlab_api.prospective import cohort_report

    conflicting = cpi_html().replace(b"</pre>",
        b" Over the last 12 months, the all items index increased 3.5 percent before seasonal adjustment.</pre>")
    _stub(monkeypatch, conflicting)
    with SessionLocal() as session:
        cohort = _cohort(session)
        row, contract = _entry(session, entry_id="entry", threshold=3.4)
        _freeze_cohort(cohort, [(row, contract)])
        session.commit()
        amendment = process_prospective_entry(session, row.id)
        assert amendment.status == "exception"
        assert amendment.exception_code == "headline_measurement_conflicting"
        assert session.scalars(select(ProspectiveOutcome)).all() == []
        assert cohort_report(session, cohort.id)["questions"][0]["outcome"] is None


def test_identical_repeated_cpi_headlines_remain_valid(client, monkeypatch):
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    repeated = cpi_html().replace(b"</pre>",
        b" Over the last 12 months, the all items index increased 3.4 percent before seasonal adjustment.</pre>")
    _stub(monkeypatch, repeated)
    with SessionLocal() as session:
        cohort = _cohort(session)
        row, contract = _entry(session, entry_id="entry", threshold=3.4)
        _freeze_cohort(cohort, [(row, contract)])
        session.commit()
        amendment = process_prospective_entry(session, row.id)
        assert amendment.status == "ready"
        assert amendment.outcome == 0


def test_tampered_or_conflicting_second_release_does_not_replace_outcome(client, monkeypatch):
    from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import (
        FrozenMacroTarget,
        process_prospective_entry,
        retain_official_macro_amendment,
    )

    _stub(monkeypatch, cpi_html(value="3.4"))
    with SessionLocal() as session:
        cohort = _cohort(session)
        row, contract = _entry(session, entry_id="entry", threshold=3.2)
        _freeze_cohort(cohort, [(row, contract)])
        session.commit()
        ready = process_prospective_entry(session, "entry")
        _stub(monkeypatch, cpi_html(value="3.5"))
        exception = retain_official_macro_amendment(session, FrozenMacroTarget(
            question_id=row.question_id, prospective_entry_id=row.id,
            contract=contract, expected_contract_hash=contract_hash(contract),
            macro=MacroSpec.model_validate_json(row.macro_json), release_event=row.release_event,
        ))
        assert ready.status == "ready"
        assert exception.status == "exception"
        assert exception.exception_code == "official_release_conflicting_or_tampered"
        assert len(session.scalars(select(ProspectiveOutcome)).all()) == 1
        assert len(session.scalars(select(OfficialMacroOutcomeAmendment)).all()) == 2


@pytest.mark.parametrize("bls_raises", [False, True])
def test_dated_dol_original_pdf_is_used_when_bls_archive_is_blocked(client, monkeypatch, bls_raises):
    """The official PDF is an allowed BLS-lineage fallback, not a live-page substitute."""
    from forecastlab_api import official_macro_outcomes
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    release_at = datetime(2026, 8, 12, 12, 30, tzinfo=UTC)
    pdf = (Path(__file__).parent / "fixtures" / "official_releases" / "cpi_08122026.pdf").read_bytes()
    seen = []
    def get(url, **_):
        seen.append(url)
        if "bls.gov" in url:
            if bls_raises:
                raise TimeoutError("Preferred archive unavailable")
            return SafeResponse(url=url, final_url=url, status_code=403, content=b"blocked", content_type="text/html")
        return SafeResponse(url=url, final_url=url, status_code=200, content=pdf, content_type="application/pdf", bytes_read=len(pdf))
    monkeypatch.setattr(official_macro_outcomes, "safe_get", get)
    monkeypatch.setattr(official_macro_outcomes, "utcnow", lambda: RETRIEVED_AT)
    with SessionLocal() as session:
        cohort = _cohort(session)
        spec = MacroSpec(indicator="cpi", observation_period="2026-07", threshold=3.2, comparison="gt", release_at=release_at)
        contract = spec.template("question-pdf", "contract-pdf").model_copy(update={"status": "approved"})
        session.add(Question(id="question-pdf", original_text=contract.original_question, normalized_text=contract.normalized_question,
            requested_mode="live", requested_profile_id="root_event_ensemble_v1"))
        session.flush()
        entry = ProspectiveEntry(id="entry-pdf", cohort_id="cohort", question_id="question-pdf", contract_json=contract.model_dump_json(),
            macro_json=spec.model_dump_json(), release_event="cpi-2026-08-12", cutoff=datetime(2026, 8, 5, 6, tzinfo=UTC))
        session.add(entry)
        _freeze_cohort(cohort, [(entry, contract)])
        session.commit()
        amendment = process_prospective_entry(session, entry.id)
        assert amendment.status == "ready" and amendment.outcome == 1
        assert json.loads(amendment.source_identity_json)["source_kind"] == "dated_original_pdf_republication"
        assert seen[0].startswith("https://www.bls.gov/news.release/archives/")
        assert seen[1] == "https://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf"


def test_frozen_contract_tampering_blocks_before_fetch(client, monkeypatch):
    from forecastlab_api import official_macro_outcomes
    from forecastlab_api.db import SessionLocal
    calls = []
    monkeypatch.setattr(official_macro_outcomes, "safe_get", lambda *args, **kwargs: calls.append(args))
    with SessionLocal() as session:
        cohort = _cohort(session)
        row, contract = _entry(session, entry_id="entry", threshold=3.2)
        _freeze_cohort(cohort, [(row, contract)])
        session.commit()
        row.contract_json = row.contract_json.replace("3.2", "3.3", 1)
        session.commit()
        amendment = official_macro_outcomes.process_prospective_entry(session, "entry")
        assert amendment.status == "exception"
        assert amendment.exception_code == "frozen_contract_hash_mismatch"
        assert calls == []


def test_transient_missing_source_retries_on_schedule_without_duplicate_exception(client, monkeypatch):
    from forecastlab_api import official_macro_outcomes
    from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.official_macro_outcomes import process_prospective_entry

    clock = [RETRIEVED_AT]
    monkeypatch.setattr(official_macro_outcomes, "utcnow", lambda: clock[0])
    calls = []
    def unavailable(url, **_):
        calls.append(url)
        return SafeResponse(url=url, final_url=url, status_code=403, content=b"blocked", content_type="text/html")
    monkeypatch.setattr(official_macro_outcomes, "safe_get", unavailable)
    with SessionLocal() as session:
        cohort = _cohort(session)
        row, contract = _entry(session, entry_id="entry", threshold=3.2)
        _freeze_cohort(cohort, [(row, contract)])
        session.commit()
        first = process_prospective_entry(session, row.id)
        second = process_prospective_entry(session, row.id)
        assert first.id == second.id and first.exception_code == "official_dated_release_unavailable"
        assert len(calls) == 2  # one BLS and one DOL candidate in the first attempt only
        clock[0] = RETRIEVED_AT.replace(minute=31)
        retry = process_prospective_entry(session, row.id)
        assert retry.id != first.id and retry.exception_code == first.exception_code
        assert len(calls) == 4
        assert len(session.scalars(select(OfficialMacroOutcomeAmendment)).all()) == 2
        clock[0] = RETRIEVED_AT.replace(minute=45)
        assert process_prospective_entry(session, row.id).id == retry.id
        assert len(calls) == 4
