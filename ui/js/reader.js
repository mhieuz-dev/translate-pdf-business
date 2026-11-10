// Trình đọc song song: bản gốc bên trái, bản dịch bên phải, cùng một hàng cho mỗi trang nên
// không bao giờ lệch nhau. Bấm vào một từ (hoặc kéo chọn một cụm) để xem nghĩa và lưu thuật ngữ.
// Vẽ PDF bằng PDF.js (vendor/pdfjs), chỉ vẽ trang khi sắp cuộn tới.
const RD = {
  lib: null, el: null, scroller: null, job: null, docs: null, sizes: null,
  base: 1, zoom: 1, renderToken: 0, io: null, pageIo: null, visible: new Map(),
  cache: new Map(), pop: null, mark: null, lastTap: 0,
};
const RD_FIELD_KEY = 'thongngu.lastField';
const RD_HINT_KEY = 'thongngu.readerHintSeen';

async function rdLib() {
  if (RD.lib) return RD.lib;
  const base = new URL('vendor/pdfjs/', location.href);
  const lib = await import(new URL('pdf.min.mjs?v=4.10.38', base).href);
  lib.GlobalWorkerOptions.workerSrc = new URL('pdf.worker.min.mjs?v=4.10.38', base).href;
  RD.fonts = new URL('standard_fonts/', base).href;
  RD.lib = lib;
  return lib;
}

async function rdFetch(kind) {
  const r = await fetch(pdfUrl(`/jobs/${RD.job.id}/files/${kind}`));
  if (!r.ok) throw new Error(r.status === 404 ? 'Bản dịch đã hết hạn và bị xoá khỏi máy chủ.' : `Máy chủ báo lỗi ${r.status}`);
  return new Uint8Array(await r.arrayBuffer());
}

function rdStore(key, value) { try { localStorage.setItem(key, value); } catch { /* bỏ qua */ } }
function rdRead(key) { try { return localStorage.getItem(key) || ''; } catch { return ''; } }

// ---------------------------------------------------------------- mở / đóng
async function openReader(job) {
  closeReader();
  RD.job = job;
  RD.zoom = 1;
  const pages = job.page_numbers || [];
  const el = document.createElement('div');
  el.className = 'reader';
  el.dataset.show = 'original';
  const dl = (kind, label) => `<a class="btn btn-outline btn-sm" href="${pdfUrl(`/jobs/${job.id}/files/${kind}?download=1`)}" download>${icon('download')}${label}</a>`;
  el.innerHTML = `
    <div class="reader-bar">
      <div class="reader-title">
        <div class="brand-mark">Ă</div>
        <div style="min-width:0">
          <div class="reader-name">${esc(job.name)}</div>
          <div class="reader-sub">Bấm một từ để xem nghĩa · kéo chọn đoạn rồi bấm Dịch</div>
        </div>
      </div>
      <div class="seg reader-tabs"><button class="on" data-show="original">Bản gốc</button><button data-show="translated">Bản dịch</button></div>
      <span class="reader-page-no" id="rd-page">${pages.length ? `Trang ${pages[0]}` : ''}</span>
      <div class="zoom">
        <button class="icon-btn" data-z="out" title="Thu nhỏ">${icon('minus')}</button>
        <span id="rd-zoom">100%</span>
        <button class="icon-btn" data-z="in" title="Phóng to">${icon('plus')}</button>
        <button class="icon-btn" data-z="fit" title="Vừa khung">${icon('fit')}</button>
      </div>
      ${(job.files || []).includes('mono') ? dl('mono', 'Bản dịch') : ''}
      ${(job.files || []).includes('dual') ? dl('dual', 'Song ngữ') : ''}
      <button class="btn btn-primary btn-sm" id="rd-close">${icon('close')}Đóng</button>
    </div>
    <div class="reader-scroll">
      <div class="reader-loading"><div><div class="empty-icon">${icon('file')}</div>
        <b>Đang mở bản gốc và bản dịch...</b><div class="progress indet"><div></div></div></div></div>
    </div>`;
  document.body.appendChild(el);
  RD.el = el;
  RD.scroller = $('.reader-scroll', el);
  $('#rd-close', el).addEventListener('click', closeReader);
  $$('.reader-tabs button', el).forEach((b) => b.addEventListener('click', () => {
    el.dataset.show = b.dataset.show;
    $$('.reader-tabs button', el).forEach((x) => x.classList.toggle('on', x === b));
    rdClosePop();
  }));
  $$('[data-z]', el).forEach((b) => b.addEventListener('click', () => rdZoom(b.dataset.z)));
  RD.scroller.addEventListener('scroll', () => { if (RD.pop || RD.pill) rdClosePop(); }, { passive: true });
  RD.scroller.addEventListener('pointerdown', (e) => { if (!e.target.closest('.sel-pill')) rdHidePill(); });
  RD.scroller.addEventListener('pointerup', rdOnPointerUp);
  try {
    const [lib, a, b] = await Promise.all([rdLib(), rdFetch('original'), rdFetch('mono')]);
    if (RD.el !== el) return; // đã đóng trong lúc tải
    const open = (data) => lib.getDocument({ data, standardFontDataUrl: RD.fonts, isEvalSupported: false }).promise;
    const [orig, tr] = await Promise.all([open(a), open(b)]);
    RD.docs = { orig, tr };
    await rdBuild();
    rdHint();
  } catch (e) {
    if (RD.el !== el) return;
    RD.scroller.innerHTML = `<div class="reader-loading"><div><div class="empty-icon">${icon('alert')}</div>
      <b>Không mở được bản dịch</b><p class="muted">${esc(e.message || e)}</p></div></div>`;
  }
}

