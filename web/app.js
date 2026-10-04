/* =========================================================
   群日记 —— 前端逻辑
   ========================================================= */
'use strict';

const WEEK_CN = ['', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'];
const MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

const state = {
  entries: [],
  recorders: [],
  years: [],
  config: {},
  hasToken: false,
  git: {},
  proxy: {},
  music: { tracks: [], dir: '', hasSources: false },

  readerSide: 'right',   // 'right' = 列表在左、正文在右；'left' = 交换
  readerWidth: 440,
  readerFont: 15,        // 正文字号（px）
  milestones: [],
  msSort: 'new',

  query: '',
  jumpDate: '',
  selRecorders: new Set(),
  selYears: new Set(),
  selMonths: new Set(),
  selWeeks: new Set(),
  sort: 'date-desc',

  shown: [],
  currentId: null,

  trackIndex: -1,
  loopMode: 'all',
  muted: false,
  lastVolume: 0.6,
  seeking: false,
  autoplayTried: false,
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

/* ------------------------- 通用工具 ------------------------- */

function toast(msg, bad) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { t.hidden = true; }, bad ? 5200 : 2600);
}

async function api(path, body) {
  const opt = body === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const res = await fetch(path, opt);
  let data = null;
  let text = '';
  try { text = await res.text(); } catch (e) { text = ''; }
  try {
    data = JSON.parse(text);
  } catch (e) {
    // 后台没返回 JSON —— 最常见的原因是「界面已更新、后台还是旧版程序」
    data = {
      ok: false,
      stale: res.status === 404,
      error: res.status === 404
        ? '没有 ' + path + ' 这个接口：界面已经是最新的，但后台还跑着旧版程序。\n' +
          '请点右上角 ⏻ 退出，再重新双击 启动群日记.pyw。'
        : ('服务器返回了无法识别的内容（HTTP ' + res.status + '）'),
    };
  }
  if (data && data.stale) showStaleBar(true);
  if (!res.ok && data && data.error) data.ok = false;
  return data;
}

function showStaleBar(on) {
  const bar = $('staleBar');
  if (bar) bar.hidden = !on;
}

