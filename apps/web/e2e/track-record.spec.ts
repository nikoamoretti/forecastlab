import { test, expect, type Page } from "@playwright/test";

const forecasters = [
  { method: "claude_code_forecaster_v1", label: "Claude", about: "Researches on the web.", forecasts: 1, resolved: 0, right: 0, wrong: 0, toss_ups: 0, brier: null, coin_flip_brier: .25 },
  { method: "root_event_ensemble_v1", label: "Research pipeline", about: "Three AI estimates.", forecasts: 2, resolved: 2, right: 1, wrong: 1, toss_ups: 0, brier: .24, coin_flip_brier: .25 }];
const question = (id: string, title: string, extra: object) => ({ id, title, detail: `Exact rule for ${id}`, topic: "Jobs",
  resolves_on: "2026-10-02", status: "resolved", outcome: 1, actual: "4.2%", source_url: "https://fred.stlouisfed.org/series/UNRATE",
  report_url: null, forecasts: [], call: null, verdict: null, ...extra });

async function mockRecord(page: Page) {
  await page.clock.setFixedTime(new Date("2026-10-07T12:00:00Z"));
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-10-07", forecasters,
    summary: { resolved: 2, right: 1, wrong: 1, toss_ups: 0, brier: .24, coin_flip_brier: .25, pending: 1, cancelled: 0, total: 3,
      vs_market: { questions: 2, ours: .1683, market: .1518 } },
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
  await expect(page.getByRole("heading", { name: "Score 0.24 on all 2 decided questions" })).toBeVisible();
  await expect(page.getByText("About the same as guessing so far")).toBeVisible();
  await expect(page.getByText("lower is better; always saying 50% scores 0.25")).toBeVisible();
  await expect(page.getByText("On the 2 questions the market also priced: us 0.17, market 0.15.")).toBeVisible();
  await expect(page.getByText("1 of 2 calls right", { exact: true })).toBeVisible();
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

test("a question past its date waits for its result instead of showing as coming up", async ({ page }) => {
  await mockRecord(page);
  await page.clock.setFixedTime(new Date("2026-10-10T12:00:00Z"));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Waiting for results/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /10-year yield/ })).toContainText("Due Oct 8");
  await expect(page.getByRole("link", { name: /10-year yield/ })).toContainText("Waiting for the official figure");
  await expect(page.getByRole("link", { name: /Next result/ })).toHaveCount(0);
  await page.goto("/q/q3");
  await expect(page.getByText("waiting for the result")).toBeVisible();
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

test("a question page shows the research behind it, step by step", async ({ page }) => {
  await mockRecord(page);
  await page.route("**/api/research/**", route => route.fulfill({ json: {
    researched_by: "Cursor (Grok 4.7)", researched_at: "2026-10-06T09:30:00Z", quotes_checked_at: "2026-10-06T09:45:00Z",
    research_failed: null, base_rate: "The yield closed above the line on 6 of the last 10 days.", unknowns: ["Thursday's auction"],
    facts: [{ fact: "The 10-year closed at 5.31% on Oct 5.", source: 0 }],
    sources: [{ title: "Daily Treasury Par Yield Curve Rates", url: "https://home.treasury.gov/rates", published: "2026-10-05",
      verified: true, quotes: ["10 Yr 5.31"] }],
    brief: "The 10-year sits just above 5.28%.", brief_written_at: "2026-10-06T10:00:00Z",
    judges: [{ method: "root_event_ensemble_v1", model: "a", at: "2026-10-06T10:05:00Z", ok: true, cost_usd: 0.01 },
      { method: "judge_qwen38_27b_v1", model: "qwen/qwen3.8-27b", at: "2026-10-06T10:05:00Z", ok: false, error: "no JSON object in the reply", cost_usd: 0.002 }] } }));
  await page.goto("/q/q3");
  await expect(page.getByRole("heading", { name: "How this forecast was made" })).toBeVisible();
  await expect(page.getByText("Cursor (Grok 4.7) researched the question on the web: 1 dated fact from 1 source")).toBeVisible();
  await expect(page.getByText("confirmed on 1 of 1 source")).toBeVisible();
  await expect(page.getByText("1 of 2 judge models answered")).toBeVisible();
  await expect(page.getByText("no JSON object in the reply", { exact: false })).toBeVisible();
  await expect(page.getByText("The 10-year closed at 5.31% on Oct 5.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Daily Treasury Par Yield Curve Rates" })).toBeVisible();
  await expect(page.getByText("quotes checked on the page")).toBeVisible();
  await page.getByText("The brief the judges read").click();
  await expect(page.getByText("The 10-year sits just above 5.28%.")).toBeVisible();
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
  await expect(page.getByRole("menuitem", { name: "Autopilot" })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "All runs" }).click();
  await expect(page).toHaveURL(/\/runs$/);
});

