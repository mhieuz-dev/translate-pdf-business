// Test bản web bằng CÙNG dữ liệu thật và cùng tình huống với test Rust trong core/.
// Chạy: node --test web-tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const W = require('../ui/js/web-backend.js');

const fx = (name) => JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'core', 'tests', 'fixtures', name), 'utf8'));

function memStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}
const wiki = (link) => ({ title: 'Overfitting', url: 'u', extract: 'e', linked_title: link });
const ok = (v) => ({ ok: true, v });

// ---------- lang ----------
test('nhận chiều dịch', () => {
  assert.equal(W.detect('overfitting'), 'en-vi');
  assert.equal(W.detect('học sâu'), 'vi-en');
  assert.equal(W.detect('đa'), 'vi-en');
  assert.equal(W.detect('hoc sau'), 'en-vi');
  assert.equal(W.detect('học sâu'.normalize('NFD')), 'vi-en');
});
test('bỏ dấu để so khớp', () => {
  assert.equal(W.fold('Học Sâu'), 'hoc sau');
  assert.equal(W.fold('ĐỘ  bền   vững'), 'do ben vung');
  assert.equal(W.clean('  học sâu '.normalize('NFD')), 'học sâu');
});

// ---------- nguồn (dữ liệu thật) ----------
test('wikipedia', () => {
  const w = W.parseWiki(fx('wiki_en_overfitting.json'), 'en');
  assert.equal(w.linked_title, 'Sự quá khớp');
  assert.equal(w.url, 'https://en.wikipedia.org/wiki/Overfitting');
  assert.equal(W.parseWiki(fx('wiki_en_gradient_descent.json'), 'en').url, 'https://en.wikipedia.org/wiki/Gradient_descent');
  assert.equal(W.parseWiki(fx('wiki_en_robustness_nolink.json'), 'en').linked_title, null);
  assert.equal(W.parseWiki(fx('wiki_en_missing.json'), 'en'), null);
  assert.equal(W.parseWiki(fx('wiki_en_disambig.json'), 'en'), null);
  assert.equal(W.parseWiki(fx('wiki_vi_hoc_sau.json'), 'vi').linked_title, 'Deep learning');
  assert.equal(W.parseWiki({ error: 'x' }, 'en'), null);
  assert.equal(W.stripWikiQualifier('Cơ chế chú ý (học máy)'), 'Cơ chế chú ý');
  assert.equal(W.stripWikiQualifier('A (b) c'), 'A (b) c');
});
test('google', () => {
  assert.equal(W.parseGoogleT(fx('google_t_distribution_shift.json')), 'sự thay đổi phân phối');
  assert.equal(W.parseGoogleT([['học sâu', 'en']]), 'học sâu');
  assert.equal(W.parseGoogleT(['']), null);
  assert.equal(W.parseGoogleT({}), null);
});
test('mymemory lọc nhiễu như bản Rust', () => {
  assert.deepEqual(W.parseMyMemory(fx('mymemory_gradient_descent.json'), 'gradient descent'), []);
  const r = W.parseMyMemory(fx('mymemory_robustness.json'), 'robustness');
  assert.equal(r.length, 1);
  assert.equal(r[0].text, 'mạnh mẽ');
  assert.equal(r[0].score, 0.97);
  const t = W.parseMyMemory({ matches: [
    { translation: '- deep learning', match: 0.96 },
    { translation: '“Deep Learning”', match: 0.9 },
    { translation: '• học máy (ML)', match: 0.9 },
  ] }, 'học sâu');
  assert.deepEqual(t.map((x) => x.text), ['deep learning', 'học máy (ML)']);
  const long = W.parseMyMemory({ matches: [
    { translation: 'một câu rất dài không liên quan gì tới thuật ngữ được tra cứu cả', match: 0.9 },
    { translation: 'độ chệch', match: '0.85' },
  ] }, 'bias');
  assert.deepEqual(long.map((x) => x.text), ['độ chệch']);
});
test('từ điển', () => {
  const d = W.parseDict(fx('dict_robust.json'));
  assert.equal(d.phonetic, '/ɹəʊˈbʌst/');
  assert.ok(d.definitions.length > 0 && d.definitions.length <= 4);
  assert.deepEqual(d.definitions[0], { pos: 'adjective', text: 'Evincing strength and health; strong.', example: 'A robust wall was put up.' });
  assert.deepEqual(W.parseDict(fx('dict_notfound.json')), { phonetic: null, definitions: [] });
});

