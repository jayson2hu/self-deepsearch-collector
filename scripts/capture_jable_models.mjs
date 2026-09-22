import { createHash } from 'node:crypto';
import { existsSync, lstatSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { spawnSync } from 'node:child_process';
import { chromium } from '@playwright/test';
import { resolvePython } from './run_python.mjs';

const SOURCE = 'https://jable.tv/models/';
const VERSION = 'jable-html@2026-09-19.1';
const digest = value => createHash('sha256').update(value).digest('hex');
const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

export function publicModelUrl(value) {
  try {
    const url = new URL(value, SOURCE);
    if (url.protocol !== 'https:' || !['jable.tv', 'www.jable.tv'].includes(url.hostname)
      || url.username || url.password || url.port || url.search || url.hash
      || !/^\/models\/[^/]+\/$/.test(url.pathname) || /^\/models\/\d+\/$/.test(url.pathname)) return null;
    return `https://jable.tv${url.pathname}`;
  } catch { return null; }
}

export function publicAvatarUrl(value) {
  try {
    const url = new URL(value, SOURCE);
    if (url.protocol !== 'https:' || url.hostname !== 'assets-cdn.jable.tv'
      || url.username || url.password || url.port || url.search || url.hash
      || !url.pathname.startsWith('/contents/models/')) return null;
    return url.href;
  } catch { return null; }
}

export function projectModels(rows) {
  const unique = new Map();
  const rejected = [];
  for (const row of rows) {
    const url = publicModelUrl(row.url);
    const name = String(row.name || '').replace(/\s+/g, ' ').trim();
    if (!url || !name || name.length > 200) { rejected.push({ reason: 'invalid_model_card' }); continue; }
    const avatar = row.image ? publicAvatarUrl(row.image) : null;
    if (row.image && !avatar) rejected.push({ profile_url: url, reason: 'unapproved_avatar_url' });
    const previous = unique.get(url);
    unique.set(url, { url, name, avatar: avatar || previous?.avatar || null,
      work_count: Number.isSafeInteger(row.work_count) && row.work_count >= 0 ? row.work_count : null });
  }
  const models = [...unique.values()];
  // This is a sanitized DOM projection, not the original site's HTML.
  const html = '<!doctype html><html lang="zh"><meta charset="utf-8"><body>\n'
    + models.map(model => `<a href="${escape(model.url)}"><h6 class="title">${escape(model.name)}</h6>${model.work_count !== null ? `<span>${model.work_count} 部影片</span>` : ''}${model.avatar ? `<img src="${escape(model.avatar)}" alt="${escape(model.name)}">` : ''}</a>`).join('\n')
    + '\n</body></html>\n';
  return { models, rejected, html };
}

export function listingPage(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.hostname !== 'jable.tv' || url.username || url.password || url.port || url.search || url.hash) return null;
    if (url.pathname === '/models/') return 1;
    const match = /^\/models\/([1-9]\d*)\/$/.exec(url.pathname);
    return match && Number(match[1]) <= 10000 ? Number(match[1]) : null;
  } catch { return null; }
}

// Decide completion only from retained capture evidence and the requested database.
// A captured page may need a local ingest even if an older report marked it visited.
export function resumePlan(previous, { database, databaseExists = true } = {}) {
  if (!previous || previous.source_id !== 'jable_reference' || previous.source_url !== SOURCE
    || !Array.isArray(previous.visited_pages) || !Array.isArray(previous.pages)
    || !Number.isInteger(previous.discovered_last_page) || previous.discovered_last_page < 1 || previous.discovered_last_page > 10000
    || previous.visited_pages.some(n => !Number.isInteger(n) || n < 1 || n > previous.discovered_last_page)) throw new Error('INVALID_RESUME_REPORT');
  const captured = new Map();
  for (const item of previous.pages) {
    if (!item || !Number.isInteger(item.page) || item.page < 1 || item.page > previous.discovered_last_page) throw new Error('INVALID_RESUME_REPORT');
    if (item.status !== 'captured') continue;
    if (listingPage(item.source_url) !== item.page || typeof item.directory !== 'string' || !item.directory) throw new Error('INVALID_RESUME_REPORT');
    captured.set(item.page, { ...item });
  }
  if (previous.visited_pages.some(number => !captured.has(number))) throw new Error('RESUME_EVIDENCE_MISSING');
  const visited = [];
  const recovery = [];
  for (const [number, item] of captured) {
    if (!database || (databaseExists && item.stored === true && item.stored_database === database)) visited.push(number);
    else recovery.push(item);
  }
  return { pages: previous.pages.map(item => ({ ...item })), captured_pages: [...captured.values()],
    visited_pages: visited.sort((a, b) => a - b), recovery_pages: recovery.sort((a, b) => a.page - b.page),
    discovered_last_page: previous.discovered_last_page };
}