function closeReader() {
  rdClosePop();
  if (RD.io) RD.io.disconnect();
  if (RD.pageIo) RD.pageIo.disconnect();
  if (RD.docs) { RD.docs.orig.destroy(); RD.docs.tr.destroy(); }
  if (RD.el) RD.el.remove();
  Object.assign(RD, { el: null, scroller: null, docs: null, sizes: null, io: null, pageIo: null, mark: null });
  RD.visible.clear();
  document.body.style.overflow = '';
}

document.addEventListener('keydown', (e) => {
  if (!RD.el) return;
  if (e.key === 'Escape') { if (RD.pop) rdClosePop(); else closeReader(); }
  if ((e.ctrlKey || e.metaKey) && (e.key === '=' || e.key === '+')) { e.preventDefault(); rdZoom('in'); }
  if ((e.ctrlKey || e.metaKey) && e.key === '-') { e.preventDefault(); rdZoom('out'); }
});
window.addEventListener('resize', () => { if (RD.docs) { clearTimeout(RD.rt); RD.rt = setTimeout(() => rdLayout(true), 150); } });

// ---------------------------------------------------------------- dựng trang
function rdIsNarrow() { return window.matchMedia('(max-width: 860px)').matches; }

function rdFitScale() {
  const w = RD.scroller.clientWidth;
  const col = rdIsNarrow() ? w - 20 : (w - 48 - 24) / 2;
  const pageW = RD.sizes[0].orig.width;
  return Math.max(0.3, Math.min(col / pageW, 2.2));
}

