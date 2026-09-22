import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium, expect } from '@playwright/test';

const root = path.resolve(process.argv[2] || 'runtime/frontend-preview/20260922');
const data = JSON.parse(readFileSync(path.join(root, 'dataset.json'), 'utf8'));
const evidence = path.join(root, 'evidence');
mkdirSync(evidence, { recursive: true });
const base = pathToFileURL(path.join(root, 'index.html')).href;
const normalize = value => value.normalize('NFKC').trim().toLocaleLowerCase();
const total = value => value.toLocaleString('zh-CN');
assert.equal(data.actors.length, data.counts.performers);
assert.equal(data.tasks.length, data.counts.tasks_total);

const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [], external = [], checks = [];
  await context.route(/^https?:/, route => { external.push('unexpected_external_request'); return route.abort(); });
  page.on('pageerror', error => errors.push(error.message));
  const open = async view => { await page.goto(`${base}#${view}`); await expect(page.locator('h1')).toBeVisible(); };
  const nav = async view => { await page.locator(`#nav [data-nav="${view}"]`).click(); };
  const shot = name => page.screenshot({ path: path.join(evidence, name), fullPage: false });

  await open('overview');
  await expect(page.locator('.stat-value')).toHaveText([
    total(data.counts.performers), total(data.counts.avatars), total(data.counts.tasks_pending), total(data.counts.tasks_blocked),
  ]);
  await shot('overview-desktop.png');
  await page.locator('.stat').nth(1).click();
  await expect(page.locator('#result-count')).toContainText(total(data.counts.avatars));
  await page.reload();
  await expect(page.locator('[data-filter="portrait"]')).toHaveAttribute('aria-pressed', 'true');
  checks.push('real_overview_counts_and_stat_drilldown');

  await nav('actors');
  const firstIds = await page.locator('.actor-open').evaluateAll(nodes => nodes.map(node => node.dataset.actor));
  assert.equal(firstIds.length, Math.min(48, data.counts.performers));
  await shot('actors-desktop.png');
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  const secondIds = await page.locator('.actor-open').evaluateAll(nodes => nodes.map(node => node.dataset.actor));
  assert.equal(secondIds.length, Math.min(48, data.counts.performers - 48));
  assert.equal(firstIds.filter(id => secondIds.includes(id)).length, 0);
  await page.locator('[data-filter="missing-avatar"]').click();
  await expect(page.locator('#result-count')).toContainText(total(data.counts.missing_avatars));
  await expect(page.locator('.actor-open img')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeDisabled();
  checks.push('pagination_disjoint_pages_and_filter_reset');

  await page.locator('[data-filter="all"]').click();
  const actor = data.actors.find(a => a.avatar && /^[a-z]+$/i.test(a.external_id));
  assert.ok(actor, 'The snapshot must contain an ASCII source ID for normalization coverage');
  const fullWidth = actor.external_id.toUpperCase().replace(/[!-~]/g, ch => String.fromCharCode(ch.charCodeAt(0) + 0xfee0));
  const search = page.getByRole('searchbox', { name: '搜索演员姓名、别名或来源 ID' });
  await search.focus();
  await search.dispatchEvent('compositionstart');
  await search.fill(fullWidth);
  await expect(page.locator('#result-count')).toContainText(total(data.counts.performers));
  await search.dispatchEvent('compositionend');
  const matches = data.actors.filter(a => normalize([a.name, a.external_id, ...a.aliases].join(' ')).includes(normalize(fullWidth)));
  await expect(page.locator('#result-count')).toHaveText(`找到 ${total(matches.length)} 位演员`);
  await expect(search).toBeFocused();
  const trigger = page.locator(`.actor-open[data-actor="${actor.id}"]`);
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: '演员资料', exact: true })).toBeVisible();
  await expect(page.locator('#detail-content')).toContainText(actor.name);
  await expect(page.locator('#detail-content')).toContainText('仍需补充');
  await shot('actor-detail-desktop.png');
  await page.keyboard.press('Escape');
  await expect(page.locator('#detail')).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await expect(search).toHaveValue(fullWidth);
  checks.push('nfkc_search_ime_dialog_keyboard_and_focus_restore');

  const downloadWait = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出筛选结果' }).click();
  const download = await downloadWait;
  const exportData = JSON.parse(readFileSync(await download.path(), 'utf8'));
  assert.equal(exportData.count, matches.length);
  assert.equal(exportData.records.length, matches.length);
  assert.equal(exportData.snapshot_at, data.snapshot_at);
  assert.ok(exportData.records.every(row => !Object.hasOwn(row, '_search')));
  checks.push('filtered_export_count_and_snapshot');

  await trigger.click();
  await page.getByRole('button', { name: '查看该演员的补全任务' }).click();
  const actorTasks = data.tasks.filter(t => t.kind === 'profile' && t.source_id === actor.source_id && new URL(t.url).pathname.split('/').filter(Boolean).at(-1) === actor.external_id);
  await expect(page.locator('tbody tr')).toHaveCount(actorTasks.length);
  await expect(page.locator('.active-scope')).toContainText(actor.external_id);
  await page.goBack();
  await expect(search).toHaveValue(fullWidth);
  await expect(page.locator('#result-count')).toHaveText(`找到 ${total(matches.length)} 位演员`);
  await search.fill('no-matching-actor-00000000');
  await expect(page.getByRole('heading', { name: '没有找到对应演员' })).toBeVisible();
  await page.getByRole('button', { name: '清除搜索和筛选' }).click();
  await expect(search).toBeFocused();
  await expect(page.locator('#result-count')).toContainText(total(data.counts.performers));
  checks.push('actor_to_exact_task_back_navigation_and_empty_recovery');

  await page.getByRole('button', { name: '下一页', exact: true }).click();
  const restoredIds = await page.locator('.actor-open').evaluateAll(nodes => nodes.map(node => node.dataset.actor));
  await page.evaluate(() => scrollTo(0, 380));
  await nav('gaps');
  await page.goBack();
  assert.deepEqual(await page.locator('.actor-open').evaluateAll(nodes => nodes.map(node => node.dataset.actor)), restoredIds);
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(380);
  checks.push('back_restores_page_and_scroll');

  await nav('gaps');
  await page.locator('[data-gap="biography"]').click();
  await expect(page.locator('#result-count')).toContainText(total(data.counts.missing_fields.biography));
  await expect(page.locator('.active-scope')).toContainText('缺少简介');
  await page.reload();
  await expect(page.locator('.active-scope')).toContainText('缺少简介');
  await nav('tasks');
  await page.locator('[data-task-status="blocked"]').click();
  await expect(page.locator('tbody tr')).toHaveCount(data.counts.tasks_blocked);
  await expect(page.locator('[data-task-status="blocked"]')).toBeFocused();
  await page.locator('#task-source').selectOption('javdb_reference');
  await expect(page.locator('tbody tr')).toHaveCount(data.tasks.filter(t => t.source_id === 'javdb_reference' && t.status === 'blocked').length);
  await nav('sources');
  await expect(page.locator('.source-reason').first()).toContainText('403');
  await expect(page.locator('.source-reason').nth(1)).toContainText('重置');
  checks.push('missing_field_route_and_real_task_source_filters');

  const responsive = [];
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width < 620 ? 844 : 1050 });
    for (const view of ['overview', 'actors', 'gaps', 'tasks', 'sources']) {
      await nav(view);
      const dimensions = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: innerWidth }));
      assert.ok(dimensions.document <= dimensions.viewport, `${view} overflows at ${width}px`);
      responsive.push({ width, view, overflow: false });
      if (width === 390 && view === 'actors') await shot('actors-mobile.png');
      if (width === 390 && view === 'overview') await shot('overview-mobile.png');
      if (width < 620 && view === 'actors') {
        const controls = await page.locator('.btn,.filter,.view-switch button,#actor-sort,#actor-search').evaluateAll(nodes => nodes.map(node => ({ height: node.getBoundingClientRect().height })));
        assert.ok(controls.every(control => control.height >= 44), `Mobile primary control is shorter than 44px at ${width}px`);
        await page.getByRole('button', { name: '列表视图' }).click();
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `List view overflows at ${width}px`);
        await page.getByRole('button', { name: '卡片视图' }).click();
      }
    }
  }
  checks.push('five_pages_four_viewports_and_mobile_controls');

  await nav('actors');
  const visibleDecodes = await page.locator('.actor-open img').evaluateAll(async images => {
    const visible = images.filter(img => img.getBoundingClientRect().top < innerHeight);
    return Promise.all(visible.map(async img => { await img.decode(); return img.naturalWidth > 0 && img.naturalHeight > 0; }));
  });
  assert.ok(visibleDecodes.length > 0 && visibleDecodes.every(Boolean));
  // This is a local synthetic event-to-paint sample, not field INP or a browser benchmark.
  const searchPaintMs = await page.evaluate(async () => {
    const input = document.getElementById('actor-search'), start = performance.now();
    input.value = 'aika'; input.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return Math.round((performance.now() - start) * 10) / 10;
  });
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  const report = {
    status: 'passed', checked_at: new Date().toISOString(), snapshot_at: data.snapshot_at,
    counts: data.counts, checks, responsive, page_size: 48,
    visible_avatar_decodes: visibleDecodes.length, local_search_event_to_paint_ms: searchPaintMs,
    external_requests: external.length, runtime_errors: errors.length,
  };
  writeFileSync(path.join(root, 'browser-report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