function terms() {
  return state.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

function highlightNode(text, ts) {
  const frag = document.createDocumentFragment();
  if (!ts || !ts.length || !text) {
    frag.appendChild(document.createTextNode(text || ''));
    return frag;
  }
  const low = text.toLowerCase();
  const ranges = [];
  for (const t of ts) {
    let i = 0;
    while (true) {
      const idx = low.indexOf(t, i);
      if (idx < 0) break;
      ranges.push([idx, idx + t.length]);
      i = idx + t.length;
    }
  }
  if (!ranges.length) {
    frag.appendChild(document.createTextNode(text));
    return frag;
  }
  ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const merged = [ranges[0].slice()];
  for (const r of ranges.slice(1)) {
    const last = merged[merged.length - 1];
    if (r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push(r.slice());
  }
  let pos = 0;
  for (const [s, e] of merged) {
    if (s > pos) frag.appendChild(document.createTextNode(text.slice(pos, s)));
    const mk = document.createElement('mark');
    mk.textContent = text.slice(s, e);
    frag.appendChild(mk);
    pos = e;
  }
  if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
  return frag;
}

const URL_RE = /https?:\/\/[^\s<>"'）)】]+/g;

function richNode(text, ts) {
  const frag = document.createDocumentFragment();
  let last = 0, m;
  URL_RE.lastIndex = 0;
  while ((m = URL_RE.exec(text)) !== null) {
    if (m.index > last) frag.appendChild(highlightNode(text.slice(last, m.index), ts));
    const a = el('a', null, m[0]);
    a.href = m[0]; a.target = '_blank'; a.rel = 'noreferrer';
    frag.appendChild(a);
    last = m.index + m[0].length;
  }
  frag.appendChild(highlightNode(text.slice(last), ts));
  return frag;
}

function fmtDate(iso) {
  if (!iso) return '日期未知';
  const p = iso.split('-').map(Number);
  return `${p[0]}年${p[1]}月${p[2]}日`;
}
function dateLabelOf(e) { return e.dateLabel || '日期未知'; }
function weekTag(e) {
  if (!e.weekday) return null;
  const t = el('span', 'tag' + (e.weekday >= 6 ? ' weekend' : ''), WEEK_CN[e.weekday]);
  return t;
}
function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m + ':' + String(s).padStart(2, '0');
}

/* ------------------------- 载入与渲染 ------------------------- */

async function loadState() {
  const data = await api('/api/state');
  if (!data.ok) { toast(data.error || '无法连接本地服务', true); return; }
  state.entries = data.entries || [];
  state.recorders = data.recorders || [];
  state.years = data.years || [];
  state.music = data.music || { tracks: [] };
  state.milestones = data.milestones || [];
  state.git = data.git || {};
  state.proxy = data.proxy || {};
  state.config = data.config || {};
  state.hasToken = !!data.hasToken;
  if (data.program && data.program.stale) showStaleBar(true);

  applyTheme(state.config.theme || 'light');
  if (state.config.reader_side) state.readerSide = state.config.reader_side;
  if (state.config.reader_width) state.readerWidth = Number(state.config.reader_width) || 440;
  if (state.config.reader_font) state.readerFont = Number(state.config.reader_font) || 15;
  state.msSort = state.config.milestone_sort === 'old' ? 'old' : 'new';
  applyReaderFont();
  buildFilters();
  applyFilters();
  initPlayer();
  renderGitBadge();
  applyReaderLayout();
  initSplitter();
}

function buildFilters() {
  // 记录人
  const rc = $('recorderChips');
  rc.textContent = '';
  for (const r of state.recorders) {
    const c = el('button', 'chip', r);
    c.onclick = () => { toggleSet(state.selRecorders, r); buildFilters(); applyFilters(); };
    if (state.selRecorders.has(r)) c.classList.add('on');
    rc.appendChild(c);
  }
  if (!state.recorders.length) rc.appendChild(el('span', 'muted small', '暂无记录人'));

  // 年份
  const yc = $('yearChips');
  yc.textContent = '';
  for (const y of state.years) {
    const c = el('button', 'chip', String(y));
    c.onclick = () => { toggleSet(state.selYears, y); buildFilters(); applyFilters(); };
    if (state.selYears.has(y)) c.classList.add('on');
    yc.appendChild(c);
  }
  if (!state.years.length) yc.appendChild(el('span', 'muted small', '暂无数据'));

  // 月份
  const mc = $('monthChips');
  mc.textContent = '';
  for (const m of MONTHS) {
    const c = el('button', 'chip', m + '月');
    c.onclick = () => { toggleSet(state.selMonths, m); buildFilters(); applyFilters(); };
    if (state.selMonths.has(m)) c.classList.add('on');
    mc.appendChild(c);
  }

  // 星期
  const wc = $('weekChips');
  wc.textContent = '';
  for (let w = 1; w <= 7; w++) {
    const c = el('button', 'chip', WEEK_CN[w].replace('星期', '周'));
    c.onclick = () => { toggleSet(state.selWeeks, w); buildFilters(); applyFilters(); };
    if (state.selWeeks.has(w)) c.classList.add('on');
    wc.appendChild(c);
  }

  // 记录人下拉候选
  const dl = $('recorderList');
  dl.textContent = '';
  for (const r of state.recorders) {
    const o = document.createElement('option');
    o.value = r;
    dl.appendChild(o);
  }
}

function toggleSet(set, v) {
  if (set.has(v)) set.delete(v); else set.add(v);
}

function applyFilters() {
  const ts = terms();
  const list = state.entries.filter((e) => {
    if (state.jumpDate && e.date !== state.jumpDate) return false;
    if (state.selRecorders.size && !state.selRecorders.has(e.recorder)) return false;
    if (state.selYears.size && !state.selYears.has(e.year)) return false;
    if (state.selMonths.size && !state.selMonths.has(e.month)) return false;
    if (state.selWeeks.size && !state.selWeeks.has(e.weekday)) return false;
    if (ts.length) {
      const hay = (e.text + '\n' + e.recorder + '\n' + e.dateLabel + '\n' + fmtDate(e.date)).toLowerCase();
      if (!ts.every((t) => hay.includes(t))) return false;
    }
    return true;
  });

  const by = {
    'date-desc': (a, b) => (b.date || '').localeCompare(a.date || '') || a.recorder.localeCompare(b.recorder),
    'date-asc': (a, b) => (a.date || '').localeCompare(b.date || '') || a.recorder.localeCompare(b.recorder),
    'recorder': (a, b) => a.recorder.localeCompare(b.recorder, 'zh') || (b.date || '').localeCompare(a.date || ''),
    'count': (a, b) => b.count - a.count || (b.date || '').localeCompare(a.date || ''),
  };
  list.sort(by[state.sort] || by['date-desc']);

  state.shown = list;
  renderList();
  renderStats();
  renderListTitle();
}

function renderListTitle() {
  const parts = [];
  if (state.jumpDate) parts.push(fmtDate(state.jumpDate));
  if (state.selRecorders.size) parts.push([...state.selRecorders].join('、'));
  if (state.selYears.size) parts.push([...state.selYears].map((y) => y + '年').join('、'));
  if (state.selMonths.size) parts.push([...state.selMonths].sort((a, b) => a - b).map((m) => m + '月').join('、'));
  if (state.selWeeks.size) parts.push([...state.selWeeks].sort((a, b) => a - b).map((w) => WEEK_CN[w]).join('、'));
  if (state.query.trim()) parts.push('关键词「' + state.query.trim() + '」');

  const t = $('listTitle');
  t.textContent = '';
  if (!parts.length) { t.textContent = '全部日记'; return; }
  t.appendChild(document.createTextNode('筛选：'));
  const em = el('em', null, parts.join(' · '));
  t.appendChild(em);
}

function renderStats() {
  $('statShown').textContent = state.shown.length;
  $('statTotal').textContent = state.entries.length;
  const items = state.shown.reduce((s, e) => s + e.count, 0);
  let wd = '';
  if (state.shown.length) {
    const counts = {};
    state.shown.forEach((e) => { if (e.weekday) counts[e.weekday] = (counts[e.weekday] || 0) + 1; });
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    if (top) wd = '，' + WEEK_CN[top[0]] + '最多';
  }
  $('statHint').textContent = `共 ${items} 条记录${wd}`;
}

function renderList() {
  const box = $('cards');
  box.textContent = '';
  const ts = terms();

  if (!state.shown.length) {
    const empty = el('div', 'empty');
    empty.appendChild(el('span', 'big', '🗒'));
    if (state.entries.length === 0) {
      empty.appendChild(el('div', null, '还没有任何日记'));
      empty.appendChild(el('div', 'muted', '点右上角「＋ 写日记」开始记录第一篇'));
    } else {
      empty.appendChild(el('div', null, '没有符合条件的日记'));
      empty.appendChild(el('div', 'muted', '试试放宽筛选条件或换个关键词'));
    }
    box.appendChild(empty);
    return;
  }

  for (const e of state.shown) {
    const card = el('div', 'card' + (e.id === state.currentId ? ' active' : ''));
    card.onclick = () => openReader(e.id);

    const top = el('div', 'card-top');
    top.appendChild(el('span', 'card-date', dateLabelOf(e)));
    const wt = weekTag(e); if (wt) top.appendChild(wt);
    top.appendChild(el('span', 'recorder-pill', e.recorder));
    top.appendChild(el('span', 'card-count', e.count + ' 条'));
    card.appendChild(top);

    const lines = el('div', 'card-lines');
    // 有搜索词时优先展示命中的那几条，否则展示前 3 条
    let pick = [];
    if (ts.length) {
      e.items.forEach((it, i) => {
        const low = it.toLowerCase();
        if (ts.some((t) => low.includes(t))) pick.push(i);
      });
    }
    if (!pick.length) pick = e.items.map((_, i) => i).slice(0, 3);
    const shownIdx = new Set(pick.slice(0, 3));
    [...shownIdx].sort((a, b) => a - b).forEach((i) => {
      const line = el('div', 'card-line');
      line.appendChild(el('b', null, (i + 1) + '.'));
      line.appendChild(highlightNode(e.items[i].replace(/\n/g, ' ⏎ '), ts));
      lines.appendChild(line);
    });
    card.appendChild(lines);
    const rest = e.items.length - shownIdx.size;
    if (rest > 0) {
      card.appendChild(el('div', 'card-more', ts.length ? `…… 另有 ${rest} 条` : `…… 还有 ${rest} 条`));
    }
    if (e.warnings && e.warnings.length) {
      card.appendChild(el('div', 'card-warn', '⚠ ' + e.warnings[0]));
    }
    box.appendChild(card);
  }
}

/* ------------------------- 阅读面板 ------------------------- */

function findEntry(id) { return state.entries.find((e) => e.id === id) || null; }

function openReader(id) {
  const e = findEntry(id);
  if (!e) return;
  state.currentId = id;
  renderReader(e);
  renderList();
}

function renderReader(e) {
  const r = $('reader');
  r.hidden = false;
  syncSplitter();
  $('readerDate').textContent = dateLabelOf(e);
  const sub = $('readerWeekday');
  sub.textContent = e.weekday ? WEEK_CN[e.weekday] : '星期未知';
  sub.className = 'tag' + (e.weekday >= 6 ? ' weekend' : '');
  $('readerRecorder').textContent = e.recorder;
  $('readerFile').textContent = e.file;

  const warn = $('readerWarn');
  if (e.warnings && e.warnings.length) {
    warn.hidden = false;
    warn.textContent = '⚠ ' + e.warnings.join('；') + '（保存一次即可修正为标准格式）';
  } else warn.hidden = true;

  const ts = terms();
  const box = $('readerItems');
  box.textContent = '';
  e.items.forEach((it, i) => {
    const row = el('div', 'ritem');
    row.appendChild(el('div', 'num', String(i + 1)));
    const txt = el('div', 'txt');
    txt.appendChild(richNode(it, ts));
    row.appendChild(txt);
    box.appendChild(row);
  });
  if (!e.items.length) box.appendChild(el('div', 'muted', '（这篇日记正文为空）'));
  box.scrollTop = 0;

  const idx = state.shown.findIndex((x) => x.id === e.id);
  $('readerPrev').disabled = idx <= 0;
  $('readerNext').disabled = idx < 0 || idx >= state.shown.length - 1;
}

function stepReader(delta) {
  const idx = state.shown.findIndex((x) => x.id === state.currentId);
  if (idx < 0) return;
  const next = state.shown[idx + delta];
  if (next) openReader(next.id);
}

function closeReader() {
  $('reader').hidden = true;
  syncSplitter();
  state.currentId = null;
  renderList();
}

/* ------------------------- 编辑器 ------------------------- */

let editorOriginalId = null;

/** 本地时区的今天（不能用 toISOString，那是 UTC，在中国会差一天） */
function localToday() {
  const d = new Date();
  return d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0');
}

/** 找出「同一天 + 同一个人」是否已经写过 —— 这是会互相覆盖的组合 */
function findExistingEntry(dateStr, recorder, excludeId) {
  if (!dateStr || !recorder) return null;
  return state.entries.find(
    (e) => e.date === dateStr && e.recorder === recorder && e.id !== excludeId
  ) || null;
}

function openEditor(entry) {
  editorOriginalId = entry ? entry.id : null;
  $('editorTitle').textContent = entry ? '编辑日记' : '写日记';
  $('edDate').value = entry ? entry.date : localToday();
  $('edRecorder').value = entry ? entry.recorder
    : (state.pendingNewRecorder || localStorage.getItem('qrj.recorder') || '');
  state.pendingNewRecorder = null;
  const box = $('edItems');
  box.textContent = '';
  const items = entry ? entry.items.slice() : [''];
  items.forEach((t) => addItemRow(t));
  if (!items.length) addItemRow('');
  updatePreview();
  $('editorHint').textContent = entry ? '保存后会覆盖原文件（文件名随记录人变化）' : '';
  $('editorModal').hidden = false;
  setTimeout(() => (entry ? $('edItems').querySelector('textarea') : $('edRecorder')).focus(), 40);
}

function addItemRow(text) {
  const box = $('edItems');
  const row = el('div', 'ed-row');
  const num = el('div', 'num', '');
  const ta = el('textarea');
  ta.value = text || '';
  ta.rows = 1;
  ta.addEventListener('input', () => { autoGrow(ta); updatePreview(); renumber(); });
  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); saveEditor(); }
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey) {
      ev.preventDefault();
      addItemRow('');
      renumber();
      const tas = box.querySelectorAll('textarea');
      tas[tas.length - 1].focus();
    }
    if (ev.key === 'Backspace' && ta.value === '' && box.children.length > 1) {
      ev.preventDefault();
      const prev = row.previousElementSibling;
      row.remove(); renumber(); updatePreview();
      if (prev) prev.querySelector('textarea').focus();
    }
  });

  const tools = el('div', 'row-tools');
  const up = el('button', 'btn icon', '↑');
  up.title = '上移';
  up.onclick = () => { const p = row.previousElementSibling; if (p) { box.insertBefore(row, p); renumber(); updatePreview(); } };
  const down = el('button', 'btn icon', '↓');
  down.title = '下移';
  down.onclick = () => { const n = row.nextElementSibling; if (n) { box.insertBefore(n, row); renumber(); updatePreview(); } };
  const del = el('button', 'btn icon danger', '✕');
  del.title = '删除这一条';
  del.onclick = () => { row.remove(); if (!box.children.length) addItemRow(''); renumber(); updatePreview(); };
  tools.append(up, down, del);

  row.append(num, ta, tools);
  box.appendChild(row);
  autoGrow(ta);
  renumber();
}

function autoGrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight + 2, 260) + 'px';
}

function renumber() {
  [...$('edItems').children].forEach((row, i) => { row.querySelector('.num').textContent = String(i + 1); });
}

function collectItems() {
  return [...$('edItems').querySelectorAll('textarea')].map((t) => t.value.trim()).filter(Boolean);
}

function renderPreviewText(dateStr, recorder, items) {
  const p = (dateStr || '').split('-').map(Number);
  if (p.length !== 3 || p.some(isNaN)) return '（请先选择日期）';
  const lines = [`${p[0]}.${p[1]}.${p[2]} ${recorder || ''}`.replace(/\s+$/, '')];
  items.forEach((it, i) => {
    const seg = String(it).split('\n');
    lines.push(`${i + 1}.${seg[0].trim()}`);
    seg.slice(1).forEach((s) => { if (s.trim()) lines.push('    ' + s.trim()); });
  });
  lines.push('END');
  return lines.join('\n');
}

function updatePreview() {
  $('edPreview').textContent = renderPreviewText($('edDate').value, $('edRecorder').value.trim(), collectItems());
  updateDupWarn();
}

/** 同一天同一个人已经有日记时，醒目提示，并提供「追加」的出路 */
function updateDupWarn() {
  const box = $('editorDup');
  const dup = findExistingEntry($('edDate').value, $('edRecorder').value.trim(), editorOriginalId);
  box.textContent = '';
  if (!dup) { box.hidden = true; return; }

  box.hidden = false;
  box.appendChild(el('span', null,
    `⚠ 「${dup.dateLabel} ${dup.recorder}」已经写过 ${dup.count} 条内容了。` +
    `直接保存会覆盖它（旧内容会移进 .trash，可以找回）。`));

  const btn = el('button', 'linkish', '＋ 把现在写的追加到那一篇');
  btn.onclick = () => {
    const merged = dup.items.concat(collectItems());
    $('editorModal').hidden = true;
    openEditor(dup);
    const list = $('edItems');
    list.textContent = '';
    merged.forEach((t) => addItemRow(t));
    renumber();
    updatePreview();
    toast('已把新写的几条接到那一篇后面，确认没问题再保存');
  };
  box.appendChild(btn);
}

async function saveEditor() {
  const date = $('edDate').value;
  const recorder = $('edRecorder').value.trim();
  if (!date) { toast('请选择日期', true); return; }
  if (!recorder) { toast('请填写记录人', true); return; }
  const items = collectItems();
  if (!items.length) { toast('正文至少写一条内容', true); return; }

  // 会覆盖另一篇日记时，必须让人明确确认一次
  const dup = findExistingEntry(date, recorder, editorOriginalId);
  if (dup) {
    const go = confirm(
      `「${dup.dateLabel} ${dup.recorder}」已经写过 ${dup.count} 条内容。\n\n` +
      `继续保存会覆盖它。旧内容会被移到 .trash 文件夹，之后可以找回。\n\n` +
      `如果想两边都保留，请点「取消」，再点编辑器里的` +
      `「＋ 把现在写的追加到那一篇」。`
    );
    if (!go) return;
  }

  $('btnEditorSave').disabled = true;
  const res = await api('/api/save', { date, recorder, items, originalId: editorOriginalId });
  $('btnEditorSave').disabled = false;

  if (!res.ok) { toast(res.error || '保存失败', true); return; }
  state.entries = res.entries || [];
  state.recorders = [...new Set(state.entries.map((e) => e.recorder))].sort();
  state.years = [...new Set(state.entries.map((e) => e.year).filter(Boolean))].sort((a, b) => b - a);
  localStorage.setItem('qrj.recorder', recorder);
  $('editorModal').hidden = true;
  buildFilters();
  applyFilters();
  const saved = state.entries.find((e) => e.file === res.file);
  if (saved) openReader(saved.id);
  toast('已保存：' + res.file);
}

/* ------------------------- 日期输入 ------------------------- */

const DATE_TEXT_RE = /^(\d{4})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]\s*(\d{1,2})\s*日?$/;
const DATE_COMPACT_RE = /^(\d{4})(\d{2})(\d{2})$/;

/**
 * 解析用户手输的日期。
 * 返回 { state: 'empty' | 'ok' | 'invalid' | 'garbage', ... }
 *   empty   —— 空，清掉筛选
 *   ok      —— 合法，带 y/m/d/label
 *   invalid —— 格式对但日期不存在（13 月、2月30日…），要提示具体原因
 *   garbage —— 根本不是日期格式
 */
function parseDateInput(text) {
  const s = String(text || '').trim();
  if (!s) return { state: 'empty' };
  let m = s.match(DATE_TEXT_RE) || s.match(DATE_COMPACT_RE);
  if (!m) return { state: 'garbage' };
  const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
  if (y < 1900 || y > 2999) return { state: 'invalid', why: '年份要在 1900–2999 之间' };
  if (mo < 1 || mo > 12) return { state: 'invalid', why: '月份只能是 1–12' };
  if (d < 1 || d > 31) return { state: 'invalid', why: '日期只能是 1–31' };
  const dt = new Date(y, mo - 1, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
    return { state: 'invalid', why: y + '年' + mo + '月没有 ' + d + ' 号' };
  }
  const iso = y + '-' + String(mo).padStart(2, '0') + '-' + String(d).padStart(2, '0');
  return { state: 'ok', iso: iso, y: y, mo: mo, d: d, label: y + '.' + mo + '.' + d };
}

let lastDateTip = '';
let dateTipAt = 0;

function showDateTip(msg) {
  // 同一个提示 3 秒内只弹一次，免得边打字边刷屏
  const now = Date.now();
  if (msg === lastDateTip && now - dateTipAt < 3000) return;
  lastDateTip = msg;
  dateTipAt = now;
  toast(msg, true);
}

function markDateBad(bad) {
  $('jumpDateText').classList.toggle('bad', bad);
  document.querySelector('.date-jump').classList.toggle('bad', bad);
}