test("the over-time section shows the share of calls right each week and the trend", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-20T12:00:00Z"));
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-10-20", forecasters,
    summary: { resolved: 3, right: 2, wrong: 1, toss_ups: 0, brier: .2, coin_flip_brier: .25, pending: 0, cancelled: 0, total: 3 },
    questions: [
      question("w1", "Week one, called right", { resolves_on: "2026-10-06", outcome: 1, call: { method: "combined_median_v1", probability: .8 }, verdict: "right" }),
      question("w2", "Week one, called wrong", { resolves_on: "2026-10-08", outcome: 0, call: { method: "combined_median_v1", probability: .6 }, verdict: "wrong" }),
      question("w3", "Week two, called right", { resolves_on: "2026-10-14", outcome: 0, call: { method: "combined_median_v1", probability: .1 }, verdict: "right" }),
      question("w4", "Not scored", { resolves_on: "2026-10-15", outcome: 1, scored: false, call: { method: "combined_median_v1", probability: .9 }, verdict: "right" })] } }));
  await page.goto("/");
  const section = page.getByRole("region", { name: "How we've done over time" });
  await expect(section).toContainText("Getting better: from about 50% to 100% of calls right.");
  await expect(section.getByRole("img")).toHaveAttribute("aria-label",
    "By week: Oct 5 50% (1 of 2 right), Oct 12 100% (1 of 1 right). Getting better: from about 50% to 100% of calls right.");
});

test("past 30 results the progress box moves on to the next milestone", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-27T12:00:00Z"));
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-10-27", forecasters,
    summary: { resolved: 38, right: 23, wrong: 15, toss_ups: 0, brier: .2, coin_flip_brier: .25, pending: 0, cancelled: 0, total: 38 },
    questions: [] } }));
  await page.goto("/");
  await expect(page.getByText("38 of 50 results")).toBeVisible();
  await expect(page.getByText(/Past 30, so the score now means something/)).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Decided questions" })).toHaveAttribute("aria-valuemax", "50");
});

test("a market whose rules allow a much later settlement says so instead of looking missed", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-27T12:00:00Z"));
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-10-27", forecasters,
    summary: { resolved: 0, right: 0, wrong: 0, toss_ups: 0, brier: null, coin_flip_brier: .25, pending: 2, cancelled: 0, total: 2 },
    questions: [
      question("pm", "Will Aleksandar Vučić be the next Prime Minister of Serbia?", { status: "pending", outcome: null, actual: null,
        resolves_on: "2026-10-25", source_url: "https://polymarket.com/event/next-pm", call: { method: "combined_median_v1", probability: .57 },
        detail: "Resolves to whoever takes office by June 30, 2028, 11:59 PM ET." }),
      question("fable", "Will a new Fable model ship before October 26?", { status: "pending", outcome: null, actual: null,
        resolves_on: "2026-10-25", source_url: "https://manifold.markets/x/fable", call: { method: "combined_median_v1", probability: .3 },
        detail: "Resolves YES if released before October 26, 2026." })] } }));
  await page.goto("/");
  await expect(page.getByRole("link", { name: /Prime Minister of Serbia/ })).toContainText("Waiting for Polymarket; its rules allow until Jun 30, 2028");
  await expect(page.getByRole("link", { name: /Fable model/ })).toContainText("Waiting for Manifold");
  await expect(page.getByRole("link", { name: /Fable model/ })).not.toContainText("rules allow");
  await page.goto("/q/pm");
  await expect(page.getByText("Polymarket listed Oct 25, 2026, but its rules let it settle as late as Jun 30, 2028")).toBeVisible();
  await page.goto("/q/fable");
  await expect(page.getByText("It was due Oct 25, 2026, but Manifold hasn’t settled it yet. We check every evening.")).toBeVisible();
});

