import { defineConfig, devices } from "@playwright/test";

/**
 * FinAlly end-to-end configuration.
 *
 * The suite drives the *production container* — the same image `docker compose
 * up` serves — so it exercises the real static export against the real FastAPI
 * process. There is deliberately no `webServer` block: the app is started by
 * `docker-compose.test.yml`, which also pins `LLM_MOCK=true` and mounts a tmpfs
 * over `/app/db` so every run begins from a freshly seeded database.
 *
 * Three settings below are load-bearing rather than taste:
 *
 * - **`workers: 1` and `fullyParallel: false`.** The app is single-user by
 *   design: one cash balance, one watchlist, one position book. Two workers
 *   would trade against each other's balance.
 * - **`retries: 0`.** Specs mutate persistent state, so a retry re-runs a test
 *   whose first attempt already spent cash. Every spec here is written to be
 *   relational (see `fixtures/terminal.ts`), but a retry would still hide a
 *   genuine ordering bug behind a green second attempt. Failures should be read,
 *   not re-rolled.
 * - **The 1600x1000 viewport.** The console is desktop-first: the header's Cash,
 *   Unrealized and Positions readouts are `hidden md:flex`, and the three-column
 *   layout only assembles at `lg` (1024px). A narrower viewport does not fail the
 *   app, it fails the selectors.
 */
export default defineConfig({
  testDir: "./e2e",
  // Specs are numbered and run in filename order. 01 asserts the seeded $10,000,
  // which is only true before anything has traded.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,

  timeout: 60_000,
  expect: {
    // The simulator ticks at ~500ms and the equity curve samples once a second,
    // so several assertions here are genuinely waiting on wall-clock data rather
    // than on a render.
    timeout: 15_000,
  },

  // `list` for the compose log, `html` for the artefact left behind in
  // test/playwright-report/ — with traces, screenshots and video for anything
  // that failed. Never auto-opened: there is no browser in the container.
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],

  use: {
    // In the compose harness this is http://app.finally.test:8000 -- a dotted
    // alias, because Chromium upgrades plain HTTP to a *single-label* host like
    // `app` to HTTPS and fails the navigation (see docker-compose.test.yml). Set
    // it to http://localhost:8000 to drive a container you started yourself;
    // loopback is exempt from that upgrade.
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    viewport: { width: 1600, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
