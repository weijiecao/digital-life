/* 数字记忆控制台 · 前端（无构建步骤，直接由后端提供） */

const view = document.getElementById('view');
const overlay = document.getElementById('overlay');
const drawer = document.getElementById('drawer');
const drawerTitle = document.getElementById('drawer-title');
const drawerBody = document.getElementById('drawer-body');
const modal = document.getElementById('modal');
const modalTitle = document.getElementById('modal-title');
const modalBody = document.getElementById('modal-body');
const modalOk = document.getElementById('modal-ok');
const modalCancel = document.getElementById('modal-cancel');
const toastEl = document.getElementById('toast');
const conn = document.getElementById('conn');

const bridge = window.AstrBotPluginPage || window.AstrBotPluginView || null;
const params = new URLSearchParams(location.search);
let token = bridge ? '' : (params.get('token') || sessionStorage.getItem('dlm-token') || '');
if (!bridge && params.get('token')) {
  sessionStorage.setItem('dlm-token', params.get('token'));
  history.replaceState(null, '', location.pathname + location.hash);
}
if (!bridge && params.get('readonly') === '1') sessionStorage.setItem('dlm-readonly', '1');

let env = null;
let current = 'overview';
let toastTimer = 0;

const state = {
  store: { tab: 'captures', scope: '', status: '', q: '', page: 1, size: 20 },
  shadow: { tab: 'events', scope: '', status: '', q: '', page: 1, size: 20 },
  observations: { days: 7, scope: '', q: '', page: 1, size: 20 },
  maintenance: { dataset: 'store', months: 12, kind: 'captures' },
};

/* ------------------------------------------------------------------ 工具 */

const $ = (sel, root = document) => root.querySelector(sel);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function fmtNum(value) {
  if (value === null || value === undefined || value === '') return '—';
  return Number(value).toLocaleString('zh-CN');
}

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  const n = Number(bytes);
  if (!Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = n / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
}

function fmtTime(value) {
  if (!value) return '—';
  if (typeof value === 'number') value = new Date(value * 1000).toISOString();
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

function fromNow(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function shortScope(value) {
  if (!value) return '—';
  const text = String(value);
  if (text.length <= 30) return text;
  return `${text.slice(0, 14)}…${text.slice(-10)}`;
}

function toast(message, kind = '') {
  toastEl.textContent = message;
  toastEl.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.className = 'toast'; }, 4200);
}

