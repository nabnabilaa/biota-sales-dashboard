// Browser side: talks to the Python API and draws what comes back.
// No calculation happens here; every number comes from api/analysis.py.

const $ = (id) => document.getElementById(id);
const MONTHS = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'];
const DAYS = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu'];
const CHANNEL_CLASS = { Shopee: 'c-shopee', 'TikTok Shop': 'c-tiktok', Tokopedia: 'c-tokopedia' };

const rupiah = (n) => 'Rp' + n.toLocaleString('id-ID');
const count = (n) => n.toLocaleString('id-ID');
const pct = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0) + '%';
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const juta = (n) => 'Rp' + (n / 1e6).toLocaleString('id-ID', { maximumFractionDigits: 1 }) + ' jt';

// "BIOTA Smart Camera Battery Fixed - CCTV Wifi Tanpa Kabel" -> "Smart Camera Battery Fixed"
const shortName = (name) => String(name).split(' - ')[0].replace(/^BIOTA\s+/i, '');

const day = (iso) => {
  const [y, m, d] = iso.split('-').map(Number);
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return { y, m, d, month: MONTHS[m - 1], weekday, dayName: DAYS[weekday] };
};
const longDate = (iso) => { const x = day(iso); return `${x.dayName}, ${x.d} ${x.month} ${x.y}`; };

// ---------- API ----------

// Same server first. When the page is opened from a plain web server (e.g. Laragon at
// localhost/biota-sales-dashboard), fall back to the local Python server on port 8000.
const LOCAL_API = 'http://localhost:8000';
let apiBase = '';
async function api(path, options = {}) {
  const opts = { credentials: 'include', ...options };
  let res = await fetch(apiBase + path, opts).catch(() => null);
  if ((!res || res.status === 404 || res.status === 405) && !apiBase && location.port !== '8000') {
    apiBase = LOCAL_API;
    res = await fetch(apiBase + path, opts).catch(() => null);
  }
  if (!res) throw new Error('Server tidak bisa dihubungi. Pastikan aplikasi sedang berjalan, lalu muat ulang halaman.');
  const body = await res.json().catch(() => ({}));
  return { status: res.status, ok: res.ok, body };
}

// ---------- state ----------

let result = null;     // last dashboard response
let period = null;     // { first_day, last_day, start, end }
let pendingFile = null;
let pendingAnswers = {}; // answers given so far for the file being uploaded
let showAll = false;
let request = 0;

// ---------- screens ----------

function show(screen) {
  for (const id of ['loading', 'login', 'start', 'mapping', 'dash']) $(id).hidden = id !== screen;
  $('filter-bar').hidden = screen !== 'dash';
  $('data-info').hidden = screen !== 'dash' && screen !== 'mapping';
  document.querySelector('#data-info .file-name').hidden = !result;
}

function toast(html) {
  $('toast').innerHTML = html;
  $('toast').hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { $('toast').hidden = true; }, 7000);
}

async function boot() {
  show('loading');
  try {
    const s = await api('/api/session');
    if (s.body.password_required && !s.body.logged_in) return show('login');
    await loadDashboard();
  } catch (err) {
    show('start');
    $('error').textContent = err.message;
    $('error').hidden = false;
  }
}

async function loadDashboard(start, end) {
  const id = ++request;
  document.body.classList.add('busy');
  try {
    const q = start ? `?start=${start}&end=${end}` : '';
    const res = await api('/api/dashboard' + q);
    if (id !== request) return;
    if (res.status === 401) return show('login');
    if (!res.ok) throw new Error(res.body.detail || 'Data tidak bisa dimuat.');
    if (res.body.empty) return show('start');
    result = res.body;
    period = res.body.period;
    render();
  } finally {
    if (id === request) document.body.classList.remove('busy');
  }
}

// ---------- login ----------

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  $('login-error').hidden = true;
  const res = await api('/api/login', { method: 'POST', body: form });
  if (!res.ok) {
    $('login-error').textContent = res.body.detail || 'Tidak bisa masuk.';
    $('login-error').hidden = false;
    return;
  }
  e.target.reset();
  boot();
});

// ---------- uploading ----------

for (const input of document.querySelectorAll('.file-input')) {
  input.addEventListener('change', () => {
    if (input.files[0]) uploadFile(input.files[0]);
    input.value = '';
  });
}

let dragDepth = 0;
window.addEventListener('dragenter', (e) => {
  if (![...e.dataTransfer.types].includes('Files') || !$('login').hidden) return;
  dragDepth++;
  $('drop-overlay').hidden = false;
});
window.addEventListener('dragleave', () => {
  if (--dragDepth <= 0) { dragDepth = 0; $('drop-overlay').hidden = true; }
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  $('drop-overlay').hidden = true;
  if (e.dataTransfer.files[0] && $('login').hidden) uploadFile(e.dataTransfer.files[0]);
});

async function uploadFile(file, mapping) {
  if (!/\.(csv|xlsx|xls)$/i.test(file.name)) {
    toast(`<b>${esc(file.name)}</b> bukan file CSV atau Excel.`);
    return;
  }
  if (file !== pendingFile) pendingAnswers = {};
  pendingFile = file;
  document.body.classList.add('busy');
  $('drop').classList.add('loading');
  const form = new FormData();
  form.append('file', file);
  if (mapping) form.append('mapping', JSON.stringify(mapping));
  try {
    const res = await api('/api/upload', { method: 'POST', body: form });
    if (res.status === 401) return show('login');
    if (res.status === 409) return askMapping(res.body.questions);
    if (!res.ok) throw new Error(res.body.detail || 'File tidak bisa dibaca.');
    pendingFile = null;
    pendingAnswers = {};
    const u = res.body.upload;
    result = res.body;
    period = res.body.period;
    render();
    toast(`<b>${esc(u.filename)}</b> tersimpan: ${count(u.added)} baris baru, ${count(u.updated)} diperbarui, ${count(u.unchanged)} sudah ada.`);
    if (res.body.missing?.length) { trail.length = 0; openDetail('missing', res.body.missing); }
  } catch (err) {
    if (result) toast(esc(err.message));
    else { show('start'); $('error').textContent = err.message; $('error').hidden = false; }
  } finally {
    document.body.classList.remove('busy');
    $('drop').classList.remove('loading');
  }
}

// ---------- unknown columns / labels ----------