/** quiet=true 时只标记不弹提示（用于打字过程中的实时校验） */
function applyJumpDate(quiet) {
  const box = $('jumpDateText');
  const r = parseDateInput(box.value);

  if (r.state === 'empty') {
    markDateBad(false);
    if (state.jumpDate) {
      state.jumpDate = '';
      $('jumpDatePicker').value = '';
      applyFilters();
    }
    return true;
  }

  if (r.state === 'ok') {
    markDateBad(false);
    box.value = r.label;
    $('jumpDatePicker').value = r.iso;
    const changed = state.jumpDate !== r.iso;
    state.jumpDate = r.iso;
    applyFilters();
    if (changed) {
      const hit = state.shown[0];
      if (hit) openReader(hit.id);
      else toast('这一天没有日记记录', true);
    }
    return true;
  }

  markDateBad(true);
  if (!quiet) {
    showDateTip(r.state === 'invalid'
      ? '日期不对：' + r.why
      : '请输入正确的日期格式，例如 2026.7.13');
  }
  return false;
}

let dateCheckTimer = null;

function bindDateInput() {
  const box = $('jumpDateText');

  box.addEventListener('input', () => {
    markDateBad(false);
    clearTimeout(dateCheckTimer);
    dateCheckTimer = setTimeout(() => {
      const r = parseDateInput(box.value);
      if (r.state === 'invalid' || r.state === 'garbage') {
        markDateBad(true);
        showDateTip(r.state === 'invalid'
          ? '日期不对：' + r.why
          : '请输入正确的日期格式，例如 2026.7.13');
      } else if (r.state === 'ok') {
        // 格式已经完整且合法，顺手应用
        applyJumpDate(true);
      }
    }, 550);
  });

  box.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      clearTimeout(dateCheckTimer);
      applyJumpDate(false);
    } else if (ev.key === 'Escape') {
      box.value = state.jumpDate ? fmtDateDot(state.jumpDate) : '';
      markDateBad(false);
      box.blur();
    }
  });

  box.addEventListener('blur', () => {
    clearTimeout(dateCheckTimer);
    if (box.value.trim()) applyJumpDate(false);
  });

  // 旁边的小日历按钮：弹出原生选择器，选完填回文本框
  const picker = $('jumpDatePicker');
  $('btnJumpCalendar').addEventListener('click', () => {
    try {
      if (typeof picker.showPicker === 'function') picker.showPicker();
      else { picker.style.pointerEvents = 'auto'; picker.click(); picker.style.pointerEvents = ''; }
    } catch (e) {
      picker.style.pointerEvents = 'auto';
      picker.click();
      picker.style.pointerEvents = '';
    }
  });
  picker.addEventListener('change', () => {
    if (!picker.value) return;
    const p = picker.value.split('-').map(Number);
    box.value = p[0] + '.' + p[1] + '.' + p[2];
    state.jumpDate = '';
    applyJumpDate(true);
    const hit = state.shown[0];
    if (hit) openReader(hit.id);
    else toast('这一天没有日记记录', true);
  });
}

function fmtDateDot(iso) {
  if (!iso) return '';
  const m = String(iso).match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (!m) return String(iso);
  return Number(m[1]) + '.' + Number(m[2]) + '.' + Number(m[3]);
}

/* ------------------------- 左右分界：拖动调比例 / 交换位置 ------------------------- */

let splitterReady = false;

function applyReaderLayout() {
  const body = document.querySelector('.body');
  const r = $('reader');
  state.readerSide = (state.readerSide === 'left') ? 'left' : 'right';
  body.classList.toggle('reader-left', state.readerSide === 'left');

  const sidebar = document.querySelector('.sidebar');
  const sbW = (sidebar && sidebar.offsetWidth) ? sidebar.offsetWidth : 0;
  const maxW = Math.max(300, window.innerWidth - sbW - 320 - 7);
  const w = Math.max(300, Math.min(Number(state.readerWidth) || 440, maxW));
  r.style.flexBasis = w + 'px';
  r.style.width = w + 'px';

  const btn = $('btnSwapPanes');
  if (btn) {
    btn.title = state.readerSide === 'left'
      ? '把正文移到右边'
      : '把正文移到左边';
  }
  syncSplitter();
}

function syncSplitter() {
  const sp = $('splitter');
  if (!sp) return;
  sp.hidden = $('reader').hidden;
}

function initSplitter() {
  if (splitterReady) return;
  splitterReady = true;

  const sp = $('splitter');
  let dragging = false;

  const sidebarW = () => {
    const sb = document.querySelector('.sidebar');
    return (sb && sb.offsetWidth) ? sb.offsetWidth : 0;
  };

  sp.addEventListener('mousedown', (ev) => {
    if (ev.target === $('btnSwapPanes')) return;
    dragging = true;
    sp.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    ev.preventDefault();
  });

  window.addEventListener('mousemove', (ev) => {
    if (!dragging) return;
    const rect = document.querySelector('.body').getBoundingClientRect();
    let w = (state.readerSide === 'left')
      ? (ev.clientX - rect.left - sidebarW())
      : (rect.right - ev.clientX);
    const maxW = Math.max(300, rect.width - sidebarW() - 320 - 7);
    w = Math.max(300, Math.min(w, maxW));
    state.readerWidth = Math.round(w);
    applyReaderLayout();
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    sp.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    api('/api/config', { reader_width: state.readerWidth });
  });

  // 双击分界线 = 复位成默认 440
  sp.addEventListener('dblclick', () => {
    state.readerWidth = 440;
    applyReaderLayout();
    api('/api/config', { reader_width: 440 });
    toast('已恢复默认宽度');
  });

  $('btnSwapPanes').addEventListener('click', (ev) => {
    ev.stopPropagation();
    state.readerSide = (state.readerSide === 'left') ? 'right' : 'left';
    applyReaderLayout();
    api('/api/config', { reader_side: state.readerSide });
    toast(state.readerSide === 'left' ? '正文已移到左边' : '正文已移到右边');
  });

  window.addEventListener('resize', () => applyReaderLayout());
}

/* ------------------------- 左右分界结束 ------------------------- */

/* ------------------------- 音乐播放器 ------------------------- */

const audio = () => $('audio');
let saveVolumeTimer = null;

function initPlayer() {
  const a = audio();
  const vol = typeof state.config.volume === 'number' ? state.config.volume : 0.6;
  state.lastVolume = vol;
  a.volume = vol;
  $('volume').value = Math.round(vol * 100);
  state.loopMode = state.config.loop_mode || 'all';
  updateLoopButton();
  renderPlaylist();
  if (state.music.tracks.length) loadTrack(0, false);
  else updateNowPlaying();

  if (!initPlayer._bound) {
    initPlayer._bound = true;
    a.addEventListener('timeupdate', () => {
      if (state.seeking) return;
      const d = a.duration || 0;
      $('seek').value = d ? Math.round((a.currentTime / d) * 1000) : 0;
      $('curTime').textContent = fmtTime(a.currentTime);
      $('durTime').textContent = fmtTime(d);
    });
    a.addEventListener('loadedmetadata', () => { $('durTime').textContent = fmtTime(a.duration); });
    a.addEventListener('play', () => { $('btnPlay').textContent = '⏸'; document.querySelector('.np').classList.add('playing'); });
    a.addEventListener('pause', () => { $('btnPlay').textContent = '▶'; document.querySelector('.np').classList.remove('playing'); });
    a.addEventListener('ended', onTrackEnded);
    a.addEventListener('error', () => {
      if (state.trackIndex >= 0) toast('这个音源无法播放：' + currentTrack().name, true);
    });
    document.addEventListener('click', function once() {
      document.removeEventListener('click', once);
      maybeAutoplay();
    }, { once: true });
  }
  maybeAutoplay();
}

function currentTrack() { return state.music.tracks[state.trackIndex] || null; }

function loadTrack(index, autoplay) {
  const tracks = state.music.tracks;
  if (!tracks.length) { updateNowPlaying(); return; }
  index = ((index % tracks.length) + tracks.length) % tracks.length;
  state.trackIndex = index;
  const t = tracks[index];
  audio().src = t.src;
  updateNowPlaying();
  renderPlaylist();
  if (autoplay !== false) play();
}

