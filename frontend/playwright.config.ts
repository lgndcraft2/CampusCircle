import { defineConfig, devices } from '@playwright/test';
import { resolve } from 'node:path';

const python = `"${resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python')}"`;

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5183', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] }, testIgnore: /responsive\.spec\.ts/ },
    { name: 'mobile', use: { ...devices['Pixel 7'] }, testMatch: /responsive\.spec\.ts/ },
  ],
  webServer: [
    { command: `${python} scripts/e2e_server.py`, cwd: '../backend', url: 'http://127.0.0.1:8010/api/v1/health', reuseExistingServer: false, timeout: 120_000 },
    { command: 'npx vite --host 127.0.0.1 --port 5183 --strictPort', env: { API_PROXY_TARGET: 'http://127.0.0.1:8010' }, url: 'http://127.0.0.1:5183', reuseExistingServer: false, timeout: 120_000 },
  ],
});
