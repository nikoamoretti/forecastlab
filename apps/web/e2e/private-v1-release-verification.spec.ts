import { expect, test } from "@playwright/test";

const positiveQuestionId = process.env.PRIVATE_V1_POSITIVE_QUESTION_ID;
const negativeQuestionId = process.env.PRIVATE_V1_NEGATIVE_QUESTION_ID;

test.describe("private V1 release verification", () => {
  test.skip(
    !positiveQuestionId || !negativeQuestionId,
    "Release-verification question IDs are supplied only by the isolated verifier."
  );

  test("renders the complete synthetic private-V1 path", async ({ page }) => {
    await page.goto(`/forecasts/${positiveQuestionId}`);
    const report = page.getByRole("region", { name: "V1 Forecast Graph report" });
    await expect(report.getByRole("heading", { name: "Forecast Graph report" })).toBeVisible();
    await expect(report.getByRole("heading", { name: "Scenario Synthesis" })).toBeVisible();
    await expect(report.getByRole("heading", { name: "Relationship-aware aggregation" })).toBeVisible();
    await expect(page.getByText("synthetic execution", { exact: false })).toBeVisible();
    await expect(page.getByText("No private-V1 probability was produced", { exact: false })).toHaveCount(0);
  });

  test("renders the insufficient-evidence run without a probability", async ({ page }) => {
    await page.goto(`/forecasts/${negativeQuestionId}`);
    const report = page.getByRole("region", { name: "V1 Forecast Graph report" });
    await expect(report.getByRole("heading", { name: "Forecast Graph report" })).toBeVisible();
    await expect(
      report.getByText(
        "No private-V1 probability was produced because deterministic evidence sufficiency was not met.",
        { exact: true }
      )
    ).toBeVisible();
    await expect(page.getByText("Provider or run failure", { exact: false })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Scenario Synthesis" })).toHaveCount(0);
  });
});