function loading(text = '加载中…') {
  view.innerHTML = `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;
}

function errorView(error) {
  view.innerHTML = `
    <div class="panel"><div class="empty">
      <strong>加载失败</strong>${esc(error.message || error)}
      <div style="margin-top:14px">
        <button class="btn ghost" data-act="reload">重新加载</button>
      </div>
    </div></div>`;
}

async function api(path, options = {}) {
  if (bridge) {
    const method = (options.method || 'GET').toUpperCase();
    const [endpointPath, queryString] = path.split('?');
    const query = { ...(options.params || {}) };
    if (queryString) {
      new URLSearchParams(queryString).forEach((value, key) => { query[key] = value; });
    }
    const endpoint = 'console' + endpointPath;
    if (method === 'POST') return bridge.apiPost(endpoint, options.body || {});
    return bridge.apiGet(endpoint, query);
  }
  const headers = {};
  if (token) headers['X-Console-Token'] = token;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method: options.method || 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: 'no-store',
    });
  } catch (error) {
    throw new Error(`无法连接控制台服务：${error.message}`);
  }
  let data = null;
  try { data = await response.json(); } catch { data = { error: '响应不是 JSON' }; }
  if (!response.ok) {
    const error = new Error(data?.error || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function badge(text, kind = '') {
  return `<span class="badge ${kind}">${esc(text)}</span>`;
}

function roAttr() {
  return env?.app?.read_only ? 'disabled title="只读模式已禁用写操作"' : '';
}

function stateBadge(value) {
  const map = {
    pending: ['待审', 'warn'],
    organized: ['已整理', 'ok'],
    ignored: ['已忽略', ''],
    deleted: ['已删除', 'danger'],
    provisional: ['暂定', 'warn'],
    confirmed: ['已确认', 'ok'],
    withdrawn: ['已撤回', 'danger'],
    QUEUED: ['排队', 'info'],
    DEFERRED: ['暂缓', 'warn'],
    COMMITTED: ['已提交', 'ok'],
    ARCHIVED: ['仅归档', ''],
  };
  const [label, kind] = map[value] || [value || '未知', ''];
  return badge(label, kind);
}

function outputBadge(status) {
  const map = {
    INJECTED: ['已注入', 'ok'],
    UNCHANGED: ['已存在', 'info'],
    EMPTY: ['无内容', ''],
    REJECTED: ['已拒绝', 'danger'],
    UNAVAILABLE: ['未接线', 'warn'],
    OBSERVE: ['仅观测', ''],
  };
  const [label, kind] = map[status] || (status ? [status, ''] : ['仅观测', '']);
  return badge(label, kind);
}

function kpi(label, value, sub) {
  return `<div class="kpi"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div><div class="sub">${esc(sub)}</div></div>`;
}

function panel(title, body, hint = '', actions = '') {
  return `<div class="panel"><div class="panel-head">
    <h2 class="panel-title">${esc(title)}${hint ? `<span class="hint">${esc(hint)}</span>` : ''}</h2>
    ${actions ? `<div class="panel-actions">${actions}</div>` : ''}
  </div>${body}</div>`;
}

function table(headers, rows) {
  const head = headers.map((item) => {
    const label = typeof item === 'string' ? item : item.label;
    const cls = typeof item === 'object' && item.num ? 'num' : '';
    return `<th class="${cls}">${esc(label)}</th>`;
  }).join('');
  const body = rows.length
    ? rows.map((cells) => `<tr>${cells.map((cell) => {
      if (cell && typeof cell === 'object' && 'html' in cell) {
        return `<td class="${cell.cls || ''}">${cell.html}</td>`;
      }
      return `<td>${esc(cell)}</td>`;
    }).join('')}</tr>`).join('')
    : `<tr><td colspan="${headers.length}"><div class="empty">暂无数据</div></td></tr>`;
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function pager(data, action) {
  if (!data || !data.total) return '';
  const { page, pages, total } = data;
  const go = (value, label, disabled) => `<button data-act="${action}" data-pg="${value}"
    ${disabled ? 'disabled' : ''}>${label}</button>`;
  return `<div class="pager"><span>共 ${fmtNum(total)} 条 · 第 ${page} / ${pages} 页</span>
    <div class="pages">${go(1, '«', page <= 1)}${go(page - 1, '‹', page <= 1)}
      <span class="badge">${page} / ${pages}</span>
      ${go(page + 1, '›', page >= pages)}${go(pages, '»', page >= pages)}</div></div>`;
}

function emptyBlock(title, message) {
  return `<div class="empty"><strong>${esc(title)}</strong>${esc(message)}</div>`;
}

/* -------------------------------------------------------------- 弹窗/抽屉 */

function confirmDialog({ title, message, okText = '确认', danger = true, checkbox = false, checkboxText = '' }) {
  return new Promise((resolve) => {
    modalTitle.textContent = title;
    modalBody.innerHTML = `<p>${esc(message)}</p>` + (checkbox
      ? `<label class="check"><input type="checkbox" id="modal-check" checked> ${esc(checkboxText)}</label>`
      : '');
    modalOk.textContent = okText;
    modalOk.className = `btn ${danger ? 'danger' : ''}`;
    modal.classList.remove('hidden');

    const finish = (result) => {
      modal.classList.add('hidden');
      modalOk.onclick = null;
      modalCancel.onclick = null;
      document.removeEventListener('keydown', onKey);
      resolve(result);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') finish({ ok: false, checked: false });
      if (event.key === 'Enter') finish({ ok: true, checked: $('#modal-check')?.checked ?? false });
    };
    modalOk.onclick = () => finish({ ok: true, checked: $('#modal-check')?.checked ?? false });
    modalCancel.onclick = () => finish({ ok: false, checked: false });
    document.addEventListener('keydown', onKey);
  });
}

function openDrawer(title, html) {
  drawerTitle.textContent = title;
  drawerBody.innerHTML = html;
  drawer.classList.remove('hidden');
  overlay.classList.remove('hidden');
}

function closeDrawer() {
  drawer.classList.add('hidden');
  overlay.classList.add('hidden');
}

overlay.addEventListener('click', closeDrawer);
document.getElementById('drawer-close').addEventListener('click', closeDrawer);

/* ---------------------------------------------------------------- 页面框架 */

const PAGES = {
  overview: {
    title: '总览',
    sub: '基座、影子引擎与输入链路的当前状态',
    render: renderOverview,
  },
  store: {
    title: '记忆库',
    sub: '数字记忆基座中的原话、事件与作用域',
    render: renderStore,
  },
  shadow: {
    title: '影子引擎',
    sub: '事件、证据、输入队列与冲突',
    render: renderShadow,
  },
  observations: {
    title: '观察日志',
    sub: '每轮请求的记忆命中、渲染 token 与钩子延迟',
    render: renderObservations,
  },
  maintenance: {
    title: '备份维护',
    sub: '备份、轮转、checkpoint、完整性检查与归档',
    render: renderMaintenance,
  },
  environment: {
    title: '环境',
    sub: '数据位置、表规模与本机运行信息',
    render: renderEnvironment,
  },
};

async function navigate() {
  let name = location.hash.replace(/^#\/?/, '') || 'overview';
  if (!PAGES[name]) name = 'overview';
  current = name;
  document.querySelectorAll('#nav a').forEach((link) => {
    link.classList.toggle('active', link.dataset.page === name);
  });
  $('#page-title').textContent = PAGES[name].title;
  $('#page-sub').textContent = PAGES[name].sub;
  closeDrawer();
  loading();
  try {
    await PAGES[name].render();
  } catch (error) {
    errorView(error);
  }
}

async function boot() {
  if (bridge) {
    try { await bridge.ready(); } catch { /* 宿主上下文不可用时仍需尝试 API */ }
  }
  try {
    env = await api('/environment');
    conn.className = 'conn online';
    conn.innerHTML = '<i class="dot"></i><span>已连接</span>';
    document.getElementById('version').textContent = `v${env.app.version} · ${env.app.python}`;
    if (env.app.read_only) document.getElementById('ro-badge').classList.remove('hidden');
    $('#ro-badge').classList.toggle('hidden', !env.app.read_only);
  } catch (error) {
    conn.className = 'conn offline';
    conn.innerHTML = '<i class="dot"></i><span>未连接</span>';
    if (!bridge && error.status === 401) {
      renderTokenGate(error);
      return;
    }
  }
  await navigate();
}

function renderTokenGate(error) {
  view.innerHTML = `
    <div class="panel" style="max-width:560px;margin:40px auto">
      <h2 class="panel-title">需要访问令牌</h2>
      <p class="muted" style="line-height:1.7">${esc(error.message)}<br>
      请从启动控制台的终端复制带 <span class="mono">?token=…</span> 的地址打开，
      或在下面粘贴令牌。</p>
      <div class="toolbar" style="margin-top:16px">
        <div class="field grow"><label>访问令牌</label>
          <input id="token-input" type="text" autocomplete="off" placeholder="粘贴令牌" /></div>
        <button class="btn" data-act="set-token">进入控制台</button>
      </div>
    </div>`;
  setTimeout(() => $('#token-input')?.focus(), 30);
}

/* ------------------------------------------------------------------ 总览 */

async function renderOverview() {
  const data = await api('/overview');
  const store = data.counts.store;
  const shadow = data.counts.shadow;
  const journal = data.counts.journal;
  const observations = data.counts.observations;
  const latency = data.latency;

  const activity = data.activity || [];
  const maxActivity = Math.max(1, ...activity.map((day) => Math.max(day.captures, day.observations)));
  const bars = activity.map((day) => {
    const bar = (value, cls) => value > 0
      ? `<div class="bar ${cls}" style="height:${Math.max(3, Math.round((value / maxActivity) * 100))}%"></div>`
      : '';
    return `<div class="bar-group" title="${esc(day.date)} · 采集 ${day.captures} · 观察 ${day.observations}">
      <div class="bar-stack">${bar(day.captures, 'capture')}${bar(day.observations, 'observe')}</div></div>`;
  }).join('');
  const axis = activity.map((day) => `<span>${esc(day.date.slice(5))}</span>`).join('');

  const warnings = (data.warnings || []).map((item) => `
    <div class="warn-item ${esc(item.level)}"><i class="dot"></i><span>${esc(item.text)}</span></div>`).join('');

  const datasets = (data.datasets || []).map((item) => `
    <div class="list-item">
      <div class="meta"><span>${esc(item.label)}</span>
        <span>${item.exists ? fmtBytes(item.bytes) : '未发现'}</span></div>
      <div class="cell-sub mono">${esc(item.path || '—')}</div>
      <div class="cell-sub">备份 ${item.backups} 份
        ${item.last_backup ? `· 最近 ${esc(fromNow(item.last_backup))}` : '· 尚未备份'}</div>
    </div>`).join('');

  const recent = observations.recent || [];
  const recentRows = recent.map((item) => [
    { html: `<span class="mono">${esc(item.time ? fmtTime(item.time) : '—')}</span>`, cls: 'small' },
    { html: `<span class="cell-sub mono">${esc(shortScope(item.scope))}</span>` },
    { html: `<div class="truncate" style="max-width:420px">${esc(item.query)}</div>` },
    { html: item.items > 0 ? badge(`${item.items} 条`, 'ok') : badge('未命中', '') },
  ]);

  view.innerHTML = `
    <div class="cards">
      ${kpi('原话', `${fmtNum(store.captures)}<small>条</small>`, `待审 ${fmtNum(store.pending)} · ${fmtNum(store.scopes)} 个作用域`)}
      ${kpi('记忆事件', `${fmtNum(shadow.events || store.events)}<small>个</small>`, `证据 ${fmtNum(shadow.evidence)} 条`)}
      ${kpi('输入队列', `${fmtNum(shadow.deferred)}<small>暂缓</small>`, `journal 未投递 ${fmtNum(journal.undelivered)} 条`)}
      ${kpi('观察请求', `${fmtNum(observations.loaded)}<small>条</small>`, `注入率 ${observations.inject_rate == null ? '—' : Math.round(observations.inject_rate * 100) + '%'}`)}
      ${kpi('钩子 P95', latency.p95 == null ? '—' : `${latency.p95}<small>ms</small>`, latency.turns ? `${fmtNum(latency.turns)} 轮 · P50 ${latency.p50} ms` : '暂无延迟数据')}
      ${kpi('磁盘占用', fmtBytes((data.datasets || []).reduce((sum, item) => sum + (item.exists ? item.bytes : 0), 0)), `含 WAL ${fmtBytes((data.datasets || []).reduce((sum, item) => sum + item.wal_bytes, 0))}`)}
    </div>

    ${panel('系统提示', `<div class="warnings">${warnings}</div>`, `${(data.warnings || []).length} 条`)}
    <div class="grid-2 section-gap">
      ${panel('最近 14 天活动', `
        <div class="chart">${bars || '<div class="empty">暂无数据</div>'}</div>
        <div class="bar-axis">${axis}</div>
        <div class="legend" style="margin-top:10px"><span><i class="cap"></i>采集原话</span><span><i class="obs"></i>观察请求</span></div>`)}
      ${panel('数据组件', `<div class="list">${datasets}</div>`)}
    </div>

    <div class="section-gap">
      ${panel('最近观察', recent.length ? table(
        ['时间', '作用域', '查询', '命中'], recentRows) : emptyBlock('还没有观察记录', '影子插件在每次 LLM 请求后写入 shadow_events.jsonl。'),
        '', `<button class="btn small ghost" data-act="goto-observations">查看全部</button>`)}
    </div>

    <div class="section-gap">
      ${panel('快速操作', `<div class="dataset-actions">
        <button class="btn small" data-act="backup" data-dataset="store" ${roAttr()}>备份基座</button>
        <button class="btn small ghost" data-act="backup" data-dataset="shadow" ${roAttr()}>备份影子库</button>
        <button class="btn small ghost" data-act="integrity" data-dataset="store">基座完整性检查</button>
        <button class="btn small ghost" data-act="checkpoint" data-dataset="store" ${roAttr()}>基座 WAL checkpoint</button>
      </div>`, '写操作会记入操作审计')}
    </div>`;
}

/* ------------------------------------------------------------------ 记忆库 */

async function renderStore() {
  const s = state.store;
  const query = new URLSearchParams({
    scope: s.scope, state: s.status, q: s.q,
    page: String(s.page), size: String(s.size),
  });
  const [scopes, captures, events] = await Promise.all([
    api('/store/scopes'),
    api(`/store/captures?${query}`),
    api(`/store/events?scope=${encodeURIComponent(s.scope)}&q=&page=1&size=100`),
  ]);

  const scopeOptions = [`<option value="">全部作用域</option>`].concat(
    (scopes.items || []).map((item) => `<option value="${esc(item.scope)}" ${item.scope === s.scope ? 'selected' : ''}>
      ${esc(shortScope(item.scope))}（${item.captures}）</option>`)).join('');

  const statusOptions = ['', 'pending', 'organized', 'ignored'].map((value) => {
    const labels = { '': '全部状态', pending: '待审', organized: '已整理', ignored: '已忽略' };
    return `<option value="${value}" ${value === s.status ? 'selected' : ''}>${labels[value]}</option>`;
  }).join('');

  const captureRows = (captures.items || []).map((row) => [
    { html: `<div class="cell-title">${esc(row.preview || '（空）')}</div>
      <div class="cell-sub">#${row.seq} · ${fmtNum(row.chars)} 字</div>` },
    { html: stateBadge(row.state) },
    { html: `<span class="mono small">${esc(fmtTime(row.recorded_at))}</span>` },
    { html: row.linked_title ? esc(row.linked_title) : '<span class="muted">—</span>' },
    { html: `<button class="btn small ghost" data-act="open-capture" data-scope="${esc(row.scope)}" data-seq="${row.seq}">详情</button>
      <button class="btn small danger" data-act="delete-capture" data-scope="${esc(row.scope)}" data-seq="${row.seq}" ${roAttr()}>删除</button>`, cls: 'actions' },
  ]);

  const eventRows = (events.items || []).map((row) => [
    { html: `<div class="cell-title">${esc(row.title)}</div>
      <div class="cell-sub mono">${esc(row.id)}</div>` },
    { html: `<span class="cell-sub mono">${esc(shortScope(row.scope))}</span>` },
    { html: fmtNum(row.captures), cls: 'num' },
    { html: `<span class="mono small">${esc(fmtTime(row.last_at))}</span>` },
  ]);

  const tabs = `
    <div class="tabs">
      <button class="tab ${s.tab === 'captures' ? 'active' : ''}" data-act="store-tab" data-tab="captures">原话 ${fmtNum(captures.total)}</button>
      <button class="tab ${s.tab === 'events' ? 'active' : ''}" data-act="store-tab" data-tab="events">事件 ${fmtNum(events.total)}</button>
    </div>`;

  const toolbar = `
    <div class="toolbar">
      <div class="field"><label>作用域</label><select id="store-scope">${scopeOptions}</select></div>
      ${s.tab === 'captures' ? `<div class="field"><label>状态</label><select id="store-status">${statusOptions}</select></div>` : ''}
      ${s.tab === 'captures' ? `<div class="field grow"><label>搜索原话</label>
        <input id="store-q" type="text" data-enter="store-apply" value="${esc(s.q)}" placeholder="输入包含的文字后回车" /></div>` : ''}
      <button class="btn ghost" data-act="store-apply">查询</button>
      <button class="btn ghost" data-act="store-reset">重置</button>
    </div>`;

  const body = s.tab === 'captures'
    ? table(['原话', '状态', '记录时间', '关联事件', { label: '操作', num: true }], captureRows) + pager(captures, 'store-page')
    : table(['事件', '作用域', { label: '关联原话', num: true }, '最近活动'], eventRows);

  view.innerHTML = `
    <div class="cards">
      ${kpi('原话总数', `${fmtNum(captures.total)}<small>条</small>`, `当前筛选结果`)}
      ${kpi('作用域', `${fmtNum((scopes.items || []).length)}<small>个</small>`, '按最近活动排序')}
      ${kpi('事件', `${fmtNum(events.total)}<small>个</small>`, '基座事件表')}
    </div>
    <div class="section-gap">
      ${panel('记忆条目', tabs + toolbar + body, s.tab === 'captures' ? '删除会同时清理派生行' : '',
        `<button class="btn small danger" data-act="delete-scope" data-scope="${esc(s.scope)}" ${s.scope ? '' : 'disabled'} ${roAttr()}>删除当前作用域</button>`)}
    </div>`;
}

