import { expect, test } from '@playwright/test';
import { apiStudent } from './helpers';

const noHorizontalScroll = async (page: import('@playwright/test').Page) =>
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

test('public and signed-in pages fit a phone screen', async ({ page }) => {
  for (const path of ['/', '/login', '/register', '/terms']) {
    await page.goto(path);
    await noHorizontalScroll(page);
  }
  await apiStudent(page.context().request, 'Mobile Tester');
  await page.goto('/app');
  await expect(page.getByRole('navigation', { name: 'Mobile navigation' })).toBeVisible();
  await noHorizontalScroll(page);
  for (const label of ['Circles', 'Chat', 'Profile']) {
    await page.getByRole('navigation', { name: 'Mobile navigation' }).getByRole('link', { name: label }).click();
    await noHorizontalScroll(page);
  }
});
