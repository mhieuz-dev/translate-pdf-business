// Lõi cho BẢN WEB (chạy trong trình duyệt, không có Rust).
// Chuyển thể 1:1 từ crate core/ (lang.rs, sources.rs, lookup.rs, glossary.rs);
// khi sửa logic bên Rust thì sửa cả ở đây. web-tests/ kiểm tra file này bằng
// cùng bộ dữ liệu thật trong core/tests/fixtures.
//
// Trình duyệt của từng người gọi thẳng các nguồn (đều cho phép CORS), nên không cần
// máy chủ và mỗi người có hạn mức riêng của mình.
(function (root) {
  'use strict';

  // ======================= lang =======================
  const VI_MARKS = /[̛̣̀́̃̉̂̆]/;
  function detect(text) {
    return VI_MARKS.test(text.normalize('NFD')) || /[đĐ]/.test(text) ? 'vi-en' : 'en-vi';
  }
  function fold(text) {
    return text.toLowerCase().normalize('NFD').replace(/\p{M}/gu, '').replace(/đ/g, 'd').split(/\s+/).filter(Boolean).join(' ');
  }
  function clean(text) {
    return String(text ?? '').normalize('NFC').split(/\s+/).filter(Boolean).join(' ');
  }
  const wordCount = (t) => t.split(/\s+/).filter(Boolean).length;
  const src = (dir) => dir.slice(0, 2);
  const tgt = (dir) => dir.slice(3, 5);

  // ======================= sources =======================
  function wikiUrl(lang, title) {
    return `https://${lang}.wikipedia.org/wiki/${title.replace(/ /g, '_')}`;
  }
  function parseWiki(v, sourceLang) {
    const page = v && v.query && Array.isArray(v.query.pages) ? v.query.pages[0] : null;
    if (!page || page.missing !== undefined || page.invalid !== undefined) return null;
    if (page.pageprops && page.pageprops.disambiguation !== undefined) return null;
    if (typeof page.title !== 'string') return null;
    const link = Array.isArray(page.langlinks) && page.langlinks[0] ? page.langlinks[0].title : null;
    return { title: page.title, url: wikiUrl(sourceLang, page.title), extract: (page.extract || '').trim(), linked_title: link || null };
  }
  function stripWikiQualifier(title) {
    const i = title.indexOf(' (');
    return i >= 0 && title.endsWith(')') ? title.slice(0, i) : title;
  }

  function parseGoogleT(v) {
    if (!Array.isArray(v) || !v.length) return null;
    const first = v[0];
    const s = typeof first === 'string' ? first : Array.isArray(first) && typeof first[0] === 'string' ? first[0] : null;
    if (s === null) return null;
    const c = clean(s);
    return c || null;
  }

  const MYMEMORY_MIN_SCORE = 0.8;
  const TRIM_RE = /^[-–—•·*"'“”‘’.。,;: ]+|[-–—•·*"'“”‘’.。,;: ]+$/g;
  function lowercaseKeepAcronyms(text) {
    return text.split(' ').map((w) => {
      const core = [...w].filter((c) => /[\p{L}\p{N}]/u.test(c)).join('');
      const n = [...core].length;
      const acronym = n >= 2 && n <= 6 && /^[A-Z0-9]+$/.test(core) && /[A-Z]/.test(core);
      return acronym ? w : w.toLowerCase();
    }).join(' ');
  }
  function parseMyMemory(v, query) {
    const qFold = fold(query);
    const qWords = Math.max(1, wordCount(query));
    const lowerQuery = query === query.toLowerCase();
    const out = [];
    for (const m of (v && Array.isArray(v.matches) ? v.matches : [])) {
      if (typeof m.translation !== 'string') continue;
      const score = typeof m.match === 'number' ? m.match : parseFloat(m.match) || 0;
      if (score < MYMEMORY_MIN_SCORE) continue;
      let text = clean(m.translation.replace(TRIM_RE, ''));
      if (!text || fold(text) === qFold) continue;
      if (wordCount(text) > qWords * 4 + 2) continue;
      if (lowerQuery) text = lowercaseKeepAcronyms(text);
      if (out.some((o) => fold(o.text) === fold(text))) continue;
      out.push({ text, score });
    }
    return out.sort((a, b) => b.score - a.score);
  }

  const MAX_DEFINITIONS = 4;
  function parseDict(v) {
    const info = { phonetic: null, definitions: [] };
    if (!Array.isArray(v)) return info;
    for (const e of v) {
      if (!info.phonetic) {
        info.phonetic = (e.phonetic && e.phonetic.length ? e.phonetic : null)
          || ((e.phonetics || []).map((p) => p.text).find((t) => t && t.length) || null);
      }
      for (const m of e.meanings || []) {
        for (const d of (m.definitions || []).slice(0, 2)) {
          if (info.definitions.length >= MAX_DEFINITIONS) return info;
          if (typeof d.definition !== 'string') continue;
          info.definitions.push({ pos: m.partOfSpeech || '', text: d.definition, example: d.example || null });
        }
      }
    }
    return info;
  }

  // ======================= gọi mạng =======================
  const TIMEOUT_MS = 8000;
  async function getJson(url, { allow404 = false } = {}) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    try {
      const r = await fetch(url, { signal: ctl.signal });
      if (allow404 && r.status === 404) return null;
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } finally {
      clearTimeout(t);
    }
  }
  const qs = (o) => Object.entries(o).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');

  async function fetchWiki(text, dir) {
    const lang = src(dir);
    const v = await getJson(`https://${lang}.wikipedia.org/w/api.php?` + qs({
      action: 'query', format: 'json', formatversion: '2', origin: '*', redirects: '1',
      prop: 'langlinks|extracts|pageprops', ppprop: 'disambiguation', exintro: '1', explaintext: '1',
      exsentences: '2', lllang: tgt(dir), titles: text,
    }));
    return parseWiki(v, lang);
  }
  async function fetchGoogle(text, dir) {
    return parseGoogleT(await getJson('https://translate.googleapis.com/translate_a/t?' + qs({ client: 'dict-chrome-ex', sl: src(dir), tl: tgt(dir), q: text })));
  }
  async function fetchMyMemory(text, dir) {
    const v = await getJson('https://api.mymemory.translated.net/get?' + qs({ q: text, langpair: `${src(dir)}|${tgt(dir)}` }));
    if (v && v.quotaFinished === true) throw new Error('MyMemory hết lượt hôm nay');
    return parseMyMemory(v, text);
  }
  async function fetchDict(word) {
    const v = await getJson(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(word)}`, { allow404: true });
    return v ? parseDict(v) : { phonetic: null, definitions: [] };
  }

  // ======================= lookup =======================
  const SENTENCE_WORDS = 6;
  const MAX_CHARS = 2000;
  function prepareQuery(raw) {
    const q = clean(raw);
    if (!q) throw new Error('Chưa có chữ để tra');
    if ([...q].length > MAX_CHARS) throw new Error(`Đoạn quá dài (${[...q].length} ký tự). Hãy chọn ít hơn ${MAX_CHARS} ký tự.`);
    return q;
  }
  const isSentence = (q) => wordCount(q) > SENTENCE_WORDS;
  const settle = (p) => p.then((v) => ({ ok: true, v }), (e) => ({ ok: false, e: String(e) }));

  async function fetchOnline(q, dir) {
    if (isSentence(q)) return { google: await settle(fetchGoogle(q, dir)) };
    const wantDict = dir === 'en-vi' && wordCount(q) <= 3;
    const [wiki, google, memory, dict] = await Promise.all([
      settle(fetchWiki(q, dir)), settle(fetchGoogle(q, dir)), settle(fetchMyMemory(q, dir)),
      wantDict ? settle(fetchDict(q)) : Promise.resolve(null),
    ]);
    return { wiki, google, memory, dict };
  }

  const LABEL = { wikipedia: 'Wikipedia', google: 'Google Dịch', mymemory: 'Bộ nhớ dịch' };
  function lowerFirst(s) {
    const first = s.split(/\s+/)[0] || '';
    const letters = [...first].filter((c) => /\p{L}/u.test(c));
    if (letters.length >= 2 && letters.every((c) => c === c.toUpperCase() && c !== c.toLowerCase())) return s;
    return s ? s[0].toLowerCase() + s.slice(1) : s;
  }

  // online: { wiki, google, memory, dict } mỗi phần là {ok, v} | {ok:false} | undefined
  function merge(q, dir, glossary, inText, online) {
    const errors = [];
    const cands = [];
    const push = (text, source, detail) => {
      text = clean(text);
      if (!text || (fold(text) === fold(q) && dir === 'en-vi' && source !== 'wikipedia')) return;
      const hit = cands.find((c) => fold(c.text) === fold(text));
      if (hit) {
        const l = LABEL[source];
        if (hit.source !== source && !hit.also.includes(l)) hit.also.push(l);
        return;
      }
      cands.push({ text, source, detail, also: [] });
    };

    let wiki = null;
    if (online.wiki) {
      if (online.wiki.ok && online.wiki.v) {
        const w = online.wiki.v;
        if (w.linked_title) {
          let title = stripWikiQualifier(w.linked_title);
          if (q === q.toLowerCase()) title = lowerFirst(title);
          push(title, 'wikipedia', `Tên bài Wikipedia ${dir === 'en-vi' ? 'tiếng Việt' : 'tiếng Anh'}`);
        }
        wiki = w;
      } else if (!online.wiki.ok) errors.push('wikipedia');
    }
    if (online.google) {
      if (online.google.ok && online.google.v) push(online.google.v, 'google', 'Dịch máy');
      else if (!online.google.ok) errors.push('google');
    }
    if (online.memory) {
      if (online.memory.ok) online.memory.v.forEach((m) => push(m.text, 'mymemory', `Bản dịch người dùng, độ khớp ${Math.round(m.score * 100)}%`));
      else errors.push('mymemory');
    }
    let dict = { phonetic: null, definitions: [] };
    if (online.dict) {
      if (online.dict.ok) dict = online.dict.v;
      else errors.push('dictionary');
    }
    cands.forEach((c) => { if (c.also.length) c.detail = `${c.detail} · ${c.also.join(', ')} cũng gợi ý`; });
    // Sắp xếp ổn định: nhiều nguồn đồng ý lên trước.
    const sorted = cands.map((c, i) => [c, i]).sort((a, b) => b[0].also.length - a[0].also.length || a[1] - b[1]).map((x) => x[0]);
    return {
      query: q, direction: dir, mode: isSentence(q) ? 'sentence' : 'term',
      glossary, in_text: inText, candidates: sorted,
      phonetic: dict.phonetic, definitions: dict.definitions, wiki, errors,
    };
  }

  // ======================= bảng thuật ngữ (localStorage) =======================
  function createGlossary(storage) {
    const KEY = 'thongngu.terms.v1';
    const HKEY = 'thongngu.history.v1';
    const read = (k, d) => { try { return JSON.parse(storage.getItem(k)) ?? d; } catch { return d; } };
    const write = (k, v) => storage.setItem(k, JSON.stringify(v));
    const all = () => read(KEY, []);
    const escapeTokens = (s) => s.split(/[^\p{L}\p{N}]+/u).filter(Boolean).join(' ');

    return {
      list(query = '', field = '') {
        const q = fold(query), f = fold(field);
        return all()
          .filter((t) => (!q || fold(t.en).includes(q) || fold(t.vi).includes(q) || (t.note || '').toLowerCase().includes(q))
            && (!f || fold(t.field || '') === f))
          .map((t) => [t, q && (fold(t.en).startsWith(q) || fold(t.vi).startsWith(q)) ? 0 : 1])
          .sort((a, b) => a[1] - b[1] || fold(a[0].en).localeCompare(fold(b[0].en)))
          .map((x) => x[0]);
      },
      count() { return all().length; },
      fields() {
        const n = new Map();
        all().forEach((t) => { if (t.field) { const k = fold(t.field); const e = n.get(k) || { name: t.field, c: 0 }; e.c++; n.set(k, e); } });
        return [...n.values()].sort((a, b) => b.c - a.c || a.name.localeCompare(b.name)).map((e) => e.name);
      },
      save(t) {
        const en = clean(t.en), vi = clean(t.vi), field = clean(t.field || ''), note = String(t.note || '').trim();
        if (!en || !vi) throw new Error('Cần có cả tiếng Anh và tiếng Việt');
        const terms = all();
        const clash = terms.find((x) => fold(x.en) === fold(en) && fold(x.field || '') === fold(field));
        const now = Date.now();
        let id = t.id;
        if (id && clash && clash.id !== id) throw new Error(`"${en}" đã có trong bảng${field ? ` (chuyên ngành ${field})` : ''}`);
        if (!id && clash) id = clash.id;
        let saved;
        if (id) {
          const i = terms.findIndex((x) => x.id === id);
          if (i < 0) throw new Error('Thuật ngữ không còn tồn tại');
          saved = { ...terms[i], en, vi, field, note, updated_at: now };
          terms[i] = saved;
        } else {
          saved = { id: terms.reduce((m, x) => Math.max(m, x.id), 0) + 1, en, vi, field, note, created_at: now, updated_at: now };
          terms.push(saved);
        }
        write(KEY, terms);
        return saved;
      },
      delete(id) {
        const terms = all();
        const next = terms.filter((x) => x.id !== id);
        write(KEY, next);
        return next.length !== terms.length;
      },
      findExact(text, dir) {
        const f = fold(text);
        return all().filter((t) => fold(dir === 'en-vi' ? t.en : t.vi) === f).sort((a, b) => b.updated_at - a.updated_at);
      },
      findInText(text, dir) {
        const hay = ` ${escapeTokens(fold(text))} `;
        return all().filter((t) => {
          const needle = escapeTokens(fold(dir === 'en-vi' ? t.en : t.vi));
          return needle && hay.includes(` ${needle} `);
        }).sort((a, b) => b.en.length - a.en.length);
      },
      pushHistory(q) {
        q = clean(q);
        if (!q) return;
        const h = read(HKEY, []).filter((x) => x !== q);
        h.unshift(q);
        write(HKEY, h.slice(0, 50));
      },
      recent(limit) { return read(HKEY, []).slice(0, limit); },
      exportCsv() {
        const esc = (s) => (/[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
        const rows = [['en', 'vi', 'field', 'note'], ...this.list('', '').map((t) => [t.en, t.vi, t.field || '', t.note || ''])];
        return '﻿' + rows.map((r) => r.map(esc).join(',')).join('\n') + '\n';
      },
      importCsv(text) {
        const rows = parseCsv(text.replace(/^﻿/, ''));
        if (!rows.length) throw new Error('File CSV trống');
        const h = rows[0].map((x) => x.trim().toLowerCase());
        const idx = (names) => h.findIndex((x) => names.includes(x));
        const iEn = idx(['en', 'english', 'source', 'tiếng anh']), iVi = idx(['vi', 'vietnamese', 'target', 'tiếng việt']);
        if (iEn < 0 || iVi < 0) throw new Error('File CSV cần có cột en và vi (hoặc source và target)');
        const iField = idx(['field', 'chuyên ngành', 'domain']), iNote = idx(['note', 'ghi chú', 'notes']);
        let n = 0;
        for (const r of rows.slice(1)) {
          const g = (i) => (i >= 0 && r[i] !== undefined ? r[i] : '');
          if (!g(iEn).trim() || !g(iVi).trim()) continue;
          this.save({ en: g(iEn), vi: g(iVi), field: g(iField), note: g(iNote) });
          n++;
        }
        return n;
      },
    };
  }

  // CSV theo RFC 4180 (ngoặc kép, dấu phẩy và xuống dòng trong ô).
  function parseCsv(text) {
    const rows = [];
    let row = [], cell = '', q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') q = false; else cell += c;
      } else if (c === '"') q = true;
      else if (c === ',') { row.push(cell); cell = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        row.push(cell); cell = '';
        if (row.some((x) => x !== '')) rows.push(row);
        row = [];
      } else cell += c;
    }
    row.push(cell);
    if (row.some((x) => x !== '')) rows.push(row);
    return rows;
  }

  // ======================= các lệnh giống bản app =======================
  function createBackend(storage, fetchOnlineImpl = fetchOnline) {
    const g = createGlossary(storage);
    return {
      glossary: g,
      async lookup({ query, direction }) {
        const q = prepareQuery(query);
        const dir = direction || detect(q);
        if (!isSentence(q)) g.pushHistory(q);
        const inText = isSentence(q) ? g.findInText(q, dir) : [];
        const online = await fetchOnlineImpl(q, dir);
        return merge(q, dir, g.findExact(q, dir), inText, online);
      },
      recent_list: async () => g.recent(10),
      glossary_list: async ({ query, field } = {}) => g.list(query || '', field || ''),
      glossary_fields: async () => g.fields(),
      glossary_save: async ({ entry }) => g.save(entry),
      glossary_delete: async ({ id }) => g.delete(id),
    };
  }

  const api = {
    detect, fold, clean, parseWiki, stripWikiQualifier, parseGoogleT, parseMyMemory, parseDict,
    lowerFirst, merge, prepareQuery, isSentence, createGlossary, createBackend, parseCsv,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ThongNguWeb = api;
})(typeof self !== 'undefined' ? self : this);
