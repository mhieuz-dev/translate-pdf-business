// Trang quản trị Thông Ngữ: lấy số liệu từ "stats" mỗi 30 giây và vẽ lại.
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const WARN_MB = 800;
  const CIRC = 2 * Math.PI * 50;
  const nf = new Intl.NumberFormat('vi-VN');
  const gb = (mb) => (mb / 1024).toLocaleString('vi-VN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const ddmm = (iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
  const NS = 'http://www.w3.org/2000/svg';
  let shown = { runs: 0, people: 0, pages: 0 };

  function dur(s) {
    if (s == null) return '-';
    s = Math.round(s);
    if (s < 60) return `${s} giây`;
    const m = Math.floor(s / 60), r = s % 60;
    return r ? `${m} phút ${r} giây` : `${m} phút`;
  }

  function countUp(el, from, to) {
    if (from === to || matchMedia('(prefers-reduced-motion: reduce)').matches) { el.textContent = nf.format(to); return; }
    const t0 = performance.now(), T = 700;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / T), e = 1 - Math.pow(1 - k, 3);
      el.textContent = nf.format(Math.round(from + (to - from) * e));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function svg(tag, attrs, text) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (text != null) el.textContent = text;
    return el;
  }

  function renderRam(ram, minToday, queue) {
    const card = $('ram-card');
    if (ram) {
      const used = ram.total_mb - ram.avail_mb;
      $('ram-free').textContent = gb(ram.avail_mb);
      $('ram-total').textContent = `${gb(ram.total_mb)} GB`;
      $('ram-used').textContent = `${gb(used)} GB`;
      $('gauge-fill').style.strokeDashoffset = String(CIRC * (1 - Math.min(1, used / ram.total_mb)));
      const warn = ram.avail_mb < WARN_MB;
      card.classList.toggle('warn', warn);
      $('ram-note').textContent = warn
        ? 'RAM trống đang thấp, người dùng có thể phải chờ lâu hơn.'
        : 'RAM còn dư, máy chủ đang chạy thoải mái.';
    } else {
      $('ram-note').textContent = 'Chưa đọc được RAM, sẽ có sau ít giây.';
    }
    $('ram-min').textContent = minToday != null ? `${gb(minToday)} GB` : '-';
    $('ram-min').classList.toggle('low', minToday != null && minToday < WARN_MB);

    const busy = queue.running + queue.queued > 0;
    $('queue-pill').classList.toggle('busy', busy);
    $('queue-text').textContent = busy ? `Đang dịch ${queue.running} · Chờ ${queue.queued}` : 'Đang rảnh';
  }

  function renderDelta(key, today, yesterday) {
    const el = $(`d-${key}`), diff = today - yesterday;
    el.className = `kpi-delta${diff > 0 ? ' up' : diff < 0 ? ' down' : ''}`;
    el.textContent = diff === 0 ? `Bằng hôm qua (${nf.format(yesterday)})`
      : `${diff > 0 ? '+' : ''}${nf.format(diff)} so với hôm qua`;
  }

  function renderKpis(t, y) {
    if (y) for (const k of ['runs', 'people', 'pages']) renderDelta(k, t[k], y[k]);
    countUp($('k-runs'), shown.runs, t.runs);
    countUp($('k-people'), shown.people, t.people);
    countUp($('k-pages'), shown.pages, t.pages);
    shown = { runs: t.runs, people: t.people, pages: t.pages };
    const rs = $('k-runs-sub');
    rs.textContent = t.errors ? `${t.errors} lượt bị lỗi` : 'Chưa có lượt lỗi';
    rs.classList.toggle('alert', t.errors > 0);
    $('k-pages-sub').textContent = t.max_wait_s > 0 ? `Chờ lâu nhất ${dur(t.max_wait_s)}` : 'Chưa ai phải chờ';
  }

  function renderChart(days) {
    const data = [...days].reverse(); // cũ trước, hôm nay ở cuối bên phải
    const box = $('chart');
    const W = box.clientWidth || 600, H = box.clientHeight || 220;
    const top = 22, bottom = 26, n = data.length;
    const max = Math.max(4, ...data.map((d) => d.runs));
    const slot = W / n, bw = Math.min(34, slot * 0.58);
    const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' });
    for (let i = 1; i <= 3; i++) {
      const y = top + (H - top - bottom) * (1 - i / 3);
      s.appendChild(svg('line', { class: 'grid-line', x1: 0, x2: W, y1: y, y2: y }));
    }
    const narrow = W < 480;
    data.forEach((d, i) => {
      const today = i === n - 1;
      const h = Math.max(d.runs ? 4 : 2, (H - top - bottom) * (d.runs / max));
      const x = slot * i + (slot - bw) / 2, y = H - bottom - h;
      const g = svg('g', { class: 'bar-group' });
      const title = svg('title', {}, `${ddmm(d.date)}: ${d.runs} lượt, ${d.people} người, ${d.pages} trang`);
      g.appendChild(title);
      g.appendChild(svg('rect', { class: 'bar-hit', x: slot * i, y: 0, width: slot, height: H }));
      g.appendChild(svg('rect', { class: `bar${today ? ' today' : ''}`, x, y, width: bw, height: h, rx: Math.min(7, bw / 3) }));
      g.appendChild(svg('text', { class: `bar-val${d.runs ? '' : ' zero'}`, x: x + bw / 2, y: y - 6 }, nf.format(d.runs)));
      if (!narrow || i % 2 === (n - 1) % 2) {
        g.appendChild(svg('text', { class: `axis${today ? ' today' : ''}`, x: x + bw / 2, y: H - 6 }, today ? 'Hôm nay' : ddmm(d.date)));
      }
      s.appendChild(g);
    });
    box.replaceChildren(s);
    const total = days.reduce((a, d) => a + d.runs, 0);
    $('chart-total').textContent = `Tổng ${nf.format(total)} lượt`;
  }

  function renderTable(days) {
    const rows = days.map((d, i) => {
      const tr = document.createElement('tr');
      if (i === 0) tr.className = 'today';
      else if (!d.runs && !d.errors) tr.className = 'empty';
      const cells = [
        ddmm(d.date),
        nf.format(d.runs), nf.format(d.people), nf.format(d.pages), dur(d.avg_s),
        d.ram_min_mb != null ? `${gb(d.ram_min_mb)} GB` : '-',
      ];
      cells.forEach((c, j) => {
        const td = document.createElement('td');
        td.textContent = c;
        if (j === 0 && i === 0) {
          const tag = document.createElement('span');
          tag.className = 'tag';
          tag.textContent = 'Hôm nay';
          td.appendChild(tag);
        }
        if (j === 5 && d.ram_min_mb != null && d.ram_min_mb < WARN_MB) td.className = 'low';
        tr.appendChild(td);
      });
      return tr;
    });
    $('rows').replaceChildren(...rows);
  }

  let last = null;
  async function load() {
    try {
      const r = await fetch('stats', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      last = await r.json();
      renderRam(last.ram, last.days[0].ram_min_mb, last.queue);
      renderKpis(last.today, last.days[1]);
      renderChart(last.days);
      renderTable(last.days);
      $('banner').hidden = true;
      $('live').classList.remove('off');
      $('updated').textContent = `Cập nhật lúc ${new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    } catch {
      $('banner').hidden = false;
      $('live').classList.add('off');
    }
  }

  let rt;
  addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => last && renderChart(last.days), 150); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  load();
  setInterval(load, 30000);
})();