// ---------- gộp kết quả ----------
test('gộp và xếp theo mức đồng thuận', () => {
  const r = W.merge('overfitting', 'en-vi', [], [], {
    wiki: ok(wiki('Sự quá khớp')),
    google: ok('trang bị quá mức'),
    memory: ok([{ text: 'quá khớp', score: 0.9 }, { text: 'Trang bị quá mức', score: 0.85 }]),
  });
  assert.deepEqual(r.candidates.map((c) => c.text), ['trang bị quá mức', 'sự quá khớp', 'quá khớp']);
  assert.deepEqual(r.candidates[0].also, ['Bộ nhớ dịch']);
  assert.match(r.candidates[0].detail, /Bộ nhớ dịch cũng gợi ý/);
  assert.equal(r.mode, 'term');
  assert.deepEqual(r.errors, []);
});
test('viết thường tên bài wikipedia khi tra chữ thường', () => {
  const one = (q, dir, link) => W.merge(q, dir, [], [], { wiki: ok(wiki(link)) }).candidates[0].text;
  assert.equal(one('overfitting', 'en-vi', 'Sự quá khớp'), 'sự quá khớp');
  assert.equal(one('học sâu', 'vi-en', 'Deep learning'), 'deep learning');
  assert.equal(one('Python', 'en-vi', 'Python (ngôn ngữ lập trình)'), 'Python');
  assert.equal(one('bert', 'en-vi', 'BERT (mô hình ngôn ngữ)'), 'BERT');
  assert.equal(one('attention', 'en-vi', 'Cơ chế chú ý (học máy)'), 'cơ chế chú ý');
});
test('bỏ bản google lặp lại chữ gốc, giữ tên wikipedia trùng chữ gốc', () => {
  assert.equal(W.merge('transformer', 'en-vi', [], [], { google: ok('Transformer') }).candidates.length, 0);
  assert.equal(W.merge('transformer', 'en-vi', [], [], { wiki: ok(wiki('Transformer')) }).candidates.length, 1);
});
test('ghi lỗi theo từng nguồn', () => {
  const r = W.merge('deep learning', 'en-vi', [], [], {
    wiki: { ok: false }, google: ok('học sâu'), memory: { ok: false }, dict: { ok: false },
  });
  assert.deepEqual(r.errors, ['wikipedia', 'mymemory', 'dictionary']);
  assert.equal(r.candidates.length, 1);
});
test('chế độ câu', () => {
  const q = 'We evaluate the robustness of the model under distribution shift.';
  assert.equal(W.merge(q, 'en-vi', [], [], { google: ok('x') }).mode, 'sentence');
  assert.throws(() => W.prepareQuery('   '));
  assert.equal(W.prepareQuery('  over\nfitting '), 'over fitting');
});

