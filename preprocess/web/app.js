'use strict';

/* ------------------------------------------------------------------ helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const STATUS = {
  queued: '排队中',
  running: '运行中',
  done: '已完成',
  cancelled: '已取消',
  error: '失败',
  interrupted: '已中断',
};
const ROW_STATUS = { ok: '通过', review: '复核', not_found: '未找到', error: '错误' };
const ACTIVE = new Set(['queued', 'running']);

const ICONS = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
  history: '<svg viewBox="0 0 24 24"><path d="M3 6h18M3 12h18M3 18h12"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/></svg>',
  left: '<svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>',
  right: '<svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>',
  external: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v3h16v-3"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13M10 11v6M14 11v6"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  image: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 15-5-5-9 9"/></svg>',
  alert: '<svg viewBox="0 0 24 24"><path d="M12 3 2 21h20z"/><path d="M12 9v5M12 17h.01"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
};

const state = {
  meta: null,
  form: null,
  runs: [],
  cuts: [],
  cutImages: [],
  cutSelected: new Set(),
  cutDraft: null,
  pollTimer: null,
  currentRunId: null,
  currentRunMeta: null,
  filters: { status: 'all', q: '', sort: 'path', page: 1, page_size: 48 },
  lastRows: [],
  lastData: null,
  detailIndex: 0,
  detailKey: null,
  modalOnClose: null,
  pendingRerun: null,
};

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(digits);
}

function fmtDuration(sec) {
  sec = Number(sec);
  if (!Number.isFinite(sec) || sec <= 0) return '—';
  if (sec < 60) return `${sec.toFixed(1)} s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  if (m < 60) return `${m}m ${s}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function basename(p) {
  const s = String(p || '');
  const parts = s.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : s;
}

function shortPath(p, max = 62) {
  const s = String(p || '');
  return s.length > max ? `${s.slice(0, 26)}…${s.slice(-Math.max(8, max - 27))}` : s;
}

function normCasePath(p) {
  return String(p || '').replace(/\//g, '\\').toLowerCase();
}

function pct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(0)}%` : '—';
}

function statusBadge(status, kind = 'run') {
  const label = kind === 'row' ? (ROW_STATUS[status] || status) : (STATUS[status] || status);
  const pulse = status === 'running' ? '<span class="pulse"></span>' : '';
  return `<span class="badge st-${esc(status)}">${pulse}${esc(label)}</span>`;
}

function toast(message, type = 'info') {
  const root = $('#toast-root');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity .2s';
    setTimeout(() => el.remove(), 220);
  }, 3600);
}

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (options.body) init.headers['Content-Type'] = 'application/json';
  const res = await fetch(path, init);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = {}; }
  if (!res.ok) throw new Error(data.error || `请求失败（HTTP ${res.status}）`);
  return data;
}

function cleanupPoll() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

/* --------------------------------------------------------------- form state */
function defaultForm() {
  const d = (state.meta && state.meta.defaults) || {};
  return {
    inputs: [],
    excluded: [],
    name: '',
    output_root: '',
    select: d.select || 'largest',
    feature: d.feature || 'green_excess',
    out_size: d.out_size || 512,
    margin: d.margin || 1.35,
    edge_level: d.edge_level || 0.5,
    workers: d.workers || 4,
    rays: d.rays || 720,
    smooth_sigma: d.smooth_sigma || 1.2,
    threshold_mode: d.threshold_mode || 'otsu',
    min_area_frac: d.min_area_frac || 0.0006,
    min_snr: d.min_snr || 3,
    min_contrast: d.min_contrast || 0.01,
    max_edge_width_frac: d.max_edge_width_frac || 0.15,
    save_raw: d.save_raw !== false,
    save_polar: !!d.save_polar,
    stretch: !!d.stretch,
  };
}

function formFromRun(run) {
  const f = defaultForm();
  f.inputs = Array.isArray(run.inputs) ? [...run.inputs] : [];
  f.excluded = Array.isArray(run.exclude) ? [...run.exclude] : [];
  f.output_root = run.output_parent || '';
  const cfg = run.config || {};
  for (const key of Object.keys(f)) {
    if (key in cfg && cfg[key] !== null) f[key] = cfg[key];
  }
  f.name = '';
  return f;
}

function loadSavedForm() {
  try {
    const raw = localStorage.getItem('wellprep.form.v1');
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed.inputs)) parsed.inputs = [];
    if (!Array.isArray(parsed.excluded)) parsed.excluded = [];
    return { ...defaultForm(), ...parsed };
  } catch {
    return null;
  }
}

function saveForm() {
  try {
    localStorage.setItem('wellprep.form.v1', JSON.stringify(state.form));
  } catch { /* ignore quota */ }
}

function parseInputs(text) {
  const out = [];
  for (const line of String(text || '').split(/\r?\n/)) {
    const value = line.trim();
    if (value && !out.includes(value)) out.push(value);
  }
  return out;
}

/* --------------------------------------------------------------------- init */
document.addEventListener('DOMContentLoaded', init);
window.addEventListener('hashchange', () => route());

async function init() {
  try {
    state.meta = await api('/api/state');
    const line = $('#server-line');
    if (line) line.textContent = `本地服务 · v${state.meta.version}`;
    bindTree();
    await refreshRuns();
    await refreshCuts();
    state.form = state.pendingRerun ? formFromRun(state.pendingRerun) : (loadSavedForm() || defaultForm());
    state.pendingRerun = null;
    if (!location.hash) history.replaceState(null, '', '#/new');
    await route();
  } catch (err) {
    const view = $('#view');
    view.innerHTML = `
      <div class="card empty">
        <div class="big">${ICONS.alert}</div>
        <h3>无法连接本地服务</h3>
        <p>${esc(err.message)}</p>
        <button class="btn primary" onclick="location.reload()">重新加载</button>
      </div>`;
  }
}

async function refreshRuns() {
  const data = await api('/api/runs');
  state.runs = data.runs || [];
  const count = $('#nav-run-count');
  if (count) count.textContent = state.runs.length ? String(state.runs.length) : '';
  return state.runs;
}

async function refreshCuts() {
  try {
    const data = await api('/api/cuts');
    state.cuts = data.cuts || [];
  } catch {
    state.cuts = [];
  }
  const count = $('#nav-cut-count');
  if (count) count.textContent = state.cuts.length ? String(state.cuts.length) : '';
  return state.cuts;
}

function bindTree() {
  $$('.tree-head').forEach((btn) => {
    btn.addEventListener('click', () => btn.parentElement.classList.toggle('open'));
  });
}

function setNav(name) {
  $$('.tree-body a').forEach((a) => a.classList.toggle('active', a.dataset.nav === name));
  const active = $(`.tree-body a[data-nav="${name}"]`);
  if (active) {
    const group = active.closest('.tree-group');
    if (group) group.classList.add('open');
  }
}