async function rdBuild() {
  const { orig, tr } = RD.docs;
  const n = Math.min(orig.numPages, tr.numPages);
  const sizes = await Promise.all(Array.from({ length: n }, async (_, i) => {
    const [p, q] = await Promise.all([orig.getPage(i + 1), tr.getPage(i + 1)]);
    return { orig: p.getViewport({ scale: 1 }), tr: q.getViewport({ scale: 1 }) };
  }));
  RD.sizes = sizes;
  const nums = RD.job.page_numbers && RD.job.page_numbers.length === n ? RD.job.page_numbers : sizes.map((_, i) => i + 1);
  RD.scroller.innerHTML = `
    <div class="reader-cols-head">
      <div><span class="badge badge-teal">Bản gốc</span>English</div>
      <div><span class="badge badge-solid">Bản dịch</span>Tiếng Việt</div>
    </div>
    <div class="reader-rows">${sizes.map((_, i) => `
      <div class="reader-row" data-i="${i}">
        <div class="row-label">Trang ${nums[i]}</div>
        <div class="pdf-page orig loading" data-i="${i}" data-doc="orig"></div>
        <div class="pdf-page tr loading" data-i="${i}" data-doc="tr"></div>
      </div>`).join('')}
    </div>`;
  RD.nums = nums;
  RD.io = new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) rdRender(en.target); }),
    { root: RD.scroller, rootMargin: '900px 0px' });
  RD.pageIo = new IntersectionObserver((entries) => {
    entries.forEach((en) => RD.visible.set(Number(en.target.dataset.i), en.intersectionRatio));
    let best = 0, ratio = -1;
    RD.visible.forEach((r, i) => { if (r > ratio) { ratio = r; best = i; } });
    const lbl = $('#rd-page', RD.el);
    if (lbl) lbl.textContent = `Trang ${nums[best]} · ${best + 1}/${nums.length}`;
  }, { root: RD.scroller, threshold: [0, 0.25, 0.5, 0.75, 1] });
  $$('.pdf-page', RD.scroller).forEach((c) => RD.io.observe(c));
  $$('.reader-row', RD.scroller).forEach((r) => RD.pageIo.observe(r));
  rdLayout(true);
}

// Đặt kích thước khung trang theo tỉ lệ; vẽ lại trang đang thấy khi đổi tỉ lệ.
function rdLayout(refit) {
  if (refit) RD.base = rdFitScale();
  const scale = RD.base * RD.zoom;
  const before = RD.scroller.scrollHeight ? RD.scroller.scrollTop / RD.scroller.scrollHeight : 0;
  RD.scale = scale;
  RD.renderToken += 1;
  RD.scroller.classList.toggle('zoomed', RD.zoom > 1.001); // phóng to thì mới cho cuộn ngang
  $$('.pdf-page', RD.scroller).forEach((c) => {
    const vp = RD.sizes[Number(c.dataset.i)][c.dataset.doc];
    c.style.width = `${Math.floor(vp.width * scale)}px`;
    c.style.height = `${Math.floor(vp.height * scale)}px`;
    if (c.dataset.scale && Number(c.dataset.scale) !== scale) {
      c.dataset.scale = '';
      c.classList.add('loading');
      c.innerHTML = '';
    }
  });
  RD.scroller.scrollTop = before * RD.scroller.scrollHeight;
  const z = $('#rd-zoom', RD.el);
  if (z) z.textContent = `${Math.round(scale * 100)}%`;
  // vẽ lại những trang đang ở gần màn hình
  $$('.pdf-page', RD.scroller).forEach((c) => {
    const r = c.getBoundingClientRect();
    if (r.bottom > -900 && r.top < window.innerHeight + 900) rdRender(c);
  });
}

function rdZoom(kind) {
  if (!RD.docs) return;
  if (kind === 'fit') RD.zoom = 1;
  else RD.zoom = Math.max(0.5, Math.min(3, RD.zoom * (kind === 'in' ? 1.2 : 1 / 1.2)));
  rdClosePop();
  rdLayout(kind === 'fit');
}