// ---------- bảng thuật ngữ ----------
test('lưu, cập nhật theo (tiếng Anh, chuyên ngành), không trùng', () => {
  const g = W.createGlossary(memStorage());
  const a = g.save({ en: 'robustness', vi: 'tính mạnh mẽ', field: 'Học máy' });
  const b = g.save({ en: 'Robustness', vi: 'tính bền vững', field: 'học máy', note: 'đổi ý' });
  assert.equal(a.id, b.id);
  assert.equal(g.count(), 1);
  g.save({ en: 'robustness', vi: 'độ vững', field: 'Thống kê' });
  assert.equal(g.count(), 2);
  assert.throws(() => g.save({ en: '', vi: 'x' }));
  const v = g.save({ en: 'variance', vi: 'phương sai', field: 'Thống kê' });
  assert.throws(() => g.save({ ...v, en: 'ROBUSTNESS' }), /đã có/);
});
test('tìm không dấu, lọc chuyên ngành, khớp đầu từ lên trước', () => {
  const g = W.createGlossary(memStorage());
  g.save({ en: 'deep learning', vi: 'học sâu', field: 'Học máy' });
  g.save({ en: 'endogeneity', vi: 'tính nội sinh', field: 'Kinh tế lượng' });
  g.save({ en: 'stochastic gradient', vi: 'gradient ngẫu nhiên' });
  g.save({ en: 'gradient descent', vi: 'suy giảm độ dốc' });
  assert.equal(g.list('hoc sau').length, 1);
  assert.equal(g.list('', 'kinh te luong').length, 1);
  assert.equal(g.list('gradient')[0].en, 'gradient descent');
  assert.deepEqual(g.fields(), ['Học máy', 'Kinh tế lượng']);
  assert.equal(g.findExact('Học sâu', 'vi-en').length, 1);
});
test('dò thuật ngữ đã lưu trong câu (nguyên cụm từ)', () => {
  const g = W.createGlossary(memStorage());
  g.save({ en: 'gradient descent', vi: 'suy giảm độ dốc' });
  g.save({ en: 'gradient', vi: 'gradient' });
  g.save({ en: 'rate', vi: 'tốc độ' });
  const hits = g.findInText('We use stochastic gradient descent, with a learning-rate schedule.', 'en-vi');
  assert.deepEqual(hits.map((t) => t.en), ['gradient descent', 'gradient', 'rate']);
  assert.deepEqual(g.findInText('The gradients accelerate.', 'en-vi'), []);
});
test('lịch sử tra: mới nhất trước, không trùng', () => {
  const g = W.createGlossary(memStorage());
  ['overfitting', 'robustness', 'overfitting', '  '].forEach((q) => g.pushHistory(q));
  assert.deepEqual(g.recent(10), ['overfitting', 'robustness']);
});
test('CSV: xuất rồi nhập lại giữ tiếng Việt, dấu phẩy, ngoặc kép; nhận định dạng BabelDOC', () => {
  const g1 = W.createGlossary(memStorage());
  g1.save({ en: 'endogeneity', vi: 'tính nội sinh', field: 'Kinh tế lượng', note: 'Ghi chú có dấu phẩy, và "ngoặc"\nxuống dòng' });
  g1.save({ en: 'overfitting', vi: 'quá khớp', field: 'Học máy' });
  const csv = g1.exportCsv();
  assert.ok(csv.startsWith('﻿'));
  const g2 = W.createGlossary(memStorage());
  assert.equal(g2.importCsv(csv), 2);
  assert.equal(g2.findExact('endogeneity', 'en-vi')[0].note, 'Ghi chú có dấu phẩy, và "ngoặc"\nxuống dòng');
  const g3 = W.createGlossary(memStorage());
  assert.equal(g3.importCsv('source,target,tgt_lng\nattention,cơ chế chú ý,vi\n,thiếu,vi\nencoder,,vi\n'), 1);
  assert.throws(() => g3.importCsv('a,b\n1,2\n'));
});
test('lookup đầy đủ với nguồn giả: dùng bảng thuật ngữ và lưu lịch sử', async () => {
  const b = W.createBackend(memStorage(), async () => ({ wiki: ok(wiki('Sự quá khớp')), google: ok('trang bị quá mức') }));
  await b.glossary_save({ entry: { en: 'overfitting', vi: 'quá khớp', field: 'Học máy' } });
  const r = await b.lookup({ query: ' overfitting ' });
  assert.equal(r.direction, 'en-vi');
  assert.equal(r.glossary[0].vi, 'quá khớp');
  assert.equal(r.candidates[0].text, 'sự quá khớp');
  assert.deepEqual(await b.recent_list(), ['overfitting']);
});
