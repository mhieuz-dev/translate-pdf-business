// Hàm dùng chung cho cửa sổ chính và cửa sổ tra nhanh.
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function toast(msg, kind = 'ok') {
  const box = $('#toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

const SOURCE_LABEL = {
  glossary: 'Bảng của bạn',
  wikipedia: 'Wikipedia',
  google: 'Google Dịch',
  mymemory: 'Bộ nhớ dịch',
};
const SOURCE_BADGE = { wikipedia: 'badge-teal', glossary: 'badge-solid', google: '', mymemory: 'badge-accent' };

// Có dấu tiếng Việt -> coi là tiếng Việt (giống logic bên Rust, chỉ dùng để hiện nhãn).
const VI_CHARS = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;
function guessDirection(text) {
  return VI_CHARS.test(text) ? 'vi-en' : 'en-vi';
}
const DIR_LABEL = { 'en-vi': 'Anh → Việt', 'vi-en': 'Việt → Anh' };

function renderCandidates(container, cands, onPick) {
  if (!cands.length) {
    container.innerHTML = '<div class="muted small">Không có gợi ý nào. Bạn có thể tự gõ cách dịch rồi lưu.</div>';
    return;
  }
  container.innerHTML = cands.map((c, i) => `
    <div class="cand" data-i="${i}" role="button" tabindex="0">
      <div class="grow">
        <div class="term-text">${esc(c.text)}</div>
        ${c.detail ? `<div class="src">${esc(c.detail)}</div>` : ''}
      </div>
      <span class="badge ${SOURCE_BADGE[c.source] || ''}">${esc(SOURCE_LABEL[c.source] || c.source)}</span>
    </div>`).join('');
  $$('.cand', container).forEach((el) => {
    const pick = () => {
      $$('.cand', container).forEach((x) => x.classList.remove('chosen'));
      el.classList.add('chosen');
      onPick(cands[Number(el.dataset.i)]);
    };
    el.addEventListener('click', pick);
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
  });
}

function renderSkeleton(container, n = 3) {
  container.innerHTML = Array.from({ length: n }, () => '<div class="skeleton"></div>').join('');
}

function errorNote(errors) {
  if (!errors || !errors.length) return '';
  const names = errors.map((e) => SOURCE_LABEL[e] || e).join(', ');
  return `Không lấy được kết quả từ: ${esc(names)} (kiểm tra mạng hoặc thử lại sau).`;
}

// Hộp thoại nhỏ dựng bằng HTML (không dùng confirm() của hệ thống cho đẹp và đồng bộ).
function modal({ title, body, actions }) {
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'modal-back';
    back.innerHTML = `<div class="card modal"><h2>${esc(title)}</h2><div class="modal-body">${body}</div><div class="modal-actions"></div></div>`;
    const bar = $('.modal-actions', back);
    const close = (v) => { back.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    actions.forEach((a) => {
      const b = document.createElement('button');
      b.className = `btn ${a.cls || 'btn-ghost'}`;
      b.textContent = a.label;
      b.addEventListener('click', () => close(a.value === 'form' ? collect() : a.value));
      bar.appendChild(b);
    });
    const collect = () => Object.fromEntries($$('[name]', back).map((el) => [el.name, el.value]));
    const onKey = (e) => {
      if (e.key === 'Escape') close(null);
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') { const primary = actions.find((a) => a.primary); if (primary) close(primary.value === 'form' ? collect() : primary.value); }
    };
    back.addEventListener('mousedown', (e) => { if (e.target === back) close(null); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(back);
    const first = $('input, textarea', back);
    if (first) first.focus();
  });
}
