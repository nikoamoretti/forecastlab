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
