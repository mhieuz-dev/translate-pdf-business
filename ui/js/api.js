// Cầu nối giao diện với phần xử lý.
//  - Bình thường: lõi JS trong web-backend.js chạy ngay trong trình duyệt, bảng thuật ngữ lưu localStorage.
//  - ?mock=1: dữ liệu giả, chỉ để xem bố cục, không gọi mạng.
// Dịch PDF gọi máy chủ riêng (PDF_API), xem server/.
const USE_MOCK = new URLSearchParams(location.search).has('mock');
const GITHUB_URL = 'https://github.com/mhieuz-dev/thong-ngu';
const APP_VERSION = '0.4.1';
// Địa chỉ máy chủ dịch PDF. Thử máy chủ khác: thêm ?pdfapi=http://127.0.0.1:8787 vào link.
const PDF_API = (new URLSearchParams(location.search).get('pdfapi') || window.THONGNGU_PDF_API || '').replace(/\/$/, '');

let WEB = null;
function webBackend() {
  if (WEB) return WEB;
  let storage;
  try { storage = window.localStorage; storage.getItem('x'); } catch {
    const m = new Map(); storage = { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
  }
  const b = ThongNguWeb.createBackend(storage);
  const g = b.glossary;
  WEB = {
    ...b,
    async glossary_export() {
      const blob = new Blob([g.exportCsv()], { type: 'text/csv;charset=utf-8' });
      const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'thuat-ngu.csv' });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      return `thuat-ngu.csv (${g.count()} thuật ngữ)`;
    },
    glossary_import() {
      return new Promise((resolve, reject) => {
        const input = Object.assign(document.createElement('input'), { type: 'file', accept: '.csv,text/csv' });
        input.addEventListener('change', async () => {
          const f = input.files && input.files[0];
          if (!f) return resolve(null);
          try { resolve(g.importCsv(await f.text())); } catch (e) { reject(e.message || String(e)); }
        });
        input.click();
      });
    },
    async app_info() { return { version: APP_VERSION }; },
    async open_url({ url }) { window.open(url, '_blank', 'noopener'); },
  };
  return WEB;
}

async function call(cmd, args = {}) {
  if (USE_MOCK) return MOCK[cmd] ? MOCK[cmd](args) : null;
  const w = webBackend();
  if (!w[cmd]) return null;
  try {
    return await w[cmd](args);
  } catch (e) {
    throw (e && e.message) || String(e);
  }
}

// ---------- Dữ liệu giả chỉ dùng khi xem giao diện ngoài app ----------
const MOCK_TERMS = [
  { id: 1, en: 'overfitting', vi: 'quá khớp', field: 'Học máy', note: 'Wikipedia tiếng Việt dùng "sự quá khớp"', created_at: 0, updated_at: 0 },
  { id: 2, en: 'robustness', vi: 'tính bền vững', field: 'Học máy', note: 'We evaluate the robustness of the model under distribution shift.', created_at: 0, updated_at: 0 },
  { id: 3, en: 'endogeneity', vi: 'tính nội sinh', field: 'Kinh tế lượng', note: '', created_at: 0, updated_at: 0 },
];
const MOCK = {
  lookup: async ({ query }) => {
    await new Promise((r) => setTimeout(r, 500));
    return {
      query, direction: 'en-vi',
      glossary: MOCK_TERMS.filter((t) => t.en === query.toLowerCase()),
      candidates: [
        { text: 'sự quá khớp', source: 'wikipedia', detail: 'Tên bài Wikipedia tiếng Việt' },
        { text: 'trang bị quá mức', source: 'google', detail: 'Google Dịch' },
        { text: 'quá khớp', source: 'mymemory', detail: 'Bộ nhớ dịch (độ khớp 85%)' },
      ],
      phonetic: '/ˌəʊvəˈfɪtɪŋ/',
      definitions: [{ pos: 'noun', text: 'The production of an analysis that corresponds too closely to a particular set of data.', example: null }],
      wiki: { title: 'Overfitting', url: 'https://en.wikipedia.org/wiki/Overfitting', extract: 'In mathematical modeling, overfitting is the production of an analysis that corresponds too closely or exactly to a particular set of data.' },
      errors: [],
    };
  },
  glossary_list: async () => MOCK_TERMS,
  glossary_fields: async () => ['Học máy', 'Kinh tế lượng'],
  glossary_save: async ({ entry }) => ({ ...entry, id: entry.id || Date.now() }),
  glossary_delete: async () => true,
  recent_list: async () => ['overfitting', 'robustness', 'học tăng cường', 'endogeneity'],
  app_info: async () => ({ version: APP_VERSION }),
};
