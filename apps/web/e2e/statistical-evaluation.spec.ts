import { expect, test } from "@playwright/test";

const profiles = ["single_model_forecaster_v1", "three_track_forecaster", "graph_forecaster_v1"];

function calibration(profileId: string) {
  return {
    profile_id: profileId,
    performance: {
      calibration_buckets: {
        available: true,
        sample_count: 25,
        minimum_required: 20,
        evidence_status: "interval estimate available",
        buckets: [
          { label: "0-20%", forecast_count: 2, average_predicted_probability: 0.12, actual_outcome_frequency: 0.0 },
          { label: "20-40%", forecast_count: 5, average_predicted_probability: 0.31, actual_outcome_frequency: 0.2 },
          { label: "40-60%", forecast_count: 8, average_predicted_probability: 0.51, actual_outcome_frequency: 0.5 },
          { label: "60-80%", forecast_count: 6, average_predicted_probability: 0.69, actual_outcome_frequency: 0.67 },
          { label: "80-100%", forecast_count: 4, average_predicted_probability: 0.88, actual_outcome_frequency: 1.0 }
        ]
      }
    }
  };
}

test("controlled experiment statistical analysis", async ({ page }) => {
  await page.route(/\/api\/datasets$/, (route) =>
    route.fulfill({ json: { datasets: [{ id: "legacy", name: "Fixture", is_synthetic: true, question_count: 1 }] } })
  );
  await page.route(/\/api\/profiles$/, (route) => route.fulfill({ json: [] }));
  await page.route(/\/api\/evaluations\/v1$/, (route) =>
    route.fulfill({ json: { profiles: [], question_count: 0, task_count: 0 } })
  );
  await page.route(/\/api\/forecast-experiments$/, (route) =>
    route.fulfill({
      json: {
        experiments: [{ id: "experiment-statistical", dataset_id: "resolved-v1", status: "completed" }],
        comparison_profiles: profiles
      }
    })
  );
  await page.route(/\/api\/forecast-experiments\/experiment-statistical\/analysis$/, (route) =>
    route.fulfill({
      json: {
        experiment_id: "experiment-statistical",
        status: "completed",
        methodology: {
          bootstrap_samples: 2000,
          random_seed: 20260823,
          minimum_paired_questions: 20
        },
        comparisons: [
          {
            profile_a: profiles[0],
            profile_b: profiles[1],
            question_count: 25,
            mean_brier_difference: 0.08,
            mean_log_loss_difference: 0.167,
            brier_confidence_interval: [0.04, 0.12],
            log_loss_confidence_interval: [0.09, 0.24],
            mean_cost_difference: -1,
            mean_latency_difference: -100,
            evidence_status: "interval estimate available"
          },
          {
            profile_a: profiles[1],
            profile_b: profiles[2],
            question_count: 25,
            mean_brier_difference: 0.06,
            mean_log_loss_difference: 0.143,
            brier_confidence_interval: [0.02, 0.1],
            log_loss_confidence_interval: [0.06, 0.21],
            mean_cost_difference: -1,
            mean_latency_difference: -100,
            evidence_status: "interval estimate available"
          },
          {
            profile_a: profiles[0],
            profile_b: profiles[2],
            question_count: 25,
            mean_brier_difference: 0.14,
            mean_log_loss_difference: 0.31,
            brier_confidence_interval: [0.08, 0.2],
            log_loss_confidence_interval: [0.18, 0.42],
            mean_cost_difference: -2,
            mean_latency_difference: -200,
            evidence_status: "interval estimate available"
          }
        ],
        profiles: profiles.map(calibration),
        cost_efficiency: profiles.map((profileId, index) => ({
          profile_id: profileId,
          total_cost: 25 * (index + 1),
          cost_per_question: index + 1,
          brier_per_dollar: 0.2 - index * 0.05,
          log_loss_per_dollar: 0.6 - index * 0.1,
          latency_per_question: 100 * (index + 1)
        })),
        notice: "Internal research measurements only. Reported intervals are paired observed differences, not a system ranking."
      }
    })
  );

  await page.goto("/lab");

  await expect(page.getByRole("heading", { name: "Paired statistical evaluation" })).toBeVisible();
  await expect(page.getByTestId("statistical-comparison")).toHaveCount(3);
  await expect(page.getByText("25 paired questions · interval estimate available").first()).toBeVisible();
  await expect(page.getByRole("img", { name: /Brier observed difference \+0\.0800/ })).toBeVisible();
  await expect(page.getByText("95% interval +0.0400 to +0.1200")).toBeVisible();
  await expect(page.getByRole("img", { name: /calibration chart with five probability buckets/ })).toHaveCount(3);
  await expect(page.getByRole("heading", { name: "Recorded resource ratios" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/winner|superior/i);
});
