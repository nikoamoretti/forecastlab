from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.research_planning import ResearchPlan
from forecastlab_api.models import ResearchPlanRow


def research_plan_from_row(row: ResearchPlanRow) -> ResearchPlan:
    return ResearchPlan(
        id=row.id,
        forecast_run_id=row.forecast_run_id,
        selected_nodes=json.loads(row.selected_nodes_json or "[]"),
        skipped_nodes=json.loads(row.skipped_nodes_json or "[]"),
        priority_scores=json.loads(row.priority_scores_json or "{}"),
        budget_allocation=json.loads(row.budget_allocation_json or "{}"),
        created_at=row.created_at,
    )


def store_research_plan(session: Session, plan: ResearchPlan) -> ResearchPlanRow:
    existing = session.scalar(
        select(ResearchPlanRow).where(
            ResearchPlanRow.forecast_run_id == plan.forecast_run_id
        )
    )
    payload = {
        "selected_nodes_json": json.dumps(plan.selected_nodes),
        "skipped_nodes_json": json.dumps(plan.skipped_nodes),
        "priority_scores_json": json.dumps(plan.priority_scores, sort_keys=True),
        "budget_allocation_json": json.dumps(
            plan.budget_allocation,
            sort_keys=True,
        ),
    }
    if existing is not None:
        if (
            existing.id != plan.id
            or existing.selected_nodes_json != payload["selected_nodes_json"]
            or existing.skipped_nodes_json != payload["skipped_nodes_json"]
            or existing.priority_scores_json != payload["priority_scores_json"]
            or existing.budget_allocation_json != payload["budget_allocation_json"]
        ):
            raise ValueError("conflicting_research_plan_for_forecast_run")
        return existing
    row = ResearchPlanRow(
        id=plan.id,
        forecast_run_id=plan.forecast_run_id,
        created_at=plan.created_at,
        **payload,
    )
    session.add(row)
    session.flush()
    return row
