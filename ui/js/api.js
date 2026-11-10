// Cầu nối tới lõi Rust. Trong app thật dùng window.__TAURI__;
// mở file HTML bằng trình duyệt thường (xem thử giao diện) thì dùng dữ liệu giả.
const TAURI = window.__TAURI__;
const IN_APP = Boolean(TAURI && TAURI.core);

async function call(cmd, args = {}) {
  if (IN_APP) return TAURI.core.invoke(cmd, args);
  return MOCK[cmd] ? MOCK[cmd](args) : null;
}

function onEvent(name, handler) {
  if (IN_APP) return TAURI.event.listen(name, (e) => handler(e.payload));
  return Promise.resolve(() => {});
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
  app_info: async () => ({ version: '0.1.0', data_dir: '~/.local/share/io.github.thongngu', hotkey: 'Ctrl+Alt+E', platform: 'linux', pdf: { found: true, path: 'pdf2zh_next', version: '2.10.2' } }),
  pdf_status: async () => ({ found: true, path: 'pdf2zh_next', version: '2.10.2' }),
};
