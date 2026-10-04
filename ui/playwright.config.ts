import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests', timeout: 30_000, fullyParallel: true, workers: 1, expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }], ['junit', { outputFile: 'test-results/ui-contract.xml' }]],
  use: { baseURL: process.env.ACTIONGATE_UI_BASE_URL ?? 'http://127.0.0.1:42817', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.ACTIONGATE_UI_BASE_URL ? undefined : { command: 'npm run preview -- --port 42817 --strictPort', url: 'http://127.0.0.1:42817', reuseExistingServer: false, timeout: 60_000 },
});