async function openCapture(scope, seq) {
  openDrawer('原话详情', '<div class="loading"><span class="spinner"></span>加载中…</div>');
  try {
    const data = await api(`/store/capture?scope=${encodeURIComponent(scope)}&seq=${seq}`);
    const provenance = data.provenance
      ? `<pre>${esc(JSON.stringify(data.provenance, null, 2))}</pre>` : '<span class="muted">无导入来源记录</span>';
    drawerBody.innerHTML = `
      <dl class="kv">
        <dt>序号</dt><dd class="mono">#${data.seq}</dd>
        <dt>作用域</dt><dd class="mono">${esc(data.scope)}</dd>
        <dt>状态</dt><dd>${stateBadge(data.state)}</dd>
        <dt>记录时间</dt><dd>${esc(fmtTime(data.recorded_at))}</dd>
        <dt>字符数</dt><dd>${fmtNum((data.content || '').length)}</dd>
        <dt>内容哈希</dt><dd class="mono small">${esc(data.content_sha)}</dd>
        <dt>事件哈希</dt><dd class="mono small">${esc(data.event_hash)}</dd>
        <dt>关联事件</dt><dd>${data.linked_title ? esc(data.linked_title) : '<span class="muted">未关联</span>'}</dd>
      </dl>
      <h3 class="section-title">原文</h3>
      <div class="quote">${esc(data.content)}</div>
      <h3 class="section-title">导入来源</h3>
      ${provenance}
      <div class="modal-actions" style="justify-content:flex-start">
        <button class="btn danger" data-act="delete-capture" data-scope="${esc(data.scope)}" data-seq="${data.seq}" ${roAttr()}>删除这条原话</button>
      </div>`;
  } catch (error) {
    drawerBody.innerHTML = `<div class="empty"><strong>加载失败</strong>${esc(error.message)}</div>`;
  }
}