function askMapping(q) {
  show('mapping');
  $('mapping-ai').textContent = q.ai_used ? 'Pilihan yang sudah terisi adalah saran AI; periksa sebelum melanjutkan.' : (q.ai_error ? `${q.ai_error} Silakan pilih sendiri.` : '');
  const select = (name, options, suggestion) => `
    <select name="${esc(name)}" required>
      <option value="">Pilih…</option>
      ${options.map(([value, label]) => `<option value="${esc(value)}"${value === suggestion ? ' selected' : ''}>${esc(label)}</option>`).join('')}
    </select>`;
  const row = (title, sub, control, ai) => `
    <div class="map-row">
      <div><strong>${esc(title)}</strong><span class="muted small">${esc(sub)}</span></div>
      <div class="map-control">${control}${ai ? '<span class="ai-tag">saran AI</span>' : ''}</div>
    </div>`;
  const headers = q.headers.map((h) => [h, h]);
  const channels = q.options.channels.map((c) => [c, c]);
  const statuses = Object.entries(q.options.statuses);
  let html = '';
  if (q.columns.length) {
    html += '<h3>Kolom yang tidak ditemukan</h3>' + q.columns.map((c) =>
      row(c.label, 'Kolom mana di file yang berisi ini?', select(`columns:${c.field}`, headers, c.suggestion), c.suggestion)).join('');
  }
  if (q.channels.length) {
    html += '<h3>Channel yang tidak dikenal</h3>' + q.channels.map((c) =>
      row(`"${c.value}"`, 'Ini channel apa?', select(`channels:${c.value}`, channels, c.suggestion), c.suggestion)).join('');
  }
  if (q.statuses.length) {
    html += '<h3>Status yang tidak dikenal</h3>' + q.statuses.map((s) =>
      row(`"${s.value}"`, 'Selesai dan dalam pengiriman dihitung ke net revenue; batal dan retur tidak.', select(`statuses:${s.value}`, statuses, s.suggestion), s.suggestion)).join('');
  }
  $('mapping-form').innerHTML = html;
}

$('mapping-form').addEventListener('submit', (e) => {
  e.preventDefault();
  for (const [key, value] of new FormData(e.target)) {
    const [kind, ...rest] = key.split(':');
    pendingAnswers[kind] = { ...(pendingAnswers[kind] || {}), [rest.join(':')]: value };
  }
  uploadFile(pendingFile, pendingAnswers);
});
$('mapping-cancel').addEventListener('click', () => { pendingFile = null; result ? show('dash') : show('start'); });

// ---------- period ----------

function presetRange(name) {
  const { first_day: first, last_day: last } = period;
  const month = first.slice(0, 8);
  if (name === 'first') return [first, month + '15' < last ? month + '15' : last];
  if (name === 'second') return [month + '16', last];
  return [first, last];
}

for (const btn of document.querySelectorAll('[data-preset]')) {
  btn.addEventListener('click', () => loadDashboard(...presetRange(btn.dataset.preset)));
}
for (const el of [$('start-date'), $('end-date')]) {
  el.addEventListener('change', () => {
    const s = $('start-date').value || period.first_day;
    const e = $('end-date').value || period.last_day;
    loadDashboard(s <= e ? s : e, s <= e ? e : s);
  });
}

// ---------- drawing ----------

function render() {
  show('dash');
  const p = period;
  const s = result.summary;

  const a = day(p.first_day);
  const b = day(p.last_day);
  $('data-range').textContent = `Data ${a.d} ${a.month.slice(0, 3)} - ${b.d} ${b.month.slice(0, 3)} ${b.y} · ${count(result.uploads.length)} upload`;

  const sameMonth = p.first_day.slice(0, 7) === p.last_day.slice(0, 7);
  const m = a.month.slice(0, 3);
  const labels = { all: sameMonth ? `1-${b.d} ${a.month}` : 'Semua tanggal', first: `1-15 ${m}`, second: `16-${b.d} ${m}` };
  for (const btn of document.querySelectorAll('[data-preset]')) {
    btn.hidden = btn.dataset.preset !== 'all' && !(sameMonth && b.d > 15);
    btn.textContent = labels[btn.dataset.preset];
    const [x, y] = presetRange(btn.dataset.preset);
    btn.setAttribute('aria-pressed', String(x === p.start && y === p.end));
  }
  for (const el of [$('start-date'), $('end-date')]) { el.min = p.first_day; el.max = p.last_day; }
  $('start-date').value = p.start;
  $('end-date').value = p.end;

  for (const card of document.querySelectorAll('.kpi')) card.classList.add('row-link');
  $('kpi-revenue').textContent = rupiah(s.net_revenue);
  $('kpi-orders').textContent = count(s.orders);
  $('kpi-units').textContent = count(s.units);
  const ex = s.not_counted;
  $('kpi-excluded').innerHTML = `${count(ex.cancelled.orders + ex.returned.orders)}<span class="hide-sm"> pesanan</span>`;
  $('kpi-excluded-note').textContent = `Senilai ${rupiah(ex.cancelled.value + ex.returned.value)}, tidak dihitung`;

  if (showAll) { showAll = false; }
  renderFindings(result.findings);
  renderDaily(s.by_day);
  renderChannels(s);
  renderProducts();
  renderNotes(s, result.report, result.uploads);
}