function play() {
  const a = audio();
  if (!currentTrack()) { toast('还没有音源，先把音频放进 music 文件夹', true); return; }
  const p = a.play();
  if (p && p.catch) {
    p.catch(() => {
      toast('浏览器拦截了自动播放，点一下播放按钮 ▶ 即可');
    });
  }
}

function maybeAutoplay() {
  if (state.autoplayTried) return;
  if (!state.config.autoplay) return;
  if (!state.music.tracks.length) return;
  state.autoplayTried = true;
  if (state.trackIndex < 0) loadTrack(0, true);
  else play();
}

function togglePlay() {
  const a = audio();
  if (!currentTrack()) { toast('还没有音源，先把音频放进 music 文件夹', true); return; }
  if (a.paused) play(); else a.pause();
}

function onTrackEnded() {
  const mode = state.loopMode;
  if (mode === 'one') { audio().currentTime = 0; play(); return; }
  if (mode === 'shuffle') {
    const n = state.music.tracks.length;
    if (n <= 1) { play(); return; }
    let next = state.trackIndex;
    while (next === state.trackIndex) next = Math.floor(Math.random() * n);
    loadTrack(next, true);
    return;
  }
  if (mode === 'off' && state.trackIndex >= state.music.tracks.length - 1) {
    updateNowPlaying();
    return;
  }
  loadTrack(state.trackIndex + 1, true);
}

function updateLoopButton() {
  const map = { all: ['🔁', '列表循环'], one: ['🔂', '单曲循环'], shuffle: ['🔀', '随机播放'], off: ['➡️', '播完停止'] };
  const [icon, title] = map[state.loopMode] || map.all;
  $('btnLoop').textContent = icon;
  $('btnLoop').title = '循环模式：' + title + '（点击切换）';
}

function updateNowPlaying() {
  const t = currentTrack();
  const np = document.querySelector('.np');
  if (!t) {
    $('trackName').textContent = '未选择音源';
    $('trackKind').textContent = state.music.tracks.length ? '点击播放' : '把音频放进 music 文件夹即可自动出现';
    np.classList.remove('playing');
    return;
  }
  $('trackName').textContent = t.name;
  const pos = `${state.trackIndex + 1}/${state.music.tracks.length}`;
  $('trackKind').textContent = t.kind === 'url' ? `网络音源 · ${pos}` : `本地文件 · ${pos}${t.exists ? '' : ' · ⚠ 文件缺失'}`;
}

function renderPlaylist() {
  const box = $('playlistItems');
  box.textContent = '';
  if (!state.music.tracks.length) {
    box.appendChild(el('div', 'playlist-foot muted', 'music 文件夹里还没有音频文件'));
  }
  state.music.tracks.forEach((t, i) => {
    const row = el('div', 'pl-item' + (i === state.trackIndex ? ' on' : ''));
    row.appendChild(el('div', 'idx', String(i + 1)));
    row.appendChild(el('div', 'nm', t.name));
    if (!t.exists) row.appendChild(el('span', 'miss', '缺失'));
    row.onclick = () => { loadTrack(i, true); };
    box.appendChild(row);
  });
  $('playlistHint').textContent = state.music.hasSources
    ? '音源来自 sources.txt（点 📝 可编辑）'
    : '自动扫描 music 文件夹（写一份 sources.txt 可自定义顺序）';
}

async function refreshMusic(silent) {
  const res = await api('/api/music');
  if (!res.ok) { toast('扫描音源失败', true); return; }
  const keepName = currentTrack() ? currentTrack().name : null;
  state.music = res.music;
  let idx = state.music.tracks.findIndex((t) => t.name === keepName);
  if (idx < 0) idx = 0;
  if (state.music.tracks.length) {
    const wasPlaying = !audio().paused;
    loadTrack(idx, wasPlaying);
  } else {
    state.trackIndex = -1;
    audio().removeAttribute('src');
    updateNowPlaying();
  }
  renderPlaylist();
  if (!silent) toast(`已重新扫描，共 ${state.music.tracks.length} 个音源`);
}

/* ------------------------- Git 面板 ------------------------- */

function renderGitBadge() {
  const g = state.git || {};
  const b = $('gitBadge');
  b.className = 'badge';
  if (!g.available) { b.textContent = '未检测到 Git'; return; }
  if (!g.isRepo) { b.textContent = '未启用同步'; return; }
  const bits = [];
  if (g.branch) bits.push(g.branch);
  if (g.changes && g.changes.length) bits.push(`${g.changes.length} 处待提交`);
  if (g.ahead) bits.push(`↑${g.ahead}`);
  if (g.behind) bits.push(`↓${g.behind}`);
  b.textContent = '☁ ' + (bits.join(' · ') || '已同步');
  if (g.inConflict) { b.classList.add('dirty'); b.textContent = '☁ 有冲突'; }
  else if (g.changes && g.changes.length) b.classList.add('dirty');
  else b.classList.add('ok');
}

function renderGitPanel() {
  const g = state.git || {};
  const box = $('gitStatusBox');
  box.textContent = '';
  const add = (k, v) => {
    const d = el('div');
    d.appendChild(el('span', 'k', k + '：'));
    d.appendChild(el('span', 'v', v));
    box.appendChild(d);
  };
  if (!g.available) {
    add('状态', '未检测到 git 命令');
    add('提示', '请先安装 Git for Windows 后重启本程序');
  } else if (!g.isRepo) {
    add('状态', '当前文件夹还不是 Git 仓库');
    add('下一步', '填好仓库地址后点「初始化仓库」或「从远程克隆」');
  } else {
    add('分支', g.branch || '-');
    add('远程', g.remote || '（未绑定）');
    add('待提交改动', String((g.changes || []).length) + ' 处');
    add('领先 / 落后远程', `${g.ahead || 0} / ${g.behind || 0} 个提交`);
    add('最近提交', g.lastCommit || '（还没有提交）');
    if (g.inConflict) add('⚠', '存在未解决的冲突');
  }

  // 代理
  const px = state.proxy || {};
  if (g.available) {
    const mode = px.mode || 'auto';
    let desc;
    if (!px.effective) desc = '不使用代理';
    else if (mode === 'custom') desc = px.effective + '（手动指定）';
    else desc = px.effective + '（自动读取系统代理）';
    add('代理', desc);
    if (px.inRepo && px.inRepo !== (px.effective || '')) {
      add('⚠ 仓库里残留', px.inRepo + ' —— 下次同步会自动纠正');
    }
  }

  $('gitRepoUrl').value = g.remote || state.config.repo_url || '';
  $('gitBranch').value = state.config.branch || g.branch || 'main';
  $('gitToken').placeholder = state.hasToken ? '已保存令牌（留空表示不改动）' : 'ghp_… 粘贴你的访问令牌';
  $('gitProxyMode').value = px.mode || 'auto';
  // 非手动模式时不显示残留的手填地址，免得让人以为正在用它
  $('gitProxyValue').value = (px.mode === 'custom') ? (px.value || '') : '';
  updateProxyLine();
}

function updateProxyLine() {
  const px = state.proxy || {};
  const mode = $('gitProxyMode').value;
  const line = $('proxyLine');
  const input = $('gitProxyValue');
  const sys = px.system;

  input.disabled = (mode !== 'custom');
  if (mode === 'custom') {
    if (!input.value.trim() && px.value) input.value = px.value;
    input.placeholder = '例如 http://127.0.0.1:7897';
  } else if (mode === 'none') {
    input.placeholder = '已选择不使用代理';
  } else {
    input.placeholder = sys ? ('自动模式，将使用 ' + sys) : '自动模式：未检测到系统代理';
  }

  const eff = mode === 'none' ? ''
    : (mode === 'custom' ? input.value.trim() : (sys || ''));

  line.textContent = '';
  line.appendChild(document.createTextNode('系统代理：'));
  line.appendChild(el('b', null, sys || '未检测到'));
  line.appendChild(document.createTextNode('　·　将使用：'));
  line.appendChild(el('b', null, eff || '不经过代理'));
  if (mode === 'custom' && !eff) {
    line.appendChild(document.createTextNode('　·　请在上面的输入框里填地址'));
  }
}

