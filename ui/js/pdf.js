// Trang Dịch PDF: tải file lên máy chủ (server/), theo dõi tiến độ, mở hoặc tải bản dịch.
// Danh sách lượt dịch được nhớ trong trình duyệt để tải lại trang vẫn thấy, cho tới khi hết hạn.
const PDF_JOBS_KEY = 'thongngu.pdfjobs.v1';
const pdfState = { health: null, polling: new Set(), autoOpen: new Set(), waits: new Map() };

function pdfUrl(path) {
  return `${PDF_API}${path}`;
}

async function pdfGet(path, timeoutMs = 10000) {
  if (!PDF_API) throw new Error('chưa cấu hình máy chủ');
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(pdfUrl(path), { signal: ctl.signal });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(body.detail || `HTTP ${r.status}`); e.status = r.status; throw e; }
    return body;
  } finally {
    clearTimeout(t);
  }
}

function savedJobs() {
  try {
    const now = Date.now() / 1000;
    return (JSON.parse(localStorage.getItem(PDF_JOBS_KEY)) || []).filter((j) => !j.expires_at || j.expires_at > now);
  } catch { return []; }
}
function rememberJob(job) {
  try {
    const list = savedJobs().filter((j) => j.id !== job.id);
    list.unshift({ id: job.id, name: job.name, expires_at: job.expires_at });
    localStorage.setItem(PDF_JOBS_KEY, JSON.stringify(list.slice(0, 20)));
  } catch { /* trình duyệt chặn lưu trữ: vẫn dùng được, chỉ không nhớ sau khi tải lại */ }
}
function forgetJob(id) {
  try { localStorage.setItem(PDF_JOBS_KEY, JSON.stringify(savedJobs().filter((j) => j.id !== id))); } catch { /* bỏ qua */ }
}

async function checkPdf() {
  const offline = $('#pdf-offline'), ready = $('#pdf-ready');
  try {
    pdfState.health = await pdfGet('/health', 8000);
    offline.hidden = true;
    ready.hidden = false;
    $('#pdf-limit-mb').textContent = pdfState.health.max_mb;
    $('#pdf-limit-pages').textContent = pdfState.health.max_pages;
    $('#pdf-keep-hours').textContent = pdfState.health.keep_hours;
    savedJobs().forEach((j) => { if (!$(`#job-${j.id}`)) { jobCard(j.id, j.name); pollJob(j.id); } });
  } catch {
    offline.hidden = false;
    ready.hidden = true;
  }
}
$('#pdf-recheck').addEventListener('click', checkPdf);

