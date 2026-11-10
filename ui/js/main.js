// Cửa sổ chính: Tra cứu, Bảng thuật ngữ, Dịch PDF, Cài đặt.
hydrateIcons();

const state = {
  page: 'lookup',
  dirLocked: null,   // null = tự nhận theo chữ gõ vào; 'en-vi' | 'vi-en' khi người dùng bấm đổi chiều
  last: null,        // kết quả tra gần nhất
  terms: [],
  info: null,
  pdfQueue: [],
  pdfBusy: false,
};

// ================= Điều hướng =================
function showPage(name) {
  state.page = name;
  $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === name));
  $$('section.page').forEach((s) => { s.hidden = s.dataset.page !== name; });
  $('.main').scrollTop = 0;
  if (name === 'glossary') loadGlossary();
  if (name === 'pdf') checkPdf();
  if (name === 'settings') loadSettings();
  if (name === 'lookup') $('#q').focus();
}
$$('#nav button').forEach((b) => b.addEventListener('click', () => showPage(b.dataset.page)));

// ================= Tra cứu =================
function currentDir() {
  return state.dirLocked || guessDirection($('#q').value);
}
function refreshDirLabel() {
  $('#dir-label').textContent = DIR_LABEL[currentDir()];
  $('#dir-toggle').classList.toggle('auto', !state.dirLocked);
}
$('#q').addEventListener('input', () => {
  if (!$('#q').value.trim()) state.dirLocked = null;
  refreshDirLabel();
});
$('#dir-toggle').addEventListener('click', () => {
  state.dirLocked = currentDir() === 'en-vi' ? 'vi-en' : 'en-vi';
  refreshDirLabel();
  if ($('#q').value.trim()) doLookup($('#q').value);
});
$('#search-form').addEventListener('submit', (e) => {
  e.preventDefault();
  doLookup($('#q').value);
});

async function doLookup(raw) {
  const query = raw.replace(/\s+/g, ' ').trim();
  if (!query) return;
  $('#q').value = query;
  refreshDirLabel();
  const direction = currentDir();
  $('#lookup-empty').hidden = true;
  $('#result').hidden = false;
  $('#r-term').textContent = query;
  $('#r-meta').innerHTML = `<span class="badge badge-teal">${DIR_LABEL[direction]}</span>`;
  $('#r-saved').innerHTML = '';
  $('#r-errors').textContent = '';
  $('#r-def-card').hidden = true;
  renderSkeleton($('#r-cands'));
  $('#s-note').value = '';
  $('#save-card').hidden = false;
  $('#r-intext').hidden = true;
  prefillSave({ query, direction, glossary: [], candidates: [] });

  let res;
  try {
    res = await call('lookup', { query, direction });
  } catch (err) {
    $('#r-cands').innerHTML = `<div class="muted small">Lỗi khi tra: ${esc(err)}</div>`;
    return;
  }
  if ($('#q').value !== query) return; // người dùng đã tra từ khác trong lúc chờ
  state.last = res;
  renderResult(res);
  loadRecent();
}

