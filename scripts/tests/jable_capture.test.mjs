import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { captureModels, publicModelUrl, publicAvatarUrl, projectModels, listingPage, resumePlan, verifyCapturedPage } from '../capture_jable_models.mjs';

test('model and pagination URLs are distinguished and restricted', () => {
  assert.equal(publicModelUrl('https://jable.tv/models/example/'), 'https://jable.tv/models/example/');
  for (const url of ['https://jable.tv/models/2/', 'https://evil.example/models/a/', 'https://jable.tv:8080/models/a/', 'https://jable.tv/models/a/?token=secret']) assert.equal(publicModelUrl(url), null);
  assert.equal(listingPage('https://jable.tv/models/214/'), 214);
  assert.equal(listingPage('https://jable.tv/models/'), 1);
  assert.equal(listingPage('https://jable.tv/models/example/'), null);
  assert.equal(listingPage('https://jable.tv/models/?mode=async'), null);
});

test('only observed portrait-path assets are allowed', () => {
  assert.equal(publicAvatarUrl('https://assets-cdn.jable.tv/contents/models/1/name.jpg'), 'https://assets-cdn.jable.tv/contents/models/1/name.jpg');
  for (const url of ['https://assets-cdn.jable.tv/contents/videos/1.jpg', 'https://evil.example/contents/models/1.jpg', 'https://assets-cdn.jable.tv/contents/models/1.jpg?secret=1']) assert.equal(publicAvatarUrl(url), null);
});

test('projection escapes text, deduplicates and records missing portraits', () => {
  const result = projectModels([
    {url:'https://jable.tv/models/a/',name:'A <script>',image:'https://assets-cdn.jable.tv/contents/models/1/a.jpg',work_count:3},
    {url:'https://jable.tv/models/a/',name:'A <script>',work_count:3},
    {url:'https://jable.tv/models/b/',name:'B',image:'https://evil.example/a.jpg'},
  ]);
  assert.equal(result.models.length, 2);
  assert.equal(result.models.filter(row=>row.avatar).length, 1);
  assert.equal(result.rejected.length, 1);
  assert.ok(result.html.includes('A &lt;script&gt;'));
  assert.ok(result.html.includes('3 部影片'));
  assert.ok(!result.html.includes('evil.example'));
});

function savedCapture(root, { page = 1, lastPage = 1, visited = [page], stored = false, database } = {}) {
  const directory = path.join(root, `page-${String(page).padStart(4, '0')}`);
  mkdirSync(directory, { recursive: true });
  const sourceUrl = page === 1 ? 'https://jable.tv/models/' : `https://jable.tv/models/${page}/`;
  const projection = projectModels([{ url: 'https://jable.tv/models/example-name/', name: 'Example Name', work_count: 7 }]);
  const checkedAt = '2026-09-19T00:00:00Z';
  const capture = { source_id: 'jable_reference', source_url: sourceUrl, status: 'captured', checked_at: checkedAt,
    projection_sha256: createHash('sha256').update(projection.html).digest('hex'), discovered_pages: [lastPage] };
  const manifest = { source_id: 'jable_reference', connector_version: 'jable-html@2026-09-19.1', checked_at: checkedAt,
    sample_kind: 'sanitized_browser_dom_projection', capture_report: 'capture-report.json', samples: [{ file: 'models.html', url: sourceUrl }] };
  writeFileSync(path.join(directory, 'models.html'), projection.html);
  writeFileSync(path.join(directory, 'manifest.json'), JSON.stringify(manifest));
  writeFileSync(path.join(directory, 'capture-report.json'), JSON.stringify(capture));
  const report = { source_id: 'jable_reference', source_url: 'https://jable.tv/models/', status: 'failed',
    visited_pages: visited, discovered_last_page: lastPage,
    pages: [{ ...capture, page, directory, stored, ...(database ? { stored_database: database } : {}) }] };
  writeFileSync(path.join(root, 'crawl-report.json'), JSON.stringify(report));
  return report;
}

test('resume planning recovers captured pages marked visited before failed ingest', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jable-resume-plan-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const previous = savedCapture(root, { page: 2, lastPage: 3 });
  const original = JSON.stringify(previous);
  const result = resumePlan(previous, { database: '/data/current.db' });
  assert.deepEqual(result.visited_pages, []);
  assert.deepEqual(result.recovery_pages.map(item => item.page), [2]);
  assert.equal(JSON.stringify(previous), original);
  assert.deepEqual(resumePlan(previous).visited_pages, [2]);
  assert.throws(() => resumePlan({ ...previous, visited_pages: [1, 2] }), /RESUME_EVIDENCE_MISSING/);
});

test('database completion is bound to the actual target and also recovers a crash before visited', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jable-resume-database-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const previous = savedCapture(root, { stored: true, database: '/data/original.db', visited: [] });
  assert.deepEqual(resumePlan(previous, { database: '/data/original.db' }).visited_pages, [1]);
  assert.equal(resumePlan(previous, { database: '/data/original.db', databaseExists: false }).recovery_pages.length, 1);
  assert.equal(resumePlan(previous, { database: '/data/new.db' }).recovery_pages.length, 1);
  delete previous.pages[0].stored_database;
  assert.equal(resumePlan(previous, { database: '/data/original.db' }).recovery_pages.length, 1);
});