function readEvidenceJson(filename) {
  try {
    const metadata = lstatSync(filename);
    if (!metadata.isFile() || metadata.size > 32 * 1024 * 1024) throw new Error('INVALID_RESUME_EVIDENCE');
    return JSON.parse(readFileSync(filename, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') throw new Error('RESUME_EVIDENCE_MISSING');
    throw new Error('INVALID_RESUME_EVIDENCE');
  }
}

function readResumeReport(filename, seen = new Set()) {
  const reportPath = path.resolve(filename);
  if (seen.has(reportPath) || seen.size >= 1000) throw new Error('INVALID_RESUME_CHAIN');
  seen.add(reportPath);
  const previous = readEvidenceJson(reportPath);
  if (previous.status === 'captured' && previous.source_url === SOURCE) {
    const navigation = [...(previous.discovered_pages || []), ...(previous.listing_navigation || []).map(link => listingPage(link.url)).filter(Boolean)];
    return { ...previous, visited_pages: [1], discovered_last_page: Math.max(1, ...navigation),
      pages: [{ ...previous, page: 1, directory: path.dirname(reportPath), evidence_report: reportPath }],
      history_reports: [reportPath] };
  }
  if (!Array.isArray(previous.pages) || !Array.isArray(previous.visited_pages)) throw new Error('INVALID_RESUME_REPORT');
  previous.pages = previous.pages.map(item => ({ ...item,
    directory: typeof item.directory === 'string' ? path.resolve(path.dirname(reportPath), item.directory) : item.directory,
    evidence_report: item.evidence_report || reportPath }));
  const retained = new Set(previous.pages.filter(item => item.status === 'captured').map(item => item.page));
  if (previous.visited_pages.some(number => !retained.has(number))) {
    if (typeof previous.resumed_from !== 'string') throw new Error('RESUME_EVIDENCE_MISSING');
    const ancestor = readResumeReport(path.resolve(path.dirname(reportPath), previous.resumed_from), seen);
    previous.pages = [...ancestor.pages, ...previous.pages];
    previous.history_reports = [...(ancestor.history_reports || []), ...(previous.history_reports || [])];
  }
  previous.history_reports = [...new Set([...(previous.history_reports || []), reportPath])];
  return previous;
}

export function verifyCapturedPage(item) {
  const manifestPath = path.join(item.directory, 'manifest.json');
  const manifest = readEvidenceJson(manifestPath);
  const capture = readEvidenceJson(path.join(item.directory, 'capture-report.json'));
  if (manifest.source_id !== 'jable_reference' || manifest.connector_version !== VERSION
    || manifest.sample_kind !== 'sanitized_browser_dom_projection' || manifest.capture_report !== 'capture-report.json'
    || manifest.checked_at !== item.checked_at || !Array.isArray(manifest.samples) || manifest.samples.length !== 1
    || manifest.samples[0].file !== 'models.html' || manifest.samples[0].url !== item.source_url
    || capture.status !== 'captured' || capture.source_id !== 'jable_reference' || capture.source_url !== item.source_url
    || capture.checked_at !== item.checked_at || capture.projection_sha256 !== item.projection_sha256
    || !/^[a-f0-9]{64}$/.test(item.projection_sha256 || '') || listingPage(item.source_url) !== item.page) throw new Error('INVALID_RESUME_EVIDENCE');
  let raw;
  try {
    const projectionPath = path.join(item.directory, 'models.html');
    const metadata = lstatSync(projectionPath);
    if (!metadata.isFile() || metadata.size > 2 * 1024 * 1024) throw new Error('INVALID_RESUME_EVIDENCE');
    raw = readFileSync(projectionPath);
  } catch (error) {
    if (error.code === 'ENOENT') throw new Error('RESUME_EVIDENCE_MISSING');
    throw new Error('INVALID_RESUME_EVIDENCE');
  }
  if (digest(raw) !== item.projection_sha256) throw new Error('RESUME_PROJECTION_HASH_MISMATCH');
  return manifestPath;
}

function ingestCapturedPage(item, database, outputDir) {
  const manifest = verifyCapturedPage(item);
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const ingest = spawnSync(resolvePython(), ['-m', 'collector.jable_assets', 'ingest-jable-samples',
    '--manifest', manifest, '--database', database, '--output-dir', outputDir],
  { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'workers', 'collector-python') },
    stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, encoding: 'utf8' });
  if (ingest.error || ingest.status !== 0) throw new Error('DATABASE_INGEST_FAILED');
  item.stored = true;
  item.stored_database = database;
  item.ingest_report = path.resolve(outputDir, 'ingest-report.json');
}