test("the headline score covers every decided question, and the market comparison uses our call on the questions it priced", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-31T12:00:00Z"));
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-10-31", forecasters,
    summary: { resolved: 56, right: 35, wrong: 20, toss_ups: 1, brier: .2007, coin_flip_brier: .25, pending: 0, cancelled: 0, total: 56,
      vs_market: { questions: 37, ours: .1683, market: .1518 } },
    questions: [] } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Score 0.20 on all 56 decided questions" })).toBeVisible();
  await expect(page.getByText("Better than guessing so far")).toBeVisible();
  await expect(page.getByText("lower is better; always saying 50% scores 0.25")).toBeVisible();
  await expect(page.getByText("On the 37 questions the market also priced: us 0.17, market 0.15.")).toBeVisible();
  await expect(page.getByText("35 of 55 calls right · 1 toss-up isn’t counted as right or wrong")).toBeVisible();
  // Every forecaster row says which questions its score covers.
  await expect(page.getByRole("listitem").filter({ hasText: "Research pipeline" })).toContainText("score 0.24 on 2 decided questions");
});

test("a question page shows Claude's raw and confidence-corrected probability only when a frozen value exists", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-31T12:00:00Z"));
  const claude = { method: "claude_code_forecaster_v1", probability: .72, rationale: "Strong base rate.", made_at: "2026-10-31T09:00:00Z" };
  const open = { status: "pending", outcome: null, actual: null, resolves_on: "2026-11-04", topic: "Tech" };
  const corrected = { method: "claude_calibrated_v1", label: "Claude, confidence-corrected", about: "Shown for comparison.", forecasts: 1,
    resolved: 0, right: 0, wrong: 0, toss_ups: 0, brier: null, coin_flip_brier: .25 };
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-10-31", forecasters: [...forecasters, corrected],
    summary: { resolved: 0, right: 0, wrong: 0, toss_ups: 0, brier: null, coin_flip_brier: .25, pending: 2, cancelled: 0, total: 2 },
    questions: [
      question("fixed", "Will the corrected question resolve yes?", { ...open, call: { method: "claude_code_forecaster_v1", probability: .72 },
        forecasts: [claude, { method: "claude_calibrated_v1", probability: .78, raw: .72, slope: 1.17, intercept: .22, results: 52, as_of: "2026-10-31" }] }),
      question("older", "Will the older question resolve yes?", { ...open, call: { method: "claude_code_forecaster_v1", probability: .6 },
        forecasts: [{ ...claude, probability: .6 }] })] } }));
  await page.goto("/q/fixed");
  await expect(page.getByText("Claude said 72%. Corrected for its track record: 78%.")).toBeVisible();
  await expect(page.getByText(/how Claude’s 52 earlier decided calls turned out/)).toBeVisible();
  await expect(page.getByText(/not part of our call/)).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "Claude, confidence-corrected" })).toContainText("78.0% yes");
  await page.goto("/q/older");
  await expect(page.getByRole("heading", { name: "Will the older question resolve yes?" })).toBeVisible();
  await expect(page.getByText(/Corrected for its track record/)).toHaveCount(0);
});