async function route() {
  cleanupPoll();
  closeModal();
  if (!state.meta) return;
  const hash = (location.hash || '#/new').replace(/^#/, '');
  try {
    const m = hash.match(/^\/runs\/([^/]+)$/);
    const cm = hash.match(/^\/cut\/([^/]+)$/);
    if (m) {
      setNav('runs');
      await renderRun(decodeURIComponent(m[1]));
    } else if (hash === '/runs') {
      setNav('runs');
      await renderHistory();
    } else if (cm) {
      setNav('cuts');
      await renderCutResult(decodeURIComponent(cm[1]));
    } else if (hash === '/cut') {
      setNav('cut');
      await renderCutNew();
    } else if (hash === '/cuts') {
      setNav('cuts');
      await renderCutHistory();
    } else {
      setNav('new');
      await renderNew();
    }
  } catch (err) {
    renderError(err.message);
  }
}

function navigate(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

/* ------------------------------------------------------------------ new run */
async function renderNew() {
  if (!state.form) state.form = defaultForm();
  if (state.pendingRerun) {
    state.form = formFromRun(state.pendingRerun);
    state.pendingRerun = null;
    saveForm();
  }
  const f = state.form;
  const view = $('#view');
  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>新建预处理运行</h1>
      </div>
      <div class="head-actions">
        <a class="btn" href="#/runs">${ICONS.history} 运行记录</a>
      </div>
    </div>

    <div class="new-layout">
      <section class="card form-card">
        <h2>1 · 选择图片</h2>
        <div class="field path-wrap">
          <textarea id="paths" class="input" spellcheck="false"
            placeholder="D:\\实验图片\\26-3-662.jpg&#10;D:\\实验图片\\第二组">${esc(f.inputs.join('\n'))}</textarea>
          <button class="btn" id="browse">${ICONS.folder} 浏览…</button>
        </div>
        <div class="path-chips" id="path-chips"></div>
        <div class="scan-line">
          <button class="btn" id="scan">${ICONS.search} 扫描图片</button>
          <span class="scan-result" id="scan-result">尚未扫描</span>
          <span class="scan-result" id="excluded-note"></span>
        </div>

        <div class="field">
          <label class="lbl" for="output-root">结果输出文件夹</label>
          <div class="path-wrap">
            <input class="input" id="output-root" spellcheck="false"
              value="${esc(f.output_root || '')}"
              placeholder="${esc((state.meta && state.meta.default_output) || 'preprocess\\runs')}">
            <button class="btn" id="output-browse">${ICONS.folder} 浏览…</button>
            <button class="btn" id="output-default">默认</button>
          </div>
          <div class="hint" id="output-preview" style="margin-top:6px"></div>
        </div>

        <div class="grid-2">
          <div class="field">
            <label class="lbl" for="run-name">运行名称</label>
            <input class="input" id="run-name" value="${esc(f.name)}" placeholder="例如：乙醇组-第 3 批">
          </div>
          <div class="field">
            <label class="lbl" for="select">目标选择</label>
            <select class="select" id="select">
              <option value="largest" ${f.select === 'largest' ? 'selected' : ''}>最大绿色圆</option>
              <option value="brightest" ${f.select === 'brightest' ? 'selected' : ''}>最亮绿色圆</option>
              <option value="all" ${f.select === 'all' ? 'selected' : ''}>全部圆</option>
            </select>
          </div>
        </div>

        <div class="divider"></div>
        <h2>2 · 输出几何</h2>
        <div class="grid-3">
          <div class="field">
            <label class="lbl" for="out-size">归一化边长</label>
            <select class="select" id="out-size">
              ${[256, 384, 512, 768, 1024].map((v) => `<option value="${v}" ${Number(f.out_size) === v ? 'selected' : ''}>${v} × ${v}</option>`).join('')}
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="margin">留边倍数 margin</label>
            <input class="input" id="margin" type="number" step="0.05" min="1.05" max="2.5" value="${esc(f.margin)}">
          </div>
          <div class="field">
            <label class="lbl" for="edge-level">边缘电平</label>
            <select class="select" id="edge-level">
              ${[[0.3, '0.30'], [0.4, '0.40'], [0.5, '0.50'], [0.6, '0.60'], [0.7, '0.70']].map(([v, l]) => `<option value="${v}" ${Math.abs(Number(f.edge_level) - v) < 0.001 ? 'selected' : ''}>${l}</option>`).join('')}
            </select>
          </div>
        </div>

        <div class="divider"></div>
        <h2>3 · 计算选项</h2>
        <div class="grid-3">
          <div class="field">
            <label class="lbl" for="feature">前景特征</label>
            <select class="select" id="feature">
              <option value="green_excess" ${f.feature === 'green_excess' ? 'selected' : ''}>绿色优势 G-(R+B)/2</option>
              <option value="gray" ${f.feature === 'gray' ? 'selected' : ''}>灰度</option>
              <option value="max_rgb" ${f.feature === 'max_rgb' ? 'selected' : ''}>RGB 最大通道</option>
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="workers">并行进程</label>
            <input class="input" id="workers" type="number" step="1" min="1" max="16" value="${esc(f.workers)}">
          </div>
          <div class="field">
            <label class="lbl" for="min-snr">SNR 复核阈值</label>
            <input class="input" id="min-snr" type="number" step="0.5" min="0" value="${esc(f.min_snr)}">
          </div>
        </div>
        <div class="grid-2" style="margin-bottom:14px">
          <label class="check"><input type="checkbox" id="save-raw" ${f.save_raw ? 'checked' : ''}> 保存原始分辨率方形裁剪（roi_raw）</label>
          <label class="check"><input type="checkbox" id="polar" ${f.save_polar ? 'checked' : ''}> 额外输出极坐标展开图（polar）</label>
        </div>

        <details class="advanced">
          <summary>高级参数</summary>
          <div class="advanced-body">
            <div class="grid-3">
              <div class="field">
                <label class="lbl" for="rays">射线数 rays</label>
                <input class="input" id="rays" type="number" step="36" min="72" max="2880" value="${esc(f.rays)}">
              </div>
              <div class="field">
                <label class="lbl" for="smooth-sigma">平滑 sigma（px）</label>
                <input class="input" id="smooth-sigma" type="number" step="0.1" min="0" max="10" value="${esc(f.smooth_sigma)}">
              </div>
              <div class="field">
                <label class="lbl" for="threshold-mode">粗分割阈值</label>
                <select class="select" id="threshold-mode">
                  <option value="otsu" ${f.threshold_mode === 'otsu' ? 'selected' : ''}>Otsu 自适应</option>
                  <option value="percentile" ${f.threshold_mode === 'percentile' ? 'selected' : ''}>高分位</option>
                </select>
              </div>
              <div class="field">
                <label class="lbl" for="min-area-frac">最小连通域占比</label>
                <input class="input" id="min-area-frac" type="number" step="0.0001" min="0.00001" max="0.2" value="${esc(f.min_area_frac)}">
              </div>
              <div class="field">
                <label class="lbl" for="min-contrast">最小对比度</label>
                <input class="input" id="min-contrast" type="number" step="0.005" min="0" max="0.5" value="${esc(f.min_contrast)}">
              </div>
              <div class="field">
                <label class="lbl" for="max-edge-width">边缘软度阈值</label>
                <input class="input" id="max-edge-width" type="number" step="0.01" min="0.02" max="1" value="${esc(f.max_edge_width_frac)}">
              </div>
              <label class="check" style="align-self:end;padding:10px 0">
                <input type="checkbox" id="stretch" ${f.stretch ? 'checked' : ''}> 预览按 99.5% 分位拉伸
              </label>
            </div>
          </div>
        </details>

        <div class="start-row">
          <button class="btn primary lg" id="start">${ICONS.play} 开始运行</button>
          <button class="btn lg" id="preview-btn">${ICONS.image} 预览 / 调参</button>
        </div>
      </section>

      <aside class="card preview-card">
        <div class="viz"></div>
        <div class="preview-foot" id="preview-foot"></div>
      </aside>
    </div>`;

  bindNewView();
}

function bindNewView() {
  const f = state.form;
  const ta = $('#paths');
  const renderChips = () => {
    const box = $('#path-chips');
    box.innerHTML = f.inputs.map((p, i) => `
      <span class="chip" title="${esc(p)}">
        <span>${esc(shortPath(p))}</span>
        <button type="button" data-remove="${i}" aria-label="移除">×</button>
      </span>`).join('');
  };
  const syncFromText = () => {
    f.inputs = parseInputs(ta.value);
    renderChips();
    saveForm();
  };
  ta.addEventListener('input', syncFromText);
  renderChips();

  $('#path-chips').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-remove]');
    if (!btn) return;
    const idx = Number(btn.dataset.remove);
    f.inputs.splice(idx, 1);
    ta.value = f.inputs.join('\n');
    renderChips();
    saveForm();
  });

  const addPath = (path) => {
    if (!path) return;
    if (!f.inputs.includes(path)) f.inputs.push(path);
    ta.value = f.inputs.join('\n');
    renderChips();
    saveForm();
  };

  $('#browse').addEventListener('click', () => openFolderPicker(addPath, f.inputs[f.inputs.length - 1] || ''));

  const outInput = $('#output-root');
  const updateOutput = () => {
    f.output_root = outInput.value.trim();
    const root = f.output_root || (state.meta.default_output || '');
    $('#output-preview').innerHTML = `输出：<code>${esc(root)}${root.endsWith('\\') ? '' : '\\'}&lt;运行编号&gt;</code>`;
    saveForm();
  };
  outInput.addEventListener('input', updateOutput);
  $('#output-browse').addEventListener('click', () => {
    openFolderPicker((path) => {
      outInput.value = path;
      updateOutput();
    }, f.output_root);
  });
  $('#output-default').addEventListener('click', () => {
    outInput.value = '';
    f.output_root = '';
    updateOutput();
  });
  updateOutput();

  $('#preview-btn').addEventListener('click', openPreview);

  const renderExcluded = () => {
    const note = $('#excluded-note');
    if (!note) return;
    if (Array.isArray(f.excluded) && f.excluded.length) {
      note.innerHTML = `已排除 ${f.excluded.length} 张 <button class="btn sm" id="clear-excluded" type="button">清除</button>`;
      const btn = $('#clear-excluded');
      if (btn) {
        btn.onclick = () => {
          f.excluded = [];
          saveForm();
          renderExcluded();
          toast('已清除排除列表，所有图片会重新纳入', 'ok');
        };
      }
    } else {
      note.textContent = '';
    }
  };
  renderExcluded();

  const bindField = (id, key, cast = (v) => v) => {
    const el = $(id);
    if (!el) return;
    const handler = () => {
      f[key] = el.type === 'checkbox' ? el.checked : cast(el.value);
      saveForm();
    };
    el.addEventListener(el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input', handler);
  };
  bindField('#run-name', 'name', String);
  bindField('#select', 'select', String);
  bindField('#feature', 'feature', String);
  bindField('#out-size', 'out_size', Number);
  bindField('#margin', 'margin', Number);
  bindField('#edge-level', 'edge_level', Number);
  bindField('#workers', 'workers', Number);
  bindField('#min-snr', 'min_snr', Number);
  bindField('#rays', 'rays', Number);
  bindField('#smooth-sigma', 'smooth_sigma', Number);
  bindField('#threshold-mode', 'threshold_mode', String);
  bindField('#min-area-frac', 'min_area_frac', Number);
  bindField('#min-contrast', 'min_contrast', Number);
  bindField('#max-edge-width', 'max_edge_width_frac', Number);
  bindField('#save-raw', 'save_raw');
  bindField('#polar', 'save_polar');
  bindField('#stretch', 'stretch');

  $('#scan').addEventListener('click', scanInputs);
  $('#start').addEventListener('click', startRun);

  const foot = $('#preview-foot');
  if (foot && f.out_size && f.margin) {
    foot.textContent = `${f.out_size}×${f.out_size} · 直径 ${Math.round(Number(f.out_size) / Number(f.margin))} px`;
  }
}

async function scanInputs() {
  const f = state.form;
  f.inputs = parseInputs($('#paths').value);
  $('#paths').value = f.inputs.join('\n');
  const out = $('#scan-result');
  if (!f.inputs.length) {
    out.className = 'scan-result bad';
    out.textContent = '请先添加路径';
    return;
  }
  const btn = $('#scan');
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 扫描中…';
  out.className = 'scan-result';
  out.textContent = '正在遍历…';
  try {
    const res = await api('/api/scan', { method: 'POST', body: JSON.stringify({ inputs: f.inputs }) });
    const excluded = new Set((Array.isArray(f.excluded) ? f.excluded : []).map(normCasePath));
    const available = Array.isArray(res.items)
      ? res.items.filter((item) => !excluded.has(normCasePath(item.path))).length
      : res.count;
    if (available > 0) {
      const exts = Object.entries(res.by_ext || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
      out.className = 'scan-result ok';
      out.textContent = `找到 ${available} 张可用图片${exts ? `（${exts}）` : ''}${res.count > available ? `，已排除 ${res.count - available} 张` : ''}`;
    } else {
      out.className = 'scan-result bad';
      out.textContent = res.count ? '图片已全部被排除' : '没有找到图片，请检查路径';
    }
    if (res.truncated) out.textContent += '；列表过长，预览只显示前 5000 张';
    if (res.missing && res.missing.length) {
      out.className = 'scan-result bad';
      out.textContent += `；${res.missing.length} 个路径不存在`;
    }
    saveForm();
  } catch (err) {
    out.className = 'scan-result bad';
    out.textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

function formConfig() {
  const f = state.form;
  return {
    select: f.select,
    feature: f.feature,
    out_size: Number(f.out_size),
    margin: Number(f.margin),
    edge_level: Number(f.edge_level),
    workers: Number(f.workers),
    rays: Number(f.rays),
    smooth_sigma: Number(f.smooth_sigma),
    threshold_mode: f.threshold_mode,
    min_area_frac: Number(f.min_area_frac),
    min_snr: Number(f.min_snr),
    min_contrast: Number(f.min_contrast),
    max_edge_width_frac: Number(f.max_edge_width_frac),
    save_raw: !!f.save_raw,
    save_polar: !!f.save_polar,
    stretch: !!f.stretch,
  };
}

async function startRun() {
  const f = state.form;
  f.inputs = parseInputs($('#paths').value);
  f.name = $('#run-name').value.trim();
  f.output_root = $('#output-root').value.trim();
  if (!f.inputs.length) {
    toast('请先添加图片文件或文件夹', 'bad');
    return;
  }
  const cfg = formConfig();
  if (!Number.isFinite(cfg.out_size) || cfg.out_size < 32) {
    toast('归一化边长无效', 'bad');
    return;
  }
  if (!Number.isFinite(cfg.margin) || cfg.margin < 1.05) {
    toast('margin 必须 ≥ 1.05', 'bad');
    return;
  }
  saveForm();
  const btn = $('#start');
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> 正在启动…';
  try {
    const res = await api('/api/runs', {
      method: 'POST',
      body: JSON.stringify({
        name: f.name,
        inputs: f.inputs,
        exclude: f.excluded,
        output_root: f.output_root,
        config: cfg,
      }),
    });
    toast('运行已开始', 'ok');
    await refreshRuns();
    navigate(`#/runs/${res.run.id}`);
  } catch (err) {
    toast(err.message, 'bad');
  } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

/* --------------------------------------------------------------- preview */
function confirmDeleteImage(row) {
  const runId = state.currentRunId;
  if (!runId) return;
  const name = basename(row.rel_path) || row.file || '这张图片';
  showModal(`
    <div class="modal-head">
      <div>
        <h2>删除图片结果</h2>
        <div class="sub">${esc(name)}</div>
      </div>
      <button class="icon-btn close" id="dimg-x" aria-label="关闭">${ICONS.x}</button>
    </div>
    <div class="modal-body">
      <div class="path-list" style="margin-bottom:12px">${esc(row.file || row.rel_path || '')}</div>
      <label class="check"><input type="checkbox" id="dimg-outputs" checked> 同时删除该图片的输出文件</label>
      <label class="check" style="margin-top:8px"><input type="checkbox" id="dimg-source"> 同时删除源图片文件</label>
    </div>
    <div class="modal-foot">
      <button class="btn" id="dimg-cancel">取消</button>
      <div style="flex:1"></div>
      <button class="btn danger" id="dimg-confirm">${ICONS.trash} 删除结果</button>
    </div>`, { wide: false });

  $('#dimg-x').onclick = closeModal;
  $('#dimg-cancel').onclick = closeModal;
  $('#dimg-confirm').onclick = async () => {
    const btn = $('#dimg-confirm');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 删除中…';
    try {
      const res = await api(`/api/runs/${encodeURIComponent(runId)}/delete-image`, {
        method: 'POST',
        body: JSON.stringify({
          file: row.file,
          rel_path: row.rel_path,
          delete_outputs: $('#dimg-outputs').checked,
          delete_source: $('#dimg-source').checked,
        }),
      });
      closeModal();
      toast(`已删除 ${res.deleted} 条结果`, 'ok');
      if (res.source_error) toast(res.source_error, 'bad');
      await refreshResults();
    } catch (err) {
      toast(err.message, 'bad');
      btn.disabled = false;
      btn.innerHTML = '删除结果';
    }
  };
}

function openPreview() {
  const f = state.form;
  f.inputs = parseInputs($('#paths').value);
  $('#paths').value = f.inputs.join('\n');
  if (!f.inputs.length) {
    toast('请先添加图片文件或文件夹', 'bad');
    return;
  }
  const snapshot = JSON.parse(JSON.stringify(f));
  let applied = false;
  let token = '';
  let tab = 'overlay';
  let seq = 0;
  let timer = null;
  let last = null;
  let queue = [];
  let index = 0;
  let sample = '';

  showModal(`
    <div class="modal-head">
      <div style="min-width:0">
        <h2>预览与调参</h2>
        <div class="sub" id="pv-sample">正在扫描图片…</div>
      </div>
      <button class="btn sm danger" id="pv-delete" disabled>删除当前图片</button>
      <button class="icon-btn close" id="pv-x" aria-label="关闭">${ICONS.x}</button>
    </div>
    <div class="modal-body">
      <div class="preview-grid">
        <div class="preview-stage">
          <div class="preview-tabs">
            <button data-tab="overlay" class="active">叠加图</button>
            <button data-tab="roi">归一化 ROI</button>
            <span class="preview-nav">
              <button id="pv-prev" title="上一张" disabled>${ICONS.left}</button>
              <span id="pv-index">– / –</span>
              <button id="pv-next" title="下一张" disabled>${ICONS.right}</button>
            </span>
          </div>
          <div class="preview-frame" id="pv-frame">
            <img id="pv-img" alt="预览">
            <div class="preview-loading" id="pv-loading"><span class="spinner"></span></div>
          </div>
        </div>
        <div class="preview-controls">
          <div class="preview-metrics" id="pv-metrics">
            <div class="preview-metric" style="grid-column:1/-1"><div class="k">状态</div><div class="v">分析中…</div></div>
          </div>
          <div class="field">
            <div class="range-head"><label class="lbl" for="pv-margin">留边 margin</label><b id="pv-margin-val">${Number(f.margin).toFixed(2)}</b></div>
            <input type="range" id="pv-margin" min="1.05" max="2.5" step="0.05" value="${Number(f.margin)}">
            <div class="hint">直径 <b id="pv-diam">${Math.round(Number(f.out_size) / Number(f.margin))}</b> px</div>
          </div>
          <div class="field">
            <label class="lbl" for="pv-size">归一化边长</label>
            <select class="select" id="pv-size">
              ${[256, 384, 512, 768, 1024].map((v) => `<option value="${v}" ${Number(f.out_size) === v ? 'selected' : ''}>${v} × ${v}</option>`).join('')}
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="pv-edge">边缘电平</label>
            <select class="select" id="pv-edge">
              ${[[0.3, '0.30'], [0.4, '0.40'], [0.5, '0.50'], [0.6, '0.60'], [0.7, '0.70']].map(([v, l]) => `<option value="${v}" ${Math.abs(Number(f.edge_level) - v) < 0.001 ? 'selected' : ''}>${l}</option>`).join('')}
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="pv-select">目标选择</label>
            <select class="select" id="pv-select">
              <option value="largest" ${f.select === 'largest' ? 'selected' : ''}>最大绿色圆</option>
              <option value="brightest" ${f.select === 'brightest' ? 'selected' : ''}>最亮绿色圆</option>
              <option value="all" ${f.select === 'all' ? 'selected' : ''}>全部圆</option>
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="pv-feature">前景特征</label>
            <select class="select" id="pv-feature">
              <option value="green_excess" ${f.feature === 'green_excess' ? 'selected' : ''}>绿色优势 G-(R+B)/2</option>
              <option value="gray" ${f.feature === 'gray' ? 'selected' : ''}>灰度</option>
              <option value="max_rgb" ${f.feature === 'max_rgb' ? 'selected' : ''}>RGB 最大通道</option>
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="pv-min-snr">SNR 复核阈值</label>
            <input class="input" id="pv-min-snr" type="number" min="0" max="100" step="0.5" value="${Number(f.min_snr)}">
          </div>
          <div class="field">
            <label class="lbl" for="pv-edge-width">边缘软度阈值</label>
            <input class="input" id="pv-edge-width" type="number" min="0.02" max="1" step="0.01" value="${Number(f.max_edge_width_frac)}">
          </div>
        </div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn sm" id="pv-reset">恢复本次打开时参数</button>
      <div style="flex:1"></div>
      <button class="btn" id="pv-cancel">取消</button>
      <button class="btn" id="pv-apply">应用参数</button>
      <button class="btn primary" id="pv-run">${ICONS.play} 应用并开始运行</button>
    </div>`, {
    wide: true,
    onClose: () => {
      if (applied) return;
      for (const key of Object.keys(snapshot)) f[key] = snapshot[key];
    },
  });

  const setBusy = (busy) => {
    const el = $('#pv-loading');
    if (el) el.classList.toggle('hidden', !busy);
  };

  const updateLabels = () => {
    const margin = Number(f.margin);
    const size = Number(f.out_size);
    $('#pv-margin-val').textContent = margin.toFixed(2);
    $('#pv-diam').textContent = String(Math.round(size / margin));
  };

  const renderMetrics = (res) => {
    const box = $('#pv-metrics');
    if (!box) return;
    if (!res.fit) {
      box.innerHTML = `<div class="preview-metric" style="grid-column:1/-1"><div class="k">状态</div><div class="v" style="color:#b45309">${esc(res.message || '未检测到圆')}</div></div>`;
      return;
    }
    const fit = res.fit;
    box.innerHTML = `
      <div class="preview-metric"><div class="k">半径</div><div class="v">${fmtNum(fit.r, 1)} px</div></div>
      <div class="preview-metric"><div class="k">直径</div><div class="v">${fmtNum(fit.diameter, 1)} px</div></div>
      <div class="preview-metric"><div class="k">覆盖率</div><div class="v">${fit.coverage != null ? pct(fit.coverage) : '—'}</div></div>
      <div class="preview-metric"><div class="k">SNR</div><div class="v">${fit.snr != null ? fmtNum(fit.snr, 1) : '—'}</div></div>
      <div class="preview-metric"><div class="k">状态</div><div class="v">${statusBadge(res.status, 'row')}</div></div>
      <div class="preview-metric"><div class="k">候选圆</div><div class="v">${res.n_objects || 1} 个</div></div>`;
  };

  const refreshImage = () => {
    if (!last) return;
    const url = tab === 'roi' ? last.roi_url : last.overlay_url;
    const img = $('#pv-img');
    if (img) {
      img.src = url ? `${url}?v=${Date.now()}` : '';
      img.style.display = url ? 'block' : 'none';
    }
  };

  const setTab = (next) => {
    tab = next;
    $$('.preview-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    refreshImage();
  };

  const updateNav = () => {
    const idx = $('#pv-index');
    if (idx) idx.textContent = queue.length ? `${index + 1} / ${queue.length}` : '– / –';
    const prev = $('#pv-prev');
    const next = $('#pv-next');
    const del = $('#pv-delete');
    if (prev) prev.disabled = index <= 0;
    if (next) next.disabled = index >= queue.length - 1;
    if (del) del.disabled = !queue.length;
  };

  const goTo = (delta) => {
    if (!queue.length) return;
    const next = index + delta;
    if (next < 0 || next >= queue.length) return;
    index = next;
    sample = queue[index].path;
    last = null;
    updateNav();
    runPreview();
  };

  const removeCurrent = () => {
    if (!queue.length) return;
    const item = queue[index];
    if (!window.confirm(`从本次运行中移除这张图片？\n${item.rel_path || item.path}`)) return;
    if (!Array.isArray(f.excluded)) f.excluded = [];
    if (!f.excluded.some((p) => normCasePath(p) === normCasePath(item.path))) {
      f.excluded.push(item.path);
    }
    const inputIndex = (f.inputs || []).findIndex((p) => normCasePath(p) === normCasePath(item.path));
    if (inputIndex >= 0) {
      f.inputs.splice(inputIndex, 1);
      $('#paths').value = f.inputs.join('\n');
    }
    queue.splice(index, 1);
    if (index >= queue.length) index = Math.max(0, queue.length - 1);
    saveForm();
    if (!queue.length) {
      closeModal();
      renderNew();
      toast('已移除全部待运行图片', 'ok');
      return;
    }
    sample = queue[index].path;
    last = null;
    updateNav();
    toast('已移除', 'ok');
    runPreview();
  };

  const runPreview = async () => {
    if (!sample) return;
    const mySeq = ++seq;
    setBusy(true);
    const cfg = formConfig();
    try {
      const res = await api('/api/preview', {
        method: 'POST',
        body: JSON.stringify({ input: sample, config: cfg, preview_id: token }),
      });
      if (mySeq !== seq) return;
      token = res.preview_id || token;
      last = res;
      $('#pv-sample').textContent = `预览图：${res.sample_name || basename(sample)} · ${index + 1}/${queue.length} · ${res.elapsed != null ? res.elapsed + ' s' : ''}`;
      renderMetrics(res);
      if (tab === 'roi' && !res.roi_url) setTab('overlay');
      else refreshImage();
    } catch (err) {
      if (mySeq !== seq) return;
      toast(`预览失败：${err.message}`, 'bad');
    } finally {
      if (mySeq === seq) setBusy(false);
    }
  };

  const loadQueue = async () => {
    setBusy(true);
    const scanLabel = $('#pv-sample');
    if (scanLabel) scanLabel.textContent = '正在扫描图片…';
    try {
      const res = await api('/api/scan', { method: 'POST', body: JSON.stringify({ inputs: f.inputs }) });
      const excluded = new Set((Array.isArray(f.excluded) ? f.excluded : []).map(normCasePath));
      queue = (res.items || []).filter((item) => !excluded.has(normCasePath(item.path)));
      if (!queue.length) {
        if (scanLabel) scanLabel.textContent = '没有可预览的图片';
        updateNav();
        return;
      }
      index = 0;
      sample = queue[0].path;
      updateNav();
      await runPreview();
    } catch (err) {
      if (scanLabel) scanLabel.textContent = '扫描失败';
      toast(err.message, 'bad');
    } finally {
      setBusy(false);
    }
  };

  const schedule = () => {
    updateLabels();
    clearTimeout(timer);
    timer = setTimeout(runPreview, 280);
  };

  const syncFromControls = () => {
    f.margin = Number($('#pv-margin').value);
    f.out_size = Number($('#pv-size').value);
    f.edge_level = Number($('#pv-edge').value);
    f.select = $('#pv-select').value;
    f.feature = $('#pv-feature').value;
    f.min_snr = Number($('#pv-min-snr').value);
    f.max_edge_width_frac = Number($('#pv-edge-width').value);
    schedule();
  };

  $$('#pv-margin, #pv-size, #pv-edge, #pv-select, #pv-feature, #pv-min-snr, #pv-edge-width').forEach((el) => {
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', syncFromControls);
  });
  $$('.preview-tabs button').forEach((btn) => {
    btn.addEventListener('click', () => setTab(btn.dataset.tab));
  });
  $('#pv-prev').onclick = () => goTo(-1);
  $('#pv-next').onclick = () => goTo(1);
  $('#pv-delete').onclick = removeCurrent;

  const applySnapshot = () => {
    $('#pv-margin').value = snapshot.margin;
    $('#pv-size').value = snapshot.out_size;
    $('#pv-edge').value = snapshot.edge_level;
    $('#pv-select').value = snapshot.select;
    $('#pv-feature').value = snapshot.feature;
    $('#pv-min-snr').value = snapshot.min_snr;
    $('#pv-edge-width').value = snapshot.max_edge_width_frac;
    f.margin = snapshot.margin;
    f.out_size = snapshot.out_size;
    f.edge_level = snapshot.edge_level;
    f.select = snapshot.select;
    f.feature = snapshot.feature;
    f.min_snr = snapshot.min_snr;
    f.max_edge_width_frac = snapshot.max_edge_width_frac;
    updateLabels();
    runPreview();
  };

  $('#pv-x').onclick = () => closeModal();
  $('#pv-cancel').onclick = () => closeModal();
  $('#pv-reset').onclick = applySnapshot;
  $('#pv-apply').onclick = () => {
    applied = true;
    saveForm();
    closeModal();
    renderNew();
    toast('参数已应用', 'ok');
  };
  $('#pv-run').onclick = () => {
    applied = true;
    saveForm();
    closeModal();
    startRun();
  };

  updateLabels();
  updateNav();
  loadQueue();
}

/* --------------------------------------------------------------- cut module */
async function renderCutNew() {
  await refreshRuns();
  const runs = state.runs.filter((r) => Number(r.records) > 0);
  const draft = state.cutDraft || null;
  const view = $('#view');
  if (!runs.length) {
    view.innerHTML = `
      <div class="page-head"><div><h1>新建圆域切割</h1></div>
        <div class="head-actions"><a class="btn" href="#/runs">提取记录</a></div></div>
      <div class="card empty"><div class="big">${ICONS.image}</div><h3>没有可用的提取结果</h3>
        <a class="btn primary" href="#/new">新建提取</a></div>`;
    return;
  }
  view.innerHTML = `
    <div class="page-head">
      <div><h1>新建圆域切割</h1></div>
      <div class="head-actions"><a class="btn" href="#/cuts">切割记录</a></div>
    </div>
    <div class="cut-layout">
      <section class="card form-card">
        <h2>1 · 选择提取结果</h2>
        <div class="grid-2">
          <div class="field">
            <label class="lbl" for="cut-run">提取记录</label>
            <select class="select" id="cut-run">
              ${runs.map((r) => `<option value="${esc(r.id)}">${esc(r.name)} · ${r.records}</option>`).join('')}
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="cut-search">搜索图片</label>
            <input class="input" id="cut-search">
          </div>
        </div>
        <div class="field">
          <div class="picker-head">
            <label class="check"><input type="checkbox" id="cut-all"> 全选</label>
            <span class="hint" id="cut-selected-count">已选 0 张</span>
          </div>
          <div class="image-picker" id="cut-image-list"></div>
        </div>
        <div class="divider"></div>
        <h2>2 · 切割参数</h2>
        <div class="grid-3">
          <div class="field">
            <label class="lbl" for="cut-angle">圆心角（度）</label>
            <input class="input" id="cut-angle" type="number" min="1" max="180" step="1" value="${draft ? Number(draft.angle_step) : 15}">
          </div>
          <div class="field">
            <label class="lbl" for="cut-size">输出尺寸</label>
            <select class="select" id="cut-size">
              ${[256, 384, 512, 768, 1024].map((v) => `<option value="${v}" ${Number(draft ? draft.out_size : 512) === v ? 'selected' : ''}>${v} × ${v}</option>`).join('')}
            </select>
          </div>
          <div class="field">
            <label class="lbl" for="cut-center">圆心</label>
            <select class="select" id="cut-center">
              <option value="geometric" ${!draft || draft.center_mode === 'geometric' ? 'selected' : ''}>红域几何圆心</option>
              <option value="auto" ${draft && draft.center_mode === 'auto' ? 'selected' : ''}>自动重心</option>
            </select>
          </div>
        </div>
        <div class="start-row">
          <button class="btn primary lg" id="cut-start" disabled>${ICONS.play} 开始切割（0）</button>
        </div>
        <div class="cut-progress" id="cut-progress" style="display:none">
          <div class="bar"><i id="cut-bar" style="width:0%"></i></div>
          <div class="hint" id="cut-progress-text"></div>
        </div>
      </section>
      <aside class="card preview-card">
        <div id="cut-selected-thumb" class="cut-empty-thumb">${ICONS.image}</div>
        <div class="cut-meta" id="cut-selected-meta"></div>
      </aside>
    </div>`;

  const runSel = $('#cut-run');
  if (draft && runs.some((r) => r.id === draft.run_id)) runSel.value = draft.run_id;
  const search = $('#cut-search');
  const list = $('#cut-image-list');
  const all = $('#cut-all');
  const selectedCount = $('#cut-selected-count');
  const start = $('#cut-start');
  state.cutSelected = new Set();

  const visibleItems = () => {
    const query = search.value.trim().toLowerCase();
    return state.cutImages.filter((x) => !query || String(x.rel_path).toLowerCase().includes(query));
  };

  const updatePreview = () => {
    const first = state.cutImages.find((x) => state.cutSelected.has(x.file));
    const thumb = $('#cut-selected-thumb');
    const meta = $('#cut-selected-meta');
    if (!first) {
      thumb.className = 'cut-empty-thumb';
      thumb.innerHTML = ICONS.image;
      meta.innerHTML = '';
      return;
    }
    thumb.className = '';
    thumb.innerHTML = first.thumb_url
      ? `<img class="cut-thumb" src="${esc(first.thumb_url)}" alt="">`
      : `<div class="cut-empty-thumb">${ICONS.image}</div>`;
    meta.innerHTML = `
      <div class="item"><div class="k">半径</div><div class="v">${fmtNum(first.radius, 1)} px</div></div>
      <div class="item"><div class="k">状态</div><div class="v">${esc(ROW_STATUS[first.status] || first.status)}</div></div>
      <div class="item"><div class="k">圆心</div><div class="v">${fmtNum(first.cx, 1)}, ${fmtNum(first.cy, 1)}</div></div>
      <div class="item"><div class="k">已选</div><div class="v">${state.cutSelected.size}</div></div>`;
  };

  const updateSelection = (visible) => {
    const selectedVisible = visible.filter((x) => state.cutSelected.has(x.file));
    all.checked = visible.length > 0 && selectedVisible.length === visible.length;
    all.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visible.length;
    selectedCount.textContent = `已选 ${state.cutSelected.size} 张`;
    start.textContent = `开始切割（${state.cutSelected.size}）`;
    start.disabled = state.cutSelected.size === 0;
    updatePreview();
  };

  const renderList = () => {
    const items = visibleItems();
    if (!items.length) {
      list.innerHTML = '<div class="cut-empty-thumb" style="aspect-ratio:auto;padding:24px">没有匹配图片</div>';
      updateSelection(items);
      return;
    }
    list.innerHTML = items.map((x) => `
      <label class="image-row">
        <input type="checkbox" data-file="${esc(x.file)}" ${state.cutSelected.has(x.file) ? 'checked' : ''}>
        <span class="name">${esc(x.rel_path)}</span>
        <span class="meta">${fmtNum(x.radius, 0)} px · ${esc(ROW_STATUS[x.status] || x.status)}</span>
      </label>`).join('');
    updateSelection(items);
  };

  const loadImages = async () => {
    list.innerHTML = '<div class="cut-empty-thumb" style="aspect-ratio:auto;padding:24px"><span class="spinner"></span></div>';
    try {
      const data = await api(`/api/runs/${encodeURIComponent(runSel.value)}/images`);
      state.cutImages = data.images || [];
    } catch (err) {
      state.cutImages = [];
      toast(err.message, 'bad');
    }
    state.cutSelected = new Set();
    if (draft && draft.file) {
      const found = state.cutImages.find((x) => x.file === draft.file || x.rel_path === draft.file);
      if (found) state.cutSelected.add(found.file);
    }
    renderList();
  };

  list.addEventListener('change', (e) => {
    const box = e.target.closest('input[data-file]');
    if (!box) return;
    if (box.checked) state.cutSelected.add(box.dataset.file);
    else state.cutSelected.delete(box.dataset.file);
    updateSelection(visibleItems());
  });
  all.addEventListener('change', () => {
    const items = visibleItems();
    if (all.checked) items.forEach((x) => state.cutSelected.add(x.file));
    else items.forEach((x) => state.cutSelected.delete(x.file));
    renderList();
  });
  search.addEventListener('input', renderList);
  runSel.addEventListener('change', () => {
    state.cutDraft = null;
    loadImages();
  });

  start.addEventListener('click', async () => {
    const files = [...state.cutSelected];
    if (!files.length) {
      toast('请选择图片', 'bad');
      return;
    }
    const angle = Number($('#cut-angle').value);
    if (!(angle >= 1 && angle <= 180)) {
      toast('圆心角需在 1–180 度之间', 'bad');
      return;
    }
    const outSize = Number($('#cut-size').value);
    const centerMode = $('#cut-center').value;
    const old = start.innerHTML;
    start.disabled = true;
    runSel.disabled = true;
    search.disabled = true;
    all.disabled = true;
    start.innerHTML = '<span class="spinner"></span> 切割中…';
    const progress = $('#cut-progress');
    const bar = $('#cut-bar');
    const text = $('#cut-progress-text');
    progress.style.display = 'block';
    const cutIds = [];
    const failures = [];
    for (let i = 0; i < files.length; i += 1) {
      const item = state.cutImages.find((x) => x.file === files[i]);
      if (!item) continue;
      const pct = (i / files.length) * 100;
      bar.style.width = `${pct}%`;
      text.textContent = `${i + 1} / ${files.length} · ${item.rel_path}`;
      try {
        const res = await api('/api/cuts', {
          method: 'POST',
          body: JSON.stringify({
            run_id: runSel.value,
            file: item.file,
            rel_path: item.rel_path,
            angle_step: angle,
            out_size: outSize,
            center_mode: centerMode,
          }),
        });
        cutIds.push(res.cut.id);
      } catch (err) {
        failures.push(`${item.rel_path}: ${err.message}`);
      }
    }
    bar.style.width = '100%';
    await refreshCuts();
    start.disabled = false;
    runSel.disabled = false;
    search.disabled = false;
    all.disabled = false;
    start.innerHTML = old;
    if (failures.length) toast(`完成 ${cutIds.length} 张，失败 ${failures.length} 张`, 'bad');
    else toast(`完成 ${cutIds.length} 张`, 'ok');
    if (cutIds.length === 1) navigate(`#/cut/${cutIds[0]}`);
    else if (cutIds.length > 1) navigate('#/cuts');
  });

  await loadImages();
}

async function renderCutHistory() {
  await refreshCuts();
  const cuts = state.cuts;
  const view = $('#view');
  view.innerHTML = `
    <div class="page-head">
      <div><h1>切割记录</h1></div>
      <div class="head-actions">
        <button class="btn" id="cut-refresh">${ICONS.refresh} 刷新</button>
        <a class="btn primary" href="#/cut">${ICONS.play} 新建切割</a>
      </div>
    </div>
    <div class="cut-history" id="cut-history">
      ${cuts.length ? cuts.map(cutCard).join('') : `<div class="card empty"><div class="big">${ICONS.image}</div><h3>还没有切割记录</h3><a class="btn primary" href="#/cut">新建第一次切割</a></div>`}
    </div>`;
  $('#cut-refresh').addEventListener('click', () => renderCutHistory());
  $('#cut-history').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-del-cut]');
    if (del) {
      e.preventDefault();
      const cut = cuts.find((c) => c.id === del.dataset.delCut);
      if (cut && window.confirm(`删除切割记录？\n${cut.source_name || cut.id}`)) {
        try {
          await api(`/api/cuts/${encodeURIComponent(cut.id)}/delete`, { method: 'POST' });
          toast('已删除', 'ok');
          await renderCutHistory();
        } catch (err) {
          toast(err.message, 'bad');
        }
      }
    }
  });
}

