import { test, expect } from "@playwright/test";

const probabilityDistribution = Array.from({ length: 10 }, (_, index) => ({
  bin_start: index / 10,
  bin_end: (index + 1) / 10,
  count: index === 6 ? 1 : 0
}));

test("controlled experiment analysis and internal failure review", async ({ page }) => {
  let classificationSaved = false;
  await page.route("**/api/evaluation/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/evaluation/datasets") {
      await route.fulfill({
        json: {
          datasets: [{ id: "dataset-1", name: "Synthetic UI fixture", version: "1", question_count: 1 }],
          execution_supported: true,
          comparison_profiles: ["three_track_forecaster", "graph_forecaster_v1"]
        }
      });
      return;
    }
    if (path === "/api/evaluation/experiments" && request.method() === "POST") {
      await route.fulfill({ status: 201, json: { id: "experiment-1" } });
      return;
    }
    if (path === "/api/evaluation/experiments/experiment-1/report") {
      await route.fulfill({
        json: {
          notice: "Synthetic UI fixture. No superiority claim.",
          profiles: [
            {
              profile_id: "three_track_forecaster",
              brier_score: 0.16,
              log_loss: 0.51,
              total_cost: 0,
              mean_latency: 12,
              completion_rate: 1,
              evidence_coverage: 0.8
            },
            {
              profile_id: "graph_forecaster_v1",
              brier_score: 0.09,
              log_loss: 0.36,
              total_cost: 0,
              mean_latency: 18,
              completion_rate: 1,
              evidence_coverage: 1
            }
          ]
        }
      });
      return;
    }
    if (path === "/api/evaluation/experiments/experiment-1/analysis") {
      const failure = classificationSaved
        ? [{
            id: "failure-1",
            failure_group: "reasoning",
            category: "overconfidence",
            annotation: "The retained uncertainty did not justify this probability."
          }]
        : [];
      const profile = (profileId: string) => ({
        profile_id: profileId,
        performance: {
          brier_score: profileId === "three_track_forecaster" ? 0.16 : 0.09,
          log_loss: profileId === "three_track_forecaster" ? 0.51 : 0.36,
          calibration_buckets: {
            available: false,
            sample_count: 1,
            minimum_required: 20,
            message: "Reliability display needs at least 20 resolved predictions; have 1."
          },
          probability_distribution: probabilityDistribution,
          scored_questions: 1
        },
        reliability: {
          assigned_questions: 1,
          completed: 1,
          partial: 0,
          failures: 0,
          completion_rate: 1,
          partial_rate: 0,
          failure_rate: 0
        },
        research: { evidence_coverage: 1, source_count: 2, claim_count: 3 },
        cost: { total_cost: 0, cost_per_question: 0 }
      });
      await route.fulfill({
        json: {
          notice: "Internal research diagnostics only. No superiority claim.",
          profiles: [profile("three_track_forecaster"), profile("graph_forecaster_v1")],
          rows: [
            {
              evaluation_run_id: "run-baseline",
              question: "Will the synthetic indicator cross its threshold?",
              profile_id: "three_track_forecaster",
              probability: 0.6,
              outcome: 1,
              brier_score: 0.16,
              error: null,
              failures: failure
            },
            {
              evaluation_run_id: "run-graph",
              question: "Will the synthetic indicator cross its threshold?",
              profile_id: "graph_forecaster_v1",
              probability: 0.7,
              outcome: 1,
              brier_score: 0.09,
              error: null,
              failures: []
            }
          ]
        }
      });
      return;
    }
    if (path === "/api/evaluation/experiments/experiment-1") {
      await route.fulfill({
        json: {
          id: "experiment-1",
          status: "completed",
          completed_runs: 2,
          total_runs: 2,
          partial_runs: 0,
          failed_runs: 0,
          percent: 100
        }
      });
      return;
    }
    if (path === "/api/evaluation/runs/run-baseline/failures" && request.method() === "POST") {
      classificationSaved = true;
      await route.fulfill({
        status: 201,
        json: {
          id: "failure-1",
          failure_group: "reasoning",
          category: "overconfidence",
          annotation: "The retained uncertainty did not justify this probability."
        }
      });
      return;
    }
    await route.abort();
  });

  await page.goto("/lab");
  await expect(page.getByLabel("Frozen evaluation dataset")).toHaveValue("dataset-1");
  await page.getByRole("button", { name: "Start controlled comparison" }).click();
  await expect(page.getByRole("heading", { name: "Performance analysis" })).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("Internal error review")).toBeVisible();
  await page.getByLabel("Failure classification").selectOption("overconfidence");
  await page.getByLabel("Internal annotation").fill(
    "The retained uncertainty did not justify this probability."
  );
  await page.getByRole("button", { name: "Save internal classification" }).click();
  await expect(page.getByText("The retained uncertainty did not justify this probability.")).toBeVisible();
  await expect(page.getByText("Internal review annotation saved.")).toBeVisible();
});
