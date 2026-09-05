import type { FullConfig } from "@playwright/test";

export default async function setup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL;
  const response = await fetch(`${baseURL}/api/settings`);
  if (!response.ok) throw new Error(`Browser test API unavailable: HTTP ${response.status}`);
  const settings = await response.json();
  if (settings.model_provider !== "mock" || settings.search_provider !== "mock") {
    throw new Error("Browser tests require an isolated API with mock model and search providers. Check the web server's backend origin before running tests.");
  }
  const worker = await fetch(`${baseURL}/health/worker`);
  if (!worker.ok || !(await worker.json()).fresh) {
    throw new Error("Browser tests require the isolated background worker to be running before any jobs are created.");
  }
}
