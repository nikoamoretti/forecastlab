import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // These manual-workflow checks must not consume the public BLS daily quota.
  await page.route("**/api/question-suggestions", route => route.fulfill({ json: {
    items: [], gaps: [], checked_at: "2040-01-01T00:00:00Z", policy: "Test fixtures"
  } }));
});

test("personal macro draft has one review, survives reload, and renders abstention history", async ({ page }) => {
  await page.goto("/new?profile=root_event_ensemble_v1");
  await expect(page.getByRole("heading", { name: "One question. A traceable forecast." })).toBeVisible();
  await page.getByRole("button", { name: "Write my own question" }).click();
  await page.getByLabel("Mode", { exact: true }).selectOption("demo");
  await page.getByLabel("Observation month").fill("2040-01");
  await page.getByLabel("Release time (UTC)").fill("2040-02-05T13:30");
  await page.getByRole("button", { name: "Prepare for review" }).click();
  await expect(page.getByRole("button", { name: "Approve question and forecast" })).toBeVisible({ timeout: 30000 });
  await page.reload();
  await expect(page.getByLabel("resolution method", { exact: true })).toHaveValue(/first/);
  await page.getByRole("button", { name: "Approve question and forecast" }).click();
  await expect(page).toHaveURL(/\/forecasts\//);
  await expect(page.getByText(/No forecast: not enough evidence/).first()).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole("heading", { name: "Research gaps" })).toBeVisible();
  const history = page.getByRole("heading", { name: "Version history" }).locator("..");
  await expect(history.getByRole("listitem")).toHaveCount(1);
  await page.getByRole("button", { name: "Run a fresh forecast" }).click();
  await expect(history.getByRole("listitem")).toHaveCount(2, { timeout: 30000 });
  await page.screenshot({ path: "/tmp/forecastlab-personal-v1-report.png", fullPage: true });
});

test("general binary contract can be edited and approved", async ({ page }) => {
  await page.goto("/new?profile=root_event_ensemble_v1");
  await page.getByRole("button", { name: "Write my own question" }).click();
  await page.getByLabel("Mode", { exact: true }).selectOption("demo");
  await page.getByLabel("Question type").selectOption("general");
  await page.getByLabel("Binary question").fill("Will unemployment exceed 5% by January 2040?");
  await page.getByRole("button", { name: "Prepare for review" }).click();
  await expect(page.getByRole("button", { name: "Approve question and forecast" })).toBeVisible({ timeout: 30000 });
  await page.getByLabel("resolution date", { exact: true }).fill("2040-02-05T13:30:00Z");
  await page.getByRole("button", { name: "Approve question and forecast" }).click();
  await expect(page).toHaveURL(/\/forecasts\//);
  await expect(page.getByText(/No forecast: not enough evidence/).first()).toBeVisible({ timeout: 30000 });
});

test("a newer withheld result never presents the older probability as current", async ({ page }) => {
  await page.route("**/api/questions/latest-withheld/report", route => route.fulfill({ json: {
    id: "latest-withheld", original_text: "A reviewed event", outcome_status: "insufficient_evidence",
    latest_run: { status: "completed", profile_id: "root_event_ensemble_v1", mode: "live", total_cost_usd: .15 },
    personal_report: { probability: null, evidence_gaps: ["missing_current_conditions_evidence"], estimates: [], contract: {} },
    versions: [{ id: "v2", created_at: "2026-09-04", ensemble_probability: null, profile_id: "root_event_ensemble_v1" },
      { id: "v1", created_at: "2026-09-03", ensemble_probability: .75, profile_id: "root_event_ensemble_v1" }]
  } }));
  await page.goto("/forecasts/latest-withheld");
  await expect(page.locator("article header")).toContainText("No forecast: not enough evidence");
  await expect(page.locator("article header")).not.toContainText("75% sure");
  await expect(page.getByRole("heading", { name: "Version history" }).locator("..")).toContainText("Yes · 75% sure");
});

test("a forecast leads with a verdict in words and keeps the exact probability", async ({ page }) => {
  await page.route("**/api/questions/verdict-first/report", route => route.fulfill({ json: {
    id: "verdict-first", original_text: "Will the October unemployment rate be above 4.2%?", outcome_status: "forecasted",
    latest_run: { status: "completed", profile_id: "root_event_ensemble_v1", mode: "live", total_cost_usd: .2 },
    personal_report: { probability: .374, evidence_gaps: [], estimates: [], contract: {} },
    versions: [{ id: "v1", created_at: "2026-10-02", ensemble_probability: .374, profile_id: "root_event_ensemble_v1" }]
  } }));
  await page.goto("/forecasts/verdict-first");
  const header = page.locator("article header");
  await expect(header.getByText("No, 63% sure", { exact: true })).toBeVisible();
  await expect(header).toContainText("We think it doesn't happen, 63% sure (37.4% chance of yes).");
  await expect(page.getByRole("heading", { name: "Version history" }).locator("..")).toContainText("No · 63% sure");
});

test("all runs show the call before the percentage", async ({ page }) => {
  await page.route("**/api/forecast-summaries?*", route => route.fulfill({ json: { total: 1, offset: 0, limit: 25, items: [{
    id: "verdict-board", original_text: "Will the 10-year yield be above 5.24%?", created_at: "2026-10-03T00:00:00Z", stale: false,
    run_id: "r1", profile_id: "single_model_forecaster_v1", mode: "live", status: "completed", outcome_status: "forecasted",
    probability: .8, cost_usd: .1, finished_at: "2026-10-03T00:00:00Z", run_created_at: "2026-10-03T00:00:00Z" }] } }));
  await page.goto("/runs");
  const row = page.getByRole("listitem").filter({ hasText: "10-year yield" });
  await expect(row.getByText("Yes · 80% sure", { exact: true })).toBeVisible();
  await expect(row).toContainText("80.0% chance of yes");
});

test("prospective setup creates unknown outcomes without spending and settings PATCH works", async ({ page }) => {
  await page.goto("/lab");
  await page.getByText("Create a prospective cohort from macro templates", { exact: true }).click();
  const name = `Browser cohort ${Date.now()}`;
  await page.getByLabel("Cohort name", { exact: true }).fill(name);
  await page.getByLabel("Forecast completion deadline (UTC)").fill("2039-12-31T23:00");
  await page.getByLabel("Observation month").fill("2040-01");
  await page.getByLabel("Release time (UTC)").fill("2040-02-05T13:30");
  await page.getByRole("button", { name: "Create reviewable cohort" }).click();
  await expect(page.getByLabel("Cohort", { exact: true })).toContainText(name);
  await expect(page.getByText(/\$0.0000 \/ \$15.00/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm outcome", exact: true })).toHaveCount(0);
  await page.getByText("Historical macro data settings", { exact: true }).click();
  await page.getByLabel("FRED API key").fill("browser-test-key");
  await page.getByRole("button", { name: "Save key locally" }).click();
  await expect(page.getByText(/ALFRED historical vintages: key configured/)).toBeVisible();
  await page.getByLabel("FRED API key").fill("");
  await page.getByRole("button", { name: "Save key locally" }).click();
  await expect(page.getByText(/ALFRED historical vintages: key not configured/)).toBeVisible();
});