async function deleteCapture(scope, seq) {
  const answer = await confirmDialog({
    title: '删除原话',
    message: `将删除 #${seq} 及其派生数据，并执行 checkpoint / VACUUM。该操作不可撤销。`,
    okText: '删除',
    checkbox: true,
    checkboxText: '删除前先创建一份基座备份（推荐）',
  });
  if (!answer.ok) return;
  const result = await api('/store/delete', {
    method: 'POST', body: { scope, seq, backup: answer.checked, purge: true },
  });
  toast(`已删除 ${result.deleted} 条原话${result.backup ? '，并已备份' : ''}`, 'ok');
  closeDrawer();
  await renderStore();
}

async function deleteScope(scope) {
  if (!scope) { toast('请先选择一个作用域', 'err'); return; }
  const answer = await confirmDialog({
    title: '删除整个作用域',
    message: `将删除作用域 ${scope} 下所有原话及派生数据。该操作不可撤销。`,
    okText: '删除整个作用域',
    checkbox: true,
    checkboxText: '删除前先创建一份基座备份（推荐）',
  });
  if (!answer.ok) return;
  const result = await api('/store/delete', {
    method: 'POST', body: { scope, all: true, backup: answer.checked, purge: true },
  });
  toast(`已删除作用域内 ${result.deleted} 条原话`, 'ok');
  await renderStore();
}

/* ---------------------------------------------------------------- 影子引擎 */

async function renderShadow() {
  const s = state.shadow;
  const [overview, scopes, events] = await Promise.all([
    api('/overview'),
    api('/shadow/scopes'),
    api(`/shadow/events?scope=${encodeURIComponent(s.scope)}&q=${encodeURIComponent(s.q)}&state=${encodeURIComponent(s.status)}&page=${s.page}&size=${s.size}`),
  ]);
  const inbox = await api(`/shadow/inbox?scope=${encodeURIComponent(s.scope)}&page=1&size=50`);
  const conflicts = await api('/shadow/conflicts?page=1&size=100');
  const summary = overview.counts.shadow;

  const scopeOptions = [`<option value="">全部作用域</option>`].concat(
    (scopes.items || []).map((item) => `<option value="${esc(item.scope_id)}" ${item.scope_id === s.scope ? 'selected' : ''}>
      ${esc(shortScope(item.scope_id))}（${item.events}）</option>`)).join('');

  const statuses = ['', 'provisional', 'confirmed', 'withdrawn'];
  const statusLabels = { '': '全部状态', provisional: '暂定', confirmed: '已确认', withdrawn: '已撤回' };
  const statusOptions = statuses.map((value) => `<option value="${value}" ${value === s.status ? 'selected' : ''}>${statusLabels[value]}</option>`).join('');

  const toolbar = `
    <div class="toolbar">
      <div class="field"><label>作用域</label><select id="shadow-scope">${scopeOptions}</select></div>
      <div class="field"><label>状态</label><select id="shadow-status">${statusOptions}</select></div>
      <div class="field grow"><label>搜索标题或证据</label>
        <input id="shadow-q" type="text" data-enter="shadow-apply" value="${esc(s.q)}" placeholder="输入关键词后回车" /></div>
      <button class="btn ghost" data-act="shadow-apply">查询</button>
      <button class="btn ghost" data-act="shadow-reset">重置</button>
    </div>`;

  const eventRows = (events.items || []).map((row) => [
    { html: `<div class="cell-title">${esc(row.title)}</div>
      <div class="cell-sub mono">${esc(row.event_id)}</div>` },
    { html: `<span class="cell-sub mono">${esc(shortScope(row.scope_id))}</span>` },
    { html: stateBadge(row.state) },
    { html: `<span class="small">${esc(row.narrator)} · ${esc(row.subject)}<br>
      <span class="muted">${esc(row.reality_status)}</span></span>` },
    { html: fmtNum(row.evidence), cls: 'num' },
    { html: `<span class="mono small">${esc(fromNow(row.updated_at))}</span>` },
    { html: `<button class="btn small ghost" data-act="open-event" data-id="${esc(row.event_id)}">详情</button>`, cls: 'actions' },
  ]);

  const scopeRows = (scopes.items || []).map((row) => [
    { html: `<span class="cell-sub mono">${esc(row.scope_id)}</span>` },
    { html: fmtNum(row.events), cls: 'num' },
    { html: fmtNum(row.evidence), cls: 'num' },
    { html: fmtNum(row.provisional), cls: 'num' },
    { html: `<span class="mono small">${esc(fromNow(row.last_at))}</span>` },
  ]);

  const inboxRows = (inbox.items || []).map((row) => [
    { html: `<span class="mono small">${esc(shortScope(row.scope_id))}</span>` },
    { html: stateBadge(row.state) },
    { html: `<span class="small">${esc(row.role || '—')}</span>` },
    { html: `<div class="truncate" style="max-width:380px">${esc(row.text || '—')}</div>` },
    { html: row.reason ? `<span class="badge warn">${esc(row.reason)}</span>` : '<span class="muted">—</span>' },
    { html: `<span class="mono small">${esc(fmtTime(row.recorded_at))}</span>` },
  ]);

  const conflictRows = (conflicts.items || []).map((row) => [
    { html: `<span class="badge violet">${esc(row.kind)}</span>` },
    { html: `<span class="cell-sub mono">${esc(shortScope(row.scope_id || row.stream_id || ''))}</span>` },
    { html: `<div class="truncate" style="max-width:420px">${esc(row.detail || '')}</div>` },
    { html: `<span class="mono small">${esc(fmtTime(row.created_at))}</span>` },
  ]);

  const tab = s.tab;
  let content = '';
  if (tab === 'events') {
    content = toolbar + table(['事件', '作用域', '状态', '来源', { label: '证据', num: true }, '更新', { label: '操作', num: true }], eventRows) + pager(events, 'shadow-page');
  } else if (tab === 'inbox') {
    content = `<div class="toolbar">
      <span class="chip">排队 ${inbox.states?.QUEUED || 0}</span>
      <span class="chip warn">暂缓 ${inbox.states?.DEFERRED || 0}</span>
      <span class="chip">已提交 ${inbox.states?.COMMITTED || 0}</span>
      <span class="chip">仅归档 ${inbox.states?.ARCHIVED || 0}</span>
    </div>` + (inbox.available
      ? table(['作用域', '状态', '角色', '内容', '暂缓原因', '时间'], inboxRows)
      : emptyBlock('没有输入队列表', '该影子库还没有 native_input_inbox；运行一次输入管线后生成。'));
  } else if (tab === 'conflicts') {
    content = table(['类型', '作用域 / 输入流', '详情', '时间'], conflictRows);
  } else {
    content = table(['作用域', { label: '事件', num: true }, { label: '证据', num: true }, { label: '暂定', num: true }, '最近更新'], scopeRows);
  }

  view.innerHTML = `
    <div class="cards">
      ${kpi('事件', `${fmtNum(summary.events)}<small>个</small>`, `暂定 ${fmtNum(summary.provisional)}`)}
      ${kpi('证据', `${fmtNum(summary.evidence)}<small>条</small>`, `${fmtNum(summary.scopes)} 个作用域`)}
      ${kpi('输入队列', `${fmtNum(summary.deferred)}<small>暂缓</small>`, `排队 ${fmtNum(summary.inbox?.QUEUED || 0)}`)}
      ${kpi('待解析', `${fmtNum(summary.pending_resolution)}<small>条</small>`, `冲突 ${fmtNum(summary.conflicts)}`)}
    </div>
    <div class="section-gap">
      ${panel('影子引擎', `
        <div class="tabs">
          <button class="tab ${tab === 'events' ? 'active' : ''}" data-act="shadow-tab" data-tab="events">事件 ${fmtNum(events.total)}</button>
          <button class="tab ${tab === 'inbox' ? 'active' : ''}" data-act="shadow-tab" data-tab="inbox">输入队列</button>
          <button class="tab ${tab === 'conflicts' ? 'active' : ''}" data-act="shadow-tab" data-tab="conflicts">冲突 ${fmtNum(conflicts.total)}</button>
          <button class="tab ${tab === 'scopes' ? 'active' : ''}" data-act="shadow-tab" data-tab="scopes">作用域 ${fmtNum((scopes.items || []).length)}</button>
        </div>${content}`, '只读展示；事件由引擎写入')}
    </div>`;
}