test("the improving chart compares our score with the market by forecast week, or says it is too early", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-08T12:00:00Z"));
  const week = (start: string, questions: number, ours: number, market: number) => ({ start, questions, ours, market, right: questions, wrong: 0 });
  const split = (after: number) => ({ date: "2026-11-06", before: { questions: 168, ours: .22, market: .16 },
    after: { questions: after, ours: after ? .18 : null, market: after ? .16 : null } });
  const progress = (after: number) => ({ weeks: [week("2026-10-19", 41, .2, .15), week("2026-10-26", 92, .23, .17), week("2026-11-02", 2, .04, .05)],
    changes: [{ date: "2026-10-24", label: "Weighted by track record" }, { date: "2026-11-06", label: "Confidence correction" },
      { date: "2026-11-09", label: "Deeper research" }, { date: "2026-11-10", label: "Call anchored to market" }], since_last_change: split(after) });
  for (const [after, headline] of [[0, "Too early to tell: 0 questions decided since Nov 6, we need about 40."],
    [24, "Since the Nov 6 changes: our score 0.18 vs the market's 0.16 on 24 questions (before: 0.22 vs 0.16 on 168)."]] as const) {
    await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-11-08", forecasters,
      summary: { resolved: 56, right: 35, wrong: 20, toss_ups: 1, brier: .2007, coin_flip_brier: .25, pending: 0, cancelled: 0, total: 56,
        progress: progress(after) }, questions: [] } }));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Are we improving?" })).toBeVisible();
    await expect(page.getByText(headline)).toBeVisible();
    await expect(page.getByRole("img", { name: /Score by forecast week/ })).toBeVisible();
  }
});

test("a market-anchored call shows both our call and Claude alone, and the page says what the comparison measures", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-14T12:00:00Z"));
  const claude = { method: "claude_code_forecaster_v1", probability: .9, rationale: "Strong base rate.", made_at: "2026-11-10T09:00:00Z" };
  const market = { method: "market_price_v1", probability: .5 };
  const open = { status: "pending", outcome: null, actual: null, resolves_on: "2026-11-20", topic: "Tech" };
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-11-14", forecasters,
    summary: { resolved: 40, right: 25, wrong: 15, toss_ups: 0, brier: .19, coin_flip_brier: .25, pending: 2, cancelled: 0, total: 42,
      call_policy: { default_move: .1, promoted: [], bar: { questions: 40, dates: 10, interval: .9 } },
      vs_market: { questions: 30, ours: .17, market: .15, claude: .2, claude_questions: 30,
        since_anchored: { date: "2026-11-10", questions: 12, ours: .16, claude: .21, market: .15 } } },
    questions: [
      question("anchored", "Will the anchored question resolve yes?", { ...open, forecasts: [claude, market],
        call: { method: "market_anchored_call_v1", probability: .6, members: 1, claude: .9, market: .5, max_move: .1 } }),
      question("held", "Will the held question resolve yes?", { ...open, forecasts: [claude, market],
        call: { method: "market_anchored_call_v1", probability: .5, members: 1, claude: .9, market: .5, max_move: .1, held: "weak research" } })] } }));
  await page.goto("/");
  await expect(page.getByText("Since Nov 10, on 12 questions the market also priced: our call 0.16, Claude alone 0.21, market 0.15.")).toBeVisible();
  await expect(page.getByText("Claude alone on the same questions: 0.20.")).toBeVisible();
  await expect(page.getByText("cannot differ from it by more than 10 points")).toBeVisible();
  await page.goto("/q/anchored");
  await expect(page.getByText("Our call (starts from the market)").first()).toBeVisible();
  await expect(page.getByText("60.0% chance of yes · the market’s 50.0%, moved toward Claude’s forecast by at most 10 points")).toBeVisible();
  await expect(page.locator("p").filter({ hasText: "Claude alone:" })).toContainText("90.0% yes");
  await expect(page.getByRole("row").filter({ hasText: "Our call (starts from the market)" })).toContainText("60.0% yes");
  await page.goto("/q/held");
  await expect(page.getByText("held at the market’s 50.0% because the research behind Claude’s forecast was thin")).toBeVisible();
});