function cutCard(cut) {
  return `
    <article class="card cut-card">
      <div>
        <div class="title"><h3>${esc(cut.source_name || cut.rel_path || cut.id)}</h3></div>
        <div class="sub">${esc(cut.run_name || '')} · ${esc(fmtTime(cut.created_at))}</div>
        <div class="stats">
          <span>半径 <b>${fmtNum(cut.radius, 1)}</b> px</span>
          <span>圆心角 <b>${fmtNum(cut.angle_step, 0)}</b>°</span>
          <span>扇形 <b>${cut.sectors}</b></span>
          <span>圆心偏差 <b>${fmtNum(cut.center_offset, 1)}</b> px</span>
          <span>${fmtNum(cut.out_size, 0)} × ${fmtNum(cut.out_size, 0)}</span>
        </div>
      </div>
      <div class="cut-actions">
        <a class="btn primary sm" href="#/cut/${esc(cut.id)}">查看</a>
        <button class="btn danger sm" data-del-cut="${esc(cut.id)}">删除</button>
      </div>
    </article>`;
}

async function renderCutResult(cutId) {
  let data;
  try {
    data = await api(`/api/cuts/${encodeURIComponent(cutId)}`);
  } catch (err) {
    renderError(err.message);
    return;
  }
  const cut = data.cut;
  const rows = cut.sector_rows || [];
  const view = $('#view');
  const firstUrl = cut.circle_url || cut.overlay_url || '';
  view.innerHTML = `
    <div class="run-head">
      <a class="icon-btn back" href="#/cuts" title="返回">${ICONS.left}</a>
      <div style="min-width:0;flex:1">
        <h1>${esc(cut.source_name || cut.rel_path || cut.id)}</h1>
        <div class="sub">
          <span>${esc(cut.run_name || '')}</span>
          <span>${esc(fmtTime(cut.created_at))}</span>
          <span>${fmtNum(cut.out_size, 0)} × ${fmtNum(cut.out_size, 0)}</span>
        </div>
      </div>
      <div class="head-actions">
        <a class="btn" href="/api/cuts/${esc(cut.id)}/zip">${ICONS.download} 扇形 ZIP</a>
        <a class="btn" href="${esc(cut.csv_url)}">${ICONS.download} CSV</a>
        <button class="btn danger" id="cut-delete">${ICONS.trash} 删除</button>
      </div>
    </div>

    <div class="cut-summary">
      <div class="card s-card"><div class="k">半径</div><div class="v">${fmtNum(cut.radius, 1)}<small> px</small></div></div>
      <div class="card s-card"><div class="k">圆心角</div><div class="v">${fmtNum(cut.angle_step, 0)}<small>°</small></div></div>
      <div class="card s-card"><div class="k">扇形数</div><div class="v">${cut.sectors}</div></div>
      <div class="card s-card"><div class="k">圆心偏差</div><div class="v">${fmtNum(cut.center_offset, 1)}<small> px</small></div></div>
      <div class="card s-card"><div class="k">红域圆心</div><div class="v" style="font-size:12px">${fmtNum(cut.cx, 1)}, ${fmtNum(cut.cy, 1)}</div></div>
      <div class="card s-card"><div class="k">自动重心</div><div class="v" style="font-size:12px">${fmtNum(cut.auto_cx, 1)}, ${fmtNum(cut.auto_cy, 1)}</div></div>
    </div>

    <div class="card" style="padding:14px;margin-bottom:16px">
      <div class="cut-tabs" id="cut-tabs">
        <button data-src="${esc(cut.circle_url)}" class="active">圆域</button>
        <button data-src="${esc(cut.overlay_url)}">分割线</button>
        <button data-src="${esc(cut.contact_url)}">扇形拼图</button>
      </div>
      <div class="cut-preview"><img id="cut-img" src="${esc(firstUrl)}" alt=""></div>
    </div>

    <div class="card" style="padding:14px">
      <div class="sector-table-wrap">
        <table class="sector-table">
          <thead><tr><th>#</th><th>角度</th><th>绿色均值</th><th>绿色中位</th><th>P10</th><th>P90</th><th>面积%</th><th>PNG</th></tr></thead>
          <tbody id="sector-rows">
            ${rows.map((r) => `
              <tr data-url="${esc(r.url)}" data-angle="${fmtNum(r.start, 0)}–${fmtNum(r.end, 0)}">
                <td>${r.index}</td>
                <td>${fmtNum(r.start, 0)}–${fmtNum(r.end, 0)}°</td>
                <td>${fmtNum(r.green_mean, 4)}</td>
                <td>${fmtNum(r.green_median, 4)}</td>
                <td>${fmtNum(r.green_p10, 4)}</td>
                <td>${fmtNum(r.green_p90, 4)}</td>
                <td>${fmtNum((r.area_frac || 0) * 100, 1)}</td>
                <td><a href="${esc(r.url)}" download>下载</a></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>`;

  $('#cut-tabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-src]');
    if (!btn) return;
    $$('#cut-tabs button').forEach((b) => b.classList.toggle('active', b === btn));
    $('#cut-img').src = btn.dataset.src;
  });
  $('#sector-rows').addEventListener('click', (e) => {
    if (e.target.closest('a')) return;
    const tr = e.target.closest('tr[data-url]');
    if (!tr) return;
    showModal(`
      <div class="modal-head">
        <div><h2>${esc(tr.dataset.angle)}°</h2></div>
        <button class="icon-btn close" id="sec-x">${ICONS.x}</button>
      </div>
      <div class="modal-body"><div class="cut-preview"><img src="${esc(tr.dataset.url)}" alt=""></div></div>
      <div class="modal-foot">
        <a class="btn" href="${esc(tr.dataset.url)}" download>${ICONS.download} 下载 PNG</a>
        <div style="flex:1"></div>
        <button class="btn" id="sec-close">关闭</button>
      </div>`, { wide: false });
    $('#sec-x').onclick = closeModal;
    $('#sec-close').onclick = closeModal;
  });
  $('#cut-delete').addEventListener('click', async () => {
    if (!window.confirm(`删除切割记录？\n${cut.source_name || cut.id}`)) return;
    try {
      await api(`/api/cuts/${encodeURIComponent(cut.id)}/delete`, { method: 'POST' });
      toast('已删除', 'ok');
      navigate('#/cuts');
    } catch (err) {
      toast(err.message, 'bad');
    }
  });
}

/* ------------------------------------------------------------------ history */
async function renderHistory(auto = false) {
  cleanupPoll();
  const view = $('#view');
  if (!auto) {
    view.innerHTML = '<div class="loading-card"><span class="spinner"></span> 正在读取运行记录…</div>';
  }
  try {
    await refreshRuns();
  } catch (err) {
    view.innerHTML = errorHtml('读取运行记录失败', err.message);
    return;
  }
  const runs = state.runs;
  const doneRuns = runs.filter((r) => r.status === 'done').length;
  const images = runs.reduce((s, r) => s + (Number(r.images) || Number(r.total) || 0), 0);
  const circles = runs.reduce((s, r) => s + (Number(r.records) || 0), 0);
  const active = runs.some((r) => ACTIVE.has(r.status));

  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>运行记录</h1>
      </div>
      <div class="head-actions">
        <button class="btn" id="refresh-runs">${ICONS.refresh} 刷新</button>
        <a class="btn primary" href="#/new">${ICONS.play} 新建运行</a>
      </div>
    </div>
    <div class="stats-row">
      <div class="card stat"><div class="k">运行次数</div><div class="v">${runs.length}<small>次</small></div></div>
      <div class="card stat"><div class="k">已完成</div><div class="v">${doneRuns}<small>次</small></div></div>
      <div class="card stat"><div class="k">处理图片</div><div class="v">${images}<small>张</small></div></div>
      <div class="card stat"><div class="k">提取圆域</div><div class="v">${circles}<small>个</small></div></div>
    </div>
    <div class="run-list" id="run-list">
      ${runs.length ? runs.map(runCard).join('') : emptyRunsHtml()}
    </div>`;

  $('#refresh-runs').addEventListener('click', () => renderHistory());
  bindRunCards();
  if (active) {
    state.pollTimer = setTimeout(() => renderHistory(true), 2200);
  }
}