async function gitAction(action, extra) {
  const pmode = $('gitProxyMode').value;
  const body = Object.assign({
    action,
    repo_url: $('gitRepoUrl').value.trim(),
    branch: $('gitBranch').value.trim() || 'main',
    proxy_mode: pmode,
    autoResolve: $('chkAutoResolve').checked,
  }, extra || {});
  // 只有手动模式才提交地址，避免把已保存的值清空
  if (pmode === 'custom') body.proxy_value = $('gitProxyValue').value.trim();
  const tok = $('gitToken').value.trim();
  if (tok) body.token = tok;

  const out = $('gitOutput');
  const hintBox = $('gitHint');
  out.textContent = '正在执行…';
  hintBox.hidden = true;
  hintBox.textContent = '';

  const res = await api('/api/git', body);
  const lines = [];
  if (res.output) lines.push(res.output);
  if (res.error) lines.push('⚠ ' + res.error);
  out.textContent = lines.join('\n\n') || (res.ok ? '完成。' : '（没有输出）');

  if (res.hint) {
    hintBox.hidden = false;
    hintBox.textContent = '💡 ' + res.hint;
  }

  if (res.proxy) state.proxy = res.proxy;
  if (res.git) { state.git = res.git; renderGitBadge(); renderGitPanel(); }
  if (tok) { $('gitToken').value = ''; state.hasToken = true; }
  if (res.entries) state.entries = res.entries;

  if (action === 'clone' && res.ok) {
    await loadState();
    toast('克隆完成，日记已载入');
  } else {
    toast(res.ok ? '操作完成' : '操作未成功，看下方提示', !res.ok);
  }
}

/* ------------------------- 音源清单 ------------------------- */

async function openSources() {
  const res = await api('/api/music/sources');
  $('sourcesText').value = res.text || '';
  $('sourcesHint').textContent = res.text && res.text.trim()
    ? '当前音源顺序由本文件决定。'
    : '当前文件为空 —— 程序正在自动扫描 music 文件夹。';
  $('sourcesModal').hidden = false;
  setTimeout(() => $('sourcesText').focus(), 40);
}

/* ------------------------- 正文字号 ------------------------- */

let saveFontTimer = null;

function applyReaderFont() {
  const size = Math.max(12, Math.min(28, Number(state.readerFont) || 15));
  state.readerFont = size;
  document.documentElement.style.setProperty('--reader-font', size + 'px');
  const lbl = $('fontLabel');
  if (lbl) lbl.textContent = String(size);
}

function setReaderFont(size) {
  state.readerFont = Math.max(12, Math.min(28, size));
  applyReaderFont();
  clearTimeout(saveFontTimer);
  saveFontTimer = setTimeout(() => api('/api/config', { reader_font: state.readerFont }), 400);
}

/* ------------------------- 大事记 ------------------------- */

function msSorted() {
  const list = (state.milestones || []).slice();
  const dir = state.msSort === 'old' ? 1 : -1;
  list.sort((a, b) => {
    const d = String(a.date || '').localeCompare(String(b.date || ''));
    if (d !== 0) return d * dir;
    return String(a.recorder || '').localeCompare(String(b.recorder || ''), 'zh') * dir;
  });
  return list;
}

function renderMilestones() {
  const box = $('msList');
  box.textContent = '';
  const list = msSorted();

  $('msCount').textContent = list.length ? '共 ' + list.length + ' 件' : '';
  $('btnMsSort').textContent = state.msSort === 'old' ? '旧 → 新' : '新 → 旧';

  if (!list.length) {
    const empty = el('div', 'ms-empty');
    empty.appendChild(el('span', 'big', '🕳'));
    empty.appendChild(el('div', null, '不存在这样的事'));
    empty.appendChild(el('div', 'muted small', '点左上角的 ＋ 添加第一条'));
    box.appendChild(empty);
    return;
  }

  const ts = terms();
  for (const m of list) {
    const item = el('div', 'ms-item');

    const head = el('div', 'ms-itemhead');
    if (m.dateLabel) head.appendChild(el('span', 'ms-date', m.dateLabel));
    if (m.weekday) {
      head.appendChild(el('span', 'tag' + (m.weekday >= 6 ? ' weekend' : ''), WEEK_CN[m.weekday]));
    }
    head.appendChild(el('span', 'recorder-pill', m.recorder));
    item.appendChild(head);

    const txt = el('div', 'ms-text');
    txt.appendChild(richNode(m.text, ts));
    item.appendChild(txt);

    const del = el('button', 'ms-del', '🗑 删除');
    del.type = 'button';
    del.onclick = () => msDelete(m);
    item.appendChild(del);

    box.appendChild(item);
  }
}

async function msDelete(m) {
  const okGo = confirm('删除这条大事记？\n\n' +
    (m.dateLabel ? m.dateLabel + ' ' : '') + m.recorder + '\n' + m.text +
    '\n\n（会直接改写 大事记.txt，不能撤销）');
  if (!okGo) return;
  const res = await api('/api/milestone', { action: 'delete', id: m.id });
  if (!res.ok) { toast(res.error || '删除失败', true); return; }
  state.milestones = res.milestones || [];
  renderMilestones();
  toast('已删除');
}

function openMilestones() {
  $('msAddPanel').hidden = true;
  renderMilestones();
  $('msModal').hidden = false;
}

function closeMilestones() {
  $('msAddPanel').hidden = true;
  $('msModal').hidden = true;
}

function switchMsTab(name) {
  document.querySelectorAll('.ms-tab').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  $('msTabDiary').hidden = (name !== 'diary');
  $('msTabManual').hidden = (name !== 'manual');
  if (name === 'diary') renderMsPickList();
}

function renderMsPickList() {
  const box = $('msPickList');
  box.textContent = '';
  const q = $('msPickSearch').value.trim().toLowerCase();
  const ts = q ? q.split(/\s+/).filter(Boolean) : [];

  // 和大事记列表用同一套排序，按「新→旧 / 旧→新」一起变
  const dir = state.msSort === 'old' ? 1 : -1;
  const entries = state.entries.filter((e) => {
    if (!ts.length) return true;
    const hay = (e.text + '\n' + e.recorder + '\n' + e.dateLabel).toLowerCase();
    return ts.every((t) => hay.includes(t));
  }).sort((a, b) => {
    const d = String(a.date || '').localeCompare(String(b.date || ''));
    if (d !== 0) return d * dir;
    return String(a.recorder || '').localeCompare(String(b.recorder || ''), 'zh') * dir;
  });

  if (!entries.length) {
    box.appendChild(el('div', 'muted small', state.entries.length ? '没有匹配的日记' : '还没有任何日记'));
    return;
  }

  for (const e of entries) {
    const group = el('div', 'ms-pickgroup');
    const head = el('div', 'ms-pickhead');
    head.appendChild(el('span', 'ms-date', dateLabelOf(e)));
    if (e.weekday) {
      head.appendChild(el('span', 'tag' + (e.weekday >= 6 ? ' weekend' : ''), WEEK_CN[e.weekday]));
    }
    head.appendChild(el('span', 'recorder-pill', e.recorder));
    group.appendChild(head);

    e.items.forEach((it) => {
      const row = el('div', 'ms-pickitem');
      const t = el('div', 'txt');
      t.appendChild(highlightNode(it.replace(/\n/g, ' ⏎ '), ts));
      row.appendChild(t);

      const btn = el('button', 'ms-pickadd', '＋');
      btn.type = 'button';
      btn.title = '把这条加进大事记';
      const dup = (state.milestones || []).some(
        (m) => m.date === e.date && m.recorder === e.recorder && m.text === it);
      if (dup) {
        btn.disabled = true;
        btn.title = '已经在大事记里了';
      } else {
        btn.onclick = () => msAddFromDiary(e, it, btn);
      }
      row.appendChild(btn);
      group.appendChild(row);
    });
    box.appendChild(group);
  }
}

