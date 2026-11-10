import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

const receiptPath = process.env.AUTOPILOT_ACCEPTANCE_RECEIPT;
const actionsPath = process.env.AUTOPILOT_ACCEPTANCE_BROWSER_ACTIONS;
if (!receiptPath || !actionsPath) {
  throw new Error(
    "Isolated Autopilot outcome acceptance requires AUTOPILOT_ACCEPTANCE_RECEIPT and AUTOPILOT_ACCEPTANCE_BROWSER_ACTIONS. This spec is not part of the ordinary Playwright suite and cannot be skipped."
  );
}

const receipt = JSON.parse(readFileSync(receiptPath, "utf8")) as {
  question_id: string;
  proposal_id: string;
  probability: number;
  proposed_outcome: number;
  source_url: string;
  source_sha256: string;
};

const expectedInitialBrier = "0.1600";
const expectedInitialLogLoss = (-Math.log(0.6)).toFixed(4);
const expectedCorrectedBrier = "0.3600";
const expectedCorrectedLogLoss = (-Math.log(0.4)).toFixed(4);

test("browser confirms a real outcome through API and leaves append-only database history", async ({ page, request }) => {
  await page.goto("/autopilot");
  await expect(page.getByRole("heading", { name: "Autopilot", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm outcome", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Outcomes to confirm/ })).toContainText("1");

  const before = await (await request.get("/api/autopilot")).json();
  const metricsBefore = await (await request.get("/api/autopilot/metrics")).json();
  expect(before.outcomes[0].id).toBe(receipt.proposal_id);
  expect(before.outcomes[0].confirmed).toBeFalsy();
  expect(before.metrics.resolved_questions).toBe(0);
  expect(before.metrics.initial.brier_score).toBeNull();
  expect(before.metrics.initial.log_loss).toBeNull();
  expect(metricsBefore.resolved_questions).toBe(0);
  expect(metricsBefore.latest_prerelease.scored_questions).toBe(0);
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText("0 / 0");
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText("—");

  const official = page.getByRole("link", { name: "Official source", exact: true });
  const retained = page.getByRole("link", { name: "Retained release", exact: true });
  await expect(official).toHaveAttribute("href", receipt.source_url);
  await expect(retained).toHaveAttribute("href", `/api/autopilot/outcomes/${receipt.proposal_id}/source`);
  const source = await request.get(`/api/autopilot/outcomes/${receipt.proposal_id}/source`);
  expect(source.ok()).toBeTruthy();
  const sourceBytes = Buffer.from(await source.body());
  expect(createHash("sha256").update(sourceBytes).digest("hex")).toBe(receipt.source_sha256);
  expect(sourceBytes.subarray(0, 5).toString("ascii")).toBe("%PDF-");

  await page.getByRole("button", { name: "Confirm outcome", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Saved");
  await expect(page.getByRole("button", { name: "Confirm outcome", exact: true })).toHaveCount(0);
  await expect(page.getByText("Unconfirmed proposals never enter your scores")).toBeVisible();
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText("1 / 1");
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText(expectedInitialBrier);
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText(expectedInitialLogLoss);
  await expect(page.getByRole("row", { name: /Latest eligible prerelease version/ })).toContainText("0 / 1");
  await expect(page.getByRole("row", { name: /Latest eligible prerelease version/ })).toContainText("—");

  const afterConfirm = await (await request.get("/api/autopilot/metrics")).json();
  expect(afterConfirm.initial.scored_questions).toBe(1);
  expect(afterConfirm.initial.resolved_questions).toBe(1);
  expect(afterConfirm.initial.brier_score).toBeCloseTo(0.16, 10);
  expect(afterConfirm.initial.log_loss).toBeCloseTo(-Math.log(0.6), 10);
  expect(afterConfirm.latest_prerelease.scored_questions).toBe(0);

  const firstConfirm = await (await request.post(`/api/autopilot/outcomes/${receipt.proposal_id}/confirm`)).json();
  const repeatConfirm = await (await request.post(`/api/autopilot/outcomes/${receipt.proposal_id}/confirm`)).json();
  expect(firstConfirm.adjudication_id).toBe(repeatConfirm.adjudication_id);
  expect(firstConfirm.revision).toBe(1);
  expect(repeatConfirm.revision).toBe(1);
  expect(firstConfirm.outcome).toBe(1);

  await page.getByText("Confirmed outcomes and corrections", { exact: true }).click();
  await page.locator("select").selectOption("0");
  const evidence = page.getByLabel("Official evidence URL");
  await expect(evidence).toHaveValue(receipt.source_url);
  expect(receipt.source_url).toBe("https://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf");
  await page.getByLabel("Correction reason").fill("Reviewed official first-release rounding");
  await page.getByRole("button", { name: "Record correction" }).click();
  await expect(page.getByRole("status")).toContainText("Saved");
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText(expectedCorrectedBrier);
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText(expectedCorrectedLogLoss);

  const afterCorrection = await (await request.get("/api/autopilot/metrics")).json();
  expect(afterCorrection.initial.brier_score).toBeCloseTo(0.36, 10);
  expect(afterCorrection.initial.log_loss).toBeCloseTo(-Math.log(0.4), 10);
  expect(afterCorrection.latest_prerelease.scored_questions).toBe(0);
  const correction = await (await request.get(`/api/autopilot/questions/${receipt.question_id}/adjudications`)).json();
  expect(correction).toHaveLength(2);
  expect(correction[1].evidence.correction.evidence_url).toBe(receipt.source_url);

  await page.reload();
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText(expectedCorrectedBrier);
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText(expectedCorrectedLogLoss);
  await expect(page.getByText("Confirmed outcomes and corrections", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Official evidence URL")).toHaveValue(receipt.source_url);

  await page.goto(`/forecasts/${receipt.question_id}`);
  const history = page.getByRole("heading", { name: "Version history" }).locator("..");
  await expect(history.getByRole("listitem")).toHaveCount(2);
  await expect(history).toContainText("Forecast failed");
  await expect(history).toContainText("60.0%");
  await expect(page.locator("article header")).toContainText("Forecast failed");
  await expect(page.locator("article header")).not.toContainText("60.0%");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/autopilot");
  await expect(page.getByRole("heading", { name: "Autopilot", exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: /Initial forecast/ })).toContainText(expectedCorrectedBrier);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();

  writeFileSync(
    actionsPath,
    JSON.stringify(
      {
        confirm: firstConfirm,
        correction: {
          adjudication_id: correction[1].id,
          revision: correction[1].revision,
          outcome: correction[1].outcome,
          evidence_url: correction[1].evidence.correction.evidence_url
        }
      },
      null,
      2
    )
  );
});