function emptyRunsHtml() {
  return `
    <div class="card empty">
      <div class="big">${ICONS.image}</div>
      <h3>还没有运行记录</h3>
      <a class="btn primary" href="#/new">${ICONS.play} 新建第一次运行</a>
    </div>`;
}

function runCard(run) {
  const counts = run.counts || {};
  const done = Number(run.done) || 0;
  const total = Number(run.total) || 0;
  const running = ACTIVE.has(run.status);
  const width = total > 0 ? Math.min(100, (done / total) * 100) : 0;
  const input = (run.inputs || [])[0] || '';
  const chips = [
    ['通过', counts.ok || 0],
    ['复核', counts.review || 0],
    ['未找到', counts.not_found || 0],
    ['错误', counts.error || 0],
  ].filter(([, v]) => v > 0)
    .map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join('');

  return `
    <article class="card run-card" data-status="${esc(run.status)}">
      <div class="run-main">
        <div class="run-title">
          <h3>${esc(run.name || run.id)}</h3>
          ${statusBadge(run.status)}
          <span class="run-time">${esc(fmtTime(run.created_at))}</span>
        </div>
        <div class="run-inputs" title="${esc(input)}">${esc(shortPath(input, 90))}</div>
        <div class="run-stats">
          <span>图片 <b>${total || 0}</b></span>
          <span>圆域 <b>${Number(run.records) || 0}</b></span>
          <span>半径中位 <b>${run.radius_median ? fmtNum(run.radius_median, 1) + ' px' : '—'}</b></span>
          <span>用时 <b>${fmtDuration(run.elapsed)}</b></span>
          ${chips}
          ${run.note ? `<span style="color:#b45309">${esc(run.note)}</span>` : ''}
        </div>
        ${running ? `
          <div class="run-progress">
            <div class="bar"><i style="width:${width}%"></i></div>
            <div class="hint" style="margin-top:4px">${done} / ${total || '…'} 张${run.current ? ` · ${esc(run.current)}` : ''}</div>
          </div>` : ''}
      </div>
      <div class="run-actions">
        <a class="btn sm ${running ? '' : 'primary'}" href="#/runs/${esc(run.id)}">${running ? '查看进度' : '查看结果'}</a>
        ${running ? '' : `<button class="btn sm js-rerun" data-id="${esc(run.id)}">重新运行</button>`}
        <button class="icon-btn js-open-folder" data-id="${esc(run.id)}" title="打开输出目录">${ICONS.folder}</button>
        ${running ? '' : `<button class="btn sm danger js-delete" data-id="${esc(run.id)}">删除</button>`}
      </div>
    </article>`;
}