test('capture evidence verifies both manifest binding and exact projection bytes', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jable-resume-evidence-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const item = savedCapture(root).pages[0];
  const manifestPath = path.join(item.directory, 'manifest.json');
  const originalManifest = readFileSync(manifestPath);
  assert.equal(verifyCapturedPage(item), manifestPath);
  const invalid = JSON.parse(originalManifest);
  invalid.samples[0].url = 'https://jable.tv/models/2/';
  writeFileSync(manifestPath, JSON.stringify(invalid));
  assert.throws(() => verifyCapturedPage(item), /INVALID_RESUME_EVIDENCE/);
  writeFileSync(manifestPath, originalManifest);
  writeFileSync(path.join(item.directory, 'models.html'), 'tampered');
  assert.throws(() => verifyCapturedPage(item), /RESUME_PROJECTION_HASH_MISMATCH/);
  rmSync(path.join(item.directory, 'models.html'));
  assert.throws(() => verifyCapturedPage(item), /RESUME_EVIDENCE_MISSING/);
});

test('a failed local ingest stays pending and resumes from retained evidence without launching a browser', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jable-resume-ingest-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const originalDir = path.join(root, 'original');
  const previous = savedCapture(originalDir);
  const badDatabase = path.join(root, 'not-a-directory');
  writeFileSync(badDatabase, 'regular file');
  const failedDir = path.join(root, 'failed');
  const failed = await captureModels({ outputDir: failedDir, resumeReport: path.join(originalDir, 'crawl-report.json'),
    database: path.join(badDatabase, 'db.sqlite'), proxy: undefined });
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error_code, 'DATABASE_INGEST_FAILED');
  assert.deepEqual(failed.visited_pages, []);
  assert.deepEqual(failed.pending_ingest_pages, [1]);
  assert.equal(failed.captured_this_run, 0);

  const database = path.join(root, 'recovered.db');
  const recoveredDir = path.join(root, 'recovered');
  const recovered = await captureModels({ outputDir: recoveredDir, resumeReport: path.join(failedDir, 'crawl-report.json'), database, proxy: undefined });
  assert.equal(recovered.status, 'listing_complete', recovered.error_code);
  assert.deepEqual(recovered.visited_pages, [1]);
  assert.deepEqual(recovered.pending_ingest_pages, []);
  assert.equal(recovered.recovered_this_run, 1);
  assert.equal(recovered.captured_this_run, 0);
  assert.equal(recovered.pages[0].directory, previous.pages[0].directory);
  assert.equal(recovered.pages[0].stored_database, database);
  assert.equal(recovered.history_reports.length, 2);
  const receipt = JSON.parse(readFileSync(recovered.pages[0].ingest_report));
  assert.equal(receipt.database_counts.performers, 1);

  const complete = await captureModels({ outputDir: path.join(root, 'complete'), resumeReport: path.join(recoveredDir, 'crawl-report.json'), database, proxy: undefined });
  assert.equal(complete.status, 'listing_complete');
  assert.equal(complete.recovered_this_run, 0);
  assert.equal(complete.history_reports.length, 3);
});

test('missing saved projection stops recovery explicitly without advancing or refetching', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jable-resume-missing-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const originalDir = path.join(root, 'original');
  const previous = savedCapture(originalDir);
  rmSync(path.join(previous.pages[0].directory, 'models.html'));
  const result = await captureModels({ outputDir: path.join(root, 'retry'), resumeReport: path.join(originalDir, 'crawl-report.json'), database: path.join(root, 'db.sqlite'), proxy: undefined });
  assert.equal(result.status, 'failed');
  assert.equal(result.error_code, 'RESUME_EVIDENCE_MISSING');
  assert.deepEqual(result.visited_pages, []);
  assert.equal(result.captured_this_run, 0);
});

test('legacy resume chains retain earlier page evidence and reject a missing ancestor', async t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jable-resume-chain-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const ancestorDir = path.join(root, 'ancestor');
  savedCapture(ancestorDir, { lastPage: 2 });
  const currentDir = path.join(root, 'current');
  const current = savedCapture(currentDir, { page: 2, lastPage: 2, visited: [1, 2] });
  current.resumed_from = path.join(ancestorDir, 'crawl-report.json');
  const currentReport = path.join(currentDir, 'crawl-report.json');
  writeFileSync(currentReport, JSON.stringify(current));
  const result = await captureModels({ outputDir: path.join(root, 'complete'), resumeReport: currentReport, proxy: undefined });
  assert.equal(result.status, 'listing_complete');
  assert.deepEqual(result.pages.map(item => item.page), [1, 2]);
  assert.equal(result.history_reports.length, 2);
  rmSync(current.resumed_from);
  const missing = await captureModels({ outputDir: path.join(root, 'missing'), resumeReport: currentReport, proxy: undefined });
  assert.equal(missing.status, 'failed');
  assert.equal(missing.error_code, 'RESUME_EVIDENCE_MISSING');
  assert.equal(missing.captured_this_run, 0);
});
