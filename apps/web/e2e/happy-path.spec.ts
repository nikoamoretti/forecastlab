import { test, expect } from "@playwright/test";

test("mock happy path", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("ForecastLab")).toBeVisible();
  await page.getByRole("link", { name: "New question", exact: true }).click();
  await expect(page.getByRole("heading", { name: /State a binary claim/i })).toBeVisible();
  await page.getByRole("button", { name: /Generate Forecast Contract/i }).click();
  await expect(page.getByRole("heading", { name: /Review the contract/i })).toBeVisible({
    timeout: 30000
  });
  await expect(page.getByText("Yes means")).toBeVisible();
  await expect(page.getByText("No means")).toBeVisible();
  await expect(page.getByText("Resolution source")).toBeVisible();
  await page.getByRole("button", { name: /Approve and generate Research Graph/i }).click();
  await expect(page.getByRole("heading", { name: /Review the research plan/i })).toBeVisible({
    timeout: 30000
  });
  await expect(page.getByRole("heading", { name: "Research Graph", exact: true })).toBeVisible();
  await expect(page.getByText("base rate", { exact: true })).toBeVisible();
  await expect(page.getByText("adversarial", { exact: true })).toBeVisible();
  await expect(page.getByText("resolver", { exact: true })).toBeVisible();
  await expect(page.getByText(/Importance 90%/i)).toBeVisible();
  const evidenceHref = await page.getByRole("link", { name: "View evidence claims" }).first().getAttribute("href");
  expect(evidenceHref).toBeTruthy();
  const evidencePage = await page.context().newPage();
  await evidencePage.goto(evidenceHref!);
  await expect(evidencePage.getByText("Forecast Node", { exact: true })).toBeVisible();
  await expect(evidencePage.getByRole("heading", { name: "Claims", exact: true })).toBeVisible();
  await expect(evidencePage.getByText(/No Evidence Claims have been extracted/i)).toBeVisible();
  await evidencePage.close();
  await page.getByRole("button", { name: /Launch mock run/i }).click();
  await expect(page.getByText("Ensemble estimate")).toBeVisible({ timeout: 120000 });
  await expect(page.getByText("37.4%").first()).toBeVisible({ timeout: 120000 });
  await expect(page.getByText("DEMO", { exact: true })).toBeVisible();
  await expect(page.getByText("demo_fixtures")).toBeVisible();
  await expect(page.getByText("Fixture evidence used")).toBeVisible();
  await expect(page.getByText("Independent tracks")).toBeVisible();
  await expect(page.getByText("Provider usage audit")).toBeVisible();
  await expect(page.getByText("Evidence ledger")).toBeVisible();
  await expect(page.getByRole("table").getByRole("link").first()).toBeVisible();
  await expect(page.getByText("base_rate").first()).toBeVisible();
  await expect(page.getByText("current_evidence").first()).toBeVisible();
  await expect(page.getByText("skeptic").first()).toBeVisible();
  await page.getByText("base_rate").first().click();
  await page.getByRole("button", { name: "Simulate watch change" }).click();
  await page.getByRole("button", { name: "Rerun" }).click();
  await expect(page.getByText(/2 versions/i)).toBeVisible({ timeout: 120000 });
});

test("operational graph smoke report shows node evidence and calculation trace", async ({ page }) => {
  test.setTimeout(180000);
  await page.goto("/new");
  await page.getByLabel("Forecast profile").selectOption("graph_live_smoke_v1");
  await page.getByRole("button", { name: /Generate Forecast Contract/i }).click();
  await expect(page.getByRole("heading", { name: /Review the contract/i })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: /Approve and generate Research Graph/i }).click();
  await expect(page.getByRole("heading", { name: /Review the research plan/i })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: /Launch mock run/i }).click();

  await expect(page.getByRole("heading", { name: "Forecast Graph report" })).toBeVisible({ timeout: 120000 });
  const report = page.getByRole("region", { name: "V1 Forecast Graph report" });
  await expect(report.getByText(/3\/7/)).toBeVisible();
  await expect(page.getByText("Supporting evidence", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Opposing evidence", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Uncertainty", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Model: mock:mock-forecast-v1/).first()).toBeVisible();
  await expect(report.getByText("Published date verified").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Calculation trace" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Final answer" })).toBeVisible();
  await expect(report.getByText(/graph_live_smoke_v1 probability is/i)).toBeVisible();
  await expect(report.getByRole("heading", { name: "Graph generation audit" })).toBeVisible();
  await expect(report).toContainText("completion/visible caps 8192 / 1536");
  await expect(report).toContainText("reasoning/verbosity minimal / low");
});