async function rdRender(cell) {
  const scale = RD.scale;
  if (!RD.docs || Number(cell.dataset.scale) === scale || cell.dataset.busy === String(RD.renderToken)) return;
  const token = RD.renderToken;
  cell.dataset.busy = String(token);
  try {
    const page = await RD.docs[cell.dataset.doc].getPage(Number(cell.dataset.i) + 1);
    const vp = page.getViewport({ scale });
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(vp.width * dpr);
    canvas.height = Math.floor(vp.height * dpr);
    await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
    if (token !== RD.renderToken) return; // người dùng đã đổi tỉ lệ trong lúc vẽ
    const tl = document.createElement('div');
    tl.className = 'textLayer';
    tl.style.setProperty('--scale-factor', String(scale));
    cell.innerHTML = '';
    cell.append(canvas, tl);
    cell.classList.remove('loading');
    cell.dataset.scale = String(scale);
    await new RD.lib.TextLayer({ textContentSource: page.streamTextContent(), container: tl, viewport: vp }).render();
  } catch (e) {
    console.warn('không vẽ được trang', e);
  } finally {
    if (cell.dataset.busy === String(token)) cell.dataset.busy = '';
  }
}

// ---------------------------------------------------------------- bấm từ / chọn cụm
function rdWordAt(x, y) {
  let node, offset;
  if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(x, y);
    if (!p) return null;
    node = p.offsetNode; offset = p.offset;
  } else if (document.caretRangeFromPoint) {
    const r = document.caretRangeFromPoint(x, y);
    if (!r) return null;
    node = r.startContainer; offset = r.startOffset;
  }
  if (!node || node.nodeType !== 3 || !node.parentElement.closest('.textLayer')) return null;
  const t = node.textContent;
  const isW = (c) => c !== undefined && /[\p{L}\p{N}'’-]/u.test(c);
  let s = offset, e = offset;
  while (s > 0 && isW(t[s - 1])) s--;
  while (e < t.length && isW(t[e])) e++;
  if (s === e) return null;
  const range = document.createRange();
  range.setStart(node, s);
  range.setEnd(node, e);
  // Con trỏ phải thật sự nằm trên chữ (không phải khoảng trắng cạnh chữ).
  const rect = range.getBoundingClientRect();
  if (x < rect.left - 2 || x > rect.right + 2 || y < rect.top - 4 || y > rect.bottom + 4) return null;
  const word = t.slice(s, e).replace(/^['’-]+|['’-]+$/g, '');
  if (!/\p{L}/u.test(word)) return null;
  return { text: word, range, span: node.parentElement };
}

function rdContext(span) {
  const parts = [span.previousElementSibling, span, span.nextElementSibling]
    .filter((x) => x && x.tagName === 'SPAN').map((x) => x.textContent);
  return parts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 300);
}

function rdOnPointerUp(e) {
  if (e.button && e.button !== 0) return;
  if (e.target.closest('.word-pop, .sel-pill')) return;
  setTimeout(() => {
    const sel = window.getSelection();
    const picked = sel && !sel.isCollapsed ? sel.toString().replace(/\s+/g, ' ').trim() : '';
    if (picked && sel.anchorNode && sel.anchorNode.parentElement && sel.anchorNode.parentElement.closest('.textLayer')) {
      rdClosePop();
      rdPill(picked.slice(0, 1500), sel.getRangeAt(0).cloneRange(), sel.anchorNode.parentElement);
      return;
    }
    const hit = rdWordAt(e.clientX, e.clientY);
    if (hit) rdShow(hit.text, hit.range, hit.span);
    else rdClosePop();
  }, 10);
}