const block = (questions: number, dates: number, rule: number | null, market: number | null) => ({ questions, dates, rule, market,
  difference: rule == null || market == null ? null : rule - market, interval: null, meets_bar: false });
const edge = (id: string, label: string, status: string, counted: ReturnType<typeof block>) =>
  ({ id, label, status, registered: "2026-11-10", counted, before: block(29, 8, .119, .138) });

test("the page lists the edges under test with their progress, and says what it takes to join our call", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-20T12:00:00Z"));
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-11-20", forecasters,
    summary: { resolved: 40, right: 25, wrong: 15, toss_ups: 0, brier: .19, coin_flip_brier: .25, pending: 0, cancelled: 0, total: 40,
      call_policy: { default_move: .1, promoted: ["Every question"], bar: { questions: 40, dates: 10, interval: .9 } },
      edge_tests: [edge("tech_move_v1", "Tech questions", "testing", block(0, 0, null, null)),
        edge("every_question_move_v1", "Every question", "promoted", block(12, 4, .141, .157))] },
    questions: [] } }));
  await page.goto("/");
  const section = page.getByRole("region", { name: "Edges under test" });
  await expect(section.getByText("An idea joins our call only after it beats the market on at least 40 new results across 10 or more resolution days.")).toBeVisible();
  const tech = section.getByRole("listitem").filter({ hasText: "Tech questions" });
  await expect(tech).toContainText("0 of 40 results since Nov 10");
  await expect(tech).toContainText("Testing");
  await expect(tech).not.toContainText("vs market");
  const every = section.getByRole("listitem").filter({ hasText: "Every question" });
  await expect(every).toContainText("12 of 40 results since Nov 10");
  await expect(every).toContainText("Edge 0.14 vs market 0.16");
  await expect(every).toContainText("Proven: part of our call");
});

test("when the call is the market's price by default, the copy and the question page say so", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-20T12:00:00Z"));
  const claude = { method: "claude_code_forecaster_v1", probability: .9, rationale: "Strong base rate.", made_at: "2026-11-10T09:00:00Z" };
  const market = { method: "market_price_v1", probability: .5 };
  const open = { status: "pending", outcome: null, actual: null, resolves_on: "2026-11-25" };
  const anchored = { method: "market_anchored_call_v1", members: 1, claude: .9, market: .5 };
  await page.route("**/api/track-record", route => route.fulfill({ json: { generated_on: "2026-11-20", forecasters,
    summary: { resolved: 40, right: 25, wrong: 15, toss_ups: 0, brier: .19, coin_flip_brier: .25, pending: 2, cancelled: 0, total: 42,
      call_policy: { default_move: 0, promoted: ["Tech questions"], bar: { questions: 40, dates: 10, interval: .9 } },
      vs_market: { questions: 30, ours: .15, market: .15, claude: .2, claude_questions: 30,
        since_anchored: { date: "2026-11-10", questions: 12, ours: .15, claude: .21, market: .15 } },
      edge_tests: [edge("tech_move_v1", "Tech questions", "promoted", block(41, 11, .12, .15))] },
    questions: [
      question("plain", "Will the plain question resolve yes?", { ...open, topic: "Politics", forecasts: [claude, market],
        call: { ...anchored, probability: .5, max_move: 0 } }),
      question("edge", "Will the tech question resolve yes?", { ...open, topic: "Tech", forecasts: [claude, market],
        call: { ...anchored, probability: .6, max_move: .1, rule: "tech_move_v1" } })] } }));
  await page.goto("/");
  await expect(page.getByText("is its price unless a tested edge applies, so “ours vs market” mostly measures those edges.")).toBeVisible();
  await expect(page.getByText("cannot differ from it by more than")).toHaveCount(0);
  await page.goto("/q/plain");
  await expect(page.getByText("50.0% chance of yes · the market’s 50.0%; no tested edge applies to this question")).toBeVisible();
  await page.goto("/q/edge");
  await expect(page.getByText("60.0% chance of yes · the market’s 50.0%, moved toward Claude’s forecast under the tested edge “Tech questions”")).toBeVisible();
});