// Findings: compact cards; the full explanation and advice open in the detail sheet.
let findingCards = [];
function renderFindings(f) {
  const share = (x) => (x * 100).toLocaleString('id-ID', { maximumFractionDigits: 0 }) + '%';
  const date = (iso) => { const x = day(iso); return `${x.d} ${x.month}`; };
  findingCards = [];

  if (f.lost.orders) {
    findingCards.push({
      tone: 'warn', icon: 'ph-arrow-u-down-left', title: 'Batal dan retur', value: juta(f.lost.value),
      line: `${share(f.lost.share)} dari nilai semua pesanan`,
      body: `<p>${count(f.lost.orders)} pesanan senilai <b>${rupiah(f.lost.value)}</b> batal atau diretur, ${share(f.lost.share)} dari nilai semua pesanan.</p>
        ${ordersButton(`Lihat ${count(f.lost.orders)} pesanan batal & retur`, { status: 'lost', title: 'Pesanan batal & retur' })}
        <h3>Per channel</h3><div class="link-list">${f.lost.by_channel.map((c) => linkRow(`<span class="dot ${CHANNEL_CLASS[c.name]}"></span>${esc(c.name)}`, `${share(c.share)} · ${rupiah(c.cancelled + c.returned)}`, { channel: c.name, status: 'lost', title: `Batal & retur di ${c.name}` })).join('')}</div>
        <h3>Per produk (porsi batal/retur)</h3><div class="link-list">${f.lost.by_product.map((p) => linkRow(esc(shortName(p.name)), `${share(p.share)} · ${rupiah(p.cancelled + p.returned)}`, { sku: p.key, status: 'lost', title: `Batal & retur: ${shortName(p.name)}` })).join('')}</div>`,
      tip: 'Kalau persentasenya mirip di semua channel, penyebabnya ada di internal (stok, waktu proses, COD), bukan di marketplace. Tambahkan alasan pembatalan ke ekspor supaya bisa ditelusuri.',
    });
  }
  if (f.stuck_shipping.orders) {
    const by = Object.entries(f.stuck_shipping.by_channel);
    findingCards.push({
      tone: 'warn', icon: 'ph-truck', title: `Dikirim lebih dari ${f.stuck_shipping.days} hari`, value: `${count(f.stuck_shipping.orders)} pesanan`,
      line: `${juta(f.stuck_shipping.value)} belum pasti diterima`,
      body: `<p>${count(f.stuck_shipping.orders)} pesanan senilai <b>${rupiah(f.stuck_shipping.value)}</b> sudah masuk net revenue, tapi statusnya masih "dikirim" lebih dari ${f.stuck_shipping.days} hari. Yang tertua dipesan ${date(f.stuck_shipping.oldest)}.</p>
        ${ordersButton(`Lihat ${count(f.stuck_shipping.orders)} pesanan`, { status: 'stuck', title: `Dikirim lebih dari ${f.stuck_shipping.days} hari` })}
        <h3>Per channel</h3><div class="link-list">${by.map(([k, v]) => linkRow(`<span class="dot ${CHANNEL_CLASS[k]}"></span>${esc(k)}`, `${count(v)} pesanan`, { channel: k, status: 'stuck', title: `Masih dikirim: ${k}` })).join('')}</div>`,
      tip: 'Cek resi yang lama: paket tersangkut, atau ekspor diambil sebelum status diperbarui. Upload ekspor terbaru tiap minggu: status yang berubah akan diperbarui otomatis.',
    });
  }
  if (f.camera_without_card.camera_orders) {
    const c = f.camera_without_card;
    findingCards.push({
      tone: 'idea', icon: 'ph-package', title: 'Kamera tanpa microSD', value: `${count(c.with_card)} dari ${count(c.camera_orders)}`,
      line: `hanya ${share(c.with_card / c.camera_orders)} ikut beli microSD`,
      body: `<p>Dari ${count(c.camera_orders)} pesanan kamera, hanya <b>${count(c.with_card)}</b> yang ikut membeli microSD, padahal CCTV butuh kartu memori untuk merekam.</p>`,
      tip: 'Coba paket bundling kamera + microSD, atau tawaran tambahan di halaman produk kamera.',
    });
  }
  if (f.best_day.date) {
    const b = f.best_day;
    findingCards.push({
      tone: 'idea', icon: 'ph-trend-up', title: 'Hari tertinggi', value: date(b.date),
      line: `${juta(b.revenue)}, ${(b.revenue / Math.max(1, b.median)).toLocaleString('id-ID', { maximumFractionDigits: 1 })}× hari biasa`,
      body: `<p><b>${longDate(b.date)}</b> menghasilkan ${rupiah(b.revenue)}, sementara hari biasa (median) ${rupiah(b.median)}. Channel terbesar hari itu: ${esc(b.top_channel)}.</p>
        <button type="button" class="btn btn-outline" data-open-day="${b.date}">Lihat detail hari ini</button>`,
      tip: 'Cari tahu apa yang terjadi hari itu (live, promo, iklan) supaya bisa diulang di bulan berikutnya.',
    });
  }
  const listings = Object.entries(f.listings_without_sku);
  const total = listings.reduce((n, [, v]) => n + v.length, 0);
  if (total) {
    findingCards.push({
      tone: 'data', icon: 'ph-barcode', title: 'Listing tanpa SKU', value: `${count(total)} listing`,
      line: 'dicocokkan lewat nama produk',
      body: `<p>Dashboard mencocokkan listing ini lewat nama produk, tapi lebih aman jika SKU penjual diisi langsung di setiap marketplace.</p>
        ${listings.map(([ch, names]) => `<h3>${esc(ch)}</h3><ul class="plain-list">${names.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`).join('')}`,
      tip: 'Isi kolom SKU penjual di tiap listing marketplace, supaya laporan tidak bergantung pada nama produk yang bisa berubah.',
    });
  }

  $('findings').innerHTML = findingCards.map((c, i) => `
    <button type="button" class="finding ${c.tone}" data-finding="${i}">
      <i class="ph ${c.icon} finding-icon" aria-hidden="true"></i>
      <span class="finding-title">${esc(c.title)}</span>
      <strong class="finding-value">${c.value}</strong>
      <span class="finding-line">${c.line}</span>
      <span class="finding-more">Lihat detail dan saran <i class="ph ph-caret-right"></i></span>
      <i class="ph ph-caret-right finding-chev" aria-hidden="true"></i>
    </button>`).join('');
}

// Daily chart: bars with a Rupiah scale, average line, best day marked, tooltip, click for detail.
function niceMax(v) {
  if (v <= 0) return 1_000_000;
  const step = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / step) * step;
}

function renderDaily(days) {
  const values = days.map((d) => d.revenue);
  const top = niceMax(Math.max(...values));
  const avg = values.reduce((a, b) => a + b, 0) / Math.max(1, values.length);
  const best = Math.max(...values);

  $('chart-y').innerHTML = [1, 0.75, 0.5, 0.25, 0].map((f) => `<span>${f ? juta(top * f).replace('Rp', '') : '0'}</span>`).join('');
  $('chart-grid').innerHTML = [1, 0.75, 0.5, 0.25, 0].map(() => '<span></span>').join('');
  $('chart-avg').style.top = `calc((1 - ${avg / top}) * var(--plot-h))`;
  $('chart-avg').firstElementChild.textContent = `Rata-rata ${juta(avg)}`;

  $('daily').innerHTML = days.map((d) => {
    const x = day(d.date);
    return `<button type="button" role="listitem" class="col${d.revenue === best && best > 0 ? ' best' : ''}" data-date="${d.date}" aria-label="${longDate(d.date)}: ${rupiah(d.revenue)}">
      <span style="height:${Math.max(d.revenue ? 1.5 : 0, (d.revenue / top) * 100)}%"></span>
    </button>`;
  }).join('');

  // Date labels: about seven, always including the first and last day.
  const stepX = Math.max(1, Math.ceil(days.length / 7));
  $('chart-x').innerHTML = days.map((d, i) => {
    const show = i % stepX === 0 || i === days.length - 1;
    return `<span>${show ? day(d.date).d : ''}</span>`;
  }).join('');
}

function showTooltip(col) {
  const d = result.details.days[col.dataset.date];
  if (!d) return;
  const x = day(col.dataset.date);
  const tip = $('tooltip');
  tip.innerHTML = `
    <b>${x.dayName}, ${x.d} ${x.month}</b>
    <span class="tt-big">${rupiah(d.revenue)}</span>
    <span>${count(d.orders)} pesanan · ${count(d.units)} unit</span>
    ${d.by_channel.filter((c) => c.revenue).map((c) => `<span class="tt-row"><i class="dot ${CHANNEL_CLASS[c.channel]}"></i>${esc(c.channel)} <em>${juta(c.revenue)}</em></span>`).join('')}
    <span class="tt-hint">Klik untuk detail</span>`;
  tip.hidden = false;
  const plot = col.closest('.chart-plot').getBoundingClientRect();
  const r = col.getBoundingClientRect();
  // Beside the bar (right, or left near the end) so it never covers the bar itself.
  const w = tip.offsetWidth;
  let left = r.right - plot.left + 8;
  if (left + w > plot.width) left = r.left - plot.left - w - 8;
  tip.style.left = `${Math.max(0, left)}px`;
}

