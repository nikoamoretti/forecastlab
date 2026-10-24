import { test, expect } from '@playwright/test';

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
  await expect(history).toContainText('Yes · 70% sure');
  await expect(page.locator('article header')).not.toContainText('70% sure');
});