function bindRunCards() {
  const list = $('#run-list');
  if (!list) return;
  list.addEventListener('click', async (e) => {
    const open = e.target.closest('.js-open-folder');
    if (open) {
      e.preventDefault();
      await openOutput(open.dataset.id);
      return;
    }
    const rerun = e.target.closest('.js-rerun');
    if (rerun) {
      e.preventDefault();
      const run = state.runs.find((r) => r.id === rerun.dataset.id);
      if (run) {
        state.pendingRerun = run;
        navigate('#/new');
      }
      return;
    }
    const del = e.target.closest('.js-delete');
    if (del) {
      e.preventDefault();
      const run = state.runs.find((r) => r.id === del.dataset.id);
      if (run) confirmDelete(run);
    }
  });
}

async function openOutput(runId) {
  try {
    await api('/api/open', { method: 'POST', body: JSON.stringify({ run_id: runId }) });
    toast('已在文件管理器中打开输出目录', 'ok');
  } catch (err) {
    toast(`无法自动打开：${err.message}`, 'bad');
  }
}

function confirmDelete(run) {
  showModal(`
    <div class="modal-head">
      <div>
        <h2>删除运行记录</h2>
        <div class="sub">${esc(run.name || run.id)}</div>
      </div>
      <button class="icon-btn close" id="del-x" aria-label="关闭">${ICONS.x}</button>
    </div>
    <div class="modal-body">
      <label class="check"><input type="checkbox" id="del-files"> 同时删除输出文件夹</label>
      <div class="hint" style="margin-top:10px">${esc(run.output || '')}</div>
    </div>
    <div class="modal-foot">
      <button class="btn" id="del-cancel">取消</button>
      <div style="flex:1"></div>
      <button class="btn danger" id="del-confirm">${ICONS.trash} 删除${run.records ? '（含结果）' : ''}</button>
    </div>`, { wide: false });

  $('#del-x').onclick = closeModal;
  $('#del-cancel').onclick = closeModal;
  $('#del-confirm').onclick = async () => {
    const btn = $('#del-confirm');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 删除中…';
    try {
      await api(`/api/runs/${encodeURIComponent(run.id)}/delete`, {
        method: 'POST',
        body: JSON.stringify({ delete_files: $('#del-files').checked }),
      });
      closeModal();
      toast('已删除', 'ok');
      if (location.hash.startsWith('#/runs/')) navigate('#/runs');
      else await renderHistory();
    } catch (err) {
      toast(err.message, 'bad');
      btn.disabled = false;
      btn.innerHTML = '删除';
    }
  };
}

