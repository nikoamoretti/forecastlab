import { expect, test } from "@playwright/test";

test("manual evidence URL intake is explicit and audit-visible", async ({ page }) => {
  const attachment = {
    id: "manual-evidence-1",
    question_id: "manual-evidence-fixture",
    forecast_node_id: "node-driver",
    intended_use: "forecast_node",
    note: "Official source",
    mode: "live",
    as_of: null,
    submitted_url: "https://www.bls.gov/news.release/empsit.toc.htm",
    canonical_url: "https://www.bls.gov/news.release/empsit.toc.htm",
    final_url: "https://www.bls.gov/news.release/empsit.toc.htm",
    status: "accepted",
    accepted: true,
    rejection_reason: null,
    title: "Employment Situation",
    publisher: "U.S. Bureau of Labor Statistics",
    publication_date: null,
    publication_date_verified: false,
    retrieval_date: "2026-08-29T12:00:00Z",
    source_available_at: "2026-08-29T12:00:00Z",
    temporal_basis: "retrieval_date",
    source_class: "primary",
    content_hash: "a".repeat(64),
    extracted_text_hash: "b".repeat(64),
    content_type: "text/html",
    byte_length: 4096,
    as_of_eligible: true,
    attached_run_ids: [],
    fresh_explicit_rerun_required: true,
    claim_created_at_intake: false,
    created: true
  };
  let manualEvidence: typeof attachment[] = [];
  let submittedBody: Record<string, unknown> | null = null;

  await page.route("**/api/questions/manual-evidence-fixture/report", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        original_text: "Will the official threshold be reached?",
        status: manualEvidence.length ? "stale" : "completed",
        stale: manualEvidence.length > 0,
        version_count: 1,
        latest_probability: 0.5,
        requested_mode: "live",
        versions: [],
        watches: [],
        contract: { resolution_deadline: "2027-01-01T00:00:00Z" },
        manual_evidence_urls: manualEvidence,
        v1_report: {
          execution_status: "completed",
          final_probability: 0.5,
          nodes: [{ id: "node-driver", question: "What does the official series show?" }],
          calculation: { method: "direct_model_probability_v1", trace: [] }
        },
        latest_run: {
          status: "completed",
          mode: "live",
          cost_usd: 0,
          latency_ms: 5,
          progress_stage: "report",
          execution_context: { effective_mode: "live" },
          budget: { cost_is_estimated: true },
          aggregation: { method: "direct_model_probability_v1" },
          tracks: [],
          evidence: []
        }
      })
    });
  });
  await page.route("**/api/questions/manual-evidence-fixture/evidence-urls", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fulfill({ json: { attachments: manualEvidence } });
      return;
    }
    submittedBody = route.request().postDataJSON() as Record<string, unknown>;
    manualEvidence = [attachment];
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(attachment) });
  });

  await page.goto("/forecasts/manual-evidence-fixture");
  await expect(page.getByRole("heading", { name: "Add evidence URL" })).toBeVisible();
  await page.getByRole("textbox", { name: "Evidence URL" }).fill(attachment.submitted_url);
  await page.getByLabel("Intended use").selectOption("node-driver");
  await page.getByLabel("Optional note").fill("Official source");
  await page.getByRole("button", { name: "Add evidence URL" }).click();

  await expect(page.getByRole("status")).toContainText("fresh explicit rerun is required");
  await expect(page.getByText("Employment Situation", { exact: true })).toBeVisible();
  await expect(page.getByText("accepted", { exact: true })).toBeVisible();
  await expect(page.getByText(/basis retrieval date/i)).toBeVisible();
  await expect(page.getByText(/Target node-driver/i)).toContainText("fresh explicit rerun required");
  await expect(page.getByText(new RegExp(`content ${"a".repeat(64)}`))).toBeVisible();

  expect(submittedBody).toEqual({
    url: attachment.submitted_url,
    note: "Official source",
    intended_use: "forecast_node",
    forecast_node_id: "node-driver",
    mode: "live"
  });
});
