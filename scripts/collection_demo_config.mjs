// Shared validation for browser-local website registration and task plans.
// This module never performs network requests or enables a connector.
export const STORAGE_KEY = 'mujian.collection-config.v1';
export const FIELDS = ['canonical_code', 'title', 'release_date', 'studio_name', 'performer_aliases'];
export const JOB_TYPES = ['source_probe', 'work_incremental', 'performer_incremental', 'work_detail', 'reconcile_source'];

function textField(value, label, limit, optional = false) {
  if (typeof value !== 'string') throw new Error(`${label}格式无效`);
  const result = value.trim();
  if ((!optional && !result) || result.length > limit || /[\u0000-\u001f\u007f]/u.test(result)) {
    throw new Error(`${label}${optional ? '最多' : '需填写 1–'}${limit} 个字符`);
  }
  return result;
}

function regionField(region) {
  if (!['japan', 'beijing'].includes(region)) throw new Error('请选择日本或北京执行区域');
  return region;
}

export function normalizeWebsite(raw) {
  const value = textField(raw, '网站地址', 500);
  let url;
  try { url = new URL(/^[a-z][a-z0-9+.-]*:/i.test(value) ? value : `https://${value}`); }
  catch { throw new Error('请输入有效的网站域名，例如 https://catalog.example.com'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('网站地址仅支持 HTTP/HTTPS，不能包含账号或密码');
  }
  const domain = url.hostname.toLowerCase().replace(/\.$/, '');
  const labels = domain.split('.');
  if (domain.length > 253 || labels.length < 2 || !/[a-z]/i.test(labels.at(-1)) ||
      labels.some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label)) ||
      /(?:^|\.)(?:localhost|local|internal|lan)$/.test(domain)) {
    throw new Error('请填写公开网站的域名，不能使用 IP 或本机地址');
  }
  if (url.port || url.pathname !== '/' || url.search || url.hash || /[\s\\]/u.test(value)) {
    throw new Error('网站地址只填写域名；目录路径在任务中设置，不填写端口、查询参数或片段');
  }
  return { domain, base_url: `${url.protocol}//${domain}/` };
}

export function createWebsite(input, existingSources) {
  const { domain, base_url } = normalizeWebsite(input.url);
  const name = textField(input.name, '网站名称', 60);
  if (existingSources.some(source => source.domain?.toLowerCase() === domain)) throw new Error('该域名已登记，请直接为它创建任务');
  if (existingSources.some(source => source.name.toLowerCase() === name.toLowerCase())) throw new Error('网站名称已存在，请使用可区分的名称');
  if (!Array.isArray(input.fields) || !input.fields.length || input.fields.some(field => !FIELDS.includes(field))) {
    throw new Error('请至少选择一个允许采集的资料字段');
  }
  return {
    id: `website:${domain}`, name, domain, base_url, source_type: 'public_web', status: 'unverified',
    custom: true, network_enabled: false, connector_version: null, robots_status: 'unverified',
    terms_status: 'unverified', rights_status: 'needs_review', probe_result: 'not_probed', verified_fields: [],
    proposed_fields: [...new Set(input.fields)], role: textField(input.role ?? '', '用途说明', 240, true),
    preferred_region: regionField(input.region),
  };
}

function pathField(value) {
  const raw = textField(value, '目录路径', 300);
  let decoded;
  try { decoded = decodeURIComponent(raw); } catch { throw new Error('目录路径包含无效编码'); }
  if (!raw.startsWith('/') || decoded.startsWith('//') || /[\\?#\s\u0000-\u001f\u007f]/u.test(decoded) ||
      decoded.split('/').some(segment => segment === '..' || segment === '.') || decoded.includes('%')) {
    throw new Error('目录路径需为本站路径，例如 /works/，不能填写完整 URL、查询参数或上级目录');
  }
  return raw;
}

function boundedInteger(value, label, maximum) {
  if (typeof value !== 'number' && typeof value !== 'string') throw new Error(`${label}需为整数`);
  if (typeof value === 'string' && !/^\d+$/.test(value)) throw new Error(`${label}需为整数`);
  const number = Number(value);
  if (!Number.isInteger(number) || number < 1 || number > maximum) throw new Error(`${label}需为 1–${maximum} 的整数`);
  return number;
}

export function createTaskPlan(input, sources, metadata) {
  const source = sources.find(item => item.id === input.source_id && item.id !== 'fixture');
  if (!source) throw new Error('请先登记并选择对应网站');
  if (!JOB_TYPES.includes(input.job_type)) throw new Error('请选择已支持的任务类型');
  if (!/^PLAN-[a-z0-9-]{1,60}$/i.test(metadata.id)) throw new Error('任务编号无效');
  if (typeof metadata.created_at !== 'string' || !Number.isFinite(Date.parse(metadata.created_at))) throw new Error('任务时间无效');
  const scopePath = pathField(input.scope_path);
  const website = normalizeWebsite(source.base_url || `https://${source.domain}/`);
  return {
    id: metadata.id, title: textField(input.title, '任务名称', 60), source_id: source.id, source_name: source.name,
    job_type: input.job_type, region: regionField(input.region), scope: { path: scopePath, start_url: new URL(scopePath, website.base_url).href },
    limits: { max_pages: boundedInteger(input.max_pages, '页数上限', 20), max_records: boundedInteger(input.max_records, '记录上限', 100), max_images: 0 },
    fields: [...source.proposed_fields], schedule: 'manual', execution_mode: 'plan', network_access: false,
    connector_version: null, blocked_reason: 'CONNECTOR_NOT_INSTALLED', status: 'pending', progress: 0,
    logs: [], created_at: metadata.created_at,
  };
}

export function restoreConfig(raw, builtins) {
  if (raw === null) return { version: 1, sources: [], tasks: [] };
  if (typeof raw !== 'string' || raw.length > 1000000) throw new Error('本机配置体积或格式无效');
  const parsed = JSON.parse(raw);
  if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.sources) || !Array.isArray(parsed.tasks) ||
      parsed.sources.length > 100 || parsed.tasks.length > 200) throw new Error('本机配置版本、数量或格式无效');
  const sources = [];
  for (const stored of parsed.sources) {
    const source = createWebsite({ name: stored.name, url: stored.base_url, role: stored.role, fields: stored.proposed_fields, region: stored.preferred_region }, [...builtins, ...sources]);
    if (source.id !== stored.id) throw new Error('本机网站编号与域名不一致');
    sources.push(source);
  }
  const tasks = [];
  for (const stored of parsed.tasks) {
    if (!['pending', 'cancelled'].includes(stored.status)) throw new Error('任务计划不应包含已执行状态');
    if (tasks.some(task => task.id === stored.id)) throw new Error('任务编号重复');
    const task = createTaskPlan({ title: stored.title, source_id: stored.source_id, job_type: stored.job_type, region: stored.region,
      scope_path: stored.scope?.path, max_pages: stored.limits?.max_pages, max_records: stored.limits?.max_records }, [...builtins, ...sources], stored);
    task.status = stored.status;
    tasks.push(task);
  }
  return { version: 1, sources, tasks };
}

export function canRunFixture(job) {
  return job?.source_id === 'fixture' && job.execution_mode === 'fixture' && ['pending', 'retry'].includes(job.status);
}