function renderResult(res) {
  const meta = [`<span class="badge badge-teal">${DIR_LABEL[res.direction]}</span>`];
  if (res.phonetic) meta.push(`<span class="phonetic">${esc(res.phonetic)}</span>`);
  $('#r-meta').innerHTML = meta.join('');

  if (res.glossary.length) {
    $('#r-saved').innerHTML = res.glossary.map((t) => `
      <div class="saved-banner">
        ${icon('checkCircle')}
        <div class="grow">
          <div class="small muted">Đã có trong bảng thuật ngữ${t.field ? ` · ${esc(t.field)}` : ''}</div>
          <div class="term-text">${esc(res.direction === 'en-vi' ? t.vi : t.en)}</div>
          ${t.note ? `<div class="small muted selectable">${esc(t.note)}</div>` : ''}
        </div>
      </div>`).join('');
  }

  const sentence = res.mode === 'sentence';
  $('#r-cands-title').textContent = sentence ? 'Bản dịch máy' : 'Gợi ý cách dịch';
  $('#r-cands-hint').hidden = sentence;
  renderCandidates($('#r-cands'), res.candidates, (c) => {
    if (res.direction === 'en-vi') $('#s-vi').value = c.text;
    else $('#s-en').value = c.text;
  });
  $('#r-errors').textContent = errorNote(res.errors);

  // Câu dài: không lưu cả câu làm thuật ngữ; thay vào đó nhắc các thuật ngữ đã lưu có trong câu.
  $('#save-card').hidden = sentence;
  $('#r-intext').hidden = !sentence;
  if (sentence) {
    $('#r-intext-list').innerHTML = res.in_text.length
      ? res.in_text.map((t) => `<div class="cand"><div class="grow"><div class="term-text">${esc(t.en)} → <b style="color:var(--teal)">${esc(t.vi)}</b></div>${t.field ? `<div class="src">${esc(t.field)}</div>` : ''}</div></div>`).join('')
      : '<div class="muted small">Câu này chưa chứa thuật ngữ nào trong bảng của bạn. Bôi đen từng thuật ngữ để tra và lưu.</div>';
  }

  const defs = [];
  (res.definitions || []).forEach((d) => {
    defs.push(`<div><div class="def-pos">${esc(d.pos)}</div><p class="def-text">${esc(d.text)}</p>${d.example ? `<p class="def-ex">"${esc(d.example)}"</p>` : ''}</div>`);
  });
  if (res.wiki && res.wiki.extract) {
    defs.push(`<div><div class="def-pos">wikipedia · ${esc(res.wiki.title)}</div><p class="wiki-extract selectable">${esc(res.wiki.extract)}</p>
      <button class="link small" data-url="${esc(res.wiki.url)}">Đọc tiếp trên Wikipedia</button></div>`);
  }
  $('#r-def-card').hidden = !defs.length;
  $('#r-defs').innerHTML = defs.join('');
  $$('#r-defs [data-url]').forEach((b) => b.addEventListener('click', () => call('open_url', { url: b.dataset.url })));

  prefillSave(res);
}

function prefillSave(res) {
  const hit = res.glossary && res.glossary[0];
  const top = res.candidates && res.candidates[0];
  if (res.direction === 'en-vi') {
    $('#s-en').value = res.query;
    $('#s-vi').value = hit ? hit.vi : (top ? top.text : '');
  } else {
    $('#s-vi').value = res.query;
    $('#s-en').value = hit ? hit.en : (top ? top.text : '');
  }
  if (hit) {
    $('#s-field').value = hit.field || '';
    $('#s-note').value = hit.note || '';
  }
  $('#s-save').innerHTML = `${icon('check')}${hit ? 'Cập nhật thuật ngữ' : 'Lưu thuật ngữ'}`;
  const firstCand = $('#r-cands .cand');
  if (firstCand && !hit) firstCand.classList.add('chosen');
}

$('#s-save').addEventListener('click', async () => {
  const entry = {
    id: null,
    en: $('#s-en').value.trim(),
    vi: $('#s-vi').value.trim(),
    field: $('#s-field').value.trim(),
    note: $('#s-note').value.trim(),
  };
  if (!entry.en || !entry.vi) { toast('Cần có cả tiếng Anh và tiếng Việt', 'err'); return; }
  try {
    const saved = await call('glossary_save', { entry });
    toast(`Đã lưu: ${saved.en} → ${saved.vi}`);
    refreshCount();
    loadFields();
    if (state.last) {
      state.last.glossary = [saved];
      renderResult(state.last);
    }
  } catch (err) {
    toast(String(err), 'err');
  }
});

async function loadRecent() {
  const items = (await call('recent_list')) || [];
  $('#recent-wrap').hidden = !items.length;
  $('#recent').innerHTML = items.map((q) => `<button class="chip">${esc(q)}</button>`).join('');
  $$('#recent .chip').forEach((c) => c.addEventListener('click', () => { state.dirLocked = null; doLookup(c.textContent); }));
}

