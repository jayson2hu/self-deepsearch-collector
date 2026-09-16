import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { resolvePython } from './run_python.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.join(root, 'docs/evidence/collection-demo');
mkdirSync(evidence, { recursive: true });
const executable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || chromium.executablePath();
if (!existsSync(executable)) throw new Error('Set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to an installed Chromium browser');
const server = spawn(resolvePython(), ['scripts/serve_collection_demo.py', '--port', '0'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
const checks = [];
let browser;
try {
  const origin = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Preview startup timeout')), 10000);
    server.stdout.on('data', chunk => {
      output += chunk.toString();
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) { clearTimeout(timer); resolve(match[0]); }
    });
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.once('exit', code => { clearTimeout(timer); reject(new Error(`Preview exited: ${code}`)); });
  });
  for (const route of ['/dataset.json', '/candidates.jsonl', '/healthz', '/robots.txt']) {
    const response = await fetch(origin + route);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('x-robots-tag'), /noindex/);
  }
  for (const route of ['/.env', '/.git/config', '/README.md', '/%2e%2e/README.md']) {
    assert.equal((await fetch(origin + route)).status, 404);
  }
  checks.push('Preview serves only allowed artifacts; private repository paths return 404');
  browser = await chromium.launch({ executablePath: executable, headless: true });
  for (const [name, viewport] of [['desktop', { width: 1440, height: 1050 }], ['mobile', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport, acceptDownloads: true });
    const page = await context.newPage();
    const errors = [];
    const external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => { if (!req.url().startsWith(origin) && !req.url().startsWith('blob:')) external.push(req.url()); });
    await page.goto(origin);
    await expect(page.getByRole('heading', { name: '让每一条资料，有据可依。' })).toBeVisible();
    await page.screenshot({ path: path.join(evidence, `${name}-overview.png`), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('[data-action="run-sample"]').click();
    await expect(page.getByText('候选已接收', { exact: true })).toBeVisible();
    await page.locator('#nav [data-nav="review"]').click();
    await page.locator('[data-action="review-detail"][data-id="demo-work-025"]').click();
    await page.getByRole('button', { name: '确认并加入预览', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('请先确认');
    await page.locator('#review-confirm').check();
    await page.getByRole('button', { name: '确认并加入预览', exact: true }).click();
    await expect(page.locator('#dialog')).not.toBeVisible();
    checks.push(`${name}: sample runs; approval requires explicit confirmation`);

    await page.locator('[data-action="review-detail"][data-id="demo-work-033"]').click();
    await page.locator('#review-confirm').check();
    await page.getByRole('button', { name: '确认并加入预览', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('请选择');
    await page.locator('input[name="resolution-release_date"][value="1"]').check();
    await page.screenshot({ path: path.join(evidence, `${name}-review.png`), fullPage: true });
    await page.getByRole('button', { name: '确认并加入预览', exact: true }).click();
    await page.locator('[data-action="review-detail"][data-id="demo-work-035"]').click();
    await expect(page.getByRole('button', { name: '确认并加入预览', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: '退回候选', exact: true }).click();
    checks.push(`${name}: unresolved conflicts and missing title cannot be published`);

    await page.locator('#nav [data-nav="catalog"]').click();
    await expect(page.locator('.work-card')).toHaveCount(26);
    await page.locator('#catalog-search').fill('ｄｅｍｏ ０２５');
    await expect(page.locator('.work-card')).toHaveCount(1);
    await page.locator('.work-card').click();
    await expect(page.locator('#dialog')).toContainText('海风中的书页');
    await expect(page.locator('#dialog')).not.toContainText('fixture-editor');
    await page.getByRole('button', { name: '返回资料列表', exact: true }).click();
    await page.locator('#catalog-search').fill('DEMO-033');
    await page.locator('.work-card').click();
    await expect(page.locator('#dialog')).toContainText('2026-06-01');
    await page.getByRole('button', { name: '返回资料列表', exact: true }).click();
    await page.locator('#catalog-search').fill('<img src=x onerror="window.injected=1">');
    await expect(page.getByRole('heading', { name: '没有找到对应资料' })).toBeVisible();
    assert.equal(await page.evaluate(() => window.injected), undefined);
    await page.getByRole('button', { name: '清除查询', exact: true }).click();
    await page.locator('[data-entity="performers"]').click();
    await expect(page.locator('.entity-card')).toHaveCount(8);
    await page.locator('.entity-card').first().click();
    await expect(page.locator('#dialog [data-action="work-detail"]')).not.toHaveCount(0);
    await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    await page.locator('[data-entity="studios"]').click();
    await expect(page.locator('.entity-card')).toHaveCount(4);
    await page.locator('[data-entity="works"]').click();
    await page.screenshot({ path: path.join(evidence, `${name}-catalog.png`), fullPage: true });
    checks.push(`${name}: normalized search, conflict result, entity browsing and escaped untrusted query`);

    await page.locator('#nav [data-nav="tasks"]').click();
    await page.getByRole('button', { name: '新建采集任务', exact: true }).click();
    await page.locator('#job-scenario').selectOption('timeout');
    await page.getByRole('button', { name: '创建演示任务', exact: true }).click();
    await page.locator('[data-action="run-job"]').click();
    await expect(page.getByText('等待重试', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '重试样例', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('没有新增重复资料');
    await page.getByRole('button', { name: '新建采集任务', exact: true }).click();
    await page.locator('#job-scenario').selectOption('schema');
    await page.getByRole('button', { name: '创建演示任务', exact: true }).click();
    await page.locator('[data-action="run-job"]').click();
    await expect(page.getByText('结构变化 · 已停止', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '新建采集任务', exact: true }).click();
    await page.getByRole('button', { name: '创建演示任务', exact: true }).click();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await expect(page.getByText('已取消', { exact: true })).toBeVisible();
    checks.push(`${name}: timeout retry, repeated batch, schema stop and cancellation`);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    const data = JSON.parse(readFileSync(await download.path(), 'utf8'));
    assert.equal(data.synthetic, true);
    assert.equal(data.published.length, 26);
    assert.equal(data.decisions.length, 3);
    assert.equal(data.published.some(w => w.id === 'demo-work-035'), false);
    checks.push(`${name}: exported state preserves decisions and excludes rejected candidate`);
    for (const view of ['sources', 'blueprint', 'review', 'catalog', 'tasks']) {
      await page.locator(`#nav [data-nav="${view}"]`).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${view} overflow`);
    }
    if (name === 'mobile') {
      await page.setViewportSize({ width: 320, height: 740 });
      for (const view of ['dashboard', 'sources', 'blueprint', 'catalog', 'tasks']) {
        await page.locator(`#nav [data-nav="${view}"]`).click();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `320px ${view} overflow`);
      }
    }
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    checks.push(`${name}: no external requests, runtime errors or whole-page horizontal overflow`);
    await page.reload();
    await page.locator('#nav [data-nav="catalog"]').click();
    await expect(page.locator('.work-card')).toHaveCount(24);
    await context.close();
  }
  for (const [name, viewport] of [['desktop', { width: 1440, height: 1050 }], ['mobile', { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport, acceptDownloads: true });
    const page = await context.newPage();
    const remoteRequests = [];
    const errors = [];
    page.on('request', request => { if (!request.url().startsWith(origin) && !request.url().startsWith('blob:')) remoteRequests.push(request.url()); });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/#sources');
    await page.getByRole('button', { name: '新增网站', exact: true }).click();
    await page.locator('#website-name').fill('目录样例站');
    await page.locator('#website-url').fill('https://user:secret@catalog.example.test');
    await page.getByRole('button', { name: '保存网站', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('账号或密码');
    await expect(page.locator('#dialog')).toBeVisible();
    await page.locator('#website-url').fill('HTTPS://CATALOG.EXAMPLE.TEST/');
    await page.locator('#website-region').selectOption('beijing');
    await page.locator('[name="fields"][value="studio_name"]').check();
    await page.getByRole('button', { name: '保存并创建任务', exact: true }).click();
    await expect(page.locator('#job-source')).toHaveValue('website:catalog.example.test');
    await expect(page.locator('#job-region')).toHaveValue('beijing');
    await page.locator('#job-title').fill('目录首轮采集');
    await page.locator('#job-path').fill('//other.example.test/');
    await page.getByRole('button', { name: '保存采集任务', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('本站路径');
    await page.locator('#job-path').fill('/works/');
    await page.locator('#job-pages').fill('3');
    await page.locator('#job-records').fill('25');
    await page.getByRole('button', { name: '保存采集任务', exact: true }).click();
    const row = page.locator('tr').filter({ hasText: '目录首轮采集' });
    await expect(row).toContainText('目录样例站');
    await expect(row).toContainText('待接入采集器');
    await expect(row.locator('[data-action="run-job"]')).toHaveCount(0);
    await row.getByRole('button', { name: '查看配置', exact: true }).click();
    await expect(page.locator('#dialog')).toContainText('https://catalog.example.test/works/');
    await expect(page.locator('#dialog')).toContainText('3 页 / 25 条');
    await expect(page.locator('#dialog')).toContainText('北京');
    await page.screenshot({ path: path.join(evidence, `${name}-task-plan.png`), fullPage: true });
    await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    checks.push(`${name}: website registration validates URLs; corresponding scoped task is saved without an executable action`);

    // A source plan must never be picked by the fixture runner, even when it is the earliest pending job.
    await page.locator('[data-action="run-job"]').click();
    await expect(page.getByText('候选已接收', { exact: true })).toBeVisible();
    await page.locator('#nav [data-nav="dashboard"]').click();
    await page.locator('[data-action="run-sample"]').click();
    await expect(page.getByRole('status')).toContainText('没有新增重复资料');
    await page.locator('#nav [data-nav="tasks"]').click();
    await expect(row).toContainText('待接入采集器');
    await page.reload();
    await expect(row).toContainText('目录样例站');
    await row.getByRole('button', { name: '取消', exact: true }).click();
    await page.reload();
    await expect(row).toContainText('已取消');
    await page.locator('#nav [data-nav="sources"]').click();
    await expect(page.locator('[data-source-id="website:catalog.example.test"]')).toContainText('1 个');
    await page.getByRole('button', { name: '新增网站', exact: true }).click();
    await page.locator('#website-name').fill('重复目录站');
    await page.locator('#website-url').fill('catalog.example.test');
    await page.getByRole('button', { name: '保存网站', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('已登记');
    await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    checks.push(`${name}: website/task survive reload, cancellation persists, duplicate domain is rejected and fixture runner ignores site plans`);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export').click();
    const download = await downloadPromise;
    const exported = JSON.parse(readFileSync(await download.path(), 'utf8'));
    assert.equal(exported.configuration.sources[0].domain, 'catalog.example.test');
    assert.equal(exported.configuration.tasks[0].scope.start_url, 'https://catalog.example.test/works/');
    assert.equal(exported.configuration.tasks[0].status, 'cancelled');
    assert.equal(exported.configuration.tasks[0].network_access, false);
    await expect(page.getByRole('status')).toBeHidden({ timeout: 6000 });
    await page.screenshot({ path: path.join(evidence, `${name}-sources.png`), fullPage: true });
    if (name === 'mobile') {
      await page.setViewportSize({ width: 320, height: 740 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.getByRole('button', { name: '新增网站', exact: true }).click();
      assert.equal(await page.locator('#dialog').evaluate(dialog => dialog.scrollWidth <= dialog.clientWidth), true);
      await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
    }
    assert.deepEqual(remoteRequests, []);
    assert.deepEqual(errors, []);
    checks.push(`${name}: configuration export, mobile source form and zero source-network requests verified`);
    await context.close();
  }
  const unavailable = await browser.newPage();
  await unavailable.goto(origin + '/#sources');
  await unavailable.getByRole('button', { name: '新增网站', exact: true }).click();
  await unavailable.locator('#website-name').fill('<img src=x onerror="window.siteInjected=1">');
  await unavailable.locator('#website-url').fill('escaped.example.test');
  await unavailable.getByRole('button', { name: '保存网站', exact: true }).click();
  await expect(unavailable.locator('[data-source-id="website:escaped.example.test"]')).toContainText('<img src=x');
  assert.equal(await unavailable.locator('img').count(), 0);
  await unavailable.locator('#nav [data-nav="dashboard"]').click();
  assert.equal(await unavailable.locator('img').count(), 0);
  assert.equal(await unavailable.evaluate(() => window.siteInjected), undefined);
  await unavailable.locator('#nav [data-nav="sources"]').click();
  await unavailable.evaluate(() => { Storage.prototype.setItem = () => { throw new Error('Storage unavailable'); }; });
  await unavailable.getByRole('button', { name: '新增网站', exact: true }).click();
  await unavailable.locator('#website-name').fill('无法保存的网站');
  await unavailable.locator('#website-url').fill('unavailable.example.test');
  await unavailable.getByRole('button', { name: '保存网站', exact: true }).click();
  await expect(unavailable.getByRole('status')).toContainText('配置未保存');
  await expect(unavailable.locator('#dialog')).toBeVisible();
  await expect(unavailable.locator('[data-source-id="website:unavailable.example.test"]')).toHaveCount(0);
  await unavailable.close();
  checks.push('Website names render as text; storage failure preserves the form and does not claim creation succeeded');
  const offline = await browser.newPage();
  await offline.goto(new URL('../docs/collection-demo/index.html', import.meta.url).href);
  await expect(offline.getByRole('heading', { name: '让每一条资料，有据可依。' })).toBeVisible();
  await offline.locator('#nav [data-nav="catalog"]').click();
  await expect(offline.locator('.work-card')).toHaveCount(24);
  await offline.close();
  checks.push('Standalone file:// page opens and renders catalog without a server');
  writeFileSync(path.join(evidence, 'report.json'), JSON.stringify({ status: 'passed', checked_at: new Date().toISOString(), scope: 'synthetic local preview and browser-local website/task plans; no live collection or database', checks, screenshots: ['desktop-overview.png', 'mobile-overview.png', 'desktop-review.png', 'mobile-review.png', 'desktop-catalog.png', 'mobile-catalog.png', 'desktop-task-plan.png', 'mobile-task-plan.png', 'desktop-sources.png', 'mobile-sources.png'] }, null, 2) + '\n');
  console.log(`Collection demo passed: ${checks.length} check groups, 10 screenshots`);
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
}
