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

test("V1 graph report shows node evidence and calculation trace", async ({ page }) => {
  test.setTimeout(180000);
  await page.goto("/new");
  await page.getByLabel("Forecast profile").selectOption("graph_forecaster_v1");
  await page.getByRole("button", { name: /Generate Forecast Contract/i }).click();
  await expect(page.getByRole("heading", { name: /Review the contract/i })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: /Approve and generate Research Graph/i }).click();
  await expect(page.getByRole("heading", { name: /Review the research plan/i })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: /Launch mock run/i }).click();

  await expect(page.getByRole("heading", { name: "Forecast Graph report" })).toBeVisible({ timeout: 120000 });
  const report = page.getByRole("region", { name: "V1 Forecast Graph report" });
  await expect(report.getByText(/7\/7/)).toBeVisible();
  await expect(page.getByText("Supporting evidence", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Opposing evidence", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Uncertainty", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Model: mock:mock-forecast-v1/).first()).toBeVisible();
  await expect(report.getByText("Published date verified").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Calculation trace" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Final answer" })).toBeVisible();
  await expect(report.getByText(/graph_forecaster_v1 probability is/i)).toBeVisible();
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
