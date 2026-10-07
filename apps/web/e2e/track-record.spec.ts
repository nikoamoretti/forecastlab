import { test, expect, type Page } from "@playwright/test";

const forecasters = [
  { method: "claude_code_forecaster_v1", label: "Claude", about: "Researches on the web.", forecasts: 1, resolved: 0, right: 0, wrong: 0, toss_ups: 0, brier: null, coin_flip_brier: .25 },
  { method: "root_event_ensemble_v1", label: "Research pipeline", about: "Three AI estimates.", forecasts: 2, resolved: 2, right: 1, wrong: 1, toss_ups: 0, brier: .24, coin_flip_brier: .25 }];
const question = (id: string, title: string, extra: object) => ({ id, title, detail: `Exact rule for ${id}`, topic: "Jobs",
  resolves_on: "2026-10-02", status: "resolved", outcome: 1, actual: "4.2%", source_url: "https://fred.stlouisfed.org/series/UNRATE",
  report_url: null, forecasts: [], call: null, verdict: null, ...extra });

async function mockRecord(page: Page) {
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-10-07", forecasters,
    summary: { resolved: 2, right: 1, wrong: 1, toss_ups: 0, brier: .24, coin_flip_brier: .25, pending: 1, cancelled: 0, total: 3 },
    questions: [
      question("q1", "Will unemployment come in above 4.1%?", { forecasts: [{ method: "root_event_ensemble_v1", probability: .37 }],
        call: { method: "root_event_ensemble_v1", probability: .37 }, verdict: "wrong" }),
      question("q2", "Will the economy add more than 162,000 jobs?", { outcome: 0, actual: "+29,000 jobs",
        forecasts: [{ method: "root_event_ensemble_v1", probability: .33 }], call: { method: "root_event_ensemble_v1", probability: .33 }, verdict: "right" }),
      question("q3", "Will the 10-year yield on Oct 7 be above 5.28%?", { status: "pending", outcome: null, actual: null, resolves_on: "2026-10-08",
        topic: "Interest rates", call: { method: "combined_median_v1", probability: .48, members: 2 },
        forecasts: [{ method: "claude_code_forecaster_v1", probability: .56, rationale: "The yield sits just above the threshold.",
          sources: [{ url: "https://fred.stlouisfed.org/series/DGS10", published: "2026-10-06" }], made_at: "2026-10-06T11:00:00Z" },
        { method: "root_event_ensemble_v1", probability: .4 }] })] } }));
}

test("the home page leads with the score, the next result and clickable questions", async ({ page }) => {
  await mockRecord(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "1 of 2 calls right" })).toBeVisible();
  await expect(page.getByText("About the same as guessing so far")).toBeVisible();
  await expect(page.getByText("2 of 30 results")).toBeVisible();
  const next = page.getByRole("link", { name: /Next result · Tomorrow/ });
  await expect(next).toContainText("Our call: No, 52% sure");
  const wrong = page.getByRole("link", { name: /above 4\.1%/ });
  await expect(wrong).toContainText("✗ Wrong");
  await expect(wrong).toContainText("We said No, 63% sure · actual 4.2%");
  await expect(page.getByRole("link", { name: /162,000 jobs/ })).toContainText("✓ Right");
  await expect(page.getByRole("listitem").filter({ hasText: "Research pipeline" })).toContainText("1 of 2 right");
  await wrong.click();
  await expect(page).toHaveURL(/\/q\/q1$/);
  await expect(page.getByRole("heading", { name: "Will unemployment come in above 4.1%?" })).toBeVisible();
  await expect(page.getByText("✗ Wrong")).toBeVisible();
  await expect(page.getByText("Actual: 4.2%")).toBeVisible();
});

test("an open question explains our call with its sources", async ({ page }) => {
  await mockRecord(page);
  await page.goto("/q/q3");
  await expect(page.getByText("result due tomorrow")).toBeVisible();
  await expect(page.getByText("No, 52% sure").first()).toBeVisible();
  await expect(page.getByText("48.0% chance of yes · the middle (median) of 2 forecasters")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Why Claude said yes" })).toBeVisible();
  await expect(page.getByText("The yield sits just above the threshold.")).toBeVisible();
  await expect(page.getByRole("link", { name: "fred.stlouisfed.org, Oct 6, 2026" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Research pipeline" })).toContainText("No, 60% sure");
  await expect(page.getByRole("row").filter({ hasText: "Combined (our call)" })).toContainText("No, 52% sure");
});

test("asking a question queues it for Claude and can be withdrawn", async ({ page }) => {
  await page.goto("/ask");
  const ask = page.getByRole("button", { name: "Ask Claude" });
  await expect(ask).toBeDisabled();
  const text = `Will the browser test question ${Date.now()} resolve yes by 2030?`;
  await page.getByLabel("Your question").fill(text);
  await ask.click();
  await expect(page.getByRole("status")).toContainText("Got it. Claude will research it and answer by");
  const waiting = page.getByRole("listitem").filter({ hasText: text });
  await expect(waiting).toBeVisible();
  await waiting.getByRole("button", { name: "Withdraw" }).click();
  await expect(waiting).toHaveCount(0);
});

test("navigation keeps two main links and moves the rest under More", async ({ page }) => {
  await mockRecord(page);
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link")).toHaveCount(2);
  await nav.getByRole("button", { name: "More ▾" }).click();
  await page.getByRole("menuitem", { name: "Autopilot" }).click();
  await expect(page).toHaveURL(/\/autopilot$/);
});