$('daily').addEventListener('pointerover', (e) => { const col = e.target.closest('.col'); if (col) showTooltip(col); });
$('daily').addEventListener('focusin', (e) => { const col = e.target.closest('.col'); if (col) showTooltip(col); });
$('daily').addEventListener('pointerleave', () => { $('tooltip').hidden = true; });
$('daily').addEventListener('focusout', () => { $('tooltip').hidden = true; });
$('daily').addEventListener('click', (e) => {
  const col = e.target.closest('.col');
  if (col) { $('tooltip').hidden = true; trail.length = 0; openDetail('day', col.dataset.date); }
});

function renderChannels(s) {
  const max = Math.max(1, ...s.by_channel.map((c) => c.revenue));
  $('channels').innerHTML = s.by_channel.map((c) => `
    <button type="button" class="channel row-link" data-channel="${esc(c.channel)}">
      <div class="channel-top">
        <span class="channel-name"><span class="dot ${CHANNEL_CLASS[c.channel]}"></span>${esc(c.channel)}</span>
        <span class="channel-value">${rupiah(c.revenue)}</span>
      </div>
      <div class="bar"><span class="${CHANNEL_CLASS[c.channel]}" style="width:${(c.revenue / max) * 100}%"></span></div>
      <div class="channel-sub muted small"><span>${pct(c.revenue, s.net_revenue)} dari total</span><span>${count(c.orders)} pesanan <i class="ph ph-caret-right"></i></span></div>
    </button>`).join('');
}

// ---------- shared table filters (search, date range, channel, category, status) ----------

const STATUS_FILTERS = [
  ['', 'Semua status'], ['counted', 'Dihitung (selesai + dikirim)'], ['completed', 'Selesai'], ['shipping', 'Dikirim'],
  ['lost', 'Batal + retur'], ['cancelled', 'Batal'], ['returned', 'Retur'], ['stuck', 'Dikirim > 7 hari'],
];

