import { test, expect } from "@playwright/test";

const pick = {
  id: "a".repeat(64), question: "Will U.S. unemployment exceed 4.3% in the February 2040 release?",
  macro: { indicator: "unemployment", observation_period: "2040-01", threshold: 4.3, comparison: "gt",
    release_at: "2040-02-03T13:30:00Z", revision_policy: "first_release" },
  reason: "Next scheduled unemployment release. The threshold is the latest observed value: 4.3 percent for 2039-12.",
  baseline: { period: "2039-12", source_url: "https://data.bls.gov/timeseries/LNS14000000", available_at: "2040-01-12T18:00:00Z" },
  schedule: { source_url: "https://www.bls.gov/schedule/2040/home.htm", checked_at: "2040-01-12T18:00:00Z" },
  release_event: "bls:empsit:2040-01", related_question_ids: ["different-threshold"],
  existing_question_id: null, existing_draft_run_id: null,
};
const body = { items: [pick], gaps: [], checked_at: "2040-01-12T18:00:00Z", policy: "Next release per indicator; review before research." };

test("system picks a question and opens one durable review without launching research", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", request => { if (request.method() === "POST") writes.push(request.url()); });
  const draft = { run_id: "chosen-draft", question_id: "chosen-question", status: "awaiting_review",
    progress_message: "Review the event, deadline, and resolver", cost_usd: 0, max_cost_usd: 5,
    macro: pick.macro, result: { question_selection: pick }, contract: {
      normalized_question: pick.question, yes_condition: "First release exceeds 4.3 percent.",
      no_condition: "First release is at most 4.3 percent.", resolution_date: pick.macro.release_at,
      authoritative_source: "https://www.bls.gov/news.release/empsit.nr0.htm", resolution_method: "Use the first published BLS value."
    } };
  await page.route("**/api/question-suggestions", route => route.fulfill({ json: body }));
  await page.route(`**/api/question-suggestions/${pick.id}/draft`, route => route.fulfill({ status: 201, json: draft }));
  await page.route("**/api/forecast-drafts/chosen-draft", route => route.fulfill({ json: draft }));
  await page.goto("/new?profile=root_event_ensemble_v1");
  await expect(page.getByRole("heading", { name: "Let ForecastLab pick." })).toBeVisible();
  await expect(page.getByText("Recommended next", { exact: true })).toBeVisible();
  await expect(page.getByText(/1 existing question shares this release/)).toBeVisible();
  await expect(page.getByLabel("Observation month")).toHaveCount(0);
  await page.getByRole("button", { name: "Review recommended question" }).click();
  await expect(page.getByRole("button", { name: "Approve question and forecast" })).toBeVisible();
  await expect(page.getByLabel("normalized question", { exact: true })).toHaveValue(pick.question);
  await expect(page.getByText("Selected by ForecastLab", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("resolution method", { exact: true })).toHaveValue(/first published/);
  await expect(page.getByText(/\$0.0000 spent of \$5.00/)).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toContain(`/api/question-suggestions/${pick.id}/draft`);
});

test("a suggested question can be adjusted using the existing macro form", async ({ page }) => {
  await page.route("**/api/question-suggestions", route => route.fulfill({ json: body }));
  await page.goto("/new?profile=root_event_ensemble_v1");
  await page.getByRole("button", { name: "Adjust inputs" }).click();
  await expect(page.getByLabel("Observation month")).toHaveValue("2040-01");
  await expect(page.getByLabel("Threshold", { exact: true })).toHaveValue("4.3");
  await expect(page.getByLabel("Release time (UTC)")).toHaveValue("2040-02-03T13:30");
  await expect(page.getByLabel("Mode", { exact: true })).toHaveValue("live");
});

test("already tracked questions resume their draft instead of creating another", async ({ page }) => {
  await page.route("**/api/question-suggestions", route => route.fulfill({ json: {
    ...body, items: [{ ...pick, existing_question_id: "tracked", existing_draft_run_id: "tracked-draft" }]
  } }));
  await page.goto("/new?profile=root_event_ensemble_v1");
  await expect(page.getByText("Already on your board", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Continue reviewing" })).toHaveAttribute("href", /draft=tracked-draft/);
  await expect(page.getByRole("button", { name: "Review recommended question" })).toHaveCount(0);
});

test("missing official sources show the reason and retain manual question entry", async ({ page }) => {
  await page.route("**/api/question-suggestions", route => route.fulfill({ json: {
    ...body, items: [], gaps: ["BLS release calendar unavailable."]
  } }));
  await page.goto("/new?profile=root_event_ensemble_v1");
  await expect(page.getByText(/No source-backed questions are available/)).toBeVisible();
  await expect(page.getByText("BLS release calendar unavailable.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Write my own question" }).click();
  await expect(page.getByRole("button", { name: "Prepare for review" })).toBeVisible();
});

test("recommendations and navigation fit a narrow mobile screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/question-suggestions", route => route.fulfill({ json: body }));
  await page.goto("/new?profile=root_event_ensemble_v1");
  await expect(page.getByRole("button", { name: "Review recommended question" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