async function openShadowEvent(eventId) {
  openDrawer('事件详情', '<div class="loading"><span class="spinner"></span>加载中…</div>');
  try {
    const data = await api(`/shadow/event?event_id=${encodeURIComponent(eventId)}`);
    const event = data.event;
    const revisions = (data.revisions || []).map((rev) => `
      <div class="list-item"><div class="meta"><span>rev ${rev.rev} · ${esc(rev.status)}</span>
        <span>${esc(rev.created_at)}</span></div>
        <div>${esc(rev.summary)}</div></div>`).join('');
    const evidence = (data.evidence || []).map((item) => `
      <div class="list-item"><div class="meta"><span class="mono">${esc(item.scope_id)} #${item.source_seq}</span>
        <span>rev ${item.rev}</span></div><div class="quote">${esc(item.quote)}</div></div>`).join('');
    const relations = (data.relations || []).map((item) => `
      <div class="list-item"><div class="meta"><span>${esc(item.relation_type)} · ${esc(item.status)}</span>
        <span>${esc(item.created_at)}</span></div>
        <div class="mono small">${esc(item.from_event)} → ${esc(item.to_event)}</div></div>`).join('');
    const items = (data.items || []).map((item) => `
      <div class="list-item"><div class="meta"><span>${esc(item.item_type)} · ${esc(item.modality)}</span>
        <span>${esc(item.created_at)}</span></div><div>${esc(item.text)}</div></div>`).join('');

    drawerBody.innerHTML = `
      <dl class="kv">
        <dt>事件 ID</dt><dd class="mono">${esc(event.event_id)}</dd>
        <dt>作用域</dt><dd class="mono">${esc(event.scope_id)}</dd>
        <dt>标题</dt><dd>${esc(event.title)}</dd>
        <dt>状态</dt><dd>${stateBadge(event.state)}</dd>
        <dt>类型</dt><dd>${esc(event.event_type)}</dd>
        <dt>来源</dt><dd>${esc(event.narrator)} / ${esc(event.subject)} / ${esc(event.reality_status)}</dd>
        <dt>当前版本</dt><dd>rev ${event.current_revision}</dd>
        <dt>创建</dt><dd>${esc(fmtTime(event.created_at))}</dd>
        <dt>更新</dt><dd>${esc(fmtTime(event.updated_at))}</dd>
      </dl>
      <h3 class="section-title">版本（${(data.revisions || []).length}）</h3>
      <div class="list">${revisions || '<span class="muted">无</span>'}</div>
      <h3 class="section-title">证据（${(data.evidence || []).length}）</h3>
      <div class="list">${evidence || '<span class="muted">无</span>'}</div>
      <h3 class="section-title">关系（${(data.relations || []).length}）</h3>
      <div class="list">${relations || '<span class="muted">无</span>'}</div>
      <h3 class="section-title">记忆条目（${(data.items || []).length}）</h3>
      <div class="list">${items || '<span class="muted">无</span>'}</div>`;
  } catch (error) {
    drawerBody.innerHTML = `<div class="empty"><strong>加载失败</strong>${esc(error.message)}</div>`;
  }
}

/* ---------------------------------------------------------------- 观察日志 */

async function renderObservations() {
  const s = state.observations;
  const [latency, data, scopes] = await Promise.all([
    api('/shadow/latency'),
    api(`/shadow/observations?scope=${encodeURIComponent(s.scope)}&q=${encodeURIComponent(s.q)}&days=${s.days}&page=${s.page}&size=${s.size}`),
    api('/shadow/scopes'),
  ]);
  const scopeOptions = [`<option value="">全部作用域</option>`].concat(
    (scopes.items || []).map((item) => `<option value="${esc(item.scope_id)}" ${item.scope_id === s.scope ? 'selected' : ''}>
      ${esc(shortScope(item.scope_id))}（${item.events}）</option>`)).join('');
  const buckets = latency.buckets || {};
  const maxBucket = Math.max(1, ...Object.values(buckets));
  const hist = Object.entries(buckets).map(([label, value]) => `
    <div class="hist-row"><span>${esc(label)}</span>
      <div class="hist-track"><div class="hist-fill" style="width:${Math.round(value / maxBucket * 100)}%"></div></div>
      <b>${fmtNum(value)}</b></div>`).join('');

  const rows = (data.items || []).map((item) => [
    { html: `<span class="mono small">${esc(item.time ? fmtTime(item.time) : '—')}</span>` },
    { html: `<span class="cell-sub mono">${esc(shortScope(item.scope))}</span>` },
    { html: `<div class="truncate" style="max-width:440px">${esc(item.query)}</div>` },
    { html: item.items > 0 ? badge(`${item.items} 条`, 'ok') : badge('未命中', '') },
    { html: outputBadge(item.output_status) },
    { html: fmtNum(item.rendered_tokens ?? item.host_memory_tokens), cls: 'num' },
  ]);
  const outputs = data.outputs || {};

  view.innerHTML = `
    <div class="cards">
      ${kpi('钩子轮次', `${fmtNum(latency.turns)}<small>轮</small>`, latency.available ? `P50 ${latency.p50} ms · P95 ${latency.p95} ms` : '暂无延迟数据')}
      ${kpi('记忆命中', `${fmtNum(latency.injected)}<small>次</small>`, latency.turns ? `${Math.round(latency.injected / latency.turns * 100)}% 的轮次命中记忆` : '—')}
      ${kpi('输出注入', `${fmtNum(outputs.INJECTED)}<small>次</small>`, `拒绝 ${fmtNum(outputs.REJECTED)} · 无内容 ${fmtNum(outputs.EMPTY)}`)}
      ${kpi('观察请求', `${fmtNum(data.total)}<small>条</small>`, data.truncated ? '只读取了日志尾部' : `扫描 ${fmtNum(data.scanned)} 行`)}
      ${kpi('平均命中', `${(data.items || []).length ? (data.items.reduce((sum, item) => sum + (item.items || 0), 0) / data.items.length).toFixed(1) : '—'}<small>条/轮</small>`, '当前页平均值')}
    </div>
    <div class="grid-2 section-gap">
      ${panel('延迟分布', latency.available ? `<div class="hist">${hist}</div>
        <p class="muted small" style="margin:12px 0 0">最大 ${latency.max} ms · 平均 ${latency.avg} ms · 样本 ${fmtNum(latency.turns)} 轮</p>`
        : emptyBlock('暂无延迟数据', '基座开启 latency_metrics_enabled 后会写入 latency.jsonl。'))}
      ${panel('说明', `<div class="list">
        <div class="list-item"><div class="meta"><span>观察日志</span><span>shadow_events.jsonl</span></div>
          每次 LLM 请求后记录命中条数、事件 ID、渲染 token 与输出状态；默认只观测，
          开启注入后也不改写 prompt、持久化历史或回复。</div>
        <div class="list-item"><div class="meta"><span>延迟日志</span><span>latency.jsonl</span></div>
          钩子耗时与是否命中记忆；数据来自基座插件。</div>
        <div class="list-item"><div class="meta"><span>只读</span><span>不影响运行</span></div>
          控制台只读取日志尾部，不轮转、不删除。</div>
      </div>`)}
    </div>
    <div class="section-gap">
      ${panel('观察明细', `
        <div class="toolbar">
          <div class="field"><label>时间范围</label>
            <select id="obs-days">
              ${[[7, '最近 7 天'], [1, '最近 24 小时'], [30, '最近 30 天'], [0, '全部（尾部）']]
                .map(([value, label]) => `<option value="${value}" ${value === s.days ? 'selected' : ''}>${label}</option>`).join('')}
            </select></div>
          <div class="field"><label>作用域</label>
            <select id="obs-scope">${scopeOptions}</select></div>
          <div class="field grow"><label>搜索查询</label>
            <input id="obs-q" type="text" data-enter="obs-apply" value="${esc(s.q)}" placeholder="输入关键词后回车" /></div>
          <button class="btn ghost" data-act="obs-apply">查询</button>
          <button class="btn ghost" data-act="obs-reset">重置</button>
        </div>
        ${table(['时间', '作用域', '查询', '命中', '输出', { label: '渲染 token', num: true }], rows)}
        ${pager(data, 'obs-page')}`, data.truncated ? '日志较大，只读取最近尾部' : '')}
    </div>`;
}