// Nút nổi cạnh đoạn vừa chọn: [Ă Dịch] | sao chép | nghe
function rdPill(text, range, span) {
  rdHidePill();
  const rects = [...range.getClientRects()].filter((r) => r.width > 0);
  const last = rects[rects.length - 1] || range.getBoundingClientRect();
  const pill = document.createElement('div');
  pill.className = 'sel-pill';
  pill.innerHTML = `
    <button data-act="tr"><span class="mini-mark">Ă</span>Dịch</button>
    <span class="sep"></span>
    <button data-act="copy" title="Sao chép">${icon('copy')}</button>
    <button data-act="speak" title="Nghe">${icon('speaker')}</button>`;
  document.body.appendChild(pill);
  RD.pill = pill;
  const w = pill.offsetWidth;
  let left = Math.min(Math.max(8, last.right - w / 2), window.innerWidth - w - 8);
  let top = last.bottom + 8;
  if (top + 40 > window.innerHeight) top = Math.max(8, rects[0] ? rects[0].top - 46 : last.top - 46);
  Object.assign(pill.style, { left: `${left}px`, top: `${top}px` });
  pill.addEventListener('pointerdown', (ev) => ev.preventDefault()); // giữ nguyên vùng chọn khi bấm nút
  $('[data-act="tr"]', pill).addEventListener('click', () => { rdHidePill(); rdShow(text, range, span); });
  $('[data-act="copy"]', pill).addEventListener('click', () => { rdCopy(text); rdHidePill(); });
  $('[data-act="speak"]', pill).addEventListener('click', () => rdSpeak(text));
}

function rdHidePill() {
  if (RD.pill) { RD.pill.remove(); RD.pill = null; }
}

function rdCopy(text) {
  const done = () => toast('Đã sao chép');
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => toast('Không sao chép được', 'err'));
  else {
    const t = Object.assign(document.createElement('textarea'), { value: text });
    document.body.appendChild(t); t.select();
    try { document.execCommand('copy'); done(); } catch { toast('Không sao chép được', 'err'); }
    t.remove();
  }
}

// Đọc to bằng giọng có sẵn của trình duyệt (miễn phí, không gửi đi đâu).
function rdSpeak(text) {
  if (!('speechSynthesis' in window)) { toast('Trình duyệt không hỗ trợ đọc to', 'err'); return; }
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.slice(0, 600));
  u.lang = guessDirection(text) === 'vi-en' ? 'vi-VN' : 'en-US';
  u.rate = 0.95;
  speechSynthesis.speak(u);
}

function rdMark(range, cell) {
  if (RD.mark) RD.mark.forEach((m) => m.remove());
  RD.mark = [];
  const box = cell.getBoundingClientRect();
  [...range.getClientRects()].slice(0, 12).forEach((r) => {
    const m = document.createElement('div');
    m.className = 'word-mark';
    Object.assign(m.style, { left: `${r.left - box.left - 2}px`, top: `${r.top - box.top - 1}px`, width: `${r.width + 4}px`, height: `${r.height + 2}px` });
    cell.appendChild(m);
    RD.mark.push(m);
  });
}

// ---------------------------------------------------------------- thẻ nghĩa
function rdClosePop() {
  rdHidePill();
  if (RD.pop) { RD.pop.remove(); RD.pop = null; }
  if (RD.mark) { RD.mark.forEach((m) => m.remove()); RD.mark = null; }
}

function rdPlace(pop, anchor) {
  if (rdIsNarrow()) return; // điện thoại: CSS đưa thẻ xuống đáy màn hình
  const w = pop.offsetWidth, h = pop.offsetHeight, gap = 10;
  let left = Math.min(Math.max(12, anchor.left + anchor.width / 2 - w / 2), window.innerWidth - w - 12);
  let top = anchor.bottom + gap;
  if (top + h > window.innerHeight - 12) top = Math.max(12, anchor.top - h - gap);
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}

async function rdLookup(query) {
  if (RD.cache.has(query)) return RD.cache.get(query);
  const p = call('lookup', { query });
  RD.cache.set(query, p);
  try { return await p; } catch (e) { RD.cache.delete(query); throw e; }
}