// Esc trong ô tra cứu: xoá và quay về màn hình gợi ý
$('#q').addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    $('#q').value = '';
    state.dirLocked = null;
    refreshDirLabel();
    $('#result').hidden = true;
    $('#lookup-empty').hidden = false;
  }
});

// ================= Bảng thuật ngữ =================
async function refreshCount() {
  const all = (await call('glossary_list', { query: '', field: '' })) || [];
  $('#nav-count').textContent = all.length;
}

async function loadFields() {
  const fields = (await call('glossary_fields')) || [];
  $('#fields-list').innerHTML = fields.map((f) => `<option value="${esc(f)}">`).join('');
  const sel = $('#g-field');
  const cur = sel.value;
  sel.innerHTML = '<option value="">Tất cả chuyên ngành</option>' + fields.map((f) => `<option>${esc(f)}</option>`).join('');
  if (fields.includes(cur)) sel.value = cur;
}

let glossaryTimer = null;
$('#g-q').addEventListener('input', () => { clearTimeout(glossaryTimer); glossaryTimer = setTimeout(loadGlossary, 150); });
$('#g-field').addEventListener('change', loadGlossary);

async function loadGlossary() {
  const query = $('#g-q').value.trim();
  const field = $('#g-field').value;
  state.terms = (await call('glossary_list', { query, field })) || [];
  const filtered = Boolean(query || field);
  $('#g-table-card').hidden = !state.terms.length;
  $('#g-empty').hidden = Boolean(state.terms.length);
  $('#g-empty-title').textContent = filtered ? 'Không tìm thấy thuật ngữ phù hợp' : 'Chưa có thuật ngữ nào';
  $('#g-empty-text').textContent = filtered ? 'Thử từ khoá khác hoặc bỏ lọc chuyên ngành.' : 'Tra một từ ở mục Tra cứu rồi bấm "Lưu thuật ngữ", hoặc thêm tay bằng nút phía trên.';
  $('#g-rows').innerHTML = state.terms.map((t) => `
    <tr data-id="${t.id}">
      <td class="en">${esc(t.en)}</td>
      <td class="vi">${esc(t.vi)}</td>
      <td>${t.field ? `<span class="badge">${esc(t.field)}</span>` : '<span class="muted">-</span>'}</td>
      <td class="note">${esc(t.note)}</td>
      <td class="actions">
        <button class="icon-btn" data-act="edit" title="Sửa">${icon('edit')}</button>
        <button class="icon-btn" data-act="del" title="Xoá">${icon('trash')}</button>
      </td>
    </tr>`).join('');
  $('#g-foot').textContent = `${state.terms.length} thuật ngữ${filtered ? ' khớp bộ lọc' : ''}`;
  $$('#g-rows button').forEach((b) => b.addEventListener('click', () => {
    const id = Number(b.closest('tr').dataset.id);
    const term = state.terms.find((t) => t.id === id);
    if (b.dataset.act === 'edit') editTerm(term);
    else deleteTerm(term);
  }));
  loadFields();
  refreshCount();
}

function termForm(t = {}) {
  return `
    <div class="field"><label>Tiếng Anh</label><input class="input" name="en" value="${esc(t.en)}"></div>
    <div class="field"><label>Tiếng Việt</label><input class="input" name="vi" value="${esc(t.vi)}"></div>
    <div class="field"><label>Chuyên ngành</label><input class="input" name="field" list="fields-list" value="${esc(t.field)}"></div>
    <div class="field"><label>Ghi chú / ngữ cảnh</label><textarea class="textarea" name="note">${esc(t.note)}</textarea></div>`;
}