/* ---------------------------------------------------------------- 备份维护 */

async function renderMaintenance() {
  const m = state.maintenance;
  const [overview, backups, audit] = await Promise.all([
    api('/overview'),
    api(`/maintenance/backups?dataset=${m.dataset}`),
    api('/maintenance/audit?limit=80'),
  ]);

  const datasets = overview.datasets || [];
  const cards = datasets.map((item) => `
    <div class="dataset-card">
      <h3>${esc(item.label)}</h3>
      <div class="path mono">${esc(item.path || '未发现')}</div>
      <div class="dataset-stats">
        <div><b>${item.exists ? fmtBytes(item.bytes) : '—'}</b><span>数据库</span></div>
        <div><b>${fmtBytes(item.wal_bytes)}</b><span>WAL</span></div>
        <div><b>${fmtNum(item.backups)}</b><span>备份</span></div>
      </div>
      <div class="dataset-actions">
        <button class="btn small" data-act="backup" data-dataset="${item.key}" ${item.exists ? '' : 'disabled'} ${roAttr()}>立即备份</button>
        <button class="btn small ghost" data-act="rotate" data-dataset="${item.key}" ${item.exists ? '' : 'disabled'} ${roAttr()}>轮转</button>
        <button class="btn small ghost" data-act="checkpoint" data-dataset="${item.key}" ${item.exists ? '' : 'disabled'} ${roAttr()}>checkpoint</button>
        <button class="btn small ghost" data-act="integrity" data-dataset="${item.key}" ${item.exists ? '' : 'disabled'}>完整性</button>
        <button class="btn small danger" data-act="vacuum" data-dataset="${item.key}" ${item.exists ? '' : 'disabled'} ${roAttr()}>VACUUM</button>
      </div>
    </div>`).join('');

  const backupRows = (backups.items || []).map((item) => [
    { html: `<span class="mono">${esc(item.name)}</span>` },
    { html: `<span class="badge ${item.kind === 'console' ? 'info' : item.kind === 'plugin' ? '' : 'violet'}">${item.kind === 'console' ? '控制台' : item.kind === 'plugin' ? '插件' : '其他'}</span>` },
    { html: fmtBytes(item.bytes), cls: 'num' },
    { html: `<span class="mono small">${esc(fmtTime(item.at))}</span>` },
    { html: `<button class="btn small ghost" data-act="verify-backup" data-dataset="${m.dataset}" data-name="${esc(item.name)}">校验</button>
      <button class="btn small ghost" data-act="restore-backup" data-dataset="${m.dataset}" data-name="${esc(item.name)}" ${roAttr()}>恢复副本</button>`, cls: 'actions' },
  ]);

  const auditRows = (audit.items || []).map((item) => [
    { html: `<span class="mono small">${esc(fmtTime(item.ts))}</span>` },
    { html: `<span class="badge ${item.ok ? 'ok' : 'danger'}">${esc(item.action)}</span>` },
    { html: `<span class="cell-sub mono">${esc(item.dataset || '—')}</span>` },
    { html: `<div class="truncate" style="max-width:380px">${esc(JSON.stringify(item.result ?? item.error ?? ''))}</div>` },
  ]);

  const datasetOptions = datasets.map((item) => `<option value="${item.key}" ${item.key === m.dataset ? 'selected' : ''}>${esc(item.label)}</option>`).join('');

  const capacity = overview.capacity || {};
  const capacityRows = Object.entries(capacity.projections_5y || {}).map(([rate, row]) => [
    { html: `<span class="mono">${esc(rate)}</span>`, cls: 'num' },
    { html: `${fmtNum(row.db_gb)} GB`, cls: 'num' },
    { html: `${fmtNum(row.with_backups_gb)} GB`, cls: 'num' },
  ]);

  view.innerHTML = `
    <div class="dataset-grid">${cards}</div>
    <div class="section-gap">
      ${panel('五年容量预估', table(['条/天', { label: '5 年库', num: true }, { label: '含 11 份备份', num: true }], capacityRows),
        capacity.per_message_bytes
          ? (capacity.sample_sufficient
            ? `按当前每条 ${fmtBytes(capacity.per_message_bytes)} 估算`
            : `样本不足 100 条，按默认每条 ${fmtBytes(capacity.per_message_bytes)} 估算`)
          : '暂无原话，按默认每条 11 KB 估算')}
    </div>
    <div class="grid-2 section-gap">
      ${panel('归档导出', `
        <p class="muted small" style="margin-top:0">只导出，不删除主库数据；文件写入插件数据目录的 archives/。</p>
        <div class="toolbar">
          <div class="field"><label>对象</label>
            <select id="archive-kind">
              <option value="captures" ${m.kind === 'captures' ? 'selected' : ''}>基座原话</option>
              <option value="events" ${m.kind === 'events' ? 'selected' : ''}>影子事件</option>
            </select></div>
          <div class="field"><label>早于（个月）</label>
            <input id="archive-months" type="number" min="1" max="120" value="${m.months}" /></div>
          <button class="btn ghost" data-act="archive-preview">预览</button>
          <button class="btn" data-act="archive-export" ${roAttr()}>导出 JSONL</button>
        </div>
        <div id="archive-result"></div>`, '导出前请确认目标路径')}
      ${panel('操作审计', audit.items?.length ? table(['时间', '操作', '数据集', '结果'], auditRows)
        : emptyBlock('还没有操作记录', '备份、轮转、删除等操作会写在这里。'),
        `最近 ${audit.items?.length || 0} 条`)}
    </div>
    <div class="section-gap">
      ${panel(`备份文件 · ${esc((datasets.find((item) => item.key === m.dataset) || {}).label || m.dataset)}`, `
        <div class="toolbar">
          <div class="field"><label>数据集</label>
            <select id="backup-dataset">${datasetOptions}</select></div>
          <button class="btn ghost" data-act="dataset-select">切换</button>
          <div class="field"><label>保留份数</label>
            <input id="rotate-keep" type="number" min="1" max="100" value="7" /></div>
          <button class="btn ghost" data-act="rotate" data-dataset="${m.dataset}" ${roAttr()}>按份数轮转</button>
        </div>
        ${table(['文件', '来源', { label: '大小', num: true }, '时间', { label: '操作', num: true }], backupRows)}`,
        backups.dir || '')}
    </div>`;
}

/* -------------------------------------------------------------------- 环境 */

