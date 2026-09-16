import assert from 'node:assert/strict';
import test from 'node:test';
import { canRunFixture, createTaskPlan, createWebsite, normalizeWebsite, restoreConfig } from '../collection_demo_config.mjs';

const input = { name: 'Catalog', url: 'catalog.example.test', region: 'japan', role: '作品元数据', fields: ['canonical_code', 'title'] };
const meta = { id: 'PLAN-001', created_at: '2026-09-16T00:00:00Z' };
const source = () => createWebsite(input, []);
const planInput = () => ({ title: '首轮目录', source_id: source().id, job_type: 'work_incremental', region: 'japan', scope_path: '/works/', max_pages: 5, max_records: 30 });

test('canonical domains prevent duplicate website registration', () => {
  assert.equal(normalizeWebsite('HTTPS://CATALOG.EXAMPLE.TEST.:443/').domain, 'catalog.example.test');
  assert.throws(() => createWebsite({ ...input, url: 'https://CATALOG.example.test/' }, [source()]), /已登记/);
});

test('website entry rejects credentials, private hosts, protocols and page URLs', () => {
  for (const url of ['javascript:alert(1)', 'ftp://example.com', 'https://user:secret@example.com', '127.1', '0x7f000001', 'http://[::1]', 'http://localhost', 'https://catalog.local', 'https://example.com/path', 'https://example.com?token=secret', 'https://example.com:8080']) {
    assert.throws(() => normalizeWebsite(url), undefined, url);
  }
});

test('website registration cannot enable a connector or invent field verification', () => {
  const saved = createWebsite({ ...input, network_enabled: true, connector_version: 'fake', verified_fields: ['title'] }, []);
  assert.equal(saved.network_enabled, false);
  assert.equal(saved.connector_version, null);
  assert.deepEqual(saved.verified_fields, []);
  assert.throws(() => createWebsite({ ...input, fields: ['video_url'] }, []), /字段/);
});

test('task scope stays on the chosen website and remains a plan', () => {
  const task = createTaskPlan(planInput(), [source()], meta);
  assert.equal(task.scope.start_url, 'https://catalog.example.test/works/');
  assert.equal(task.source_id, source().id);
  assert.equal(task.network_access, false);
  assert.equal(task.execution_mode, 'plan');
  assert.equal(canRunFixture(task), false);
  assert.equal(task.limits.max_images, 0);
});

test('task limits and source ownership are validated before saving', () => {
  for (const max_pages of [0, 21, -1, 1.5, '', true]) assert.throws(() => createTaskPlan({ ...planInput(), max_pages }, [source()], meta));
  assert.throws(() => createTaskPlan({ ...planInput(), max_records: 101 }, [source()], meta));
  assert.throws(() => createTaskPlan({ ...planInput(), source_id: 'missing' }, [source()], meta), /登记/);
  assert.throws(() => createTaskPlan({ ...planInput(), job_type: 'arbitrary_code' }, [source()], meta), /类型/);
});

test('task paths reject remote, encoded, credential-bearing and traversal input', () => {
  for (const scope_path of ['//other.example/', 'https://other.example/', '/%2fother.example/', '/%5cother.example/', '/../secret', '/%2e%2e/secret', '/works?cookie=secret', '/works#part', '/%252fother.example/']) {
    assert.throws(() => createTaskPlan({ ...planInput(), scope_path }, [source()], meta), undefined, scope_path);
  }
});

test('restoring browser config preserves task cancellation and strips executable flags', () => {
  const website = source();
  const task = createTaskPlan(planInput(), [website], meta);
  task.status = 'cancelled';
  website.network_enabled = true;
  task.network_access = true;
  task.execution_mode = 'fixture';
  const restored = restoreConfig(JSON.stringify({ version: 1, sources: [website], tasks: [task] }), []);
  assert.equal(restored.sources[0].network_enabled, false);
  assert.equal(restored.tasks[0].status, 'cancelled');
  assert.equal(restored.tasks[0].network_access, false);
  assert.equal(restored.tasks[0].execution_mode, 'plan');
  assert.equal(canRunFixture(restored.tasks[0]), false);
});

test('malformed, dangling, duplicate and fabricated completed tasks fail restoration', () => {
  const website = source();
  const task = createTaskPlan(planInput(), [website], meta);
  assert.throws(() => restoreConfig('{broken', []));
  assert.throws(() => restoreConfig(JSON.stringify({ version: 9, sources: [], tasks: [] }), []));
  assert.throws(() => restoreConfig(JSON.stringify({ version: 1, sources: [], tasks: [task] }), []));
  assert.throws(() => restoreConfig(JSON.stringify({ version: 1, sources: [website], tasks: [task, task] }), []));
  assert.throws(() => restoreConfig(JSON.stringify({ version: 1, sources: [website], tasks: [{ ...task, status: 'completed' }] }), []));
});