async function msAddFromDiary(entry, text, btn) {
  btn.disabled = true;
  const res = await api('/api/milestone', {
    action: 'add', date: entry.date, recorder: entry.recorder, text: text,
  });
  if (!res.ok) {
    btn.disabled = false;
    toast(res.error || '添加失败', true);
    return;
  }
  state.milestones = res.milestones || [];
  renderMilestones();
  toast('已加进大事记');
}

async function msSaveManual() {
  const recorder = $('msRecorder').value.trim();
  const text = $('msText').value.trim();
  if (!recorder) { toast('请填写记录人', true); $('msRecorder').focus(); return; }
  if (!text) { toast('正文不能为空', true); $('msText').focus(); return; }
  const res = await api('/api/milestone', {
    action: 'add', date: $('msDate').value, recorder: recorder, text: text,
  });
  if (!res.ok) { toast(res.error || '添加失败', true); return; }
  state.milestones = res.milestones || [];
  $('msText').value = '';
  renderMilestones();
  toast('已添加');
}

/* ------------------------- 主题 ------------------------- */

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme === 'dark' ? 'dark' : 'light');
  $('btnTheme').textContent = theme === 'dark' ? '☀️' : '🌙';
  $('btnTheme').title = theme === 'dark' ? '切换到浅色' : '切换到深色';
}

/* ------------------------- 事件绑定 ------------------------- */

function bind() {
  // 搜索
  let searchTimer = null;
  $('search').addEventListener('input', (ev) => {
    state.query = ev.target.value;
    $('searchClear').hidden = !state.query;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { applyFilters(); if (state.currentId) renderReader(findEntry(state.currentId)); }, 140);
  });
  $('searchClear').onclick = () => {
    $('search').value = ''; state.query = ''; $('searchClear').hidden = true;
    applyFilters(); if (state.currentId) renderReader(findEntry(state.currentId));
  };

  // 跳到某天（手输 + 日历按钮）
  bindDateInput();

  $('sortSel').addEventListener('change', (ev) => { state.sort = ev.target.value; applyFilters(); });
  $('btnNew').onclick = () => openEditor(null);

  $('btnClearFilters').onclick = () => {
    state.selRecorders.clear(); state.selYears.clear(); state.selMonths.clear(); state.selWeeks.clear();
    state.jumpDate = '';
    $('jumpDateText').value = '';
    $('jumpDatePicker').value = '';
    markDateBad(false);
    state.query = ''; $('search').value = ''; $('searchClear').hidden = true;
    buildFilters(); applyFilters();
  };

  $('btnOpenDiaryFolder').onclick = () => api('/api/open-folder', { which: 'diaries' });

  $('btnCleanup').onclick = async () => {
    if (!confirm('清理其他还在后台运行的群日记进程？\n\n（不影响当前这个窗口和你的日记；\n只会结束那些关了窗口却没退出的旧实例）')) return;
    const res = await api('/api/cleanup', {});
    if (!res.ok) { toast(res.error || '清理失败', true); return; }
    toast(res.message || '已清理', false);
  };

  // 正文字号
  $('btnFontUp').onclick = () => setReaderFont(state.readerFont + 1);
  $('btnFontDown').onclick = () => setReaderFont(state.readerFont - 1);
  $('btnFontReset').onclick = () => { setReaderFont(15); toast('字号已恢复默认'); };

  // 大事记
  $('btnMilestone').onclick = openMilestones;
  $('btnMsClose').onclick = closeMilestones;
  $('btnMsAdd').onclick = () => {
    $('msAddPanel').hidden = false;
    $('msDate').value = localToday();
    $('msRecorder').value = localStorage.getItem('qrj.recorder') || (state.recorders[0] || '');
    $('msPickSearch').value = '';
    switchMsTab('diary');
  };
  $('btnMsAddCancel').onclick = () => { $('msAddPanel').hidden = true; };
  $('btnMsSave').onclick = msSaveManual;
  $('msPickSearch').addEventListener('input', renderMsPickList);
  $('msText').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); msSaveManual(); }
  });
  document.querySelectorAll('.ms-tab').forEach((b) => {
    b.onclick = () => switchMsTab(b.dataset.tab);
  });
  $('btnMsSort').onclick = () => {
    state.msSort = (state.msSort === 'old') ? 'new' : 'old';
    renderMilestones();
    // 添加面板开着的话，从群日记选那一栏也要跟着换顺序
    if (!$('msAddPanel').hidden && !$('msTabDiary').hidden) renderMsPickList();
    api('/api/milestone', { action: 'sort', sort: state.msSort });
  };
  $('btnMsReload').onclick = async () => {
    const res = await api('/api/milestones');
    if (!res.ok) { toast('读取失败', true); return; }
    state.milestones = res.milestones || [];
    renderMilestones();
    toast('已重新读取 大事记.txt');
  };
  $('btnMsOpenFile').onclick = () => api('/api/open-folder', { which: 'root' });

  // 阅读面板
  $('btnCloseReader').onclick = closeReader;
  $('readerPrev').onclick = () => stepReader(-1);
  $('readerNext').onclick = () => stepReader(1);
  $('btnEdit').onclick = () => { const e = findEntry(state.currentId); if (e) openEditor(e); };
  $('btnDelete').onclick = async () => {
    const e = findEntry(state.currentId);
    if (!e) return;
    if (!confirm(`确定删除「${e.dateLabel} ${e.recorder}」吗？\n文件会被移到 .trash 文件夹，可以找回。`)) return;
    const res = await api('/api/delete', { id: e.id });
    if (!res.ok) { toast(res.error || '删除失败', true); return; }
    state.entries = res.entries || [];
    state.recorders = [...new Set(state.entries.map((x) => x.recorder))].sort();
    state.years = [...new Set(state.entries.map((x) => x.year).filter(Boolean))].sort((a, b) => b - a);
    closeReader(); buildFilters(); applyFilters();
    toast('已删除，文件在 .trash 里');
  };
  $('btnCopy').onclick = async () => {
    const e = findEntry(state.currentId);
    if (!e) return;
    const text = e.dateLabel + ' ' + e.recorder + '\n' + e.items.map((x, i) => `${i + 1}.${x}`).join('\n') + '\nEND';
    try { await navigator.clipboard.writeText(text); toast('正文已复制'); }
    catch (err) {
      const ta = el('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove(); toast('正文已复制');
    }
  };
  $('btnReveal').onclick = () => {
    if (state.currentId) api('/api/reveal', { id: state.currentId });
  };

  // 编辑器
  $('btnAddItem').onclick = () => { addItemRow(''); const tas = $('edItems').querySelectorAll('textarea'); tas[tas.length - 1].focus(); };
  $('btnEditorSave').onclick = saveEditor;
  $('edDate').addEventListener('change', updatePreview);
  $('edRecorder').addEventListener('input', updatePreview);
  $('editorModal').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); saveEditor(); }
  });

  // 音乐
  $('btnPlay').onclick = togglePlay;
  $('btnPrev').onclick = () => loadTrack(state.trackIndex - 1, true);
  $('btnNext').onclick = () => loadTrack(state.trackIndex + 1, true);
  $('btnMute').onclick = () => {
    const a = audio();
    state.muted = !state.muted;
    a.muted = state.muted;
    $('btnMute').textContent = state.muted ? '🔇' : '🔊';
  };
  $('volume').addEventListener('input', (ev) => {
    const v = Number(ev.target.value) / 100;
    audio().volume = v;
    state.muted = false; audio().muted = false; $('btnMute').textContent = '🔊';
    state.lastVolume = v;
    clearTimeout(saveVolumeTimer);
    saveVolumeTimer = setTimeout(() => api('/api/config', { volume: v }), 500);
  });
  const seek = $('seek');
  seek.addEventListener('input', () => {
    state.seeking = true;
    const d = audio().duration || 0;
    if (d) $('curTime').textContent = fmtTime((Number(seek.value) / 1000) * d);
  });
  seek.addEventListener('change', () => {
    const d = audio().duration || 0;
    if (d) audio().currentTime = (Number(seek.value) / 1000) * d;
    state.seeking = false;
  });
  $('btnLoop').onclick = () => {
    const order = ['all', 'one', 'shuffle', 'off'];
    state.loopMode = order[(order.indexOf(state.loopMode) + 1) % order.length];
    updateLoopButton();
    api('/api/config', { loop_mode: state.loopMode });
    toast('循环模式：' + { all: '列表循环', one: '单曲循环', shuffle: '随机播放', off: '播完停止' }[state.loopMode]);
  };
  $('btnPlaylist').onclick = () => { $('playlist').hidden = !$('playlist').hidden; };
  $('btnPlaylistClose').onclick = () => { $('playlist').hidden = true; };
  $('btnMusicRefresh').onclick = () => refreshMusic(false);
  $('btnOpenMusic').onclick = () => api('/api/open-folder', { which: 'music' });
  $('btnEditSources').onclick = openSources;
  $('btnSourcesSave').onclick = async () => {
    const res = await api('/api/music/sources', { text: $('sourcesText').value });
    if (!res.ok) { toast(res.error || '保存失败', true); return; }
    state.music = res.music;
    const wasPlaying = !audio().paused;
    state.trackIndex = -1;
    if (state.music.tracks.length) loadTrack(0, wasPlaying);
    else { audio().removeAttribute('src'); updateNowPlaying(); }
    renderPlaylist();
    $('sourcesModal').hidden = true;
    toast(`音源清单已保存，共 ${state.music.tracks.length} 个音源`);
  };
  $('btnSourcesReload').onclick = openSources;

  // Git
  $('gitBadge').onclick = () => { renderGitPanel(); $('gitModal').hidden = false; };
  $('btnGitInit').onclick = () => gitAction('init');
  $('btnGitSetRemote').onclick = () => gitAction('set_remote');
  $('btnGitClone').onclick = () => {
    if (!confirm('克隆会从远程拉取全部日记与音乐，并覆盖同名文件。确定继续吗？')) return;
    gitAction('clone');
  };
  $('btnGitCommit').onclick = () => gitAction('commit');
  $('btnGitPull').onclick = () => gitAction('pull');
  $('btnGitPush').onclick = () => gitAction('push');
  $('btnGitSync').onclick = () => gitAction('sync');
  $('btnGitProxyTest').onclick = () => gitAction('proxy');
  $('gitProxyMode').onchange = updateProxyLine;
  $('gitProxyValue').oninput = updateProxyLine;
  $('btnGitRefresh').onclick = async () => {
    const res = await api('/api/git/status');
    if (res.ok) { state.git = res.git; renderGitBadge(); renderGitPanel(); }
  };

  // 主题
  $('btnTheme').onclick = () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    state.config.theme = next;
    api('/api/config', { theme: next });
  };

  // 弹窗关闭
  document.querySelectorAll('[data-close]').forEach((b) => {
    b.onclick = () => { $(b.dataset.close).hidden = true; };
  });
  document.querySelectorAll('.modal').forEach((m) => {
    m.addEventListener('mousedown', (ev) => {
      if (ev.target !== m) return;
      m.hidden = true;
      // 点灰暗处关掉大事记时，顺便把添加面板收起来
      if (m.id === 'msModal') $('msAddPanel').hidden = true;
    });
  });

  // 退出程序
  $('btnQuit').onclick = async () => {
    if (!confirm('退出群日记程序？\n（会停止后台服务；日记文件不受影响，下次双击「启动群日记.bat」即可继续）')) return;
    await api('/api/quit', {});
    document.body.innerHTML =
      '<div style="display:grid;place-items:center;height:100vh;font:16px/2 var(--font);text-align:center">' +
      '<div><div style="font-size:44px">👋</div><div>群日记已退出</div>' +
      '<div style="color:var(--text-3);font-size:13px">现在可以直接关闭这个窗口了</div></div></div>';
    setTimeout(() => { try { window.close(); } catch (e) {} }, 600);
  };

  // 快捷键
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      const open = [...document.querySelectorAll('.modal')].filter((m) => !m.hidden);
      if (open.length) {
        const top = open[open.length - 1];
        // 大事记的添加面板先关，再关整个弹层
        if (top.id === 'msModal' && !$('msAddPanel').hidden) {
          $('msAddPanel').hidden = true;
          return;
        }
        top.hidden = true;
        if (top.id === 'msModal') $('msAddPanel').hidden = true;
        return;
      }
      if (!$('playlist').hidden) { $('playlist').hidden = true; return; }
      if (!$('reader').hidden) closeReader();
      return;
    }
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (typing) return;
    if (ev.key === '/') { ev.preventDefault(); $('search').focus(); }
    if (ev.key === 'n' || ev.key === 'N') { ev.preventDefault(); openEditor(null); }
    if (ev.key === ' ') { ev.preventDefault(); togglePlay(); }
    if (ev.key === 'j' || ev.key === 'J') stepReader(1);
    if (ev.key === 'k' || ev.key === 'K') stepReader(-1);
  });
}