function filterBar(name, f, { status = false, placeholder = 'Cari', defaults = {} } = {}) {
  const opt = (list, value) => list.map(([v, l]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(l)}</option>`).join('');
  const channels = [['', 'Semua'], ...result.summary.by_channel.map((c) => [c.channel, c.channel])];
  const categories = [['', 'Semua'], ...(result.summary.categories || []).map((c) => [c, c])];
  const range = `min="${period.first_day}" max="${period.last_day}"`;
  const active = ['start', 'end', 'channel', 'category', 'status'].filter((k) => (f[k] || '') !== (defaults[k] || '')).length;
  return `<div class="table-filters" data-filters="${name}">
    <div class="tf-top">
      <label class="search"><i class="ph ph-magnifying-glass" aria-hidden="true"></i><span class="sr-only">Cari</span>
        <input type="search" data-f="q" value="${esc(f.q)}" placeholder="${esc(placeholder)}"></label>
      <button type="button" class="tf-toggle" data-f-toggle aria-expanded="false"><i class="ph ph-sliders-horizontal"></i>Filter${active ? ` <span class="tf-count">${active}</span>` : ''}</button>
    </div>
    <div class="tf-row">
      <label class="tf-field"><span>Dari</span><input type="date" data-f="start" ${range} value="${f.start}"></label>
      <label class="tf-field"><span>Sampai</span><input type="date" data-f="end" ${range} value="${f.end}"></label>
      <label class="tf-field"><span>Channel</span><select data-f="channel">${opt(channels, f.channel)}</select></label>
      <label class="tf-field"><span>Kategori</span><select data-f="category">${opt(categories, f.category)}</select></label>
      ${status ? `<label class="tf-field"><span>Status</span><select data-f="status">${opt(STATUS_FILTERS, f.status)}</select></label>` : ''}
      <button type="button" class="btn-link tf-reset" data-f-reset>Reset</button>
    </div>
  </div>`;
}

// Reads a change in any filter bar; search filters on the spot, the rest asks Python again.
function onFilter(e) {
  const bar = e.target.closest('[data-filters]');
  if (!bar) return;
  const view = bar.dataset.filters === 'products' ? productView : orderView;
  if (e.target.matches('[data-f-reset]')) {
    Object.assign(view.filters, view.defaults, { q: '' });
    bar.outerHTML = filterBar(bar.dataset.filters, view.filters, view.barOptions);
    return view.reload();
  }
  const key = e.target.dataset.f;
  if (!key) return;
  view.filters[key] = e.target.value;
  view.page = 1;
  if (key === 'q') return view.render();
  if (e.type !== 'change') return;
  const f = view.filters;
  if (f.start && f.end && f.start > f.end) [f.start, f.end] = [f.end, f.start];
  const wasOpen = bar.classList.contains('open');
  bar.outerHTML = filterBar(bar.dataset.filters, view.filters, view.barOptions);
  if (wasOpen) document.querySelector(`[data-filters="${bar.dataset.filters}"]`).classList.add('open');
  view.reload();
}
document.addEventListener('input', onFilter);
document.addEventListener('change', onFilter);
document.addEventListener('click', (e) => {
  if (e.target.matches('[data-f-reset]')) return onFilter(e);
  const t = e.target.closest('[data-f-toggle]');
  if (t) {
    const bar = t.closest('.table-filters');
    bar.classList.toggle('open');
    t.setAttribute('aria-expanded', String(bar.classList.contains('open')));
  }
});

// Products: "10 teratas" for the quick answer, "Semua produk" with its own filters, search and pages.
const PAGE_SIZE = 10;
const productView = {
  filters: {}, defaults: {}, data: null, page: 1, barOptions: { placeholder: 'Cari nama produk atau SKU' },
  async reload() {
    const f = this.filters;
    const q = new URLSearchParams();
    for (const k of ['start', 'end', 'channel', 'category']) if (f[k]) q.set(k, f[k]);
    const res = await api('/api/products?' + q.toString());
    this.data = res.ok ? res.body : { products: [], net_revenue: 0 };
    this.render();
  },
  render() { renderProducts(); },
};

for (const btn of document.querySelectorAll('[data-view]')) {
  btn.addEventListener('click', () => {
    showAll = btn.dataset.view === 'all';
    productView.page = 1;
    if (showAll) {
      // Starts from the page's period; the table can then pick its own dates.
      productView.defaults = { start: period.start, end: period.end, channel: '', category: '' };
      productView.filters = { ...productView.defaults, q: '' };
      productView.barOptions = { placeholder: 'Cari nama produk atau SKU', defaults: productView.defaults };
      $('product-tools').innerHTML = filterBar('products', productView.filters, productView.barOptions);
      productView.reload();
    } else {
      renderProducts();
    }
  });
}
$('pager').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-page]');
  if (!btn) return;
  productView.page = Number(btn.dataset.page);
  renderProducts();
});

function renderProducts() {
  for (const btn of document.querySelectorAll('[data-view]')) {
    btn.setAttribute('aria-pressed', String((btn.dataset.view === 'all') === showAll));
  }
  $('product-tools').hidden = !showAll;
  const products = showAll && productView.data ? productView.data.products : result.summary.products;
  const query = showAll ? (productView.filters.q || '').toLowerCase() : '';
  let page = productView.page;

  // Rank is the position by net revenue, kept when searching so "#3" always means third best.
  const ranked = products.map((p, i) => ({ ...p, rank: i + 1 }));
  const matches = query
    ? ranked.filter((p) => p.product.toLowerCase().includes(query) || p.sku.toLowerCase().includes(query))
    : ranked;
  const pages = showAll ? Math.max(1, Math.ceil(matches.length / PAGE_SIZE)) : 1;
  page = productView.page = Math.min(page, pages);
  const list = showAll ? matches.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE) : ranked.slice(0, 10);

  const max = products[0]?.revenue || 1;
  const empty = !products.length ? 'Tidak ada produk terjual dengan filter ini.' : `Tidak ada produk yang cocok dengan "${esc(query)}".`;
  $('product-rows').innerHTML = list.length ? list.map((p) => `
    <tr class="row-link" tabindex="0" data-sku="${esc(p.sku)}" aria-label="Detail ${esc(shortName(p.product))}">
      <td class="col-no">${p.rank}</td>
      <td class="col-name"><span class="product" title="${esc(p.product)}">${esc(shortName(p.product))}</span><span class="sku">${esc(p.sku)} · ${esc(p.category || '')}</span></td>
      <td class="num col-units">${count(p.units)}<span class="m-label"> unit</span></td>
      <td class="num strong col-rev">${rupiah(p.revenue)}</td>
      <td class="col-bar"><div class="bar-cell"><div class="bar thin"><span style="width:${(p.revenue / max) * 100}%"></span></div><i class="ph ph-caret-right chev"></i></div></td>
    </tr>`).join('') : `<tr><td colspan="5" class="muted">${empty}</td></tr>`;

  $('pager').hidden = !showAll || matches.length <= PAGE_SIZE;
  if (showAll) {
    const from = matches.length ? (page - 1) * PAGE_SIZE + 1 : 0;
    const to = Math.min(page * PAGE_SIZE, matches.length);
    $('pager').innerHTML = `
      <span class="muted">${from}-${to} dari ${count(matches.length)} produk</span>
      <span class="pager-buttons">
        <button type="button" class="chip" data-page="${page - 1}" ${page === 1 ? 'disabled' : ''} aria-label="Halaman sebelumnya"><i class="ph ph-caret-left"></i></button>
        <span>Hal. ${page} / ${pages}</span>
        <button type="button" class="chip" data-page="${page + 1}" ${page === pages ? 'disabled' : ''} aria-label="Halaman berikutnya"><i class="ph ph-caret-right"></i></button>
      </span>`;
  }
}

function renderNotes(s, r, uploads) {
  const ex = s.not_counted;
  const items = [
    `Net revenue = total harga pesanan berstatus <b>selesai</b> atau <b>sedang dikirim</b> (${rupiah(s.shipping_revenue)} masih dalam pengiriman).`,
    `Tidak dihitung: ${count(ex.cancelled.orders)} pesanan batal (${rupiah(ex.cancelled.value)}) dan ${count(ex.returned.orders)} retur (${rupiah(ex.returned.value)}).`,
    `Tersimpan ${count(result.stored_rows)} baris pesanan. Upload yang sama atau tumpang tindih tidak dihitung dua kali; status yang berubah diperbarui.`,
  ];
  if (r && r.rows_in_file) {
    items.push(`Upload terakhir: ${count(r.duplicates_removed)} baris ganda dihapus, ${count(r.sku_filled)} SKU kosong dilengkapi, ${count(r.channel_spellings)} cara penulisan channel dan ${count(r.status_labels)} label status dirapikan` +
      (r.rows_dropped?.length ? `, ${count(r.rows_dropped.length)} baris tidak terbaca (${r.rows_dropped.slice(0, 3).map((d) => `baris ${d.line}: ${esc(d.reason)}`).join('; ')}).` : '.'));
    if (r.duplicate_lines?.length) {
      items.push(`Baris yang dianggap ekspor ganda (isinya sama persis dengan baris lain di pesanan yang sama): ${r.duplicate_lines.slice(0, 12).map((d) => `baris ${d.line} (${esc(d.order_id)})`).join(', ')}${r.duplicate_lines.length > 12 ? ', …' : ''}. ` +
        (r.has_line_id ? 'File ini punya ID baris, jadi hanya baris dengan ID yang sama yang dianggap ganda.' : 'Jika ada yang memang dua baris asli, sertakan kolom ID baris (mis. "Order Item ID") di ekspor.'));
    }
  }
  $('notes').innerHTML = items.map((t) => `<li>${t}</li>`).join('');

  $('uploads').innerHTML = uploads.map((u) => {
    const t = new Date(u.uploaded_at);
    const when = t.toLocaleString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    if (u.removed_orders) {
      return `<li><span class="up-name"><i class="ph ph-trash" aria-hidden="true"></i>${esc(u.filename)}</span>
        <span class="muted small">${when} · ${count(u.removed_lines)} baris dihapus</span></li>`;
    }
    return `<li><span class="up-name"><i class="ph ph-file-text" aria-hidden="true"></i>${esc(u.filename)}</span>
      <span class="muted small">${when} · +${count(u.added)} baru, ${count(u.updated)} diperbarui, ${count(u.unchanged)} sama</span></li>`;
  }).join('');
}

// Delete everything: needs a second click within 4 seconds.
$('clear-data').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (!btn.dataset.armed) {
    btn.dataset.armed = '1';
    btn.textContent = 'Klik sekali lagi untuk menghapus semua data';
    setTimeout(() => { delete btn.dataset.armed; btn.textContent = 'Hapus semua data'; }, 4000);
    return;
  }
  delete btn.dataset.armed;
  btn.textContent = 'Hapus semua data';
  await api('/api/data', { method: 'DELETE' });
  result = null;
  show('start');
  toast('Semua data dihapus.');
});

// ---------- detail sheet (product, channel, day or finding) ----------

const sheet = $('sheet');
const trail = []; // lets "back" return to where a product was opened from

function periodText() {
  const a = day(period.start);
  const b = day(period.end);
  return a.m === b.m ? `${a.d}-${b.d} ${a.month} ${a.y}` : `${a.d} ${a.month} - ${b.d} ${b.month} ${b.y}`;
}

// Every number is a door: revenue, units and orders open the orders behind them,
// "porsi" opens the comparison with the others of the same kind.
function statsBlock(d, ctx, name, kind, key) {
  const orders = esc(JSON.stringify({ ...ctx, status: 'counted', title: `Pesanan ${name}` }));
  const share = esc(JSON.stringify({ of: kind, key }));
  const tile = (label, value, attr) => `<button type="button" class="stat" ${attr}><span>${label}</span><strong>${value}</strong><i class="ph ph-caret-right chev"></i></button>`;
  return `
    <div class="stats">
      ${tile('Net revenue', rupiah(d.revenue), `data-orders="${orders}"`)}
      ${tile('Porsi dari total', pct(d.share, 1), `data-share="${share}"`)}
      ${tile('Unit terjual', count(d.units), `data-orders="${orders}"`)}
      ${tile('Pesanan', count(d.orders), `data-orders="${orders}"`)}
    </div>`;
}

// Comparison list behind a "porsi" number.
function shareView({ of, key }) {
  const s = result.summary;
  let rows;
  if (of === 'product') {
    rows = s.products.map((p) => ({ label: esc(shortName(p.product)), revenue: p.revenue, open: `data-open-sku="${esc(p.sku)}"`, me: p.sku === key }));
  } else if (of === 'channel') {
    rows = s.by_channel.map((c) => ({ label: `<span class="dot ${CHANNEL_CLASS[c.channel]}"></span>${esc(c.channel)}`, revenue: c.revenue, open: `data-open-channel="${esc(c.channel)}"`, me: c.channel === key }));
  } else {
    rows = s.by_day.map((d) => { const x = day(d.date); return { label: `${x.dayName.slice(0, 3)}, ${x.d} ${x.month.slice(0, 3)}`, revenue: d.revenue, open: `data-open-day="${d.date}"`, me: d.date === key }; });
  }
  const max = Math.max(1, ...rows.map((r) => r.revenue));
  return `<p class="muted">Porsi masing-masing dari net revenue ${rupiah(s.net_revenue)}.</p>
    <div class="share-list">${rows.map((r) => `
      <button type="button" class="share-row${r.me ? ' me' : ''}" ${r.open}>
        <span class="link-label">${r.label}</span>
        <span class="num">${pct(r.revenue, s.net_revenue)}</span>
        <span class="bar thin"><span style="width:${(r.revenue / max) * 100}%"></span></span>
        <span class="num small muted">${rupiah(r.revenue)}</span>
      </button>`).join('')}</div>`;
}

function channelBlock(list, sub) {
  const max = Math.max(1, ...list.map((c) => c.revenue));
  return `<section><h3>Per channel</h3>${list.map((c) => `
    <div class="mini-row">
      <span class="channel-name"><span class="dot ${CHANNEL_CLASS[c.channel]}"></span>${esc(c.channel)}</span>
      <div class="bar"><span class="${CHANNEL_CLASS[c.channel]}" style="width:${(c.revenue / max) * 100}%"></span></div>
      <span class="num"><b>${rupiah(c.revenue)}</b><small>${sub(c)}</small></span>
    </div>`).join('')}</section>`;
}

function topProductsBlock(title, list) {
  return `<section><h3>${title}</h3>${list.length ? list.map((p, i) => `
    <button type="button" class="mini-row mini-product row-link" data-open-sku="${esc(p.sku)}">
      <span class="mini-rank">${i + 1}</span>
      <span class="mini-name">${esc(shortName(p.product))}<small>${count(p.units)} unit</small></span>
      <span class="num"><b>${rupiah(p.revenue)}</b></span>
      <i class="ph ph-caret-right chev"></i>
    </button>`).join('') : '<p class="muted">Belum ada penjualan.</p>'}</section>`;
}

function lostBlock(lost) {
  const orders = lost.cancelled.orders + lost.returned.orders;
  const value = lost.cancelled.value + lost.returned.value;
  return `<section><h3>Batal dan retur</h3><p class="lost-line">${orders
    ? `<b>${count(orders)} pesanan, ${rupiah(value)}</b> (${pct(value, lost.gross)} dari nilai pesanan) tidak dihitung: ${count(lost.cancelled.orders)} batal, ${count(lost.returned.orders)} retur.`
    : 'Tidak ada pesanan batal atau retur.'}</p></section>`;
}

// ctx narrows the orders to what the sheet is about ({ sku }, { channel } or { date }).
function citiesBlock(cities, ctx, name) {
  return `<section><h3>Kota pembeli teratas</h3>${cities.length
    ? `<div class="link-list">${cities.map((c) => linkRow(`<i class="ph ph-map-pin"></i>${esc(c.city)}`, rupiah(c.revenue),
        { ...ctx, city: c.city, status: 'counted', title: `${name} · ${c.city}` })).join('')}</div>`
    : '<p class="muted">Belum ada data.</p>'}</section>`;
}

// filter: { sku } or { channel }; each bar opens that day's orders for it.
function dailyBlock(values, filter, name) {
  const days = result.summary.by_day;
  const max = Math.max(1, ...values);
  const first = day(days[0].date);
  const last = day(days.at(-1).date);
  return `<section><h3>Net revenue per hari</h3>
    <p class="readout muted small" data-default="Arahkan atau ketuk batang untuk melihat angka per hari">Arahkan atau ketuk batang untuk melihat angka per hari</p>
    <div class="small-daily">${values.map((v, i) => {
      const x = day(days[i].date);
      const label = `${x.dayName}, ${x.d} ${x.month}: ${rupiah(v)}`;
      const params = esc(JSON.stringify({ ...filter, date: days[i].date, status: 'counted', title: `${name} · ${x.d} ${x.month}` }));
      return `<button type="button" class="col" data-orders="${params}" data-label="${esc(label)}" aria-label="${esc(label)}"${v ? '' : ' disabled'}><span style="height:${Math.max(v ? 3 : 0, (v / max) * 100)}%"></span></button>`;
    }).join('')}</div>
    <div class="daily-axis muted small"><span>${first.d} ${first.month.slice(0, 3)}</span><span>Tertinggi ${juta(max)}</span><span>${last.d} ${last.month.slice(0, 3)}</span></div>
  </section>`;
}

function ordersButton(text, params) {
  return `<button type="button" class="btn btn-outline orders-btn" data-orders="${esc(JSON.stringify(params))}"><i class="ph ph-list-bullets"></i>${esc(text)}</button>`;
}

function linkRow(label, value, params) {
  return `<button type="button" class="link-row" data-orders="${esc(JSON.stringify(params))}"><span class="link-label">${label}</span><span class="num">${value}</span><i class="ph ph-caret-right chev"></i></button>`;
}

const STATUS_LABEL = { completed: 'Selesai', shipping: 'Dikirim', cancelled: 'Batal', returned: 'Retur' };
let orderView = null; // { params, defaults, filters, data, page }

async function loadOrders(params) {
  const q = new URLSearchParams();
  for (const k of ['start', 'end', 'sku', 'channel', 'status', 'city', 'category']) if (params[k]) q.set(k, params[k]);
  const res = await api('/api/orders?' + q.toString());
  return res.ok ? res.body : { count: 0, lines: 0, total: 0, orders: [] };
}

function orderSummary(data) {
  return `<b>${count(data.count)} pesanan</b> · ${count(data.lines)} baris · ${rupiah(data.total)}`;
}

function renderOrderList() {
  const v = orderView;
  if (!v.data) return;
  const q = (v.filters.q || '').toLowerCase();
  const list = q ? v.data.orders.filter((o) => [o.order_id, o.product, o.sku, o.channel, o.city].some((x) => String(x).toLowerCase().includes(q))) : v.data.orders;
  const pages = Math.max(1, Math.ceil(list.length / 10));
  v.page = Math.min(v.page, pages);
  const rows = list.slice((v.page - 1) * 10, v.page * 10);
  $('order-rows').innerHTML = rows.length ? rows.map((o) => {
    const x = day(o.date);
    return `<button type="button" class="order-row" data-open-sku="${esc(o.sku)}">
      <span class="order-main">${v.params.sku
        ? `<b>${x.dayName.slice(0, 3)}, ${x.d} ${x.month.slice(0, 3)}</b><small>${esc(o.order_id)} · ${esc(o.city)}</small>`
        : `<b>${esc(shortName(o.product))}</b><small>${x.d} ${x.month.slice(0, 3)} · ${esc(o.order_id)} · ${esc(o.city)}</small>`}</span>
      <span class="order-side"><b>${rupiah(o.total)}</b><small><span class="dot ${CHANNEL_CLASS[o.channel]}"></span>${o.qty} unit · <span class="badge s-${o.status}">${STATUS_LABEL[o.status]}</span></small></span>
    </button>`;
  }).join('') : `<p class="muted">${v.data.orders.length ? 'Tidak ada pesanan yang cocok.' : 'Tidak ada pesanan.'}</p>`;
  $('order-pager').hidden = list.length <= 10;
  $('order-pager').innerHTML = `<span class="muted small">${(v.page - 1) * 10 + (rows.length ? 1 : 0)}-${(v.page - 1) * 10 + rows.length} dari ${count(list.length)}</span>
    <span class="pager-buttons">
      <button type="button" class="chip" data-order-page="${v.page - 1}" ${v.page === 1 ? 'disabled' : ''} aria-label="Sebelumnya"><i class="ph ph-caret-left"></i></button>
      <span class="small">${v.page} / ${pages}</span>
      <button type="button" class="chip" data-order-page="${v.page + 1}" ${v.page === pages ? 'disabled' : ''} aria-label="Berikutnya"><i class="ph ph-caret-right"></i></button>
    </span>`;
}

function openDetail(kind, key, fromTrail = false) {
  if (!fromTrail) trail.push([kind, key]);
  $('sheet-back').hidden = trail.length < 2;
  let html = '';

  if (kind === 'orders') {
    $('sheet-kicker').textContent = 'Daftar pesanan';
    $('sheet-title').textContent = key.title || 'Pesanan';
    $('sheet-sub').textContent = periodText();
    $('sheet-body').innerHTML = '<div class="loading-state"><span class="spinner"></span>Memuat pesanan…</div>';
    if (!sheet.open) sheet.showModal();
    // Fixed context (product, city) stays; dates, channel, category and status can be changed.
    const defaults = {
      start: key.date || key.start || period.start,
      end: key.date || key.end || period.end,
      channel: key.channel || '', category: key.category || '', status: key.status || '',
    };
    const fixed = [key.sku && shortName(result.summary.products.find((p) => p.sku === key.sku)?.product || key.sku), key.city]
      .filter(Boolean).join(' · ');
    orderView = {
      params: key, defaults, filters: { ...defaults, q: '' }, data: null, page: 1,
      barOptions: { status: true, placeholder: 'Cari no. pesanan, produk, kota', defaults },
      async reload() {
        const mine = this;
        $('order-summary').innerHTML = '<span class="spinner"></span>';
        const data = await loadOrders({ ...mine.params, date: undefined, ...mine.filters });
        if (orderView !== mine) return; // user already moved on
        mine.data = data;
        $('order-summary').innerHTML = orderSummary(data);
        renderOrderList();
      },
      render() { renderOrderList(); },
    };
    $('sheet-body').innerHTML = `
      ${fixed ? `<p class="muted small">Untuk: <b>${esc(fixed)}</b></p>` : ''}
      ${filterBar('orders', orderView.filters, orderView.barOptions)}
      <div id="order-summary" class="order-summary"></div>
      <div id="order-rows" class="order-list"></div>
      <nav id="order-pager" class="pager" hidden></nav>`;
    orderView.reload();
    return;
  }

  if (kind === 'missing') {
    const list = key;
    const value = list.reduce((n, o) => n + o.total, 0);
    $('sheet-kicker').textContent = 'Perlu dicek';
    $('sheet-title').textContent = `${count(list.length)} pesanan lama tidak ada di file ini`;
    $('sheet-sub').textContent = 'Di rentang tanggal dan channel yang sama dengan file yang baru diupload';
    html = `<p class="lost-line">Pesanan ini tersimpan dari upload sebelumnya (nilai ${rupiah(value)}), tapi tidak muncul di file terbaru.
        Bisa karena pesanannya dihapus marketplace, atau karena file ini hanya berisi sebagian pesanan (mis. diekspor dengan filter status).
        Pesanan <b>tidak dihapus</b> kecuali kamu memilih hapus.</p>
      <div class="missing-actions">
        <button type="button" class="btn btn-outline" data-missing="keep">Biarkan</button>
        <button type="button" class="btn danger-btn" data-missing="remove">Hapus ${count(list.length)} pesanan dari data</button>
      </div>
      <div class="order-list">${list.map((o) => {
        const x = day(o.date);
        return `<div class="order-row static-row">
          <span class="order-main"><b>${esc(o.order_id)}</b><small>${x.d} ${x.month.slice(0, 3)} · ${o.lines} baris · ${esc(o.status.split(',').map((st) => STATUS_LABEL[st] || st).join(', '))}</small></span>
          <span class="order-side"><b>${rupiah(o.total)}</b><small><span class="dot ${CHANNEL_CLASS[o.channel]}"></span>${esc(o.channel)}</small></span>
        </div>`;
      }).join('')}</div>`;
    missingList = list;
  } else if (kind === 'share') {
    const names = { product: 'produk', channel: 'channel', day: 'hari' };
    $('sheet-kicker').textContent = 'Porsi dari total';
    $('sheet-title').textContent = `Perbandingan per ${names[key.of]}`;
    $('sheet-sub').textContent = periodText();
    html = shareView(key);
  } else if (kind === 'finding') {
    const c = findingCards[key];
    $('sheet-kicker').textContent = 'Temuan';
    $('sheet-title').textContent = c.title;
    $('sheet-sub').textContent = periodText();
    html = `<div class="finding-detail">${c.body}</div><div class="tip-box"><b>Saran</b><p>${c.tip}</p></div>`;
  } else if (kind === 'day') {
    const d = result.details.days[key];
    if (!d) return;
    $('sheet-kicker').textContent = 'Hari';
    $('sheet-title').textContent = longDate(key);
    $('sheet-sub').textContent = d.revenue ? `${pct(d.revenue, result.summary.net_revenue)} dari net revenue periode ini` : 'Tidak ada penjualan';
    html = ordersButton(`Lihat semua pesanan ${day(key).d} ${day(key).month}`, { date: key, title: `Pesanan ${longDate(key)}` }) +
      statsBlock(d, { date: key }, `${day(key).d} ${day(key).month}`, 'day', key) + channelBlock(d.by_channel, (c) => `${count(c.orders)} pesanan`) +
      topProductsBlock('Produk terlaris hari ini', d.top_products) + lostBlock(d.lost) + citiesBlock(d.cities, { date: key }, `${day(key).d} ${day(key).month}`);
  } else if (kind === 'product') {
    const d = result.details.products[key];
    if (!d) return;
    const all = result.summary.products;
    const idx = all.findIndex((p) => p.sku === key);
    const p = all[idx] || { product: key, sku: key };
    $('sheet-kicker').textContent = idx >= 0 ? `Peringkat ${idx + 1} dari ${all.length} produk` : 'Produk';
    $('sheet-title').textContent = shortName(p.product);
    $('sheet-sub').textContent = `${p.sku} · ${periodText()}`;
    html = ordersButton('Lihat semua pesanan produk ini', { sku: key, title: shortName(p.product) }) +
      statsBlock(d, { sku: key }, shortName(p.product), 'product', key) + channelBlock(d.by_channel, (c) => `${count(c.units)} unit`) + dailyBlock(d.by_day, { sku: key }, shortName(p.product)) +
      lostBlock(d.lost) + citiesBlock(d.cities, { sku: key }, shortName(p.product)) +
      `<section><h3>Nama listing di tiap marketplace</h3><ul class="listing-list">${Object.entries(d.listings).map(([ch, names]) =>
        `<li><span class="dot ${CHANNEL_CLASS[ch]}"></span><div><b>${esc(ch)}</b>${names.map((n) => `<span>${esc(n)}</span>`).join('')}</div></li>`).join('')}</ul></section>`;
  } else {
    const d = result.details.channels[key];
    if (!d) return;
    $('sheet-kicker').textContent = 'Channel';
    $('sheet-title').textContent = key;
    $('sheet-sub').textContent = periodText();
    html = ordersButton(`Lihat semua pesanan ${key}`, { channel: key, title: `Pesanan ${key}` }) +
      statsBlock(d, { channel: key }, key, 'channel', key) + topProductsBlock(`Produk terlaris di ${esc(key)}`, d.top_products) + dailyBlock(d.by_day, { channel: key }, key) +
      lostBlock(d.lost) + citiesBlock(d.cities, { channel: key }, key);
  }

  $('sheet-body').innerHTML = html;
  $('sheet-body').scrollTop = 0;
  if (!sheet.open) sheet.showModal();
}

function closeSheet() {
  trail.length = 0;
  sheet.close();
}

$('findings').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-finding]');
  if (btn) { trail.length = 0; openDetail('finding', Number(btn.dataset.finding)); }
});
$('product-rows').addEventListener('click', (e) => {
  const row = e.target.closest('tr[data-sku]');
  if (row) { trail.length = 0; openDetail('product', row.dataset.sku); }
});
$('product-rows').addEventListener('keydown', (e) => {
  const row = e.target.closest('tr[data-sku]');
  if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); trail.length = 0; openDetail('product', row.dataset.sku); }
});
$('channels').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-channel]');
  if (btn) { trail.length = 0; openDetail('channel', btn.dataset.channel); }
});
let missingList = [];
$('sheet-body').addEventListener('click', async (e) => {
  const miss = e.target.closest('[data-missing]');
  if (miss) {
    if (miss.dataset.missing === 'keep') return closeSheet();
    if (!miss.dataset.armed) {
      miss.dataset.armed = '1';
      miss.textContent = 'Yakin? Klik sekali lagi untuk menghapus';
      setTimeout(() => { if (miss.isConnected) { delete miss.dataset.armed; miss.textContent = `Hapus ${count(missingList.length)} pesanan dari data`; } }, 4000);
      return;
    }
    const res = await api('/api/remove-orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ order_ids: missingList.map((o) => o.order_id) }) });
    if (res.ok) {
      result = res.body; period = res.body.period;
      closeSheet(); render();
      toast(`${count(missingList.length)} pesanan dihapus (${count(res.body.removed_lines)} baris).`);
    } else toast('Gagal menghapus. Coba lagi.');
    return;
  }
  const ord = e.target.closest('[data-orders]');
  if (ord) return openDetail('orders', JSON.parse(ord.dataset.orders));
  const pg = e.target.closest('[data-order-page]');
  if (pg) { orderView.page = Number(pg.dataset.orderPage); return renderOrderList(); }
  const shareBtn = e.target.closest('[data-share]');
  if (shareBtn) return openDetail('share', JSON.parse(shareBtn.dataset.share));
  const chBtn = e.target.closest('[data-open-channel]');
  if (chBtn) return openDetail('channel', chBtn.dataset.openChannel);
  const sku = e.target.closest('[data-open-sku]');
  if (sku) return openDetail('product', sku.dataset.openSku);
  const dayBtn = e.target.closest('[data-open-day]');
  if (dayBtn) openDetail('day', dayBtn.dataset.openDay);
});
$('sheet-back').addEventListener('click', () => {
  trail.pop();
  const [kind, key] = trail.at(-1);
  openDetail(kind, key, true);
});
$('sheet-close').addEventListener('click', closeSheet);
sheet.addEventListener('click', (e) => { if (e.target === sheet) closeSheet(); }); // tap outside the panel
sheet.addEventListener('cancel', () => { trail.length = 0; }); // Escape key

boot();

// Small chart in the sheet: show the hovered day's number above the bars.
$('sheet-body').addEventListener('pointerover', (e) => {
  const col = e.target.closest('.small-daily .col');
  const out = e.target.closest('section')?.querySelector('.readout');
  if (col && out) { out.textContent = col.dataset.label + (col.disabled ? '' : ' · klik untuk lihat pesanan'); out.classList.add('on'); }
});
$('sheet-body').addEventListener('pointerout', (e) => {
  const out = e.target.closest('section')?.querySelector('.readout');
  if (out && !e.relatedTarget?.closest?.('.small-daily')) { out.textContent = out.dataset.default; out.classList.remove('on'); }
});

// KPI cards: open the orders behind each number.
const KPI_ORDERS = [
  { status: 'counted', title: 'Pesanan yang dihitung' },
  { status: 'counted', title: 'Pesanan yang dihitung' },
  { status: 'counted', title: 'Pesanan yang dihitung' },
  { status: 'lost', title: 'Pesanan batal & retur' },
];
document.querySelectorAll('.kpi').forEach((card, i) => {
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  const open = () => { trail.length = 0; openDetail('orders', KPI_ORDERS[i]); };
  card.addEventListener('click', open);
  card.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
});
