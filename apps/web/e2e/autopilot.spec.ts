import { test, expect } from '@playwright/test';

test('policy approval remains paused, shows budget and preserves its inbox', async ({ page }) => {
  await page.goto('/autopilot');
  await expect(page.getByRole('heading', { name: 'Autopilot', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enable Autopilot', exact: true })).toBeDisabled();
  const budget = await page.getByLabel('Weekly budget ($)').inputValue() === '25' ? '24' : '25';
  await page.getByLabel('Weekly budget ($)').fill(budget);
  await page.getByRole('button', { name: 'Approve policy', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Saved');
  await page.reload();
  await expect(page.getByText('Autopilot policy approved', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('region', { name: 'Weekly spending' })).toContainText(`$${budget}.00`);
  await expect(page.getByRole('button', { name: 'Enable Autopilot', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Mark read', exact: true }).first().click();
  await expect(page.getByRole('status')).toContainText('Saved');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('Weekly budget ($)')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: '/tmp/forecastlab-autopilot-mobile.png', fullPage: true });
});

test('outcomes enter scores only after an explicit confirmation', async ({ page, request }) => {
  const base = await (await request.get('/api/autopilot')).json();
  let confirmed = false;
  let confirmations = 0;
  await page.route('**/api/autopilot', route => route.fulfill({ json: {
    ...base, outcomes: [{ id: 'browser-outcome', question_id: 'browser-question', indicator: 'cpi', period: '2026-08',
      value: 3.2, units: 'percent_yoy', source_url: 'https://www.bls.gov/news.release/cpi.nr0.htm',
      quote: 'Official first release measurement retained for review.', outcome: 1,
      confirmed, adjudication_revision: confirmed ? 1 : null, confirmed_outcome: confirmed ? 1 : null }]
  } }));
  await page.route('**/api/autopilot/outcomes/browser-outcome/confirm', route => {
    confirmed = true; confirmations += 1;
    return route.fulfill({ json: { adjudication_id: 'confirmed', revision: 1, outcome: 1 } });
  });
  await page.goto('/autopilot');
  await expect(page.getByRole('button', { name: 'Confirm outcome', exact: true })).toBeVisible();
  expect(confirmations).toBe(0);
  await page.getByRole('button', { name: 'Confirm outcome', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Confirm outcome', exact: true })).toHaveCount(0);
  expect(confirmations).toBe(1);
  await page.getByText('Confirmed outcomes and corrections', { exact: true }).click();
  await expect(page.getByRole('button', { name: 'Record correction' })).toBeVisible();
});

test('authentication failure opens the owner login without starting OAuth automatically', async ({ page }) => {
  await page.route('**/api/autopilot', route => route.fulfill({ status: 401, json: { detail: 'Sign in' } }));
  await page.goto('/autopilot');
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole('button', { name: 'Continue with GitHub' })).toBeVisible();
});

test('a failed refresh remains visible in version history', async ({ page }) => {
  await page.route('**/api/questions/failed-refresh/report', route => route.fulfill({ json: {
    id: 'failed-refresh', original_text: 'Reviewed CPI event', outcome_status: 'execution_failed',
    latest_run: { id: 'failed', status: 'failed', mode: 'live', total_cost_usd: .2, profile_id: 'root_event_ensemble_v1' },
    personal_report: { probability: null, evidence_assessments: [] },
    runs: [{ id: 'failed', status: 'failed', created_at: '2026-09-04', profile_id: 'root_event_ensemble_v1' },
      { id: 'original', status: 'completed', created_at: '2026-09-03', profile_id: 'root_event_ensemble_v1' }],
    versions: [{ id: 'v1', run_id: 'original', ensemble_probability: .7, created_at: '2026-09-03' }]
  } }));
  await page.goto('/forecasts/failed-refresh');
  const history = page.getByRole('heading', { name: 'Version history' }).locator('..');
  await expect(history.getByRole('listitem')).toHaveCount(2);
  await expect(history).toContainText('Forecast failed');
  await expect(history).toContainText('70.0%');
  await expect(page.locator('article header')).not.toContainText('70.0%');
});
