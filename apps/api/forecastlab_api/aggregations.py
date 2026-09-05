from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.schemas import ForecastAggregation
from forecastlab.timeutil import as_utc
from forecastlab_api.models import ForecastAggregationRow


class ForecastAggregationStoreError(ValueError):
    """Raised when a run already has a different immutable aggregation record."""


def _json_payload(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw or "[]")
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("forecast_aggregation_json_must_be_a_list_of_objects")
    return payload


def forecast_aggregation_from_row(row: ForecastAggregationRow) -> ForecastAggregation:
    return ForecastAggregation(
        id=row.id,
        forecast_run_id=row.forecast_run_id,
        method=row.method,
        final_probability=row.final_probability,
        calculation_trace=_json_payload(row.calculation_trace_json),
        node_contributions=_json_payload(row.node_contributions_json),
        created_at=as_utc(row.created_at),
    )


def _calculation_payload(aggregation: ForecastAggregation) -> dict[str, Any]:
    return {
        "method": aggregation.method,
        "final_probability": aggregation.final_probability,
        "calculation_trace": aggregation.calculation_trace,
        "node_contributions": [
            contribution.model_dump(mode="json") for contribution in aggregation.node_contributions
        ],
    }


def store_forecast_aggregation(
    session: Session,
    aggregation: ForecastAggregation,
) -> ForecastAggregationRow:
    """Persist one immutable aggregation per run, returning an identical prior write."""

    existing = session.scalar(
        select(ForecastAggregationRow).where(
            ForecastAggregationRow.forecast_run_id == aggregation.forecast_run_id
        )
    )
    if existing is not None:
        if _calculation_payload(forecast_aggregation_from_row(existing)) != _calculation_payload(aggregation):
            raise ForecastAggregationStoreError("forecast_run_already_has_different_aggregation")
        return existing

    row = ForecastAggregationRow(
        id=aggregation.id,
        forecast_run_id=aggregation.forecast_run_id,
        method=aggregation.method,
        final_probability=aggregation.final_probability,
        calculation_trace_json=json.dumps(aggregation.calculation_trace, sort_keys=True),
        node_contributions_json=json.dumps(
            [contribution.model_dump(mode="json") for contribution in aggregation.node_contributions],
            sort_keys=True,
        ),
        created_at=aggregation.created_at,
    )
    session.add(row)
    session.flush()
    return row
