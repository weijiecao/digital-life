const bridge = window.AstrBotPluginPage || window.AstrBotPluginView;
const output = document.getElementById('output');

function show(value) {
  output.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

async function callGet(endpoint, params) {
  try {
    const data = await bridge.apiGet(endpoint, params);
    show(data);
    return data;
  } catch (error) {
    show({ error: String(error) });
    return null;
  }
}

async function callPost(endpoint, body) {
  try {
    const data = await bridge.apiPost(endpoint, body);
    show(data);
    return data;
  } catch (error) {
    show({ error: String(error) });
    return null;
  }
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"]/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[ch]));
}

function cards(data) {
  const db = data.db || {};
  const items = [
    ['数据库', `${db.mb ?? 0} MB`, `WAL ${db.wal_mb ?? 0} MB`],
    ['原话', `${data.captures ?? 0}`, `待审 ${data.pending ?? 0}`],
    ['日均（7天）', `${data.daily_7d ?? 0} 条`, `每条 ${data.per_message_bytes ?? 0} B`],
    ['备份', `${(data.backups || {}).count ?? 0} 份`, '7 日 + 4 周建议'],
    ['磁盘剩余', `${(data.disk || {}).free_gb ?? '-'} GB`, `总 ${(data.disk || {}).total_gb ?? '-'} GB`],
    ['钩子 P95', data.latency && data.latency.p95 != null ? `${data.latency.p95} ms` : '无数据',
      data.latency && data.latency.turns ? `${data.latency.turns} 轮` : ''],
  ];
  document.getElementById('cards').innerHTML = items.map(([title, value, sub]) => `
    <div class="card"><div class="card-title">${esc(title)}</div>
    <div class="card-value">${esc(value)}</div><div class="card-sub">${esc(sub)}</div></div>`).join('');
}

function warnings(data) {
  const list = data.warnings || [];
  const box = document.getElementById('warnings');
  box.innerHTML = list.length
    ? `<div class="warn-title">需要注意</div><ul>${list.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`
    : '<div class="ok">状态正常，没有触发阈值。</div>';
}

function projection(data) {
  const rows = Object.entries(data.projections_5y || {});
  document.querySelector('#projection tbody').innerHTML = rows.map(([rate, item]) => `
    <tr><td>${esc(rate)}</td><td>${esc(item.db_gb)} GB</td><td>${esc(item.with_backups_gb)} GB</td></tr>`).join('');
}

function backups(data) {
  const items = (data.backups || {}).items || [];
  document.querySelector('#backups tbody').innerHTML = items.map((item) => `
    <tr><td>${esc(item.name)}</td><td>${(item.bytes / 1024 / 1024).toFixed(2)} MB</td><td>${esc(item.at)}</td></tr>`).join('');
}

async function refresh() {
  const data = await callGet('panel/stats');
  if (!data) return;
  cards(data);
  warnings(data);
  projection(data);
  backups(data);
}

document.getElementById('refresh').addEventListener('click', refresh);

document.querySelectorAll('[data-action]').forEach((button) => {
  button.addEventListener('click', async () => {
    const action = button.dataset.action;
    if (action === 'backup') {
      await callPost('panel/backup', {});
    } else if (action === 'rotate') {
      const keep = Number(document.getElementById('keep').value || 7);
      await callPost('panel/rotate', { keep });
    } else if (action === 'checkpoint') {
      await callPost('panel/checkpoint', {});
    } else if (action === 'integrity') {
      await callPost('panel/integrity', {});
    } else if (action === 'vacuum') {
      if (!window.confirm('VACUUM 可能耗时并需要临时空间，确定继续？')) return;
      await callPost('panel/vacuum', {});
    } else if (action === 'archive-preview') {
      const months = Number(document.getElementById('months').value || 12);
      await callPost('panel/archive', { months, export: false });
    } else if (action === 'archive-export') {
      const months = Number(document.getElementById('months').value || 12);
      await callPost('panel/archive', { months, export: true });
    }
    await refresh();
  });
});

if (bridge) {
  await bridge.ready();
  await refresh();
} else {
  show('bridge 未加载：请从 AstrBot 插件页打开此视图。');
}