test("private V1 report fails closed when deterministic evidence is insufficient", async ({ page }) => {
  test.setTimeout(180000);
  await page.goto("/new");
  await page.getByLabel("Forecast profile").selectOption("graph_forecaster_v1");
  await page.getByRole("button", { name: /Generate Forecast Contract/i }).click();
  await expect(page.getByRole("heading", { name: /Review the contract/i })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: /Approve and generate Research Graph/i }).click();
  await expect(page.getByRole("heading", { name: /Review the research plan/i })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: /Launch mock run/i }).click();

  await expect(page.getByText(/No private-V1 probability was produced because deterministic evidence sufficiency was not met/i).first()).toBeVisible({ timeout: 120000 });
  await expect(page.getByRole("heading", { name: "Evidence Sufficiency" })).toBeVisible();
  await expect(page.getByText(/failed · policy private_v1_evidence_gate_v1/i)).toBeVisible();
  await expect(page.getByText("two_distinct_source_hosts_required", { exact: true })).toBeVisible();
  const material = page.getByLabel("Material Node Completeness");
  await expect(material.getByRole("heading", { name: "Material Node Completeness" })).toBeVisible();
  await expect(material.getByText(/Plan passed · policy private_v1_material_node_gate_v1/i)).toBeVisible();
  await expect(material.getByText(/Execution passed · assessment/i)).toBeVisible();
  const report = page.getByRole("region", { name: "V1 Forecast Graph report" });
  await expect(report).toContainText("final —");
  await expect(report.getByText(/No private-V1 probability was produced because deterministic evidence sufficiency was not met/i)).toBeVisible();
});

test("private V1 material-node failure is explicit and has no probability", async ({ page }) => {
  await page.route("**/api/questions/material-gate-fixture/report", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        original_text: "Will the material outcome occur?",
        status: "failed",
        stale: false,
        version_count: 0,
        latest_probability: null,
        versions: [],
        watches: [],
        contract: { resolution_deadline: null },
        latest_run: {
          status: "failed",
          mode: "demo",
          cost_usd: 0,
          latency_ms: 1,
          progress_stage: "failed",
          execution_context: { effective_mode: "demo" },
          budget: { cost_is_estimated: true },
          aggregation: {},
          tracks: [],
          evidence: [],
          v1_report: {
            profile_id: "graph_forecaster_v1",
            execution_status: "failed",
            final_probability: null,
            graph: { node_count: 5 },
            nodes: [],
            calculation: { trace: [] },
            material_node_completeness: {
              plan_audit: {
                status: "passed",
                policy_version: "private_v1_material_node_gate_v1",
                selected_frontier_weight: "0.2",
                maximum_skipped_weight: "0.1",
                higher_importance_skipped_node_ids: [],
                frontier_tie_skipped_node_ids: [],
                reasons: []
              },
              execution_assessment: {
                id: "material-assessment-fixture",
                status: "failed",
                included_frontier_weight: "0.15",
                maximum_excluded_weight: "0.3",
                included_graph_weight: "0.6",
                excluded_graph_weight: "0.4",
                higher_importance_excluded_node_ids: ["node-a"],
                reasons: ["higher_importance_graph_node_excluded"],
                warnings: [],
                assessment_input_hash: "a".repeat(64)
              }
            },
            final_answer: {
              status: "failed",
              probability: null,
              statement:
                "No private-V1 probability was produced because a higher-importance graph uncertainty was omitted while lower-importance nodes were retained."
            }
          }
        }
      })
    });
  });

  await page.goto("/forecasts/material-gate-fixture");

  await expect(
    page.getByText(
      /No private-V1 probability was produced because a higher-importance graph uncertainty was omitted/i
    ).first()
  ).toBeVisible();
  const material = page.getByLabel("Material Node Completeness");
  await expect(material.getByText(/Plan passed/i)).toBeVisible();
  await expect(material.getByText(/Execution failed/i)).toBeVisible();
  await expect(material.getByText("higher_importance_graph_node_excluded", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "V1 Forecast Graph report" })).toContainText("final —");
});

test("single-model baseline skips graph construction and aggregation", async ({ page }) => {
  test.setTimeout(180000);
  await page.goto("/new");
  await page.getByLabel("Forecast profile").selectOption("single_model_forecaster_v1");
  await page.getByRole("button", { name: /Generate Forecast Contract/i }).click();
  await expect(page.getByRole("heading", { name: /Review the contract/i })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: "Approve Forecast Contract" }).click();

  await expect(page.getByRole("heading", { name: /Ready for a single-model forecast/i })).toBeVisible({
    timeout: 30000
  });
  await expect(page.getByText(/creates no Forecast Graph and performs no probability aggregation/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Research Graph", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /Launch mock run/i }).click();

  await expect(page.getByText("Single-model estimate")).toBeVisible({ timeout: 120000 });
  await expect(page.getByText("36.0%").first()).toBeVisible();
  await expect(
    page.getByText("Direct structured model probability. No probability aggregation.", { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Single-model forecast" })).toBeVisible();
  await expect(page.getByText(/one approved Forecast Contract/i)).toBeVisible();
  await expect(page.getByText("Uncertainty", { exact: true })).toBeVisible();
  await expect(page.getByText(/Synthetic mock evidence is not real-world forecasting evidence/i)).toBeVisible();
  await expect(page.getByText("single_model_forecast", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Forecast Graph report" })).toHaveCount(0);
});