/* --------------------------------------------------------------- run detail */
async function renderRun(runId) {
  cleanupPoll();
  state.currentRunId = runId;
  const view = $('#view');
  view.innerHTML = '<div class="loading-card"><span class="spinner"></span> 正在读取运行状态…</div>';
  let data;
  try {
    data = await api(`/api/runs/${encodeURIComponent(runId)}/status`);
  } catch (err) {
    renderError(err.message);
    return;
  }
  const run = data.run;
  if (ACTIVE.has(run.status)) {
    renderRunning(run, data.log || []);
    return;
  }
  if (run.status === 'error') {
    renderFailure(run, data.log || []);
    return;
  }
  if (run.status === 'interrupted' && !run.records) {
    renderFailure(run, data.log || [], '服务重启导致运行中断，没有生成结果。');
    return;
  }
  try {
    await renderResultsPage(run);
  } catch (err) {
    renderFailure(run, data.log || [], err.message);
  }
}

function runHead(run, subtitle = '') {
  return `
    <div class="run-head">
      <a class="icon-btn back" href="#/runs" title="返回运行记录">${ICONS.left}</a>
      <div style="min-width:0;flex:1">
        <h1>${esc(run.name || run.id)}</h1>
        <div class="sub">
          ${statusBadge(run.status)}
          <span>${esc(fmtDate(run.created_at))}</span>
          <span>${esc(run.total ? `${run.done || 0}/${run.total} 张` : '')}</span>
          <span>${esc(fmtDuration(run.elapsed))}</span>
          ${subtitle ? `<span>${esc(subtitle)}</span>` : ''}
        </div>
      </div>
    </div>`;
}

function renderRunning(run, initialLog) {
  const total = Number(run.total) || 0;
  const done = Number(run.done) || 0;
  const width = total ? Math.min(100, (done / total) * 100) : 0;
  const view = $('#view');
  view.innerHTML = `
    ${runHead(run, '正在运行')}
    <div class="card running-card">
      <div class="running-top">
        <span class="spinner"></span>
        <div style="flex:1">
          <div class="running-title" id="run-progress-text">${done} / ${total || '扫描中'} 张</div>
          <div class="running-sub" id="run-current">${esc(run.current || '正在准备图片列表…')}</div>
        </div>
        <button class="btn danger" id="cancel-run">取消运行</button>
      </div>
      <div class="bar running-bar"><i id="run-bar" style="width:${width}%"></i></div>
      <div class="running-stats" id="run-stats">${runningStats(run)}</div>
      <div class="log-box" id="run-log">${logHtml(initialLog)}</div>
    </div>`;
  scrollLog();
  $('#cancel-run').addEventListener('click', async () => {
    const btn = $('#cancel-run');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 正在取消…';
    try {
      await api(`/api/runs/${encodeURIComponent(run.id)}/cancel`, { method: 'POST' });
      toast('已请求取消，正在收尾', 'ok');
    } catch (err) {
      toast(err.message, 'bad');
      btn.disabled = false;
      btn.textContent = '取消运行';
    }
  });
  pollRun(run.id);
}