async function editTerm(term) {
  const data = await modal({
    title: term ? 'Sửa thuật ngữ' : 'Thêm thuật ngữ',
    body: termForm(term || {}),
    actions: [{ label: 'Huỷ', value: null }, { label: term ? 'Lưu thay đổi' : 'Thêm', value: 'form', cls: 'btn-primary', primary: true }],
  });
  if (!data) return;
  if (!data.en.trim() || !data.vi.trim()) { toast('Cần có cả tiếng Anh và tiếng Việt', 'err'); return; }
  try {
    await call('glossary_save', { entry: { id: term ? term.id : null, en: data.en.trim(), vi: data.vi.trim(), field: data.field.trim(), note: data.note.trim() } });
    toast(term ? 'Đã lưu thay đổi' : 'Đã thêm thuật ngữ');
    loadGlossary();
  } catch (err) { toast(String(err), 'err'); }
}
$('#g-add').addEventListener('click', () => editTerm(null));

async function deleteTerm(term) {
  const ok = await modal({
    title: 'Xoá thuật ngữ?',
    body: `<p style="margin:0">Xoá <b>${esc(term.en)}</b> → <b>${esc(term.vi)}</b> khỏi bảng thuật ngữ. Không hoàn tác được.</p>`,
    actions: [{ label: 'Huỷ', value: false }, { label: 'Xoá', value: true, cls: 'btn-danger', primary: true }],
  });
  if (!ok) return;
  await call('glossary_delete', { id: term.id });
  toast('Đã xoá');
  loadGlossary();
}

$('#g-export').addEventListener('click', async () => {
  try {
    const path = await call('glossary_export');
    if (path) toast(`Đã xuất ra ${path}`);
  } catch (err) { toast(String(err), 'err'); }
});
$('#g-import').addEventListener('click', async () => {
  try {
    const n = await call('glossary_import');
    if (n !== null && n !== undefined) { toast(`Đã nhập ${n} thuật ngữ`); loadGlossary(); }
  } catch (err) { toast(String(err), 'err'); }
});

// ================= Dịch PDF =================
async function checkPdf() {
  const st = await call('pdf_status');
  $('#pdf-missing').hidden = st.found;
  $('#pdf-ready').hidden = !st.found;
  $('#pdf-missing-detail').textContent = st.found ? '' : (st.detail || '');
  return st;
}
$('#pdf-recheck').addEventListener('click', checkPdf);

$('#dropzone').addEventListener('click', async () => {
  const paths = await call('pick_pdfs');
  if (paths && paths.length) enqueuePdfs(paths);
});

if (IN_APP && TAURI.webview) {
  TAURI.webview.getCurrentWebview().onDragDropEvent((ev) => {
    const p = ev.payload;
    const dz = $('#dropzone');
    if (p.type === 'over' || p.type === 'enter') dz.classList.add('over');
    else dz.classList.remove('over');
    if (p.type === 'drop') {
      const pdfs = (p.paths || []).filter((x) => x.toLowerCase().endsWith('.pdf'));
      if (state.page !== 'pdf') showPage('pdf');
      if (pdfs.length) enqueuePdfs(pdfs);
      else toast('Chỉ nhận file .pdf', 'err');
    }
  });
}

function enqueuePdfs(paths) {
  paths.forEach((path) => {
    const id = `j${Date.now()}${Math.random().toString(36).slice(2, 6)}`;
    const name = path.split(/[\\/]/).pop();
    const el = document.createElement('div');
    el.className = 'card job';
    el.id = id;
    el.innerHTML = `
      <div class="job-head">
        <div class="job-icon">${icon('file')}</div>
        <div class="grow"><div class="job-name">${esc(name)}</div><div class="job-status">Đang chờ</div></div>
        <div class="job-actions row"></div>
      </div>
      <div class="progress"><div></div></div>
      <details class="small muted" style="margin-top:8px"><summary style="cursor:pointer">Nhật ký</summary><div class="job-log"></div></details>`;
    $('#jobs').prepend(el);
    state.pdfQueue.push({ id, path });
  });
  runPdfQueue();
}