async function renderEnvironment() {
  const data = await api('/environment');
  const pathRows = (data.paths || []).map((item) => [
    { html: `<span class="cell-title">${esc(item.label)}</span>` },
    { html: `<span class="mono small">${esc(item.path || '—')}</span>` },
    { html: item.exists ? badge('存在', 'ok') : badge('未发现', 'warn') },
    { html: item.kind === 'file' ? fmtBytes(item.bytes) : '<span class="muted">目录</span>', cls: 'num' },
    { html: item.mtime ? `<span class="mono small">${esc(fmtTime(item.mtime))}</span>` : '<span class="muted">—</span>' },
  ]);

  const tableBlocks = Object.entries(data.tables || {}).map(([key, items]) => `
    <details class="panel" style="margin-top:10px" ${key === 'store' ? 'open' : ''}>
      <summary style="cursor:pointer;color:var(--text-dim)">
        ${esc(key)} · ${items.length} 张表 · ${fmtNum(items.reduce((sum, item) => sum + (item.rows || 0), 0))} 行
      </summary>
      ${table(['表', { label: '行数', num: true }], items.map((item) => [
        { html: `<span class="mono">${esc(item.name)}</span>` },
        { html: fmtNum(item.rows), cls: 'num' },
      ]))}
    </details>`).join('');

  const config = data.config || {};
  const configBlock = config.available
    ? table(['配置项', '值'], Object.entries(config.values || {}).map(([key, value]) => [
      { html: `<span class="mono">${esc(key)}</span>` },
      { html: esc(typeof value === 'object' ? JSON.stringify(value) : value) },
    ]))
    : `<div class="empty"><strong>没有读取到插件配置</strong>${esc(config.path || '未指定 AstrBot 实例')}</div>`;

  const output = data.output || {};
  const outputBlock = output.available
    ? `<dl class="kv">
        <dt>状态</dt><dd>${badge('可用', 'ok')}</dd>
        <dt>协议版本</dt><dd class="mono">${esc(output.schema_version)}</dd>
        <dt>源码位置</dt><dd class="mono small">${esc(output.path)}</dd>
        <dt>宿主接线</dt><dd class="mono">${esc(output.host_wiring)}</dd>
        <dt>默认</dt><dd>${badge('关闭', '')} 需要显式开启才会改写模型输入</dd>
      </dl>`
    : `<div class="empty"><strong>输出模块不可用</strong>${esc(output.error || 'memory_output 未找到')}</div>`;

  view.innerHTML = `
    <div class="cards">
      ${kpi('控制台', `${esc(data.app.version)}<small>版</small>`, `${esc(data.app.python)} · ${esc(data.app.platform)}`)}
      ${kpi('模式', data.app.read_only ? '只读' : '可维护', data.app.read_only ? '已禁用写操作' : '仅本机，令牌保护')}
      ${kpi('启动时间', `<span style="font-size:15px">${esc(fmtTime(data.app.started_at))}</span>`, `运行 ${esc(fromNow(data.app.started_at))}`)}
      ${kpi('数据表', `${fmtNum(Object.values(data.tables || {}).reduce((sum, items) => sum + items.length, 0))}<small>张</small>`, '来自三个数据库')}
    </div>
    <div class="section-gap">
      ${panel('数据位置', table(['位置', '路径', '状态', { label: '大小', num: true }, '修改时间'], pathRows))}
    </div>
    <div class="section-gap">
      ${panel('输出模块', outputBlock, '记忆基座 → 本轮请求')}
    </div>
    <div class="section-gap">
      ${panel('数据库规模', tableBlocks || emptyBlock('未发现数据库', '指定 AstrBot 实例或插件数据目录后重试。'))}
    </div>
    <div class="section-gap">
      ${panel('基座插件配置', configBlock, config.available ? '敏感值已遮蔽' : '')}
    </div>
    <div class="section-gap">
      ${panel('启动方式', `<pre>cd dev
python -m dlm_console --instance "C:\\path\\to\\AstrBot"
# 或直接指定数据目录
python -m dlm_console --store-dir "…\\plugin_data\\astrbot_plugin_digital_memory_store" \\
                      --shadow-dir "…\\plugin_data\\astrbot_plugin_memory_shadow"</pre>`)}
    </div>`;
}

/* ------------------------------------------------------------------ 操作 */

