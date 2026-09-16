// The generated page embeds this file; website/task configuration stays in the browser.
let configStorageError = '';
let persistedConfig = { version: 1, sources: [], tasks: [] };
try {
  persistedConfig = CollectionConfig.restoreConfig(localStorage.getItem(CollectionConfig.STORAGE_KEY), DATA.registry.sources);
} catch {
  configStorageError = '本机配置无法读取，原有内容未覆盖。恢复浏览器存储后再保存新配置。';
}
const fieldLabels = { canonical_code: '番号', title: '标题', release_date: '发行日期', studio_name: '厂牌', performer_aliases: '人物别名' };
function allSources() { return [...DATA.registry.sources, ...state.customSources]; }
function localConfig(sources = state.customSources, jobs = state.jobs) {
  return { version: 1, sources, tasks: jobs.filter(job => job.execution_mode === 'plan') };
}
function saveLocalConfig(sources, jobs) {
  if (configStorageError) { toast(configStorageError); return false; }
  try {
    const value = JSON.stringify(localConfig(sources, jobs));
    CollectionConfig.restoreConfig(value, DATA.registry.sources);
    localStorage.setItem(CollectionConfig.STORAGE_KEY, value);
    return true;
  } catch (error) {
    toast(`配置未保存：${error instanceof Error ? error.message : '浏览器存储不可用'}。请检查后重试。`);
    return false;
  }
}
function configNotice() {
  return `<div class="note ${configStorageError ? 'orange' : ''}" style="margin-bottom:20px" ${configStorageError ? 'role="alert"' : ''}>${icon('info')}<span>${esc(configStorageError || '网站与采集计划保存在当前浏览器，刷新后保留。换电脑、浏览器或访问端口不会自动同步；可通过右上角导出备份。')}</span></div>`;
}
function jobBadge(job) {
  return job.execution_mode === 'plan' && job.status === 'pending' ? badge('待接入采集器', 'orange') : statusBadge(job.status);
}
function taskName(type) { return DATA.task_templates.tasks.find(task => task.type === type)?.name || '作品增量'; }
function jobTable(jobs) {
  return `<div class="table-wrap"><table><thead><tr><th>任务 / 批次</th><th>来源</th><th>状态</th><th>操作</th></tr></thead><tbody>${jobs.map(job => {
    const plan = job.execution_mode === 'plan';
    const source = allSources().find(source => source.id === job.source_id);
    return `<tr data-job-id="${esc(job.id)}"><td><strong>${esc(job.title)}</strong><span class="mono muted" title="${esc(job.id)}">${esc(job.id.slice(0,18))}</span></td><td><strong>${esc(source?.name || job.source_name || '未知来源')}</strong><span class="muted">${plan ? `${esc(taskName(job.job_type))} · 仅计划` : '离线样例'}</span></td><td>${jobBadge(job)}${job.status === 'running' ? `<div class="progress"><i style="width:${job.progress}%"></i></div>` : ''}</td><td>${plan ? button('查看配置', 'plan-detail', false, `data-id="${esc(job.id)}"`) : CollectionConfig.canRunFixture(job) ? button(job.status === 'retry' ? '重试样例' : '运行', 'run-job', false, `data-id="${esc(job.id)}" ${state.running ? 'disabled' : ''}`) : button('查看日志', 'job-detail', false, `data-id="${esc(job.id)}"`)}${job.status === 'pending' ? ` <button class="btn quiet" data-action="cancel-job" data-id="${esc(job.id)}">取消</button>` : ''}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function sources() {
  return intro('SOURCES / REGISTRY', '来源管理', '登记网站、定义资料字段，再为对应来源创建有界采集计划。', button(icon('plus') + '新增网站', 'add-website', true)) + configNotice() +
    `<div class="grid-3">${allSources().map(source => {
      const fixture = source.id === 'fixture';
      const probe = fixture ? '43 条样本可重放' : source.probe_result === 'proxy_connect_200_tls_reset' ? '代理隧道成功，TLS 重置' : source.probe_result === 'connection_timeout' ? '直连超时' : '尚未探测';
      const count = state.jobs.filter(job => job.source_id === source.id && job.execution_mode === 'plan').length;
      return `<article class="panel source-card" data-source-id="${esc(source.id)}"><div class="actions" style="justify-content:space-between"><div class="source-logo">${esc(fixture ? 'F' : source.name[0])}</div>${badge(fixture ? '仅本地样本' : '接入待核验', fixture ? 'green' : 'orange')}</div><h2>${esc(source.name)}</h2><small class="muted mono">${esc(source.domain || 'collection-demo@v1')}</small><p>${esc(source.role || '尚未填写用途说明')}</p><div class="kv"><span>网络采集</span><b>关闭</b></div><div class="kv"><span>robots / 条款</span><b>${fixture ? '不适用' : '尚未核验'}</b></div><div class="kv"><span>字段选择器</span><b>${fixture ? '确定性样本生成' : '未建立'}</b></div><div class="kv"><span>探测结果</span><b>${probe}</b></div>${fixture ? '' : `<div class="kv"><span>已保存任务计划</span><b>${count} 个</b></div>`}<div class="chips">${source.proposed_fields.map(field => `<span class="chip">${esc(fieldLabels[field] || field)}</span>`).join('')}</div><div class="actions" style="margin-top:12px">${button('接入说明', 'source-detail', false, `data-id="${esc(source.id)}"`)}${button(fixture ? '新建样例任务' : '创建采集任务', 'create-source-job', !fixture, `data-id="${esc(source.id)}"`)}</div></article>`;
    }).join('')}</div><section class="panel" style="margin-top:23px"><div class="panel-head"><h2>试运行预算</h2>${badge('工程建议 · 尚未启用')}</div><div class="panel-body"><div class="note orange">${icon('info')}<span>每来源并发 1，间隔至少 10 秒，单任务最多 20 页 / 100 条 / 20 MiB / 10 分钟，每日 200 页。图片预算为 0。实际启用时以来源允许的更严格规则为准。</span></div></div></section>`;
}
function addWebsite() {
  modal('新增网站', `<form id="website-form"><div class="form-field"><label for="website-name">网站名称</label><input id="website-name" name="name" required maxlength="60" placeholder="例如：资料目录站"></div><div class="form-field"><label for="website-url">网站地址</label><input id="website-url" name="url" required maxlength="500" placeholder="https://catalog.example.com" autocomplete="off"><small>填写域名即可，支持 HTTP/HTTPS；目录路径在采集任务中设置。</small></div><div class="form-field"><label for="website-region">建议执行区域</label><select id="website-region" name="region"><option value="japan">日本</option><option value="beijing">北京</option></select></div><fieldset style="border:0;margin:0 0 18px;padding:0"><legend style="font-size:12px">计划采集的资料字段</legend><div class="field-options">${CollectionConfig.FIELDS.map(field => `<label><input type="checkbox" name="fields" value="${field}" ${['canonical_code','title'].includes(field) ? 'checked' : ''}>${fieldLabels[field]}</label>`).join('')}</div></fieldset><div class="form-field"><label for="website-role">用途说明（可选）</label><input id="website-role" name="role" maxlength="240" placeholder="例如：补充作品标题与厂牌"></div><div class="note">${icon('info')}<span>保存后可创建任务计划。真实采集需要验证网站访问规则、字段解析和采集器；登记网站不会访问其页面。</span></div></form>`, button('取消', 'close') + `<button type="submit" form="website-form" class="btn">保存网站</button><button type="submit" form="website-form" class="btn primary" data-create-task="true">保存并创建任务</button>`);
}
function jobFields(source) {
  if (source.id === 'fixture') return `<div class="form-field"><label for="job-scenario">运行场景</label><select id="job-scenario" name="scenario"><option value="normal">正常批次 / 重复提交</option><option value="timeout">连接超时 → 人工重试</option><option value="schema">结构变化 → 停止连接器</option></select></div><div class="note">${icon('info')}<span>固定 43 条合成观测 / 36 个实体。样例任务与审核状态只保留在当前页面。</span></div>`;
  return `<div class="form-grid"><div class="form-field"><label for="job-type">任务类型</label><select id="job-type" name="job_type">${DATA.task_templates.tasks.filter(task => CollectionConfig.JOB_TYPES.includes(task.type)).map(task => `<option value="${task.type}" ${task.type === 'work_incremental' ? 'selected' : ''}>${esc(task.name)}</option>`).join('')}</select></div><div class="form-field"><label for="job-region">执行区域</label><select id="job-region" name="region"><option value="japan">日本</option><option value="beijing" ${source.preferred_region === 'beijing' ? 'selected' : ''}>北京</option></select></div></div><div class="form-field"><label for="job-path">目录路径</label><input id="job-path" name="scope_path" value="/" required maxlength="300" placeholder="/works/"><small>相对于 ${esc(source.base_url || `https://${source.domain}/`)} 的路径；不填写账号、查询参数或完整 URL。</small></div><div class="form-grid"><div class="form-field"><label for="job-pages">页数上限</label><input id="job-pages" name="max_pages" type="number" min="1" max="20" step="1" value="5" required></div><div class="form-field"><label for="job-records">记录上限</label><input id="job-records" name="max_records" type="number" min="1" max="100" step="1" value="30" required></div></div><div class="chips">${source.proposed_fields.map(field => `<span class="chip">${esc(fieldLabels[field] || field)}</span>`).join('')}</div><div class="note orange">${icon('info')}<span>保存为“待接入采集器”任务计划，手动触发、无自动定时。当前没有该网站的可执行采集器，也不会用合成资料代替抓取结果。</span></div>`;
}
function createJob(sourceId = 'fixture') {
  const source = allSources().find(source => source.id === sourceId) || DATA.registry.sources[0];
  const title = (source.id === 'fixture' ? '作品增量 · 合成批次' : `${source.name} · 作品增量`).slice(0,60);
  modal('新建采集任务', `<form id="job-form"><div class="form-field"><label for="job-title">任务名称</label><input id="job-title" name="title" value="${esc(title)}" data-suggested="${esc(title)}" required maxlength="60"></div><div class="form-field"><label for="job-source">来源网站</label><select id="job-source" name="source_id">${allSources().map(item => `<option value="${esc(item.id)}" ${item.id === source.id ? 'selected' : ''}>${esc(item.name)}${item.id === 'fixture' ? ' · 可运行样例' : ' · 待接入'}</option>`).join('')}</select></div><div id="job-options">${jobFields(source)}</div></form>`, button('取消','close') + `<button type="submit" form="job-form" class="btn primary" id="job-submit">${source.id === 'fixture' ? '创建演示任务' : '保存采集任务'}</button>`);
}
function switchJobSource() {
  const source = allSources().find(source => source.id === $('#job-source').value);
  if (!source) return;
  const title = $('#job-title');
  const suggested = (source.id === 'fixture' ? '作品增量 · 合成批次' : `${source.name} · 作品增量`).slice(0,60);
  if (title.value === title.dataset.suggested) title.value = suggested;
  title.dataset.suggested = suggested;
  $('#job-options').innerHTML = jobFields(source);
  $('#job-submit').textContent = source.id === 'fixture' ? '创建演示任务' : '保存采集任务';
}
function submitWebsite(form, openTask) {
  try {
    const source = CollectionConfig.createWebsite({ name: form.get('name'), url: form.get('url'), region: form.get('region'), role: form.get('role'), fields: form.getAll('fields') }, allSources());
    const sources = [...state.customSources, source];
    if (!saveLocalConfig(sources, state.jobs)) return;
    state.customSources = sources;
    $('#dialog').close(); navigate('sources');
    toast('网站已保存在当前浏览器，可创建对应采集任务');
    if (openTask) createJob(source.id);
  } catch (error) { toast(error.message); }
}
function submitJob(form) {
  try {
    if (form.get('source_id') === 'fixture') {
      const title = String(form.get('title') || '').trim();
      const scenario = form.get('scenario');
      if (!title || title.length > 60) throw new Error('任务名称需填写 1–60 个字符');
      if (!['normal','timeout','schema'].includes(scenario)) throw new Error('请选择有效的样例场景');
      newJob(title, scenario);
    } else {
      const task = CollectionConfig.createTaskPlan(Object.fromEntries(form), allSources(), { id: `PLAN-${crypto.randomUUID()}`, created_at: new Date().toISOString() });
      const jobs = [...state.jobs, task];
      if (!saveLocalConfig(state.customSources, jobs)) return;
      state.jobs = jobs;
    }
    $('#dialog').close(); navigate('tasks');
    toast(form.get('source_id') === 'fixture' ? '演示任务已创建，点击运行开始处理' : '采集任务已保存，等待接入该网站的采集器');
  } catch (error) { toast(error.message); }
}
function cancelJob(id) {
  const job = state.jobs.find(job => job.id === id);
  if (job?.status !== 'pending') return;
  const jobs = state.jobs.map(item => item.id === id ? { ...item, status: 'cancelled' } : item);
  if (job.execution_mode === 'plan' && !saveLocalConfig(state.customSources, jobs)) return;
  state.jobs = jobs; render();
  toast(job.execution_mode === 'plan' ? '采集任务计划已取消并保存' : '演示任务已取消');
}
function planDetail(id) {
  const job = state.jobs.find(job => job.id === id && job.execution_mode === 'plan');
  if (!job) return;
  modal('采集任务配置', `<div class="actions" style="justify-content:space-between;margin-bottom:16px"><h3>${esc(job.title)}</h3>${jobBadge(job)}</div><div class="kv"><span>来源网站</span><b>${esc(job.source_name)}</b></div><div class="kv"><span>任务类型</span><b>${esc(taskName(job.job_type))}</b></div><div class="kv"><span>采集入口</span><b>${esc(job.scope.start_url)}</b></div><div class="kv"><span>执行区域 / 触发</span><b>${job.region === 'japan' ? '日本' : '北京'} / 手动计划</b></div><div class="kv"><span>任务上限</span><b>${job.limits.max_pages} 页 / ${job.limits.max_records} 条 / 图片 0 张</b></div><div class="kv"><span>字段</span><b>${job.fields.map(field => esc(fieldLabels[field] || field)).join(' / ')}</b></div><div class="note orange" style="margin-top:18px">${icon('info')}<span>该计划仅保存在当前浏览器。执行前需完成来源核验、固定版本解析器和服务端任务接入；当前未派发抓取。</span></div><details class="evidence"><summary>查看任务 JSON</summary><pre>${esc(JSON.stringify(job,null,2))}</pre></details>`, button('关闭','close'));
}
function exportData() {
  const value = { version: DATA.version, synthetic: true, network_access: false, notice: DATA.notice,
    configuration_notice: 'configuration 包含本机登记的网站和未执行任务计划；资料与审核为合成演示。',
    configuration: localConfig(), report: DATA.report, jobs: state.jobs, decisions: state.decisions, published: visibleWorks(), candidates: state.works };
  const blob = new Blob([JSON.stringify(value,null,2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'mujian-collection-demo.json'; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('已导出网站配置、任务计划与合成演示资料');
}