/* ------------------------- 启动 ------------------------- */

function applyUrlParams() {
  let p;
  try { p = new URLSearchParams(location.search); } catch (e) { return; }
  const q = p.get('q');
  if (q) {
    state.query = q;
    $('search').value = q;
    $('searchClear').hidden = false;
  }
  const date = p.get('date');
  if (date) {
    // 存起来，等界面就绪后走 applyJumpDate —— 非法日期同样会弹提示
    state.pendingDate = date;
  }
  const rec = p.get('recorder');
  if (p.get('new') && rec) {
    // ?new=1&recorder=伊丝塔 —— 打开就是"写今天的日记"，署名已填好
    state.pendingNewRecorder = rec;
  } else if (rec) {
    rec.split(',').filter(Boolean).forEach((r) => state.selRecorders.add(r));
  }
  const sort = p.get('sort');
  if (sort) { state.sort = sort; $('sortSel').value = sort; }
  const open = p.get('open');
  if (open) state.pendingOpen = open;
  if (p.get('new')) state.pendingNew = true;
  if (p.get('git')) state.pendingGit = true;
  if (p.get('ms')) state.pendingMs = true;
  if (p.get('msadd')) state.pendingMsAdd = true;
}

// ---- 关掉窗口就把后台一起关掉 ----
// 界面每 3 秒报一次平安；窗口关掉（或刷新）时浏览器会在页面卸载前补发 pagehide。
// 后台收到 pagehide 后等 12 秒，这期间没有心跳才真的退出 ——
// 所以「刷新」不会误退（新页面马上又开始跳），而「关窗口」会把后台进程一起带走。
function startAutoQuitWatch() {
  const beat = () => {
    fetch('/api/alive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }).catch(() => {});
  };
  beat();
  setInterval(beat, 3000);

  window.addEventListener('pagehide', () => {
    try {
      // sendBeacon 是专门给"页面要没了"用的，浏览器保证会把它发出去
      navigator.sendBeacon('/api/pagehide',
        new Blob(['{}'], { type: 'application/json' }));
    } catch (e) {
      // 极老的浏览器没有 sendBeacon：退化成本来的样子（后台留着），不影响使用
    }
  });
}

(async function start() {
  bind();
  startAutoQuitWatch();
  applyUrlParams();
  await loadState();
  if (state.pendingOpen) {
    const hit = state.entries.find((e) => e.id === state.pendingOpen || e.file === state.pendingOpen);
    if (hit) openReader(hit.id);
    state.pendingOpen = null;
  }
  if (state.pendingNew) { state.pendingNew = false; openEditor(null); }
  if (state.pendingGit) { state.pendingGit = false; renderGitPanel(); $('gitModal').hidden = false; }
  if (state.pendingDate) {
    $('jumpDateText').value = state.pendingDate;
    state.pendingDate = null;
    applyJumpDate(false);
  }
  if (state.pendingMs || state.pendingMsAdd) {
    const withAdd = state.pendingMsAdd;
    state.pendingMs = false;
    state.pendingMsAdd = false;
    openMilestones();
    if (withAdd) $('btnMsAdd').click();
  }
})();