function runningStats(run) {
  const c = run.counts || {};
  return `
    <span>通过 <b>${c.ok || 0}</b></span>
    <span>复核 <b>${c.review || 0}</b></span>
    <span>未找到 <b>${c.not_found || 0}</b></span>
    <span>错误 <b>${c.error || 0}</b></span>
    <span>用时 <b>${fmtDuration(run.elapsed)}</b></span>`;
}

function logHtml(lines) {
  if (!lines || !lines.length) return '<div class="dim">等待日志…</div>';
  return lines.map((line) => {
    const cls = /error|失败|traceback/i.test(line) ? 'err' : '';
    return `<div class="${cls}">${esc(line)}</div>`;
  }).join('');
}

function scrollLog() {
  const box = $('#run-log');
  if (box) box.scrollTop = box.scrollHeight;
}

async function pollRun(runId) {
  if (state.currentRunId !== runId) return;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}/status`);
    const run = data.run;
    if (!ACTIVE.has(run.status)) {
      cleanupPoll();
      if (run.status === 'done') toast('运行完成', 'ok');
      if (run.status === 'cancelled') toast('运行已取消', 'info');
      await renderRun(runId);
      return;
    }
    const total = Number(run.total) || 0;
    const done = Number(run.done) || 0;
    const width = total ? Math.min(100, (done / total) * 100) : 0;
    const bar = $('#run-bar');
    const text = $('#run-progress-text');
    const current = $('#run-current');
    const stats = $('#run-stats');
    if (!bar) return;
    bar.style.width = `${width}%`;
    text.textContent = `${done} / ${total || '扫描中'} 张`;
    current.textContent = run.current || (total ? '正在处理…' : '正在准备图片列表…');
    stats.innerHTML = runningStats(run);
    const box = $('#run-log');
    if (box) {
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 50;
      box.innerHTML = logHtml(data.log || []);
      if (atBottom) box.scrollTop = box.scrollHeight;
    }
    state.pollTimer = setTimeout(() => pollRun(runId), 900);
  } catch (err) {
    state.pollTimer = setTimeout(() => pollRun(runId), 1800);
  }
}

function renderFailure(run, log = [], extra = '') {
  cleanupPoll();
  const view = $('#view');
  view.innerHTML = `
    ${runHead(run, '运行失败')}
    <div class="card empty" style="text-align:left">
      <h3 style="color:#b91c1c">${ICONS.alert} 运行没有完成</h3>
      <p>${esc(run.error || extra || run.note || '未知错误')}</p>
      <div class="log-box" style="height:auto;max-height:320px;text-align:left">${logHtml(log)}</div>
      <div style="margin-top:14px;display:flex;gap:8px">
        <a class="btn" href="#/runs">返回运行记录</a>
        <button class="btn" id="retry-run">重新运行</button>
      </div>
    </div>`;
  $('#retry-run').addEventListener('click', () => {
    state.pendingRerun = run;
    navigate('#/new');
  });
}

/* ---------------------------------------------------------------- results UI */
async function renderResultsPage(run) {
  state.currentRunMeta = run;
  state.filters = { status: 'all', q: '', sort: 'path', page: 1, page_size: 48 };
  const data = await fetchResults(1);
  state.lastData = data;
  state.lastRows = data.rows;
  const s = data.summary;
  const view = $('#view');
  const reportUrl = `/runs/${encodeURIComponent(run.id)}/files/report.html`;
  const csvUrl = `/runs/${encodeURIComponent(run.id)}/files/tables/wells.csv?download=1`;

  view.innerHTML = `
    ${runHead(run, run.note || '')}
    ${run.status === 'cancelled' ? `<div class="card" style="padding:11px 16px;margin-bottom:14px;border-color:#fde68a;background:#fffbeb;color:#92400e;font-size:12.5px">运行已取消</div>` : ''}
    <div class="summary-grid">
      <div class="card s-card"><div class="k">圆域</div><div class="v"><span id="sum-records">${s.records}</span><small>个</small></div></div>
      <div class="card s-card"><div class="k">图片</div><div class="v"><span id="sum-images">${s.images}</span><small>张</small></div></div>
      <div class="card s-card"><div class="k">通过</div><div class="v" style="color:#047857" id="sum-ok">${s.counts.ok || 0}</div></div>
      <div class="card s-card"><div class="k">待复核</div><div class="v" style="color:#92400e" id="sum-review">${s.counts.review || 0}</div></div>
      <div class="card s-card"><div class="k">未找到 / 错误</div><div class="v"><span id="sum-missing">${s.counts.not_found || 0}</span> / <span style="color:#b91c1c" id="sum-error">${s.counts.error || 0}</span></div></div>
      <div class="card s-card"><div class="k">半径中位数</div><div class="v"><span id="sum-radius">${s.radius_median ? fmtNum(s.radius_median, 1) : '—'}</span><small> px</small></div></div>
    </div>

    <div class="toolbar">
      <div class="search">${ICONS.search}<input class="input" id="result-search" placeholder="搜索文件名 / 备注…" value="${esc(state.filters.q)}"></div>
      <select class="select" id="result-status" style="width:auto">
        <option value="all">全部状态</option>
        <option value="ok">仅通过</option>
        <option value="review">仅复核</option>
        <option value="not_found">未找到</option>
        <option value="error">错误</option>
      </select>
      <select class="select" id="result-sort" style="width:auto">
        <option value="path">按文件名</option>
        <option value="status">异常优先</option>
        <option value="radius_desc">半径从大到小</option>
        <option value="radius_asc">半径从小到大</option>
        <option value="coverage_asc">覆盖率从低到高</option>
      </select>
      <span class="spacer"></span>
      <a class="btn" href="${reportUrl}" target="_blank" rel="noopener">${ICONS.external} 原始报告</a>
      <a class="btn" href="${csvUrl}">${ICONS.download} CSV</a>
      <button class="btn" id="result-open-folder">${ICONS.folder} 输出目录</button>
      <button class="btn" id="result-rerun">${ICONS.refresh} 重新运行</button>
      <button class="btn danger" id="result-delete">${ICONS.trash} 删除本次运行</button>
    </div>

    <div class="hint" style="margin:-4px 0 4px">输出：<span class="path-list" style="display:inline">${esc(run.output || '')}</span></div>
    <div class="hint" id="result-count" style="margin:-2px 0 10px">共 ${data.total} 个圆域</div>
    <div class="result-grid" id="result-grid">${gridHtml(data.rows)}</div>
    <div id="pager">${pagerHtml(data)}</div>`;

  $('#result-search').addEventListener('input', debounce(() => {
    state.filters.q = $('#result-search').value.trim();
    state.filters.page = 1;
    refreshResults();
  }, 320));
  $('#result-status').value = state.filters.status;
  $('#result-sort').value = state.filters.sort;
  $('#result-status').addEventListener('change', () => {
    state.filters.status = $('#result-status').value;
    state.filters.page = 1;
    refreshResults();
  });
  $('#result-sort').addEventListener('change', () => {
    state.filters.sort = $('#result-sort').value;
    state.filters.page = 1;
    refreshResults();
  });
  $('#result-open-folder').addEventListener('click', () => openOutput(run.id));
  $('#result-rerun').addEventListener('click', () => {
    state.pendingRerun = run;
    navigate('#/new');
  });
  $('#result-delete').addEventListener('click', () => confirmDelete(run));
  bindGrid();
  bindPager();
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function fetchResults(page) {
  state.filters.page = page;
  const qs = new URLSearchParams({
    status: state.filters.status,
    q: state.filters.q,
    sort: state.filters.sort,
    page: String(page),
    page_size: String(state.filters.page_size),
  });
  return api(`/api/runs/${encodeURIComponent(state.currentRunId)}/results?${qs.toString()}`);
}

async function refreshResults() {
  try {
    const data = await fetchResults(state.filters.page);
    state.lastData = data;
    state.lastRows = data.rows;
    $('#result-grid').innerHTML = gridHtml(data.rows);
    $('#result-count').textContent = `共 ${data.total} 个圆域${data.total !== data.summary.records ? `（全部 ${data.summary.records} 个）` : ''}`;
    $('#pager').innerHTML = pagerHtml(data);
    const s = data.summary;
    if ($('#sum-records')) $('#sum-records').textContent = s.records;
    if ($('#sum-images')) $('#sum-images').textContent = s.images;
    if ($('#sum-ok')) $('#sum-ok').textContent = s.counts.ok || 0;
    if ($('#sum-review')) $('#sum-review').textContent = s.counts.review || 0;
    if ($('#sum-missing')) $('#sum-missing').textContent = s.counts.not_found || 0;
    if ($('#sum-error')) $('#sum-error').textContent = s.counts.error || 0;
    if ($('#sum-radius')) $('#sum-radius').textContent = s.radius_median ? fmtNum(s.radius_median, 1) : '—';
    bindPager();
  } catch (err) {
    toast(err.message, 'bad');
  }
}

function gridHtml(rows) {
  if (!rows || !rows.length) {
    return `<div class="card empty" style="grid-column:1/-1">
      <div class="big">${ICONS.search}</div><h3>没有匹配的结果</h3>
    </div>`;
  }
  return rows.map((row, i) => resultCard(row, i)).join('');
}

function resultCard(row, index) {
  const thumb = row.thumb_url
    ? `<img loading="lazy" src="${esc(row.thumb_url)}" alt="${esc(row.rel_path)}">`
    : `<div style="height:100%;display:grid;place-items:center;color:#475569">${ICONS.image}</div>`;
  const name = basename(row.rel_path) || row.file || '—';
  const suffix = row.object_index > 1 ? ` · #${row.object_index}` : '';
  return `
    <article class="result-card" data-idx="${index}" title="${esc(row.rel_path)}">
      <div class="result-thumb">
        ${thumb}
        ${statusBadge(row.status, 'row')}
      </div>
      <div class="result-body">
        <div class="result-name">${esc(name)}${suffix}</div>
        <div class="result-meta">
          <span>r <b>${row.radius ? fmtNum(row.radius, 1) : '—'}</b> px</span>
          <span>覆盖 <b>${row.coverage != null ? pct(row.coverage) : '—'}</b></span>
        </div>
      </div>
    </article>`;
}

function pagerHtml(data) {
  if (!data || data.pages <= 1) {
    return `<div class="pager"><span>共 ${data ? data.total : 0} 个圆域</span></div>`;
  }
  return `
    <div class="pager">
      <button class="btn sm" id="prev-page" ${data.page <= 1 ? 'disabled' : ''}>${ICONS.left} 上一页</button>
      <span>第 ${data.page} / ${data.pages} 页 · 共 ${data.total} 个</span>
      <button class="btn sm" id="next-page" ${data.page >= data.pages ? 'disabled' : ''}>下一页 ${ICONS.right}</button>
    </div>`;
}