function robotsAllowed(body, url) {
  const result = spawnSync(resolvePython(), ['-c', 'import sys,urllib.robotparser; r=urllib.robotparser.RobotFileParser(); r.parse(sys.stdin.read().splitlines()); sys.exit(0 if r.can_fetch("self-deepsearch-collector", sys.argv[1]) else 2)', url], { input: body, windowsHide: true });
  return !result.error && result.status === 0;
}

async function capturePage(context, { outputDir, sourceUrl, waitSeconds, robotsBody }) {
  mkdirSync(outputDir);
  const report = { source_id: 'jable_reference', source_url: sourceUrl, checked_at: new Date().toISOString(), status: 'started', downloaded_images: 0, full_site_coverage: false };
  let page;
  try {
    if (!robotsAllowed(robotsBody, sourceUrl)) throw new Error('ROBOTS_DISALLOW');
    page = await context.newPage();
    const response = await page.goto(sourceUrl, { waitUntil: 'domcontentloaded', timeout: 25000 });
    report.initial_http_status = response?.status() ?? null;
    const raw = response ? await response.body() : Buffer.alloc(0);
    report.response_sha256 = digest(raw);
    report.response_bytes = raw.length;
    if (raw.length > 2 * 1024 * 1024) throw new Error('PAGE_TOO_LARGE');
    if (response?.status() === 429) throw new Error('RATE_LIMITED');
    if (response?.status() !== 200 && response?.status() !== 403) throw new Error('HTTP_ERROR');
    const deadline = Date.now() + waitSeconds * 1000;
    let rows = [];
    do {
      rows = await page.locator('a[href*="/models/"]').evaluateAll(links => links.map(a => {
        const img = a.querySelector('img');
        const count = a.querySelector('.detail span')?.textContent?.match(/([\d,]+)\s*部/);
        return {
          url: a.href,
          name: a.querySelector('.title,h6,h5,h4,h3')?.textContent || img?.alt || a.getAttribute('title') || '',
          image: img && (img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original') || img.getAttribute('src')),
          work_count: count ? Number(count[1].replaceAll(',', '')) : null,
        };
      }));
      rows = rows.filter(row => publicModelUrl(row.url));
      if (rows.some(row => row.name.trim()) || Date.now() >= deadline) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    } while (true);
    report.challenge_detected = /just a moment|cloudflare|驗證|验证/i.test(await page.title()) || (response?.status() === 403 && /cf-chl-|challenge-platform/.test(raw.toString('utf8')));
    if (response?.status() === 403) throw new Error(report.challenge_detected ? 'ACCESS_CHALLENGE' : 'ACCESS_DENIED');
    if (!rows.length || report.challenge_detected) throw new Error(report.challenge_detected ? 'ACCESS_CHALLENGE' : (response?.status() === 403 ? 'ACCESS_DENIED' : 'NO_MODEL_CARDS'));
    const projection = projectModels(rows);
    if (!projection.models.length) throw new Error('NO_VALID_MODEL_CARDS');
    report.discovered_pages = [...new Set((await page.locator('a.page-link[href]').evaluateAll(links => links.map(a => a.href))).map(listingPage).filter(Boolean))];
    report.status = 'captured';
    report.performers = projection.models.length;
    report.avatar_candidates = projection.models.filter(model => model.avatar).length;
    report.without_avatar = projection.models.filter(model => !model.avatar).map(model => ({ name: model.name, profile_url: model.url }));
    report.rejected = projection.rejected;
    report.projection_sha256 = digest(projection.html);
    report.sample_kind = 'sanitized_browser_dom_projection';
    writeFileSync(path.join(outputDir, 'models.html'), projection.html);
    writeFileSync(path.join(outputDir, 'manifest.json'), JSON.stringify({
      source_id: 'jable_reference', connector_version: VERSION, checked_at: report.checked_at,
      sample_kind: report.sample_kind, capture_report: 'capture-report.json',
      samples: [{ file: 'models.html', url: sourceUrl }],
    }, null, 2) + '\n');
  } catch (error) {
    report.status = 'failed';
    report.error_code = /^[A-Z_]+$/.test(error.message) ? error.message : error.name;
  } finally {
    await page?.close();
    writeFileSync(path.join(outputDir, 'capture-report.json'), JSON.stringify(report, null, 2) + '\n');
  }
  return report;
}

export async function captureModels({ outputDir, headless = false, waitSeconds = 30, maxPages = 5, intervalSeconds = 10, resumeReport, database, proxy = process.env.HTTPS_PROXY }) {
  if (!Number.isInteger(waitSeconds) || waitSeconds < 0 || waitSeconds > 60) throw new Error('wait-seconds must be 0..60');
  if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 200) throw new Error('max-pages must be 1..200');
  if (!Number.isInteger(intervalSeconds) || intervalSeconds < 10 || intervalSeconds > 120) throw new Error('interval-seconds must be 10..120');
  // A new directory prevents a failed run from leaving an old success manifest in use.
  mkdirSync(path.dirname(outputDir), { recursive: true });
  mkdirSync(outputDir);
  database = database ? path.resolve(database) : undefined;
  const report = {
    source_id: 'jable_reference', source_url: SOURCE, checked_at: new Date().toISOString(),
    browser: 'chrome', isolated_context: true, headless, proxy_used: Boolean(proxy),
    scope: 'public_models_pagination_portraits_only', status: 'started',
    downloaded_images: 0, full_site_coverage: false, cookies_persisted: false,
    max_pages: maxPages, interval_seconds: intervalSeconds, pages: [],
    visited_pages: [], pending_pages: [1], discovered_last_page: 1,
    database: database || null, captured_this_run: 0, recovered_this_run: 0,
  };
  const checkpoint = () => {
    report.pending_pages = Array.from({ length: report.discovered_last_page }, (_, i) => i + 1).filter(n => !report.visited_pages.includes(n));
    report.listing_pages_complete = report.pending_pages.length === 0;
    report.pending_ingest_pages = database ? [...new Set(report.pages.filter(item => item.status === 'captured'
      && !report.visited_pages.includes(item.page)).map(item => item.page))] : [];
    const filename = path.join(outputDir, 'crawl-report.json');
    writeFileSync(`${filename}.tmp`, JSON.stringify(report, null, 2) + '\n');
    renameSync(`${filename}.tmp`, filename);
  };
  checkpoint();
  let browser;
  try {
    if (resumeReport) {
      report.resumed_from = path.resolve(resumeReport);
      const previous = readResumeReport(resumeReport);
      const plan = resumePlan(previous, { database, databaseExists: database ? existsSync(database) : true });
      report.pages = plan.pages;
      report.history_reports = previous.history_reports;
      report.discovered_last_page = plan.discovered_last_page;
      // Verify all retained successes before skipping or importing any of them.
      for (const item of plan.captured_pages) verifyCapturedPage(item);
      report.visited_pages = plan.visited_pages;
      checkpoint();
      for (const item of plan.recovery_pages) {
        ingestCapturedPage(item, database, path.join(outputDir, 'recovered', `page-${String(item.page).padStart(4, '0')}`));
        // Retain the original capture directory and add this run's ingest receipt.
        const retained = report.pages.findLast(page => page.page === item.page && page.status === 'captured');
        Object.assign(retained, item);
        report.visited_pages.push(item.page);
        report.recovered_this_run++;
        checkpoint();
      }
      if (!report.pending_pages.length) {
        report.status = 'listing_complete';
        return report;
      }
    }
    browser = await chromium.launch({ channel: 'chrome', headless, ...(proxy ? { proxy: { server: proxy } } : {}) });
    const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: false });
    const robots = await context.request.get('https://jable.tv/robots.txt', { timeout: 20000, maxRedirects: 0 });
    const robotsBody = await robots.body();
    report.robots = { status: robots.status(), sha256: digest(robotsBody), bytes: robotsBody.length };
    if (robots.status() !== 200 || robotsBody.length > 65536) throw new Error('ROBOTS_UNAVAILABLE');
    if (!robotsAllowed(robotsBody, SOURCE)) throw new Error('ROBOTS_DISALLOW');
    writeFileSync(path.join(outputDir, 'robots.txt'), robotsBody);
    await context.route('**/*', route => {
      // Do not fetch videos, site image galleries, or pop-up documents.
      const request = route.request();
      if (['image', 'media'].includes(request.resourceType())) return route.abort();
      if (request.isNavigationRequest() && request.frame() === request.frame().page().mainFrame()) {
        if (!listingPage(request.url())) return route.abort();
      }
      return route.continue();
    });
    for (let index = 0; index < maxPages && report.pending_pages.length; index++) {
      if (index) await new Promise(resolve => setTimeout(resolve, intervalSeconds * 1000));
      const number = report.pending_pages[0];
      const sourceUrl = number === 1 ? SOURCE : `${SOURCE}${number}/`;
      const pageDir = path.join(outputDir, `page-${String(number).padStart(4, '0')}`);
      const pageReport = await capturePage(context, { outputDir: pageDir, sourceUrl, waitSeconds, robotsBody });
      report.pages.push({ page: number, directory: path.resolve(pageDir), ...pageReport });
      console.log(JSON.stringify({ page: number, status: pageReport.status, performers: pageReport.performers || 0, avatars: pageReport.avatar_candidates || 0 }));
      if (pageReport.status !== 'captured') { report.status = 'stopped_on_failure'; checkpoint(); break; }
      report.captured_this_run++;
      report.discovered_last_page = Math.max(report.discovered_last_page, ...pageReport.discovered_pages);
      // Persist capture evidence first. A crash or ingest failure leaves this page
      // available for local recovery, without falsely marking it complete.
      checkpoint();
      if (database) {
        ingestCapturedPage(report.pages.at(-1), database, path.join(pageDir, 'ingested'));
      }
      report.visited_pages.push(number);
      checkpoint();
    }
    if (report.status === 'started') report.status = report.pending_pages.length ? 'batch_complete' : 'listing_complete';
  } catch (error) {
    report.status = 'failed';
    // Avoid leaking signed URLs, local proxy credentials, or browser launch arguments.
    report.error_code = /^[A-Z_]+$/.test(error.message) ? error.message : error.name;
  } finally {
    try { await browser?.close(); } finally { checkpoint(); }
  }
  return report;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({ options: {
    'output-dir': { type: 'string' }, 'wait-seconds': { type: 'string', default: '30' }, headless: { type: 'boolean', default: false },
    'max-pages': { type: 'string', default: '5' }, 'interval-seconds': { type: 'string', default: '10' }, resume: { type: 'string' }, database: { type: 'string' },
  } });
  if (!values['output-dir']) throw new Error('--output-dir is required and must be a new directory');
  const result = await captureModels({ outputDir: path.resolve(values['output-dir']), waitSeconds: Number(values['wait-seconds']), headless: values.headless,
    maxPages: Number(values['max-pages']), intervalSeconds: Number(values['interval-seconds']), resumeReport: values.resume, database: values.database });
  console.log(JSON.stringify({ status: result.status, captured_this_run: result.captured_this_run, recovered_this_run: result.recovered_this_run,
    captured_pages_total: result.visited_pages.length, remaining_pages: result.pending_pages.length, report: path.join(values['output-dir'], 'crawl-report.json') }));
  process.exitCode = ['batch_complete', 'listing_complete'].includes(result.status) ? 0 : 2;
}
