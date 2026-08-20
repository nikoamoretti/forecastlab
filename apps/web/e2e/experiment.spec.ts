import { test, expect } from "@playwright/test";

test("synthetic benchmark experiment", async ({ page }) => {
  test.setTimeout(240000);
  await page.goto("/lab");
  await expect(page.getByRole("heading", { name: /Benchmark experiments/i })).toBeVisible();
  await expect(page.getByText(/Badge:\s+synthetic/i)).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: "Create experiment" }).click();
  await expect(page.getByText(/\d+\/\d+ completed/i)).toBeVisible({ timeout: 180000 });
  await expect(page.getByText("completed", { exact: false }).first()).toBeVisible({ timeout: 180000 });
  await expect(page.getByText(/10\/10 completed/i)).toBeVisible({ timeout: 180000 });
  await expect(page.getByText("Software-verification fixtures only. Not evidence of real-world forecasting quality.")).toBeVisible();
  await expect(page.getByText("Experiment spend")).toBeVisible();
  await expect(page.getByText("All-valid metrics")).toBeVisible();
  await expect(page.getByText("Full-run-only metrics")).toBeVisible();
  await expect(page.getByText("Paired comparisons (all valid)")).toBeVisible();
  await expect(page.getByText("Reliability by profile")).toBeVisible();
  await page.goto("/");
  await expect(page.getByText("synthetic series A")).toHaveCount(0);
});
