import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium, expect } from '@playwright/test';

const root = path.resolve(process.argv[2] || 'runtime/real-showcase');
const expectedWorks = Number(process.argv[3] || 1);
const executable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || chromium.executablePath();
if (!existsSync(executable)) throw new Error('Set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to an installed Chromium browser');
const catalog = JSON.parse(readFileSync(path.join(root, 'catalog.json'), 'utf8'));
assert.equal(catalog.real_data, true);
assert.equal(catalog.works.length, expectedWorks);
assert.equal(catalog.counts.works, expectedWorks);
const browser = await chromium.launch({ executablePath: executable, headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const external = [];
  const errors = [];
  page.on('request', request => {
    if (!request.url().startsWith('file:')) external.push(request.url());
  });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(pathToFileURL(path.join(root, 'index.html')).href);
  await expect(page.getByRole('heading', { name: '真实采集数据预览' })).toBeVisible();
  await expect(page.locator('.card')).toHaveCount(expectedWorks);
  await expect(page.locator('.card img')).toHaveCount(catalog.counts.covers_downloaded);
  assert.equal(await page.locator('.card img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0)), true);
  const localMediaUrls = catalog.works.flatMap(work => [work.cover, ...work.gallery].filter(Boolean).map(item => item.url));
  assert.equal(localMediaUrls.length, catalog.counts.images_downloaded);
  const decodedMedia = await page.evaluate(async urls => Promise.all(urls.map(url => new Promise(resolve => {
    const image = new Image();
    image.onload = () => resolve(image.naturalWidth > 0 && image.naturalHeight > 0);
    image.onerror = () => resolve(false);
    image.src = new URL(url, location.href).href;
  }))), localMediaUrls);
  assert.equal(decodedMedia.every(Boolean), true);
  await expect(page.locator('#result-count')).toContainText(`显示 ${expectedWorks} / ${expectedWorks}`);
  await page.locator('#search').fill(catalog.works[0].canonical_code);
  await expect(page.locator('.card:visible')).toHaveCount(1);
  await page.locator('#search').fill('');
  await page.locator('.cover-button').first().click();
  await expect(page.locator('#drawer-backdrop')).toBeVisible();
  await expect(page.locator('#detail-title')).toHaveText(catalog.works[0].title);
  await expect(page.locator('#evidence')).toContainText('SHA-256');
  const expectedDetailImages = 1 + catalog.works[0].gallery.length;
  await expect(page.locator('.detail-thumb')).toHaveCount(expectedDetailImages);
  if (expectedDetailImages > 1) {
    await page.locator('.detail-thumb').last().click();
    await expect(page.locator('.detail-thumb').last()).toHaveClass(/active/);
    await expect(page.locator('#evidence')).toContainText('gallery');
  }
  await page.screenshot({ path: path.join(root, 'detail-acceptance.png'), fullPage: false });
  await page.locator('#close-drawer').click();
  await expect(page.locator('#drawer-backdrop')).toBeHidden();
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
  const screenshotPath = path.join(root, 'acceptance.png');
  try {
    await page.screenshot({ path: screenshotPath, fullPage: true });
  } catch {
    await page.waitForTimeout(500);
    await page.screenshot({ path: screenshotPath, fullPage: false });
  }
  const report = {
    status: 'passed',
    checked_at: new Date().toISOString(),
    works: expectedWorks,
    covers: catalog.counts.covers_downloaded,
    local_images: catalog.counts.images_downloaded,
    external_requests: external.length,
    runtime_errors: errors.length,
    screenshot: 'acceptance.png',
    detail_screenshot: 'detail-acceptance.png',
  };
  writeFileSync(path.join(root, 'browser-report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(`Real showcase passed: ${expectedWorks} works, ${report.covers} local covers, no external requests`);
  await context.close();
} finally {
  await browser.close();
}
