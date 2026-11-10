// Trang Dịch PDF: tải file lên máy chủ (server/), theo dõi tiến độ, mở hoặc tải bản dịch.
// Danh sách lượt dịch được nhớ trong trình duyệt để tải lại trang vẫn thấy, cho tới khi hết hạn.
const PDF_JOBS_KEY = 'thongngu.pdfjobs.v1';
const pdfState = { health: null, polling: new Set() };

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
    <div class="job-actions row" style="margin-top:12px;flex-wrap:wrap" hidden></div>`;
  $('.job-close', el).addEventListener('click', () => { forgetJob(id); el.remove(); });
  $('#jobs').prepend(el);
  return el;
}

function setJobStatus(el, text, { percent = null, indet = false, error = false } = {}) {
  $('.job-status', el).innerHTML = error ? `<span style="color:var(--danger)">${esc(text)}</span>` : esc(text);
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
    }
  });
  xhr.addEventListener('load', () => {
    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch { /* bỏ qua */ }
    if (xhr.status !== 202) {
      setJobStatus(el, body.detail || `Máy chủ báo lỗi ${xhr.status}`, { error: true });
      return;
    }
    el.id = `job-${body.id}`;
    $('.job-close', el).replaceWith($('.job-close', el).cloneNode(true));
    $('.job-close', el).addEventListener('click', () => { forgetJob(body.id); el.remove(); });
    rememberJob(body);
    renderJob(el, body);
    pollJob(body.id);
  });
  const fail = () => setJobStatus(el, 'Không gửi được file. Kiểm tra mạng rồi thử lại.', { error: true });
  xhr.addEventListener('error', fail);
  xhr.addEventListener('timeout', fail);
  xhr.send(form);
}

function renderJob(el, st) {
  const acts = $('.job-actions', el);
  if (st.status === 'queued') {
    const ahead = Math.max(0, (st.position || 1) - 1);
    setJobStatus(el, ahead ? `Đang chờ, trước bạn còn ${ahead} file` : 'Sắp tới lượt...', { indet: true });
  } else if (st.status === 'running') {
    setJobStatus(el, `Đang dịch ${st.pages} trang · ${st.stage || 'Chuẩn bị'} · ${Math.round(st.percent)}%`, { percent: Math.max(10, st.percent) });
  } else if (st.status === 'done') {
    setJobStatus(el, `Xong sau ${Math.round(st.seconds || 0)} giây · ${st.pages} trang · tự xoá lúc ${new Date(st.expires_at * 1000).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}`, { percent: 100 });
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
      if (st.files.includes('mono')) add('Xem bản dịch', 'mono', 'btn-primary');
      if (st.files.includes('dual')) add('Xem song ngữ', 'dual', 'btn-outline');
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
