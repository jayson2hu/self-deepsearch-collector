import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium, expect } from '@playwright/test';

const root = path.resolve(process.argv[2] || 'runtime/jable-preview');
const dataset = JSON.parse(readFileSync(path.join(root, 'performers.json'), 'utf8'));
assert.equal(dataset.source_id, 'jable_reference');
assert.equal(dataset.performers.length, dataset.counts.performers);
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const external = [], errors = [];
  await context.route(/^https?:/, route => { external.push('unexpected_external_request'); return route.abort(); });
  page.on('pageerror', error => errors.push(error.name));
  await page.goto(pathToFileURL(path.join(root, 'index.html')).href);
  await expect(page.locator('h1')).toHaveText('真实演员资料库');
  await expect(page.locator('.card')).toHaveCount(dataset.counts.performers);
  await expect(page.locator('.card img')).toHaveCount(dataset.counts.avatars);
  const decoded = await page.locator('.card img').evaluateAll(async images => Promise.all(images.map(async img => {
    try { await img.decode(); return { ok: img.naturalWidth > 0, width: img.naturalWidth, height: img.naturalHeight }; }
    catch { return { ok: false }; }
  })));
  assert.ok(decoded.every(item => item.ok));
  dataset.performers.filter(p => p.avatar).forEach((p, i) => assert.deepEqual([decoded[i].width, decoded[i].height], [p.avatar.width, p.avatar.height]));
  await page.locator('#only-avatars').check();
  await expect(page.locator('.card')).toHaveCount(dataset.counts.avatars);
  await page.screenshot({ path: path.join(root, 'acceptance.png'), fullPage: false });
  if (dataset.counts.avatars) {
    await page.locator('.details-button').first().click();
    await expect(page.locator('#detail')).toBeVisible();
    await expect(page.locator('#detail')).toContainText('SHA-256');
    await page.locator('#close-detail').click();
    const searched = dataset.performers.find(p => p.avatar);
    const query = searched.external_id.toLowerCase();
    await page.locator('#search').fill(query);
    const matches = dataset.performers.filter(p => p.avatar && `${p.name} ${p.external_id}`.toLowerCase().includes(query));
    await expect(page.locator('.card')).toHaveCount(matches.length);
    await expect(page.locator(`[data-id="${searched.performer_id}"]`)).toBeVisible();
  }
  await page.locator('#search').fill('definitely-no-matching-record-000');
  await expect(page.locator('#empty')).toBeVisible();
  await page.locator('#search').fill('');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(root, 'acceptance-mobile.png'), fullPage: false });
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
  const report = { status: 'passed', checked_at: new Date().toISOString(), performers: dataset.counts.performers,
    decoded_avatars: decoded.length, failed_decodes: decoded.filter(item => !item.ok).length,
    external_requests: external.length, runtime_errors: errors.length, search_filter_detail_mobile: 'passed' };
  writeFileSync(path.join(root, 'browser-report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally { await browser.close(); }