async function rdShow(query, range, span) {
  const cell = span.closest('.pdf-page');
  if (!cell) return;
  rdClosePop();
  rdMark(range, cell);
  const anchor = range.getBoundingClientRect();
  const context = rdContext(span);
  const passage = query.split(/\s+/).length > 6;
  const pop = document.createElement('div');
  pop.className = `word-pop${passage ? ' wide' : ''}`;
  pop.innerHTML = `
    <div class="word-pop-head">
      <div class="row" style="align-items:flex-start">
        ${passage ? `<div class="grow"><div class="src-text">${esc(query)}</div></div>` : `<div class="word-pop-term grow">${esc(query)}</div>`}
        <div class="tools">
          <button class="icon-btn" data-act="speak" title="Nghe">${icon('speaker')}</button>
          <button class="icon-btn" data-act="close" title="Đóng (Esc)">${icon('close')}</button>
        </div>
      </div>
      <div class="word-pop-meta"><span class="badge badge-teal">${DIR_LABEL[guessDirection(query)]}</span></div>
    </div>
    <div class="word-pop-body">
      <div class="tl-loading">
        <div class="tl-loading-row">Đang dịch<span class="tl-dots"><i></i><i></i><i></i></span></div>
        <div class="tl-lines"><span></span><span></span>${passage ? '<span></span>' : ''}</div>
      </div>
    </div>`;
  document.body.appendChild(pop);
  RD.pop = pop;
  $('[data-act="close"]', pop).addEventListener('click', rdClosePop);
  $('[data-act="speak"]', pop).addEventListener('click', () => rdSpeak(query));
  pop.addEventListener('pointerup', (e) => e.stopPropagation());
  rdPlace(pop, anchor);

  let res;
  try {
    res = await rdLookup(query);
  } catch (e) {
    if (RD.pop !== pop) return;
    $('.word-pop-body', pop).innerHTML = `<div class="muted small">Không tra được: ${esc(e)}</div>`;
    return;
  }
  if (RD.pop !== pop) return; // người dùng đã bấm từ khác
  rdFill(pop, res, context);
  rdPlace(pop, anchor);
}

function rdFill(pop, res, context) {
  const sentence = res.mode === 'sentence';
  if (sentence) { rdFillPassage(pop, res); return; }
  const hit = res.glossary[0];
  const meta = [`<span class="badge badge-teal">${DIR_LABEL[res.direction]}</span>`];
  if (res.phonetic) meta.push(`<span class="phonetic">${esc(res.phonetic)}</span>`);
  $('.word-pop-meta', pop).innerHTML = meta.join('');
  const body = $('.word-pop-body', pop);
  body.innerHTML = `
    ${hit ? `<div class="saved-banner">${icon('checkCircle')}<div class="grow"><div class="small muted">Cách dịch bạn đã chọn${hit.field ? ` · ${esc(hit.field)}` : ''}</div>
      <div class="term-text">${esc(res.direction === 'en-vi' ? hit.vi : hit.en)}</div></div></div>` : ''}
    <div class="cands"></div>
    ${sentence && res.in_text.length ? `<div class="word-pop-def"><b>Thuật ngữ của bạn trong đoạn:</b> ${res.in_text.map((t) => `${esc(t.en)} → ${esc(t.vi)}`).join(' · ')}</div>` : ''}
    <div class="word-pop-def" data-def hidden></div>
    ${errorNote(res.errors) ? `<div class="note-warn">${errorNote(res.errors)}</div>` : ''}`;
  let chosen = hit ? null : res.candidates[0] || null;
  let save = null; // nút Lưu (không có khi tra cả câu)
  renderCandidates($('.cands', body), res.candidates.slice(0, 4), (c) => { chosen = c; if (save) save.disabled = false; });
  if (chosen) { const first = $('.cands .cand', body); if (first) first.classList.add('chosen'); }

  const d = (res.definitions || [])[0];
  const defEl = $('[data-def]', body);
  if (d) { defEl.hidden = false; defEl.innerHTML = `<b>${esc(d.pos)}</b> ${esc(d.text)}`; }
  else if (res.wiki && res.wiki.extract) { defEl.hidden = false; defEl.innerHTML = `<b>Wikipedia</b> ${esc(res.wiki.extract.slice(0, 220))}${res.wiki.extract.length > 220 ? '...' : ''}`; }

  if (sentence) return; // câu dài: chỉ đọc bản dịch, không lưu cả câu thành thuật ngữ
  const foot = document.createElement('div');
  foot.className = 'word-pop-foot';
  foot.innerHTML = `<input class="input" list="fields-list" placeholder="Chuyên ngành (tuỳ chọn)" value="${esc(hit ? hit.field : rdRead(RD_FIELD_KEY))}">
    <button class="btn btn-primary btn-sm">${icon('bookmark')}${hit ? 'Cập nhật' : 'Lưu'}</button>`;
  pop.appendChild(foot);
  save = $('button', foot);
  save.disabled = !chosen;
  save.addEventListener('click', async () => {
    if (!chosen) return;
    const field = $('input', foot).value.trim();
    const entry = res.direction === 'en-vi'
      ? { id: null, en: res.query, vi: chosen.text, field, note: hit ? hit.note : context }
      : { id: null, en: chosen.text, vi: res.query, field, note: hit ? hit.note : context };
    try {
      const saved = await call('glossary_save', { entry });
      rdStore(RD_FIELD_KEY, field);
      RD.cache.delete(res.query);
      toast(`Đã lưu: ${saved.en} → ${saved.vi}`);
      if (typeof refreshCount === 'function') refreshCount();
      if (typeof loadFields === 'function') loadFields();
      res.glossary = [saved];
      rdFill(pop, res, context);
    } catch (e) { toast(String(e), 'err'); }
  });
}