const actions = {
  reload: () => location.reload(),

  'set-token': () => {
    const value = $('#token-input')?.value.trim() || '';
    if (!value) { toast('请先填写令牌', 'err'); return; }
    token = value;
    sessionStorage.setItem('dlm-token', value);
    boot();
  },

  'goto-observations': () => { location.hash = '#/observations'; },

  /* 记忆库 */
  'store-tab': (el) => { state.store.tab = el.dataset.tab; state.store.page = 1; renderStore(); },
  'store-page': (el) => { state.store.page = Number(el.dataset.pg); renderStore(); },
  'store-apply': () => {
    state.store.scope = $('#store-scope')?.value || '';
    state.store.status = $('#store-status')?.value || '';
    state.store.q = $('#store-q')?.value.trim() || '';
    state.store.page = 1;
    renderStore();
  },
  'store-reset': () => {
    state.store = { ...state.store, scope: '', status: '', q: '', page: 1 };
    renderStore();
  },
  'open-capture': (el) => openCapture(el.dataset.scope, Number(el.dataset.seq)),
  'delete-capture': (el) => deleteCapture(el.dataset.scope, Number(el.dataset.seq)),
  'delete-scope': (el) => deleteScope(el.dataset.scope || state.store.scope),

  /* 影子引擎 */
  'shadow-tab': (el) => { state.shadow.tab = el.dataset.tab; renderShadow(); },
  'shadow-page': (el) => { state.shadow.page = Number(el.dataset.pg); renderShadow(); },
  'shadow-apply': () => {
    state.shadow.scope = $('#shadow-scope')?.value || '';
    state.shadow.status = $('#shadow-status')?.value || '';
    state.shadow.q = $('#shadow-q')?.value.trim() || '';
    state.shadow.page = 1;
    renderShadow();
  },
  'shadow-reset': () => {
    state.shadow = { ...state.shadow, scope: '', status: '', q: '', page: 1 };
    renderShadow();
  },
  'open-event': (el) => openShadowEvent(el.dataset.id),

  /* 观察 */
  'obs-apply': () => {
    state.observations.days = Number($('#obs-days')?.value || 7);
    state.observations.scope = $('#obs-scope')?.value.trim() || '';
    state.observations.q = $('#obs-q')?.value.trim() || '';
    state.observations.page = 1;
    renderObservations();
  },
  'obs-page': (el) => { state.observations.page = Number(el.dataset.pg); renderObservations(); },
  'obs-reset': () => {
    state.observations = { ...state.observations, days: 7, scope: '', q: '', page: 1 };
    renderObservations();
  },

  /* 维护 */
  backup: async (el) => {
    const dataset = el.dataset.dataset || 'store';
    const result = await api('/maintenance/backup', { method: 'POST', body: { dataset } });
    toast(`备份完成：${result.name}（${fmtBytes(result.bytes)}）`, 'ok');
    await refreshCurrent();
  },
  'verify-backup': async (el) => {
    const dataset = el.dataset.dataset || state.maintenance.dataset;
    const name = el.dataset.name;
    const result = await api('/maintenance/verify_backup', {
      method: 'POST', body: { dataset, name },
    });
    const checkLabels = {
      sqlite_integrity: 'SQLite 完整性',
      required_tables: '必需表',
      foreign_keys: '外键一致性',
      capture_hashes: '原话内容哈希',
      evidence_owners: '证据归属',
    };
    const checks = Object.entries(result.checks || {}).map(([key, value]) => `
      <div class="list-item"><div class="meta"><span>${esc(checkLabels[key] || key)}</span>
        ${value ? badge('通过', 'ok') : badge('不通过', 'danger')}</div></div>`).join('');
    const counts = Object.entries(result.counts || {}).map(([key, value]) => `
      <div class="list-item"><div class="meta"><span>${esc(key)}</span><span>${fmtNum(value)}</span></div></div>`).join('');
    const problems = (result.errors || []).map((text) => `<div class="warn-item critical"><i class="dot"></i><span>${esc(text)}</span></div>`).join('');
    openDrawer('备份校验', `
      <dl class="kv">
        <dt>文件</dt><dd class="mono">${esc(result.name)}</dd>
        <dt>数据集</dt><dd>${esc(result.dataset)}</dd>
        <dt>大小</dt><dd>${fmtBytes(result.bytes)}</dd>
        <dt>时间</dt><dd>${esc(fmtTime(result.at))}</dd>
        <dt>结论</dt><dd>${result.ok ? badge('通过', 'ok') : badge('未通过', 'danger')}</dd>
      </dl>
      <h3 class="section-title">检查项</h3>
      <div class="list">${checks || '<span class="muted">无</span>'}</div>
      <h3 class="section-title">计数</h3>
      <div class="list">${counts || '<span class="muted">无</span>'}</div>
      ${problems ? `<h3 class="section-title">问题</h3><div class="warnings">${problems}</div>` : ''}`);
    toast(result.ok ? '备份校验通过' : '备份校验未通过，详情见右侧', result.ok ? 'ok' : 'err');
  },
  'restore-backup': async (el) => {
    const dataset = el.dataset.dataset || state.maintenance.dataset;
    const name = el.dataset.name;
    const answer = await confirmDialog({
      title: '恢复备份为副本',
      message: `将把 ${name} 复制到 restores/ 下的新文件，并再次校验。不会替换正在运行的数据库。`,
      okText: '恢复副本',
      danger: false,
    });
    if (!answer.ok) return;
    const result = await api('/maintenance/restore', {
      method: 'POST', body: { dataset, name },
    });
    const counts = Object.entries(result.counts || {}).map(([key, value]) => `
      <div class="list-item"><div class="meta"><span>${esc(key)}</span><span>${fmtNum(value)}</span></div></div>`).join('');
    await refreshCurrent();
    openDrawer('恢复完成', `
      <dl class="kv">
        <dt>来源</dt><dd class="mono">${esc(result.source)}</dd>
        <dt>目标</dt><dd class="mono">${esc(result.target)}</dd>
        <dt>大小</dt><dd>${fmtBytes(result.bytes)}</dd>
        <dt>结论</dt><dd>${badge('已校验', 'ok')}</dd>
      </dl>
      <h3 class="section-title">计数</h3>
      <div class="list">${counts || '<span class="muted">无</span>'}</div>
      <p class="muted small" style="margin-top:14px">${esc(result.note || '')}</p>`);
    toast('已恢复到 restores/ 新文件（未替换运行库）', 'ok');
  },
  rotate: async (el) => {
    const dataset = el.dataset.dataset || state.maintenance.dataset;
    const keep = Number($('#rotate-keep')?.value || 7);
    const answer = await confirmDialog({
      title: '轮转备份',
      message: `将保留 ${dataset} 最近 ${keep} 份备份（memory_* 与 console_*），删除其余文件。`,
      okText: '开始轮转',
      danger: true,
    });
    if (!answer.ok) return;
    const result = await api('/maintenance/rotate', { method: 'POST', body: { dataset, keep } });
    toast(`轮转完成：保留 ${result.kept} 份，删除 ${result.removed.length} 份`, 'ok');
    await refreshCurrent();
  },
  checkpoint: async (el) => {
    const dataset = el.dataset.dataset || 'store';
    const result = await api('/maintenance/checkpoint', { method: 'POST', body: { dataset } });
    toast(result.busy ? 'checkpoint 有未完成页（数据库正忙），已尽量截断 WAL' : 'WAL checkpoint 完成', result.busy ? 'err' : 'ok');
    await refreshCurrent();
  },
  integrity: async (el) => {
    const dataset = el.dataset.dataset || 'store';
    const result = await api('/maintenance/integrity', { method: 'POST', body: { dataset } });
    toast(result.ok ? '完整性检查通过（ok）' : `完整性检查发现问题：${result.result.join('; ')}`, result.ok ? 'ok' : 'err');
  },
  vacuum: async (el) => {
    const dataset = el.dataset.dataset || 'store';
    const answer = await confirmDialog({
      title: 'VACUUM',
      message: 'VACUUM 会重建数据库文件，可能耗时并需要临时磁盘空间；运行期间请避免重启 AstrBot。',
      okText: '开始 VACUUM',
      danger: true,
    });
    if (!answer.ok) return;
    const result = await api('/maintenance/vacuum', { method: 'POST', body: { dataset } });
    toast(`VACUUM 完成：回收 ${fmtBytes(result.saved_bytes)}`, 'ok');
    await refreshCurrent();
  },
  'dataset-select': () => {
    state.maintenance.dataset = $('#backup-dataset')?.value || 'store';
    renderMaintenance();
  },
  'archive-preview': async () => {
    state.maintenance.kind = $('#archive-kind')?.value || 'captures';
    state.maintenance.months = Number($('#archive-months')?.value || 12);
    const result = await api('/maintenance/archive', {
      method: 'POST',
      body: { months: state.maintenance.months, export: false, kind: state.maintenance.kind },
    });
    $('#archive-result').innerHTML = `<div class="list-item" style="margin-top:12px">
      <div class="meta"><span>预览</span><span>${esc(result.cutoff)} 之前</span></div>
      共 ${fmtNum(result.count)} 条符合条件的${result.kind === 'events' ? '事件' : '原话'}。</div>`;
  },
  'archive-export': async () => {
    state.maintenance.kind = $('#archive-kind')?.value || 'captures';
    state.maintenance.months = Number($('#archive-months')?.value || 12);
    const answer = await confirmDialog({
      title: '导出归档',
      message: `把 ${state.maintenance.months} 个月之前的${state.maintenance.kind === 'events' ? '事件' : '原话'}导出为 JSONL；不会删除主库数据。`,
      okText: '导出',
      danger: false,
    });
    if (!answer.ok) return;
    const result = await api('/maintenance/archive', {
      method: 'POST',
      body: { months: state.maintenance.months, export: true, kind: state.maintenance.kind },
    });
    $('#archive-result').innerHTML = `<div class="list-item" style="margin-top:12px">
      <div class="meta"><span>已导出 ${fmtNum(result.count)} 条</span><span>${esc(fmtBytes(result.bytes))}</span></div>
      <div class="mono small">${esc(result.path)}</div></div>`;
    toast(`已导出 ${result.count} 条到 archives/`, 'ok');
    await renderMaintenance();
  },
};

async function refreshCurrent() {
  if (PAGES[current]) await PAGES[current].render();
}

/* --------------------------------------------------------------- 事件绑定 */

view.addEventListener('click', async (event) => {
  const el = event.target.closest('[data-act]');
  if (!el) return;
  const action = actions[el.dataset.act];
  if (!action) return;
  if (el.tagName === 'BUTTON' && el.disabled) return;
  const busy = el.tagName === 'BUTTON';
  if (busy) el.disabled = true;
  try {
    await action(el);
  } catch (error) {
    if (error.status === 401) {
      renderTokenGate(error);
    } else {
      toast(error.message || String(error), 'err');
    }
  } finally {
    if (busy) el.disabled = false;
  }
});

view.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  const target = event.target.closest('[data-enter]');
  if (!target) return;
  const action = actions[target.dataset.enter];
  if (action) { event.preventDefault(); action(target); }
});

document.getElementById('refresh').addEventListener('click', async () => {
  try {
    await refreshCurrent();
    toast('已刷新', 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
});

window.addEventListener('hashchange', navigate);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeDrawer();
});

boot();