async function runPdfQueue() {
  if (state.pdfBusy) return;
  const job = state.pdfQueue.shift();
  if (!job) return;
  state.pdfBusy = true;
  const el = $(`#${job.id}`);
  $('.job-status', el).textContent = 'Đang dịch... (lần đầu có thể lâu vì phải tải mô hình nhận dạng bố cục)';
  $('.progress', el).classList.add('indet');
  try {
    const out = await call('pdf_translate', {
      jobId: job.id,
      path: job.path,
      lang: $('#pdf-lang').value,
      dual: $('#pdf-dual').checked,
      engine: $('#pdf-engine').value,
    });
    $('.progress', el).classList.remove('indet');
    $('.progress > div', el).style.width = '100%';
    $('.job-status', el).textContent = 'Xong';
    const acts = $('.job-actions', el);
    (out.files || []).forEach((f) => {
      const b = document.createElement('button');
      b.className = 'btn btn-outline btn-sm';
      b.textContent = /dual/i.test(f) ? 'Mở bản song ngữ' : 'Mở bản dịch';
      b.addEventListener('click', () => call('open_path', { path: f }));
      acts.appendChild(b);
    });
    const fb = document.createElement('button');
    fb.className = 'icon-btn';
    fb.title = 'Mở thư mục';
    fb.innerHTML = icon('folder');
    fb.addEventListener('click', () => call('open_path', { path: out.dir }));
    acts.appendChild(fb);
  } catch (err) {
    $('.progress', el).classList.remove('indet');
    $('.job-status', el).innerHTML = `<span style="color:var(--danger)">Lỗi: ${esc(err)}</span>`;
    $('details', el).open = true;
  }
  state.pdfBusy = false;
  runPdfQueue();
}

onEvent('pdf-progress', (p) => {
  const el = $(`#${p.job_id}`);
  if (!el) return;
  if (p.line) {
    const log = $('.job-log', el);
    log.textContent = (log.textContent + p.line + '\n').slice(-6000);
    log.scrollTop = log.scrollHeight;
  }
  if (typeof p.percent === 'number') {
    $('.progress', el).classList.remove('indet');
    $('.progress > div', el).style.width = `${Math.max(2, Math.min(100, p.percent))}%`;
    $('.job-status', el).textContent = `Đang dịch... ${Math.round(p.percent)}%`;
  }
});

// ================= Cài đặt =================
async function loadSettings() {
  const info = await call('app_info');
  state.info = info;
  $('#set-data').textContent = info.data_dir;
  $('#set-version').textContent = `Thông Ngữ ${info.version}`;
  const st = info.pdf;
  $('#set-pdf').innerHTML = st.found
    ? `Đã sẵn sàng: <span class="code">${esc(st.path)}</span>${st.version ? ` (bản ${esc(st.version)})` : ''}`
    : 'Chưa cài. Vào mục Dịch PDF để xem cách cài.';
  $('#set-hotkey-text').innerHTML = info.hotkey_installed
    ? `Đã cài: bôi đen chữ rồi bấm <kbd>Ctrl</kbd> <kbd>Alt</kbd> <kbd>E</kbd>. Bấm lại nút bên cạnh nếu phím tắt ngừng hoạt động.`
    : 'Bôi đen chữ ở bất kỳ ứng dụng nào rồi bấm phím tắt để mở cửa sổ tra nhanh.';
  $('#set-hotkey').textContent = info.hotkey_installed ? 'Cài lại phím tắt' : 'Cài phím tắt';
}
$('#set-hotkey').addEventListener('click', async () => {
  try {
    const msg = await call('install_hotkey');
    toast(msg || 'Đã cài phím tắt Ctrl+Alt+E');
    loadSettings();
  } catch (err) { toast(String(err), 'err'); }
});
$('#set-open-data').addEventListener('click', () => call('open_path', { path: state.info.data_dir }));
$('#set-github').addEventListener('click', () => call('open_url', { url: 'https://github.com/mhieuz-dev/thong-ngu' }));

// ================= Nhận yêu cầu tra từ phím tắt / cửa sổ tra nhanh =================
onEvent('lookup-request', (q) => {
  showPage('lookup');
  state.dirLocked = null;
  doLookup(q);
});

// ================= Khởi động =================
refreshDirLabel();
loadRecent();
loadFields();
refreshCount();
$('#q').focus();
