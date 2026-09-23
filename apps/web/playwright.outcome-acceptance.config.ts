import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL;
const receipt = process.env.AUTOPILOT_ACCEPTANCE_RECEIPT;
const actions = process.env.AUTOPILOT_ACCEPTANCE_BROWSER_ACTIONS;
if (!baseURL) {
  throw new Error("PLAYWRIGHT_BASE_URL must point at the isolated acceptance web server");
}
if (!receipt || !actions) {
  throw new Error(
    "Dedicated Autopilot outcome acceptance cannot start without AUTOPILOT_ACCEPTANCE_RECEIPT and AUTOPILOT_ACCEPTANCE_BROWSER_ACTIONS"
  );
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: "autopilot-outcome-acceptance.spec.ts",
  timeout: 180000,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  use: {
    baseURL,
    headless: true,
    trace: "retain-on-failure"
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: []
});
