import { test, expect } from "@playwright/test";

test("mock happy path", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("ForecastLab")).toBeVisible();
  await page.getByRole("link", { name: "New question", exact: true }).click();
  await expect(page.getByRole("heading", { name: /State a binary claim/i })).toBeVisible();
  await page.getByRole("button", { name: /Review resolution contract/i }).click();
  await expect(page.getByRole("heading", { name: /Edit the yes\/no rules/i })).toBeVisible({
    timeout: 30000
  });
  await page.getByRole("button", { name: /Save contract and launch mock run/i }).click();
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