// ---------- chọn / kéo thả file ----------
const dz = $('#dropzone');
$('#pdf-file').addEventListener('change', (e) => { [...e.target.files].forEach(startUpload); e.target.value = ''; });
['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('over'); }));
dz.addEventListener('drop', (e) => [...(e.dataTransfer?.files || [])].forEach(startUpload));
// Thả file ra ngoài vùng kéo thả thì trình duyệt sẽ mở file và rời trang: chặn lại.
['dragover', 'drop'].forEach((ev) => window.addEventListener(ev, (e) => { if (state.page === 'pdf') e.preventDefault(); }));

function jobCard(id, name) {
  const el = document.createElement('div');
  el.className = 'card job';
  el.id = `job-${id}`;
  el.innerHTML = `
    <div class="job-head">
      <div class="job-icon">${icon('file')}</div>
      <div class="grow"><div class="job-name">${esc(name)}</div><div class="job-status">Đang kiểm tra...</div></div>
      <button class="icon-btn job-close" title="Ẩn khỏi danh sách">${icon('close')}</button>
    </div>
    <div class="progress"><div></div></div>
    <div class="job-actions row mt-12 wrap" hidden></div>`;
  $('.job-close', el).addEventListener('click', () => { forgetJob(id); el.remove(); });
  $('#jobs').prepend(el);
  return el;
}

function setJobStatus(el, text, { percent = null, indet = false, error = false } = {}) {
  $('.job-status', el).innerHTML = error ? `<span class="danger">${esc(text)}</span>` : esc(text);
  const bar = $('.progress', el);
  bar.classList.toggle('indet', indet);
  bar.hidden = error;
  if (percent !== null) $('.progress > div', el).style.width = `${Math.max(2, Math.min(100, percent))}%`;
}

function startUpload(file) {
  const h = pdfState.health || { max_mb: 30 };
  const isPdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
  if (!isPdf) { toast('Chỉ nhận file PDF', 'err'); return; }
  if (file.size > h.max_mb * 1024 * 1024) { toast(`File lớn hơn ${h.max_mb} MB`, 'err'); return; }

  const tmpId = `up${Date.now()}${Math.random().toString(36).slice(2, 6)}`;
  const el = jobCard(tmpId, file.name);
  setJobStatus(el, 'Đang tải lên... 0%', { percent: 0 });
  const wait = waitOpen(file.name);

  const form = new FormData();
  form.append('file', file);
  form.append('lang', $('#pdf-lang').value);
  form.append('dual', $('#pdf-dual').checked ? 'true' : 'false');
  form.append('pages', $('#pdf-pages').value.trim());

  // XMLHttpRequest thay vì fetch để có % tải lên (file 30 MB trên mạng chậm mất khá lâu).
  const xhr = new XMLHttpRequest();
  xhr.open('POST', pdfUrl('/jobs'));
  xhr.timeout = 5 * 60 * 1000;
  xhr.upload.addEventListener('progress', (e) => {
    if (e.lengthComputable) {
      const p = Math.round((e.loaded / e.total) * 100);
      setJobStatus(el, `Đang tải lên... ${p}%`, { percent: p * 0.1 });
      waitSet(wait, { percent: p * 0.1, step: 'file', detail: `Đang tải lên ${p}%` });
    }
  });
  xhr.addEventListener('load', () => {
    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch { /* bỏ qua */ }
    if (xhr.status !== 202) {
      const msg = body.detail || `Máy chủ báo lỗi ${xhr.status}`;
      setJobStatus(el, msg, { error: true });
      waitFromJob(wait, { status: 'error', error: msg });
      return;
    }
    wait.jobId = body.id;
    pdfState.waits.set(body.id, wait);
    el.id = `job-${body.id}`;
    $('.job-close', el).replaceWith($('.job-close', el).cloneNode(true));
    $('.job-close', el).addEventListener('click', () => { forgetJob(body.id); el.remove(); });
    rememberJob(body);
    pdfState.autoOpen.add(body.id); // dịch xong thì tự mở trình đọc song song
    renderJob(el, body);
    pollJob(body.id);
  });
  const fail = () => {
    setJobStatus(el, 'Không gửi được file. Kiểm tra mạng rồi thử lại.', { error: true });
    waitFromJob(wait, { status: 'error', error: 'Không gửi được tập tin. Kiểm tra mạng rồi thử lại.' });
  };
  xhr.addEventListener('error', fail);
  xhr.addEventListener('timeout', fail);
  xhr.send(form);
}

// Số lượt trước mình (máy dịch một file một lúc, ai gửi trước dịch trước).
// Máy chủ cũ chưa có "ahead" thì suy từ "position" (chỉ đếm file đang chờ).
function queueAhead(st) {
  return typeof st.ahead === 'number' ? st.ahead : Math.max(0, (st.position || 1) - 1);
}

// Giây -> "khoảng 12 phút", "khoảng 1 giờ 10 phút". Máy chủ cũ không gửi số thì trả chuỗi rỗng.
function fmtWait(sec) {
  if (typeof sec !== 'number' || sec <= 0) return '';
  if (sec < 60) return 'dưới 1 phút';
  const min = Math.round(sec / 60);
  if (min < 60) return `khoảng ${min} phút`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `khoảng ${h} giờ ${m} phút` : `khoảng ${h} giờ`;
}

// Câu báo khi đang chờ. twin: cùng file với một lượt đang dịch, xong cùng lúc với lượt đó.
function queueText(st, long) {
  const ahead = queueAhead(st);
  if (st.twin) {
    const t = fmtWait(st.eta_s);
    return `Paper này đang được dịch cho người khác, bạn nhận bản dịch cùng lúc${t ? `, ${t} nữa` : ''}`;
  }
  if (!ahead) return long ? 'Sắp tới lượt của bạn' : 'Sắp tới lượt...';
  const t = fmtWait(st.wait_s);
  return long
    ? `Hiện có người đang dịch, vui lòng chờ. Trước bạn còn ${ahead} lượt${t ? `, ${t} nữa tới lượt bạn` : ', máy rảnh là tới bạn'}`
    : `Có người đang dịch, trước bạn còn ${ahead} lượt${t ? ` · ${t} nữa tới lượt` : ''}`;
}

function renderJob(el, st) {
  const acts = $('.job-actions', el);
  const w = pdfState.waits.get(st.id);
  if (w) waitFromJob(w, st);
  if (st.status === 'queued') {
    setJobStatus(el, queueText(st, false), { indet: true });
  } else if (st.status === 'running') {
    setJobStatus(el, `Đang dịch ${st.pages} trang · ${st.stage || 'Chuẩn bị'} · ${Math.round(st.percent)}%`, { percent: Math.max(10, st.percent) });
  } else if (st.status === 'done') {
    const until = new Date(st.expires_at * 1000).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
    const head = st.shared ? 'Có sẵn bản dịch của đúng file này' : `Xong sau ${Math.round(st.seconds || 0)} giây`;
    setJobStatus(el, `${head} · ${st.pages} trang · tự xoá lúc ${until}`, { percent: 100 });
    if (acts.hidden) {
      acts.hidden = false;
      const add = (label, kind, cls, download = false) => {
        const a = document.createElement('a');
        a.className = `btn ${cls} btn-sm`;
        a.href = pdfUrl(`/jobs/${st.id}/files/${kind}${download ? '?download=1' : ''}`);
        a.target = '_blank';
        a.rel = 'noopener';
        a.innerHTML = (download ? icon('download') : icon('external')) + label;
        acts.appendChild(a);
      };
      if (pdfState.autoOpen.delete(st.id) && state.page === 'pdf' && st.files.includes('original')) openReader(st);
      if (st.files.includes('original')) {
        const read = document.createElement('button');
        read.className = 'btn btn-primary btn-sm';
        read.innerHTML = `${icon('book2')}Đọc song song`;
        read.addEventListener('click', () => openReader(st));
        acts.appendChild(read);
      }
      if (st.files.includes('mono')) add('Tải bản dịch', 'mono', 'btn-ghost', true);
      if (st.files.includes('dual')) add('Tải song ngữ', 'dual', 'btn-ghost', true);
    }
  } else if (st.status === 'error') {
    setJobStatus(el, st.error || 'Dịch không thành công', { error: true });
  }
}

async function pollJob(id) {
  if (pdfState.polling.has(id)) return;
  pdfState.polling.add(id);
  let failures = 0;
  try {
    for (;;) {
      const el = $(`#job-${id}`);
      if (!el) return; // người dùng đã ẩn thẻ
      try {
        const st = await pdfGet(`/jobs/${id}`);
        failures = 0;
        renderJob(el, st);
        if (st.status === 'done' || st.status === 'error') return;
      } catch (e) {
        if (e.status === 404) {
          setJobStatus(el, 'Lượt dịch đã hết hạn và bị xoá khỏi máy chủ.', { error: true });
          forgetJob(id);
          return;
        }
        failures += 1;
        setJobStatus(el, 'Mất kết nối tới máy chủ, đang thử lại...', { indet: true });
      }
      await new Promise((r) => setTimeout(r, Math.min(1500 * (1 + failures), 15000)));
    }
  } finally {
    pdfState.polling.delete(id);
  }
}

// ---------------------------------------------------------------- màn hình chờ "Đang dịch..."
// Theo mẫu user gửi: tiêu đề + "Vui lòng chờ trong giây lát", tiến độ tổng thể, 3 bước, ô "Mẹo hay".
const WAIT_TIPS = [
  ['Bấm từ nào cũng ra nghĩa', 'Trong màn hình đọc song song, bấm vào một từ ở bản gốc để xem nghĩa tiếng Việt, phiên âm và lưu thuật ngữ.'],
  ['Dịch nhanh một đoạn', 'Kéo chuột chọn một đoạn rồi bấm nút "Dịch" hiện ra ngay cạnh vùng chọn.'],
  ['Viết thống nhất thuật ngữ', 'Lưu cách dịch bạn chọn vào Bảng thuật ngữ, lần sau tra lại nó luôn hiện đầu tiên.'],
  ['Chỉ cần vài trang?', 'Nhập ô "Trang cần dịch", ví dụ 1-5, để có bản dịch nhanh hơn.'],
  ['Sao lưu bảng thuật ngữ', 'Bấm "Xuất CSV" ở mục Bảng thuật ngữ để mở bằng Excel hoặc chuyển sang máy khác.'],
  ['Tra ngược khi viết bài', 'Gõ tiếng Việt có dấu vào ô Tra cứu để tìm thuật ngữ tiếng Anh tương ứng.'],
];
const WAIT_STEPS = [['file', 'Đang xử lý tập tin'], ['translate', 'Đang dịch văn bản'], ['finish', 'Đang hoàn thiện tài liệu']];

function waitOpen(name) {
  const back = document.createElement('div');
  back.className = 'wait-back';
  back.innerHTML = `
    <div class="card wait-card" role="dialog" aria-live="polite">
      <div class="wait-title">Đang dịch...</div>
      <div class="wait-sub">Vui lòng chờ trong giây lát.</div>
      <div class="wait-meta"><span class="wait-file">${icon('file')}<span>${esc(name)}</span></span><span class="wait-detail"></span></div>
      <div class="wait-total-label">Tiến độ tổng thể</div>
      <div class="wait-bar-row"><div class="progress"><div></div></div><b class="wait-pct">0%</b></div>
      <div class="wait-steps">${WAIT_STEPS.map(([k, label]) => `
        <div class="wait-step" data-step="${k}"><span class="wait-ico"></span><span>${label}</span></div>`).join('')}
      </div>
      <div class="wait-tip">
        <span class="wait-tip-ico">${icon('bulb')}</span>
        <div class="grow"><div class="wait-tip-title"></div><div class="wait-tip-text"></div></div>
        <span class="badge badge-teal">Mẹo hay</span>
      </div>
      <div class="wait-foot"><span class="note" data-note>Bạn có thể đóng khung này, việc dịch vẫn chạy và tự mở khi xong.</span>
        <button class="btn btn-outline btn-sm" data-act="bg">Chạy nền</button></div>
    </div>`;
  document.body.appendChild(back);
  const w = { back, jobId: null, tip: Math.floor(Math.random() * WAIT_TIPS.length), timer: null };
  const showTip = () => {
    const [t, d] = WAIT_TIPS[w.tip % WAIT_TIPS.length];
    $('.wait-tip-title', back).textContent = t;
    const txt = $('.wait-tip-text', back);
    txt.textContent = d;
    txt.style.animation = 'none'; void txt.offsetWidth; txt.style.animation = '';
    w.tip += 1;
  };
  showTip();
  w.timer = setInterval(showTip, 6000);
  $('[data-act="bg"]', back).addEventListener('click', () => waitClose(w));
  waitSet(w, { percent: 0, step: 'file', detail: 'Đang tải tập tin lên...' });
  return w;
}

function waitSet(w, { percent, step, sub, detail, title, done = false, error = false }) {
  if (!w.back.isConnected) return;
  const card = $('.wait-card', w.back);
  if (title) $('.wait-title', card).textContent = title;
  if (sub !== undefined) $('.wait-sub', card).textContent = sub;
  if (detail !== undefined) $('.wait-detail', card).textContent = detail;
  if (typeof percent === 'number') {
    const p = Math.max(0, Math.min(100, Math.round(percent)));
    $('.progress > div', card).style.width = `${p}%`;
    $('.wait-pct', card).textContent = `${p}%`;
  }
  const order = WAIT_STEPS.map((s) => s[0]);
  const at = done ? order.length : order.indexOf(step);
  $$('.wait-step', card).forEach((el, i) => {
    const state = error && i === at ? 'failed' : i < at ? 'done' : i === at ? 'active' : 'todo';
    el.className = `wait-step ${state}`;
    $('.wait-ico', el).innerHTML = state === 'done' ? icon('checkCircle') : state === 'failed' ? icon('alert') : '';
  });
  card.classList.toggle('done', done);
  card.classList.toggle('failed', error);
}

function waitClose(w) {
  clearInterval(w.timer);
  if (w.back.isConnected) w.back.remove();
  if (w.jobId) pdfState.waits.delete(w.jobId);
}

// Trạng thái từ máy chủ -> màn hình chờ. Phần trăm: tải lên chiếm 10% đầu, phần dịch 90% còn lại.
function waitFromJob(w, st) {
  if (st.status === 'queued') {
    waitSet(w, { percent: 10, step: 'file', detail: queueText(st, true) });
  } else if (st.status === 'running') {
    const step = st.step === 'translate' || st.step === 'finish' ? st.step : 'file';
    waitSet(w, { percent: 10 + st.percent * 0.9, step, detail: `${st.stage || 'Chuẩn bị'} · ${st.pages} trang` });
  } else if (st.status === 'done') {
    waitSet(w, { percent: 100, done: true, title: 'Đã dịch xong!', sub: 'Đang mở bản gốc và bản dịch song song...', detail: `${st.pages} trang · ${Math.round(st.seconds || 0)} giây` });
    setTimeout(() => waitClose(w), 900);
  } else if (st.status === 'error') {
    waitSet(w, { step: 'translate', error: true, title: 'Dịch không thành công', sub: st.error || 'Có lỗi khi dịch tập tin.' });
    $('[data-act="bg"]', w.back).textContent = 'Đóng';
    const note = $('[data-note]', w.back);
    if (note) note.textContent = '';
  }
}