function bindPager() {
  const prev = $('#prev-page');
  const next = $('#next-page');
  if (prev) prev.addEventListener('click', () => {
    if (state.filters.page > 1) {
      state.filters.page -= 1;
      refreshResults();
    }
  });
  if (next) next.addEventListener('click', () => {
    if (state.lastData && state.filters.page < state.lastData.pages) {
      state.filters.page += 1;
      refreshResults();
    }
  });
}

function bindGrid() {
  const grid = $('#result-grid');
  if (!grid) return;
  grid.addEventListener('click', (e) => {
    const card = e.target.closest('.result-card');
    if (!card) return;
    openDetail(Number(card.dataset.idx));
  });
}

function openDetail(index) {
  const rows = state.lastRows;
  if (!rows || !rows[index]) return;
  state.detailIndex = index;
  const row = rows[index];
  closeModal();
  const images = [
    ['叠加图', row.overlay_url],
    ['归一化 ROI', row.roi_url],
    ['掩膜', row.mask_url],
    ['极坐标', row.polar_url],
  ].filter(([, url]) => url);
  const metrics = [
    ['状态', ROW_STATUS[row.status] || row.status],
    ['半径', row.radius ? `${fmtNum(row.radius, 2)} px` : '—'],
    ['圆心', row.cx != null ? `${fmtNum(row.cx, 1)}, ${fmtNum(row.cy, 1)}` : '—'],
    ['覆盖率', row.coverage != null ? pct(row.coverage) : '—'],
    ['SNR', row.snr != null ? fmtNum(row.snr, 1) : '—'],
    ['径向 CV', row.radial_cv != null ? fmtNum(row.radial_cv, 3) : '—'],
    ['边缘软度', row.edge_width != null ? `${fmtNum(row.edge_width, 1)} px` : '—'],
    ['残差 RMS', row.residual_rms != null ? `${fmtNum(row.residual_rms, 2)} px` : '—'],
    ['饱和比例', row.saturation != null ? pct(row.saturation) : '—'],
    ['阈值', row.threshold != null ? fmtNum(row.threshold, 3) : '—'],
  ];
  const tabs = images.map(([label], i) => `<button data-tab="${i}" class="${i === 0 ? 'active' : ''}">${label}</button>`).join('');
  const frame = images.length
    ? `<img id="detail-img" src="${esc(images[0][1])}" alt="${esc(images[0][0])}">`
    : `<div class="none">该记录没有可显示图像</div>`;

  showModal(`
    <div class="modal-head">
      <div style="min-width:0">
        <h2>${esc(basename(row.rel_path))}${row.object_index > 1 ? ` · #${row.object_index}` : ''}</h2>
        <div class="sub">${esc(row.rel_path)} ${row.note ? `· ${esc(row.note)}` : ''}</div>
      </div>
      <button class="icon-btn close" id="detail-x" aria-label="关闭">${ICONS.x}</button>
    </div>
    <div class="modal-body">
      <div class="detail-grid">
        <div class="detail-image">
          <div class="tabs">${tabs}</div>
          <div class="frame">${frame}</div>
        </div>
        <div>
          <div class="metric-grid">
            ${metrics.map(([k, v]) => `<div class="metric"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('')}
          </div>
          <div class="detail-section">
            <h3>文件</h3>
            <div class="path-list">
              <div>${esc(row.rel_path)}</div>
              <div>${esc(row.file)}</div>
            </div>
          </div>
          <div class="detail-section">
            <h3>操作</h3>
            <div style="display:flex;gap:7px;flex-wrap:wrap">
              ${row.overlay_url ? `<a class="btn sm" href="${esc(row.overlay_url)}" target="_blank" rel="noopener">${ICONS.external} 叠加图</a>` : ''}
              ${row.roi_url ? `<a class="btn sm" href="${esc(row.roi_url)}" download>${ICONS.download} ROI</a>` : ''}
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn sm" id="detail-prev" ${index <= 0 ? 'disabled' : ''}>${ICONS.left} 上一张</button>
      <button class="btn sm" id="detail-next" ${index >= rows.length - 1 ? 'disabled' : ''}>下一张 ${ICONS.right}</button>
      <button class="btn sm" id="detail-rerun">${ICONS.refresh} 重新运行</button>
      <button class="btn sm danger" id="detail-delete">${ICONS.trash} 删除这张图</button>
      <div style="flex:1"></div>
      <span class="hint">${index + 1} / ${rows.length}</span>
      <button class="btn" id="detail-close">关闭</button>
    </div>`, { wide: true });

  $('#detail-x').onclick = closeModal;
  $('#detail-close').onclick = closeModal;
  $('#detail-prev').onclick = () => openDetail(index - 1);
  $('#detail-next').onclick = () => openDetail(index + 1);
  $('#detail-delete').onclick = () => confirmDeleteImage(row);
  $('#detail-rerun').onclick = () => {
    const meta = state.currentRunMeta;
    if (!meta) return;
    state.pendingRerun = meta;
    closeModal();
    navigate('#/new');
  };
  $$('.detail-image .tabs button').forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('.detail-image .tabs button').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const img = $('#detail-img');
      if (img) img.src = images[Number(btn.dataset.tab)][1];
    });
  });
  const img = $('#detail-img');
  if (img) {
    img.addEventListener('error', () => {
      const frameEl = img.parentElement;
      frameEl.innerHTML = '<div class="none">图像加载失败</div>';
    });
  }
  state.detailKey = (e) => {
    if (e.key === 'ArrowLeft' && index > 0) openDetail(index - 1);
    if (e.key === 'ArrowRight' && index < rows.length - 1) openDetail(index + 1);
  };
  document.addEventListener('keydown', state.detailKey);
}

/* -------------------------------------------------------------- folder picker */
async function openFolderPicker(onPick, initialPath) {
  showModal(`
    <div class="modal-head">
      <div>
        <h2>选择图片文件夹</h2>
      </div>
      <button class="icon-btn close" id="fs-x" aria-label="关闭">${ICONS.x}</button>
    </div>
    <div class="modal-body">
      <div class="path-wrap" style="margin-bottom:10px">
        <input class="input" id="fs-path" spellcheck="false" placeholder="输入路径…">
        <button class="btn" id="fs-go">转到</button>
        <button class="btn" id="fs-up">上一级</button>
      </div>
      <div class="drive-row" id="fs-drives"></div>
      <div class="folder-list" id="fs-list"><div class="loading-card" style="padding:22px"><span class="spinner"></span> 读取目录…</div></div>
      <div class="hint" id="fs-msg" style="margin-top:8px"></div>
    </div>
    <div class="modal-foot">
      <span class="hint" id="fs-count"></span>
      <div style="flex:1"></div>
      <button class="btn" id="fs-cancel">取消</button>
      <button class="btn primary" id="fs-pick">选择此文件夹</button>
    </div>`, { wide: true });

  let current = '';
  let fs = null;
  const list = $('#fs-list');
  const msg = $('#fs-msg');

  const load = async (path) => {
    list.innerHTML = '<div class="loading-card" style="padding:22px"><span class="spinner"></span> 读取目录…</div>';
    msg.textContent = '';
    try {
      fs = await api(`/api/fs?path=${encodeURIComponent(path || '')}`);
      current = fs.path;
      $('#fs-path').value = current;
      $('#fs-count').textContent = `${fs.image_count || 0} 张图片`;
      const drives = (fs.drives || []).map((d) => `<button class="drive-chip" data-drive="${esc(d)}">${esc(d)}</button>`).join('');
      const shortcuts = (fs.shortcuts || []).map((s) => `<button class="drive-chip" data-drive="${esc(s.path)}">${esc(s.label)}</button>`).join('');
      $('#fs-drives').innerHTML = drives + (shortcuts ? `<span style="width:8px"></span>${shortcuts}` : '');
      if (!fs.dirs.length) {
        list.innerHTML = '<div class="loading-card" style="padding:22px;color:#64748b">此文件夹下没有子文件夹</div>';
      } else {
        list.innerHTML = fs.dirs.map((d) => `
          <div class="folder-row" data-path="${esc(d.path)}" title="${esc(d.path)}">
            ${ICONS.folder}<span class="name">${esc(d.name)}</span>
          </div>`).join('');
      }
      if (fs.error) msg.textContent = fs.error;
      $('#fs-drives').querySelectorAll('[data-drive]').forEach((btn) => {
        btn.addEventListener('click', () => load(btn.dataset.drive));
      });
      list.querySelectorAll('.folder-row').forEach((row) => {
        row.addEventListener('click', () => load(row.dataset.path));
      });
    } catch (err) {
      msg.textContent = err.message;
      list.innerHTML = '';
    }
  };

  $('#fs-x').onclick = closeModal;
  $('#fs-cancel').onclick = closeModal;
  $('#fs-pick').onclick = () => {
    if (!current) return;
    onPick(current);
    closeModal();
    toast('已添加文件夹', 'ok');
  };
  $('#fs-up').onclick = () => {
    if (fs && fs.parent) load(fs.parent);
  };
  $('#fs-go').onclick = () => load($('#fs-path').value.trim());
  $('#fs-path').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') load($('#fs-path').value.trim());
  });
  load(initialPath || '');
}

/* -------------------------------------------------------------------- modal */
function showModal(html, opts = {}) {
  closeModal();
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-backdrop"><div class="modal ${opts.wide ? '' : 'narrow'}">${html}</div></div>`;
  state.modalOnClose = typeof opts.onClose === 'function' ? opts.onClose : null;
  const backdrop = root.firstElementChild;
  backdrop.addEventListener('mousedown', (e) => {
    if (e.target === backdrop) closeModal();
  });
  document.body.style.overflow = 'hidden';
  return root.querySelector('.modal');
}

function closeModal() {
  const callback = state.modalOnClose;
  state.modalOnClose = null;
  const root = $('#modal-root');
  if (root) root.innerHTML = '';
  document.body.style.overflow = '';
  if (state.detailKey) {
    document.removeEventListener('keydown', state.detailKey);
    state.detailKey = null;
  }
  if (callback) {
    try { callback(); } catch { /* ignore */ }
  }
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeModal();
});

/* -------------------------------------------------------------------- error */
function errorHtml(title, message) {
  return `<div class="card empty">
    <div class="big">${ICONS.alert}</div>
    <h3>${esc(title)}</h3>
    <p>${esc(message)}</p>
    <a class="btn" href="#/runs">返回运行记录</a>
  </div>`;
}

function renderError(message) {
  cleanupPoll();
  const view = $('#view');
  view.innerHTML = errorHtml('页面加载失败', message);
}
