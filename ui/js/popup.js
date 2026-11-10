// Cửa sổ tra nhanh: mở bằng phím tắt, tra chữ đang bôi đen, lưu 1 chạm.
hydrateIcons();

const pstate = { res: null, chosen: null };

async function plookup(raw) {
  const query = (raw || '').replace(/\s+/g, ' ').trim();
  $('#pq').value = query;
  pstate.res = null;
  pstate.chosen = null;
  $('#psave').disabled = true;
  if (!query) {
    $('#pempty').hidden = false;
    $('#pres').hidden = true;
    $('#pq').focus();
    return;
  }
  const direction = guessDirection(query);
  $('#pdir').textContent = DIR_LABEL[direction];
  $('#pempty').hidden = true;
  $('#pres').hidden = false;
  $('#pterm').textContent = query;
  $('#pmeta').innerHTML = '';
  $('#psaved').innerHTML = '';
  $('#perr').textContent = '';
  $('#pdef-wrap').hidden = true;
  renderSkeleton($('#pcands'));

  let res;
  try {
    res = await call('lookup', { query, direction });
  } catch (err) {
    $('#pcands').innerHTML = `<div class="muted small">Lỗi khi tra: ${esc(err)}</div>`;
    return;
  }
  if ($('#pq').value !== query) return;
  pstate.res = res;
  $('#pmeta').innerHTML = res.phonetic ? `<span class="phonetic">${esc(res.phonetic)}</span>` : '';

  const hit = res.glossary[0];
  if (hit) {
    $('#psaved').innerHTML = `
      <div class="saved-banner">${icon('checkCircle')}
        <div class="grow"><div class="small muted">Bạn đã chọn${hit.field ? ` · ${esc(hit.field)}` : ''}</div>
        <div class="term-text">${esc(res.direction === 'en-vi' ? hit.vi : hit.en)}</div></div>
      </div>`;
    $('#pfield').value = hit.field || $('#pfield').value;
  }

  renderCandidates($('#pcands'), res.candidates, (c) => {
    pstate.chosen = c;
    $('#psave').disabled = false;
  });
  if (!hit && res.candidates.length) {
    pstate.chosen = res.candidates[0];
    $('#pcands .cand').classList.add('chosen');
    $('#psave').disabled = false;
  }
  $('#perr').textContent = errorNote(res.errors);

  const defs = (res.definitions || []).slice(0, 2).map((d) =>
    `<div><div class="def-pos">${esc(d.pos)}</div><p class="def-text">${esc(d.text)}</p></div>`);
  if (!defs.length && res.wiki && res.wiki.extract) {
    defs.push(`<div><div class="def-pos">wikipedia</div><p class="wiki-extract selectable">${esc(res.wiki.extract)}</p></div>`);
  }
  $('#pdef-wrap').hidden = !defs.length;
  $('#pdefs').innerHTML = defs.join('');
}

$('#pop-form').addEventListener('submit', (e) => { e.preventDefault(); plookup($('#pq').value); });

$('#psave').addEventListener('click', async () => {
  const res = pstate.res;
  const c = pstate.chosen;
  if (!res || !c) return;
  const hit = res.glossary[0];
  const entry = res.direction === 'en-vi'
    ? { id: null, en: res.query, vi: c.text, field: $('#pfield').value.trim(), note: hit ? hit.note : '' }
    : { id: null, en: c.text, vi: res.query, field: $('#pfield').value.trim(), note: hit ? hit.note : '' };
  try {
    const saved = await call('glossary_save', { entry });
    toast(`Đã lưu: ${saved.en} → ${saved.vi}`);
    res.glossary = [saved];
    plookup(res.query);
  } catch (err) { toast(String(err), 'err'); }
});

async function hidePopup() { await call('popup_hide'); }
$('#pclose').addEventListener('click', hidePopup);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hidePopup(); });
$('#popen').addEventListener('click', () => call('open_main', { query: $('#pq').value }));

(async () => {
  const fields = (await call('glossary_fields')) || [];
  $('#fields-list').innerHTML = fields.map((f) => `<option value="${esc(f)}">`).join('');
  const initial = new URLSearchParams(location.search).get('q');
  plookup(initial || '');
})();

onEvent('popup-lookup', (q) => plookup(q));