function rdFillPassage(pop, res) {
  const meta = [`<span class="badge badge-teal">${DIR_LABEL[res.direction]}</span>`];
  $('.word-pop-meta', pop).innerHTML = meta.join('');
  const best = res.candidates[0];
  const body = $('.word-pop-body', pop);
  body.innerHTML = best
    ? `<div class="row" style="align-items:flex-start;gap:6px">
        <div class="grow"><div class="tr-label" style="margin-top:0">Bản dịch</div><div class="tr-text">${esc(best.text)}</div></div>
        <div class="tools"><button class="icon-btn" data-act="copy" title="Sao chép bản dịch">${icon('copy')}</button></div>
      </div>
      ${res.in_text.length ? `<div class="tr-label">Thuật ngữ của bạn trong đoạn</div>
        <div class="chips">${res.in_text.map((t) => `<span class="badge badge-teal">${esc(t.en)} → ${esc(t.vi)}</span>`).join('')}</div>` : ''}
      <div class="note-warn" style="margin-top:10px">Dịch máy, chỉ để đọc hiểu nhanh. Bấm vào từng thuật ngữ để xem cách dịch chuẩn.</div>`
    : `<div class="muted small">Không dịch được đoạn này. ${errorNote(res.errors)}</div>`;
  const copy = $('[data-act="copy"]', body);
  if (copy) copy.addEventListener('click', () => rdCopy(best.text));
}

function rdHint() {
  if (rdRead(RD_HINT_KEY) || !RD.el) return;
  const h = document.createElement('div');
  h.className = 'reader-hint';
  h.innerHTML = `<span>${rdIsNarrow() ? 'Chạm vào một từ để xem nghĩa, giữ và kéo để chọn cả đoạn. Đổi Bản gốc / Bản dịch ở phía trên.' : 'Bấm vào một từ để xem nghĩa. Kéo chọn một đoạn rồi bấm "Dịch" để dịch cả đoạn.'}</span>
    <button class="icon-btn" title="Đã hiểu">${icon('close')}</button>`;
  RD.el.appendChild(h);
  const done = () => { h.remove(); rdStore(RD_HINT_KEY, '1'); };
  $('button', h).addEventListener('click', done);
  setTimeout(() => { if (h.isConnected) done(); }, 9000);
}
