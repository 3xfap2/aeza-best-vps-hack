
/* @ds-bundle: {"format":4,"namespace":"Ottok","components":[{"name":"Button"},{"name":"RiskBadge"},{"name":"Marker"},{"name":"HandNote"},{"name":"Card"},{"name":"Radar"},{"name":"ScrollStory"},{"name":"RankingDemo"},{"name":"MoneyDemo"},{"name":"TuningDemo"},{"name":"LiveDemo"},{"name":"ForecastChart"},{"name":"Formula"},{"name":"CompareTable"},{"name":"DataFlow"},{"name":"FAQ"},{"name":"SiteHeader"},{"name":"LandingA"},{"name":"LandingB"},{"name":"Overview"}]} */
/* Радар оттока — библиотека компонентов на чистом JS. Без зависимостей. Каждый компонент: Ottok.<Имя>.mount(host, options) → { destroy() }. */
(function (global) {
  'use strict';
  var doc = global.document;
  var EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';
  var SVGNS = 'http://www.w3.org/2000/svg';
  var mq = global.matchMedia ? global.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  function reduced() { return !!mq.matches; }
  function embedded() {
    try {
      var q = global.location && global.location.search || '';
      if (/ro-mode=steps/.test(q)) return true;
      if (/ro-mode=scroll/.test(q)) return false;
      return global.top !== global.self;
    } catch (e) { return true; }
  }

  /* ---------- утилиты ---------- */
  function h(tag, cls, html) { var n = doc.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  function s(tag, attrs) { var n = doc.createElementNS(SVGNS, tag); if (attrs) for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function $(root, sel) { return root.querySelector(sel); }
  function $$(root, sel) { return Array.prototype.slice.call(root.querySelectorAll(sel)); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function eur(n, noCents) {
    // панель может показывать рубли по курсу ЦБ (window.RADAR_FX); копейки — только у сумм меньше 100 ₽
    var fx = window.RADAR_FX;
    if (fx && fx.currency === 'RUB' && fx.rate) {
      var r = n * fx.rate, rn = r < 0; r = Math.abs(r);
      var rp = (noCents || r >= 100 ? Math.round(r).toString() : r.toFixed(2)).split('.');
      rp[0] = rp[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
      return (rn ? '−' : '') + rp[0] + (rp[1] ? ',' + rp[1] : '') + ' ₽';
    }
    var neg = n < 0; n = Math.abs(n);
    var p = (noCents ? Math.round(n).toString() : n.toFixed(2)).split('.');
    p[0] = p[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return (neg ? '−' : '') + '€ ' + p[0] + (p[1] ? ',' + p[1] : '');
  }
  function dec(n, d) { return n.toFixed(d == null ? 1 : d).replace('.', ','); }
  function plural(n, a, b, c) { var m10 = n % 10, m100 = n % 100; if (m10 === 1 && m100 !== 11) return a; if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return b; return c; }
  function rng(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; var t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function levelOf(score) { return score == null ? 'none' : score >= 60 ? 'high' : score >= 30 ? 'mid' : 'low'; }
  var LEVEL_NAME = { high: 'высокий', mid: 'средний', low: 'низкий', none: 'нет оценки', gone: 'ушёл' };
  function badge(level, text) { var cls = level === 'gone' ? 'none' : level; return '<span class="ro-risk ro-risk--' + cls + '">' + esc(text || LEVEL_NAME[level]) + '</span>'; }

  function visible(node, onIn, onOut, threshold) {
    if (!('IntersectionObserver' in global)) { onIn && onIn(); return function () {}; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { onIn && onIn(); } else { onOut && onOut(); } });
    }, { threshold: threshold == null ? 0.2 : threshold });
    io.observe(node);
    return function () { io.disconnect(); };
  }
  /* Смена числа/текста коротким кросс-фейдом (200 мс) — без «набегающих» цифр. host: inline-grid с data-swap. */
  function swap(host, text) {
    var cur = host.lastElementChild;
    if (cur && cur.textContent === text) return;
    var n = doc.createElement('span'); n.textContent = text;
    if (!cur || reduced()) { host.innerHTML = ''; host.appendChild(n); return; }
    n.style.opacity = '0'; host.appendChild(n);
    cur.setAttribute('aria-hidden', 'true');
    void n.offsetWidth;
    n.style.opacity = '1'; cur.style.opacity = '0';
    setTimeout(function () { if (cur.parentNode) cur.parentNode.removeChild(cur); }, 260);
  }
  function swapBox(text, cls) { return '<span class="' + (cls || '') + '" data-swap><span>' + esc(text) + '</span></span>'; }
  /* FLIP-перестановка строк, 450 мс */
  function flip(container, ordered, dur) {
    var before = new Map();
    ordered.forEach(function (c) { before.set(c, c.getBoundingClientRect().top); });
    ordered.forEach(function (c) { container.appendChild(c); });
    if (reduced() || !Element.prototype.animate) return;
    ordered.forEach(function (c) {
      var dy = before.get(c) - c.getBoundingClientRect().top;
      if (Math.abs(dy) > 0.5) c.animate([{ transform: 'translateY(' + dy + 'px)' }, { transform: 'translateY(0)' }], { duration: dur || 450, easing: EASE });
    });
  }
  function revealAll(root) {
    var nodes = $$(root, '[data-reveal], .ro-mark[data-draw]');
    if (!('IntersectionObserver' in global) || reduced()) { nodes.forEach(function (n) { n.classList.add('is-in'); n.removeAttribute('data-draw'); }); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add('is-in'); e.target.removeAttribute('data-draw'); io.unobserve(e.target);
      });
    }, { threshold: 0.15, rootMargin: '0px 0px -6% 0px' });
    nodes.forEach(function (n) { io.observe(n); });
  }

  /* ---------- иконки (линейные, 1.6 px) ---------- */
  var I = {
    arrow: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 10h11M11 5.5 15.5 10 11 14.5"/></svg>',
    menu: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3.5 6h13M3.5 10h13M3.5 14h9"/></svg>',
    close: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg>',
    plus: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>',
    chev: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5"/></svg>',
    yes: '<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3.5 9.5 3.5 3.5 7.5-8"/></svg>',
    no: '<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m4.5 4.5 9 9M13.5 4.5l-9 9"/></svg>',
    part: '<svg viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3.5 9c1.8-2.4 3.7-2.4 5.5 0s3.7 2.4 5.5 0"/></svg>',
    lock: '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6.5" y="14" width="19" height="13" rx="3"/><path d="M10.5 14V10a5.5 5.5 0 0 1 11 0v4M16 19.5v3"/></svg>',
    shield: '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 4.5 6.5 8v7.5c0 6 4 10 9.5 12 5.5-2 9.5-6 9.5-12V8L16 4.5Z"/><path d="m11.5 16 3.2 3.2 6-6.4"/></svg>',
    gauge: '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 22a11 11 0 1 1 22 0"/><path d="m16 22 5.5-7"/><circle cx="16" cy="22" r="1.6"/><path d="M9 22h-.5M23.5 22H23M16 12.5V12"/></svg>',
    term: '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4.5" y="6.5" width="23" height="19" rx="3"/><path d="m9.5 13 3.5 3-3.5 3M15.5 19.5h6"/></svg>',
    grid: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="11" y="3" width="6" height="6" rx="1.5"/><rect x="3" y="11" width="6" height="6" rx="1.5"/><rect x="11" y="11" width="6" height="6" rx="1.5"/></svg>',
    users: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="7" r="3"/><path d="M2.5 16.5c.8-2.8 3-4.3 5.5-4.3s4.7 1.5 5.5 4.3M13 4.3a3 3 0 0 1 0 5.4M15.2 12.6c1.2.7 2 1.9 2.3 3.9"/></svg>',
    sliders: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 6h8M15 6h2M3 14h2M9 14h8"/><circle cx="13" cy="6" r="2"/><circle cx="7" cy="14" r="2"/></svg>',
    chart: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 16.5h14M5.5 13.5v-4M9.5 13.5v-8M13.5 13.5v-6"/></svg>',
    plug: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 3v4M13 3v4M5 7h10v2.5a5 5 0 0 1-10 0V7ZM10 14.5V17"/></svg>',
    out: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 4H5a1.5 1.5 0 0 0-1.5 1.5v9A1.5 1.5 0 0 0 5 16h3M12 6.5 15.5 10 12 13.5M15.5 10H8"/></svg>',
    phone: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6.2 3.5H4.5A1.5 1.5 0 0 0 3 5c0 6.6 5.4 12 12 12a1.5 1.5 0 0 0 1.5-1.5v-1.7a1 1 0 0 0-.7-1l-2.6-.8a1 1 0 0 0-1 .3l-1 1.1a9.5 9.5 0 0 1-4.6-4.6l1.1-1a1 1 0 0 0 .3-1l-.8-2.6a1 1 0 0 0-1-.7Z"/></svg>',
    team: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="14" height="11" rx="2"/><circle cx="8" cy="9" r="1.8"/><path d="M5.5 13c.5-1.3 1.4-2 2.5-2s2 .7 2.5 2M12.5 8.5h2.5M12.5 11h2"/></svg>',
    book: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 5.5C8.5 4.3 6.5 4 3.5 4v11c3 0 5 .3 6.5 1.5M10 5.5c1.5-1.2 3.5-1.5 6.5-1.5v11c-3 0-5 .3-6.5 1.5M10 5.5v11"/></svg>',
    code: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m7 6-4 4 4 4M13 6l4 4-4 4M11 4.5l-2 11"/></svg>',
    more: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="10" r=".9"/><circle cx="10" cy="10" r=".9"/><circle cx="15" cy="10" r=".9"/></svg>',
    chat: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4.5h12a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H9l-4 3v-3H4A1.5 1.5 0 0 1 2.5 13V6A1.5 1.5 0 0 1 4 4.5Z"/><path d="M6.5 8.5h7M6.5 11h4.5"/></svg>',
    pen: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.5 4.5l3 3L8 15H5v-3l7.5-7.5Z"/><path d="M11 6l3 3"/><path d="M3.5 17.5h13"/></svg>'
  };
  var WORDMARK_SVG = '<svg viewBox="0 0 28 28" fill="none" aria-hidden="true"><circle cx="14" cy="14" r="12.5" stroke="#3D352F" stroke-width="1.4" stroke-dasharray="2.6 2.6"/><circle cx="14" cy="14" r="7.5" stroke="#3D352F" stroke-width="1.4" stroke-dasharray="2.6 2.6"/><path d="M14 14 14 1.5A12.5 12.5 0 0 1 24.8 7.75Z" fill="#F7B200" fill-opacity=".55"/><circle cx="18.5" cy="10" r="2.4" fill="#C01048"/><circle cx="14" cy="14" r="1.4" fill="#3D352F"/></svg>';

  /* =================== ДАННЫЕ =================== */
  /* Настоящие значения из расширенного набора: web/src/design/landing-data.js (tools/build_landing_data.py). */
  var OD = global.OTTOK_DATA;
  var TOTAL = OD.total;
  var _data = null;
  function data() {
    if (_data) return _data;
    var r0 = rng(20261024);
    var list0 = OD.clients.map(function (c) {
      var x = Object.assign({}, c); x.angle = r0() * 360; x.jitter = r0();
      if (x.id === OD.pinned.id) { x.pinned = true; x.angle = 228; x.jitter = 0.1; }
      return x;
    });
    var counts0 = { high: 0, mid: 0, low: 0, none: 0, gone: 0 }, sums0 = { high: 0, mid: 0, low: 0, none: 0, gone: 0 };
    list0.forEach(function (c) { counts0[c.level]++; sums0[c.level] += c.threat; });
    _data = { clients: list0, counts: counts0, sums: sums0, total: TOTAL };
    return _data;
  }

  /* =================== РАДАР =================== */
  var ZONES = { high: [0.05, 0.28], mid: [0.35, 0.55], low: [0.63, 0.94] };
  function radialFor(score, jitter) {
    var lv = levelOf(score), z = ZONES[lv];
    var t = lv === 'high' ? (100 - score) / 40 : lv === 'mid' ? (59 - score) / 29 : (29 - score) / 29;
    t = clamp(t * 0.8 + jitter * 0.2, 0, 1);
    return z[0] + (z[1] - z[0]) * t;
  }
  function polar(r, deg) { var a = deg * Math.PI / 180; return [r * Math.sin(a), -r * Math.cos(a)]; }
  function sector(r, a0, a1) { var p0 = polar(r, a0), p1 = polar(r, a1); return 'M0 0L' + p0[0].toFixed(2) + ' ' + p0[1].toFixed(2) + 'A' + r + ' ' + r + ' 0 0 1 ' + p1[0].toFixed(2) + ' ' + p1[1].toFixed(2) + 'Z'; }

  var Radar = {
    mount: function (host, o) {
      o = o || {};
      var D = o.data || data(); // в панели — клиенты из API
      var mode = o.mode || 'hero';
      var R = 240;
      var all = D.clients.filter(function (c) { return c.score != null; }).map(function (c) { return Object.assign({}, c); });
      var shown;
      if (mode === 'hero') {
        var hi = all.filter(function (c) { return c.level === 'high'; });
        var mi = all.filter(function (c) { return c.level === 'mid'; }).slice(0, o.small ? 9 : 14);
        var lo = all.filter(function (c) { return c.level === 'low'; }).slice(0, o.small ? 9 : 16);
        [OD.event.id].forEach(function (id) { if (!mi.some(function (c) { return c.id === id; })) { var f = all.find(function (c) { return c.id === id; }); if (f) mi.push(f); } });
        [].forEach(function (id) { if (!lo.some(function (c) { return c.id === id; })) { var f = all.find(function (c) { return c.id === id; }); if (f) lo.push(f); } });
        shown = hi.concat(mi, lo);
      } else shown = all;
      var maxT = Math.max.apply(null, all.map(function (c) { return c.threat; }));
      function size(c) { return mode === 'hero' ? 3.2 + 8 * Math.sqrt(c.threat / maxT) : 2.4 + 6 * Math.sqrt(c.threat / maxT); }
      var total = D.total;
      var counts = { high: D.counts.high, mid: D.counts.mid, low: D.counts.low };

      host.classList.add('ro-radar');
      host.innerHTML =
        (o.head === false ? '' : '<div class="ro-radar__head"><span class="ro-label">Радар клиентов · ' + D.clients.length + '</span><span class="ro-radar__live"><span class="ro-live-dot ro-pulse"></span>в реальном времени</span></div>') +
        '<p class="ro-sr" data-alt></p>' +
        '<div class="ro-radar__tip" role="tooltip" aria-hidden="true"></div>' +
        (o.foot === false ? '' : '<div class="ro-radar__foot"><div><div class="ro-radar__total" data-total><span>' + eur(total) + '</span></div><div class="ro-small">под угрозой за 30 дней</div></div><div class="ro-radar__legend" data-legend></div></div>');
      var svg = s('svg', { 'class': 'ro-radar__svg', viewBox: (-R - 14) + ' ' + (-R - 14) + ' ' + (2 * R + 28) + ' ' + (2 * R + 28), role: 'group', 'aria-label': 'Радар клиентов: чем ближе к центру, тем выше риск' });
      var altId = 'ro-alt-' + Math.random().toString(36).slice(2, 8);
      $(host, '[data-alt]').id = altId; svg.setAttribute('aria-describedby', altId);
      host.insertBefore(svg, $(host, '.ro-radar__tip'));
      // кольца и оси
      [0.315, 0.59, 0.96, 1].forEach(function (k, i) { if (i === 3) return; svg.appendChild(s('circle', { 'class': 'ro-radar__ring', r: (R * k).toFixed(1) })); });
      svg.appendChild(s('circle', { 'class': 'ro-radar__ring', r: R * 1.0 + 6, style: 'stroke-dasharray:1 7' }));
      svg.appendChild(s('line', { 'class': 'ro-radar__axis', x1: -R, y1: 0, x2: R, y2: 0 }));
      svg.appendChild(s('line', { 'class': 'ro-radar__axis', x1: 0, y1: -R, x2: 0, y2: R }));
      // луч
      var beam = s('g', { 'class': 'ro-radar__beam', 'aria-hidden': 'true' });
      [[-46, 0.06], [-24, 0.06], [-9, 0.06]].forEach(function (b) { beam.appendChild(s('path', { d: sector(R, b[0], 0), 'fill-opacity': b[1] })); });
      beam.appendChild(s('line', { x1: 0, y1: 0, x2: 0, y2: -R, 'stroke-opacity': 0.9 }));
      svg.appendChild(beam);
      // подписи зон
      [['высокий', 0.315], ['средний', 0.59], ['низкий', 0.96]].forEach(function (z) {
        var y = -R * z[1] + 15, w = z[0].length * 7.6 + 10;
        var g = s('g', { 'aria-hidden': 'true' });
        g.appendChild(s('rect', { 'class': 'ro-radar__zonebg', x: 6, y: y - 11, width: w, height: 15, rx: 3 }));
        var t = s('text', { 'class': 'ro-radar__zone', x: 11, y: y }); t.textContent = z[0]; g.appendChild(t);
        svg.appendChild(g);
      });
      svg.appendChild(s('circle', { r: 2.5, fill: '#3D352F' }));
      // точки
      var layer = s('g', {}); svg.appendChild(layer);
      var evLayer = s('g', { 'class': 'ro-radar__event', 'aria-hidden': 'true' }); svg.appendChild(evLayer);
      var focusable = function (c) { if (mode === 'hero') return true; if (filter === 'all') return c.level === 'high'; return c.level === filter; };
      var filter = 'all';
      function tipText(c) { return c.email + ' · score ' + c.score + ' · ' + eur(c.threat) + ' · ' + c.reason; }
      shown.forEach(function (c) {
        var g = s('g', { 'class': 'ro-radar__dot', 'data-level': c.level, 'data-id': c.id, role: 'img' });
        var pos = s('g', { 'class': 'ro-radar__pos' });
        var sz = size(c);
        pos.appendChild(s('circle', { 'class': 'ro-radar__hit', r: Math.max(sz + 5, 11) }));
        var br = s('g', { 'class': 'ro-radar__breathe' });
        var core = s('circle', { 'class': 'ro-radar__core', r: sz.toFixed(2) });
        br.appendChild(core);
        var halo = s('circle', { 'class': 'ro-radar__halo', r: (sz + 3.2).toFixed(2) }); br.appendChild(halo);
        pos.appendChild(br);
        pos.appendChild(s('circle', { 'class': 'ro-radar__focus', r: (sz + 6).toFixed(2) }));
        g.appendChild(pos);
        layer.appendChild(g);
        c.node = g; c.pos = pos; c.core = core; c.halo = halo;
        place(c, true);
      });
      function place(c, instant) {
        c.level = levelOf(c.score);
        var rr = radialFor(c.score, c.jitter) * R, p = polar(rr, c.angle);
        c.x = p[0]; c.y = p[1];
        if (instant) c.pos.style.transition = 'none';
        c.pos.style.transform = 'translate(' + p[0].toFixed(2) + 'px,' + p[1].toFixed(2) + 'px)';
        if (instant) { void c.pos.getBoundingClientRect(); c.pos.style.transition = ''; }
        c.node.setAttribute('data-level', c.level);
        c.node.setAttribute('aria-label', tipText(c));
        c.node.setAttribute('tabindex', focusable(c) ? '0' : '-1');
      }
      // подсказка
      var tip = $(host, '.ro-radar__tip');
      function showTip(c) {
        tip.innerHTML = '<span class="ro-mono">' + esc(c.email) + '</span><b>score ' + c.score + '</b> · ' + eur(c.threat) + '<br>' + esc(c.reason);
        var hb = host.getBoundingClientRect(), cb = c.core.getBoundingClientRect();
        var x = cb.left - hb.left + cb.width / 2, y = cb.top - hb.top;
        tip.classList.add('is-on');
        var tw = tip.offsetWidth, th = tip.offsetHeight;
        var left = clamp(x - tw / 2, 4, hb.width - tw - 4), top = y - th - 10;
        if (top < 4) top = cb.bottom - hb.top + 10;
        tip.style.left = left + 'px'; tip.style.top = top + 'px';
      }
      function hideTip() { tip.classList.remove('is-on'); }
      function find(t) { var n = t.closest && t.closest('.ro-radar__dot'); if (!n) return null; var id = n.getAttribute('data-id'); return shown.find(function (c) { return c.id === id; }); }
      svg.addEventListener('pointerover', function (e) { var c = find(e.target); if (c && !c.node.classList.contains('is-dim')) showTip(c); });
      svg.addEventListener('pointerout', function (e) { if (find(e.target)) hideTip(); });
      svg.addEventListener('focusin', function (e) { var c = find(e.target); if (c) showTip(c); });
      svg.addEventListener('focusout', hideTip);
      svg.addEventListener('keydown', function (e) { if (e.key === 'Escape') hideTip(); });

      var legend = $(host, '[data-legend]');
      function renderMeta() {
        var alt = counts.high + ' ' + plural(counts.high, 'клиент', 'клиента', 'клиентов') + ' высокого риска, ' + counts.mid + ' среднего, ' + counts.low + ' низкого; под угрозой ' + eur(total) + '.';
        $(host, '[data-alt]').textContent = alt;
        if (legend) legend.innerHTML = badge('high', 'высокий ' + counts.high) + badge('mid', 'средний ' + counts.mid) + (o.small ? '' : badge('low', 'низкий ' + counts.low));
        var tb = $(host, '[data-total]'); if (tb) swap(tb, eur(total));
        if (o.onChange) o.onChange({ total: total, counts: counts });
      }
      renderMeta();

      // рукописная пометка со стрелкой к самой крупной красной точке
      var noteEl = null, noteSvg = null;
      if (o.note && o.noteHost) {
        noteEl = h('div', 'ro-note', esc(o.note));
        noteEl.setAttribute('aria-hidden', 'true');
        o.noteHost.appendChild(noteEl);
        noteSvg = s('svg', { 'class': 'ro-note__arrow', 'aria-hidden': 'true', style: 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible;z-index:4' });
        o.noteHost.appendChild(noteSvg);
      }
      function drawNote() {
        if (!noteEl) return;
        var tEl = o.noteTarget === 'total' ? $(host, '[data-total]') : (shown.find(function (c) { return c.pinned; }) || shown[0]).core;
        var hb = o.noteHost.getBoundingClientRect(), cb = tEl.getBoundingClientRect(), nb = noteEl.getBoundingClientRect();
        if (!hb.width || !cb.width) return;
        var tx = cb.left - hb.left + cb.width / 2, ty = cb.top - hb.top + cb.height / 2;
        if (o.noteTarget === 'total') { tx = cb.right - hb.left + 10; ty = cb.top - hb.top + cb.height * 0.55; cb = { width: 8, height: 8 }; }
        var sx = tx < nb.left - hb.left ? nb.left - hb.left - 8 : nb.right - hb.left - 18, sy = nb.top - hb.top + (tx < nb.left - hb.left ? 12 : -4);
        var dx = tx - sx, dy = ty - sy, len = Math.sqrt(dx * dx + dy * dy) || 1;
        var ex = tx - dx / len * (cb.width / 2 + 9), ey = ty - dy / len * (cb.height / 2 + 9);
        var cx = (sx + ex) / 2 - dy * 0.28, cy = (sy + ey) / 2 + dx * 0.28;
        var ang = Math.atan2(ey - cy, ex - cx), a1 = ang + 2.6, a2 = ang - 2.5;
        noteSvg.innerHTML = '<path class="ro-note__stroke" d="M' + sx.toFixed(1) + ' ' + sy.toFixed(1) + ' Q' + cx.toFixed(1) + ' ' + cy.toFixed(1) + ' ' + ex.toFixed(1) + ' ' + ey.toFixed(1) +
          'M' + (ex + Math.cos(a1) * 11).toFixed(1) + ' ' + (ey + Math.sin(a1) * 11).toFixed(1) + ' L' + ex.toFixed(1) + ' ' + ey.toFixed(1) + ' L' + (ex + Math.cos(a2) * 10).toFixed(1) + ' ' + (ey + Math.sin(a2) * 10).toFixed(1) + '"/>';
      }
      var ro = global.ResizeObserver ? new ResizeObserver(drawNote) : null;
      if (ro && o.noteHost) ro.observe(o.noteHost);
      if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(drawNote);
      setTimeout(drawNote, 60);

      // события: точка переезжает на другое кольцо, подпись рядом, сумма — кросс-фейдом
      var evN = 0;
      var DOWN = ['пополнил счёт', 'оплатил счёт', 'включил автопродление'];
      var UP = ['выключил сервер', 'отключил автопродление', 'не хватает средств'];
      function event() {
        var down = evN % 2 === 0, c, to, label;
        var pickFrom = shown.filter(function (x) { return !x.pinned && (down ? (x.level === 'high' || x.level === 'mid') : x.level === 'low'); });
        if (evN > 0) return;
        down = true;
        c = shown.find(function (x) { return x.id === OD.event.id; });
        if (!c) c = pickFrom[Math.floor(Math.random() * pickFrom.length)];
        if (!c) return;
        var from = c.score;
        if (down) { to = OD.event.to; label = 'пополнил счёт на ' + eur(OD.event.amount); }
        else { to = 60 + Math.floor(Math.random() * 18); label = UP[evN % 3]; }
        evN++;
        var oldLv = c.level, oldT = c.threat;
        c.score = to; c.prob = clamp(to / 100 * 0.95 + 0.02, 0.02, 0.99); c.threat = Math.round(c.revenue * c.prob * 100) / 100;
        total = Math.round((total + c.threat - oldT) * 100) / 100;
        place(c, false);
        var nlv = c.level; if (nlv !== oldLv) { counts[oldLv]--; counts[nlv]++; }
        var sz = size(c); c.core.setAttribute('r', sz.toFixed(2)); c.halo.setAttribute('r', (sz + 3.2).toFixed(2));
        setTimeout(function () { showEvent(c, c.id + ' ' + label + ' · риск ' + from + ' → ' + to); renderMeta(); }, reduced() ? 0 : 700);
      }
      var evTimer = null;
      function showEvent(c, text) {
        var w = text.length * 6.95 + 18, hgt = 24;
        var x = c.x + 14, y = c.y - 30;
        if (x + w > R + 10) x = c.x - w - 14;
        if (x < -R - 10) x = -R - 10;
        if (y < -R) y = c.y + 14;
        evLayer.innerHTML = '';
        evLayer.appendChild(s('rect', { x: x, y: y, width: w, height: hgt, rx: 6 }));
        var t = s('text', { x: x + 9, y: y + 16 }); t.textContent = text; evLayer.appendChild(t);
        evLayer.classList.add('is-on');
        clearTimeout(evTimer); evTimer = setTimeout(function () { evLayer.classList.remove('is-on'); }, 3200);
      }

      // анимационный цикл: луч 6 с на оборот, вспышка точки, событие каждые ~8 с
      var raf = 0, last = 0, ang = 0, sinceEv = 0, running = false;
      var interval = o.interval || (o.small ? 12000 : 8000);
      function ping(c) {
        if (!c.core.animate || c.node.classList.contains('is-dim')) return;
        c.core.animate([
          { transform: 'scale(1)', strokeWidth: '0px' },
          { transform: 'scale(1.4)', strokeWidth: '2.5px', offset: 0.09 },
          { transform: 'scale(1)', strokeWidth: '0px' }
        ], { duration: 1320, easing: EASE });
      }
      function frame(t) {
        if (!running) return;
        var dt = last ? Math.min(t - last, 64) : 16; last = t;
        var prev = ang; ang = (ang + dt / 6000 * 360) % 360;
        beam.setAttribute('transform', 'rotate(' + ang.toFixed(2) + ')');
        for (var i = 0; i < shown.length; i++) {
          var a = shown[i].angle;
          if ((prev <= ang && a > prev && a <= ang) || (prev > ang && (a > prev || a <= ang))) ping(shown[i]);
        }
        if (o.events !== false) { sinceEv += dt; if (sinceEv >= interval) { sinceEv = 0; event(); } }
        raf = requestAnimationFrame(frame);
      }
      function start() { if (running || reduced() || o.animate === false) return; running = true; last = 0; raf = requestAnimationFrame(frame); }
      if (o.animate === false) host.classList.add('ro-radar--still');
      function stop() { running = false; cancelAnimationFrame(raf); }
      beam.setAttribute('transform', 'rotate(38)'); ang = 38;
      var unv = visible(host, start, stop, 0.05);
      function onVis() { if (doc.hidden) stop(); }
      doc.addEventListener('visibilitychange', onVis);
      if (mq.addEventListener) mq.addEventListener('change', function () { if (reduced()) stop(); else start(); });

      return {
        setFilter: function (lv) {
          filter = lv;
          shown.forEach(function (c) {
            var dim = lv !== 'all' && c.level !== lv;
            c.node.classList.toggle('is-dim', dim);
            c.node.setAttribute('tabindex', !dim && focusable(c) ? '0' : '-1');
            if (dim) c.node.setAttribute('aria-hidden', 'true'); else c.node.removeAttribute('aria-hidden');
          });
          hideTip();
        },
        event: event,
        destroy: function () { stop(); unv(); if (ro) ro.disconnect(); doc.removeEventListener('visibilitychange', onVis); host.innerHTML = ''; }
      };
    }
  };

  /* =================== ИСТОРИЯ «5 ДНЕЙ ДО УДАЛЕНИЯ» =================== */
  var ScrollStory = {
    mount: function (host, o) {
      o = o || {};
      var beats = o.beats || COPY.A.beats;
      var mode = o.mode || ((embedded() || reduced() || global.innerWidth < 900) ? 'steps' : 'scroll');
      host.classList.add('ro-story');
      host.setAttribute('data-mode', mode);
      host.innerHTML =
        '<div class="ro-wrap"><div class="ro-story__track"><div class="ro-story__stage"><div class="ro-panel ro-story__panel">' +
        '<div class="ro-story__copy"><h2 class="ro-h2" data-reveal="title">' + esc(o.title || '5 дней до удаления') + '</h2>' +
        '<p class="ro-text" data-reveal="body">' + esc(o.lead || '') + '</p>' +
        '<div class="ro-story__beats" aria-live="polite">' + beats.map(function (b, i) { return '<p class="ro-story__beat' + (i ? '' : ' is-on') + '"' + (i ? ' aria-hidden="true"' : '') + '><span class="ro-mono">' + esc(b[0]) + '</span>' + esc(b[1]) + '</p>'; }).join('') + '</div>' +
        '<div class="ro-story__nav"><div class="ro-story__pips" aria-hidden="true">' + beats.map(function () { return '<span></span>'; }).join('') + '</div>' +
        '<button type="button" class="ro-btn ro-btn--quiet ro-btn--sm" data-prev>Назад</button><button type="button" class="ro-btn ro-btn--sm" data-next>Дальше ' + I.arrow + '</button></div>' +
        '</div>' +
        '<div class="ro-story__desk" aria-hidden="true">' +
        '<div class="ro-card ro-card--sm ro-days"><div class="ro-tune__row"><span class="ro-label">Срок удаления</span><span class="ro-mono" data-dayl>день 0 из 5</span></div><div class="ro-days__scale"><div class="ro-days__fill"></div></div><div class="ro-days__ticks"><span>День 0</span><span>1</span><span>2</span><span>3</span><span>4</span><span>День 5</span></div></div>' +
        '<div class="ro-card ro-card--sm ro-svc"><div class="ro-svc__name">' + esc(OD.story.service) + ' · <span class="num">' + eur(OD.story.price) + '</span> / ' + esc(OD.story.term) + '</div><span class="ro-swap" data-status></span><div class="ro-svc__meta"><span>' + esc(OD.story.term) + '</span><span data-meta>срок оплаты — сегодня</span><span>автопродление: выкл</span></div></div>' +
        '<div class="ro-card ro-card--sm ro-rev"><div class="ro-rev__row"><span class="ro-small ro-strong">Выручка клиента за год</span><span class="ro-mono" data-swap data-revl><span>' + eur(OD.story.yearRevenue, true) + '</span></span></div><div class="ro-rev__bar"><div class="ro-rev__fill"></div></div></div>' +
        '<div class="ro-card ro-card--sm ro-story__save"><div class="ro-tune__row" style="margin-bottom:10px"><span class="ro-label">Список на сегодня · день 0</span>' + badge(levelOf(OD.event.from), LEVEL_NAME[levelOf(OD.event.from)] + ' · ' + OD.event.from) + '</div>' +
        '<div class="ro-row"><span class="ro-mono">' + OD.event.id + '@example.test</span></div>' +
        '<p class="ro-small" style="margin-top:6px"><span class="ro-strong">Не хватает средств:</span> на счёте ' + eur(OD.story.balance) + ', нужно ' + eur(OD.story.need) + '</p>' +
        '<div class="ro-reco"><span class="ro-label">Рекомендация</span><span class="ro-strong">' + esc(OD.story.action) + '</span></div></div>' +
        '</div></div></div></div></div>';
      var fill = $(host, '.ro-days__fill'), rev = $(host, '.ro-rev__fill'), status = $(host, '[data-status]'), meta = $(host, '[data-meta]'), dayl = $(host, '[data-dayl]');
      var ticks = $$(host, '.ro-days__ticks span'), beatEls = $$(host, '.ro-story__beat'), pips = $$(host, '.ro-story__pips span');
      var revl = $(host, '[data-revl]');
      var lastBeat = -1, lastStatus = '', saved = false;
      function setStatus(lv, text) { if (lastStatus === text) return; lastStatus = text; status.innerHTML = badge(lv, text); }
      function update(p) {
        var day = clamp(p / 0.72, 0, 1) * 5;
        fill.style.transform = 'scaleX(' + (day / 5).toFixed(4) + ')';
        var di = Math.min(5, Math.floor(day + 0.001));
        ticks.forEach(function (t, i) { t.classList.toggle('is-now', i === di); });
        dayl.textContent = 'день ' + di + ' из 5';
        if (day < 0.6) { setStatus('low', 'Активна'); meta.textContent = 'срок оплаты — сегодня'; }
        else if (day < 4.98) { setStatus('mid', 'Ждёт оплаты'); var left = Math.max(1, Math.ceil(5 - day)); meta.textContent = 'удалится через ' + left + ' ' + plural(left, 'день', 'дня', 'дней'); }
        else { setStatus('high', 'Удалена'); meta.textContent = 'данные удалены'; }
        var nowSaved = p >= 0.84;
        var melt = clamp((day - 0.6) / 4.4, 0, 1);
        if (nowSaved !== saved) { saved = nowSaved; host.classList.toggle('is-saved', saved); rev.style.transition = reduced() ? 'none' : 'transform 600ms ' + EASE; setTimeout(function () { if (mode === 'scroll') rev.style.transition = ''; }, 650); }
        rev.style.transform = 'scaleX(' + (saved ? 1 : (1 - melt)).toFixed(4) + ')';
        var yr = eur(OD.story.yearRevenue, true); swap(revl, saved ? yr + ' сохранено' : melt >= 0.999 ? '€ 0' : yr);
        var b = p < 0.2 ? 0 : p < 0.55 ? 1 : p < 0.84 ? 2 : 3;
        if (b !== lastBeat) {
          lastBeat = b;
          beatEls.forEach(function (e, i) { e.classList.toggle('is-on', i === b); if (i === b) e.removeAttribute('aria-hidden'); else e.setAttribute('aria-hidden', 'true'); });
          pips.forEach(function (e, i) { e.classList.toggle('is-on', i <= b); });
        }
      }
      var cleanup = [];
      if (mode === 'scroll') {
        var track = $(host, '.ro-story__track'), ticking = false;
        var onScroll = function () {
          if (ticking) return; ticking = true;
          requestAnimationFrame(function () {
            ticking = false;
            var r = track.getBoundingClientRect(), span = r.height - global.innerHeight + 72;
            update(clamp(-r.top / (span || 1), 0, 1));
          });
        };
        global.addEventListener('scroll', onScroll, { passive: true });
        global.addEventListener('resize', onScroll);
        cleanup.push(function () { global.removeEventListener('scroll', onScroll); global.removeEventListener('resize', onScroll); });
        onScroll();
      } else {
        var STEPS = [0.1, 0.42, 0.75, 1], step = 0;
        var prev = $(host, '[data-prev]'), next = $(host, '[data-next]');
        var go = function (n) {
          step = clamp(n, 0, 3); update(STEPS[step]);
          prev.disabled = step === 0; prev.style.opacity = step === 0 ? '.45' : '';
          next.innerHTML = (step === 3 ? 'Сначала ' : 'Дальше ') + I.arrow;
        };
        prev.addEventListener('click', function () { go(step - 1); });
        next.addEventListener('click', function () { go(step === 3 ? 0 : step + 1); });
        var stage = $(host, '.ro-story__panel'), x0 = null;
        stage.addEventListener('pointerdown', function (e) { x0 = e.clientX; });
        stage.addEventListener('pointerup', function (e) { if (x0 == null) return; var dx = e.clientX - x0; x0 = null; if (Math.abs(dx) > 48) go(step + (dx < 0 ? 1 : -1)); });
        go(0);
      }
      return { destroy: function () { cleanup.forEach(function (f) { f(); }); host.innerHTML = ''; } };
    }
  };

  /* =================== МИНИ-ДЕМО =================== */
  var RANK_ROWS = OD.rank;
  var RankingDemo = {
    mount: function (host) {
      host.classList.add('ro-card', 'ro-card--sm', 'ro-mini');
      host.innerHTML = '<div class="ro-mini__head"><span class="ro-label">Сортировка</span><span class="ro-small ro-sortlabel" data-swap><span>по score ↓</span></span></div>' +
        '<div class="ro-mini__rows" role="table" aria-label="Пример рейтинга: пять клиентов">' +
        RANK_ROWS.map(function (r) {
          return '<div class="ro-mini__row" role="row" data-id="' + r.id + '"><span class="ro-mono" role="cell">' + r.id + '</span><span class="num" role="cell"><span class="ro-score" data-level="' + levelOf(r.score) + '"><i></i>' + r.score + '</span></span><span class="num" role="cell">' + eur(r.bal) + '</span><span class="why" role="cell">' + esc(r.why) + '</span></div>';
        }).join('') + '</div>';
      var box = $(host, '.ro-mini__rows'), lbl = $(host, '[data-swap]'), byBal = false, timer = 0;
      function sort() {
        byBal = !byBal;
        var rows = $$(box, '.ro-mini__row');
        var key = function (el) { return RANK_ROWS.find(function (r) { return r.id === el.getAttribute('data-id'); }); };
        rows.sort(function (a, b) { var A = key(a), B = key(b); return byBal ? A.bal - B.bal : B.score - A.score; });
        flip(box, rows, 450);
        swap(lbl, byBal ? 'по балансу ↑' : 'по score ↓');
      }
      var un = visible(host, function () { if (!timer && !reduced()) timer = setInterval(sort, 4000); }, function () { clearInterval(timer); timer = 0; });
      return { destroy: function () { clearInterval(timer); un(); } };
    }
  };
  var MoneyDemo = {
    mount: function (host, o) {
      o = o || {};
      host.classList.add('ro-card', 'ro-card--sm', 'ro-money');
      var COVER = Math.min(100, Math.round(OD.balanceAtRisk / OD.revenueAtRisk * 100));
      host.innerHTML = '<div class="ro-tune__row"><span class="ro-label">Под угрозой · 30 дней</span><span class="ro-small">' + OD.clients.length + ' клиентов</span></div>' +
        '<div class="ro-money__big">' + (o.mark ? '<span class="ro-mark" data-draw="pending">' + eur(TOTAL) + '</span>' : eur(TOTAL)) + '</div>' +
        '<div class="ro-money__split"><div><span class="ro-small">Ожидаемо</span><b>' + eur(TOTAL) + '</b></div><div><span class="ro-small">Худший случай</span><b>' + eur(OD.worst) + '</b></div></div>' +
        '<div><div class="ro-tune__row" style="margin-bottom:8px"><span class="ro-small">На счетах клиентов под риском</span><span class="ro-mono">' + eur(OD.balanceAtRisk) + ' · ' + COVER + ' %</span></div><div class="ro-meter"><div class="ro-meter__fill"></div></div></div>';
      var f = $(host, '.ro-meter__fill');
      var un = visible(host, function () { f.style.transform = 'scaleX(' + COVER / 100 + ')'; un(); }, null, 0.4);
      return { destroy: un };
    }
  };
  var TuningDemo = {
    mount: function (host) {
      host.classList.add('ro-card', 'ro-card--sm', 'ro-tune');
      host.innerHTML = '<div class="ro-tune__row"><span class="ro-small ro-strong">Вес признака «' + esc(OD.tune.feature) + '»</span><span class="ro-mono" data-v>' + OD.tune.from + '</span></div>' +
        '<div class="ro-fake-range" aria-hidden="true"><div class="ro-fake-range__track"></div><div class="ro-fake-range__fill"></div><div class="ro-fake-range__thumb"></div></div>' +
        '<div class="ro-fake-range__ticks" aria-hidden="true"><span>0</span><span>20</span><span>40</span><span>60</span></div>' +
        '<div class="ro-preview" aria-live="off"><div data-a>Предпросмотр: <b>без изменений</b></div><div data-b style="opacity:0">Высокий риск: <b>' + OD.tune.highFrom + ' → ' + OD.tune.highTo + '</b> · <b>+' + eur(OD.tune.lossDelta) + '</b></div></div>' +
        '<p class="ro-sr">Пример: вес меняется с ' + OD.tune.from + ' до ' + OD.tune.to + ', клиентов высокого риска становится ' + OD.tune.highTo + ' вместо ' + OD.tune.highFrom + ', сумма под угрозой растёт на ' + eur(OD.tune.lossDelta) + '.</p>';
      var fillEl = $(host, '.ro-fake-range__fill'), th = $(host, '.ro-fake-range__thumb'), v = $(host, '[data-v]'), A = $(host, '[data-a]'), B = $(host, '[data-b]');
      var A0 = OD.tune.from, D0 = OD.tune.to - OD.tune.from;
      function set(val) { var p = val / 60 * 100; fillEl.style.width = p + '%'; th.style.left = p + '%'; v.textContent = Math.round(val); }
      function showB(on) { A.style.opacity = on ? 0 : 1; B.style.opacity = on ? 1 : 0; }
      set(A0);
      if (reduced()) { set(A0 + D0); showB(true); return { destroy: function () {} }; }
      var raf = 0, t0 = 0, run = false;
      // цикл 6,2 с: 0–1,2 с 20→35, держим до 3,8 с, 3,8–4,8 с 35→20, пауза
      function tick(t) {
        if (!run) return; if (!t0) t0 = t;
        var e = (t - t0) % 6200, val, k;
        if (e < 1200) { k = 1 - Math.pow(1 - e / 1200, 3); val = A0 + D0 * k; }
        else if (e < 3800) val = A0 + D0;
        else if (e < 4800) { k = 1 - Math.pow(1 - (e - 3800) / 1000, 3); val = A0 + D0 - D0 * k; }
        else val = A0;
        set(val); showB(e >= 1100 && e < 3900);
        raf = requestAnimationFrame(tick);
      }
      var un = visible(host, function () { if (!run) { run = true; t0 = 0; raf = requestAnimationFrame(tick); } }, function () { run = false; cancelAnimationFrame(raf); });
      return { destroy: function () { run = false; un(); } };
    }
  };
  var LiveDemo = {
    mount: function (host) {
      host.classList.add('ro-card', 'ro-card--sm', 'ro-live');
      var rows = OD.live;
      host.innerHTML = '<div class="ro-live__bar"><span class="ro-live__status"><span class="ro-live-dot ro-pulse"></span><span>пересчитано <b data-s>0</b> с назад</span></span><span class="ro-label">события биллинга</span></div>' +
        rows.map(function (r, i) { return '<div class="ro-live__row" data-i="' + i + '"><span class="ro-mono">' + r.id + '</span><span class="ro-small" data-why>' + esc(r.why) + '</span><span class="num" data-sc><span class="ro-score" data-level="' + levelOf(r.a) + '"><i></i>' + r.a + '</span></span></div>'; }).join('');
      var sEl = $(host, '[data-s]'), sec = 0, k = 0, timer = 0;
      var els = $$(host, '.ro-live__row');
      function tick() {
        sec++;
        if (sec > 9) {
          sec = 0;
          var i = k % rows.length, r = rows[i], row = els[i], flipState = Math.floor(k / rows.length) % 2 === 0;
          var sc = flipState ? r.b : r.a; var why = flipState ? r.why2 : r.why;
          $(row, '[data-sc]').innerHTML = '<span class="ro-score" data-level="' + levelOf(sc) + '"><i></i>' + sc + '</span>';
          $(row, '[data-why]').textContent = why;
          row.classList.remove('is-changed'); void row.offsetWidth; row.classList.add('is-changed');
          k++;
        }
        sEl.textContent = sec;
      }
      var un = visible(host, function () { if (!timer && !reduced()) timer = setInterval(tick, 1000); }, function () { clearInterval(timer); timer = 0; });
      return { destroy: function () { clearInterval(timer); un(); } };
    }
  };

  /* ---------- График «факт и прогноз» ---------- */
  var hatchN = 0;
  var ForecastChart = {
    mount: function (host, o) {
      o = o || {};
      var fact = o.fact || [22, 31, 18, 40, 27, 35, 52, 29, 44];
      var fc = o.forecast || [38, 46, 33, 49, 41];
      var labels = o.labels;
      // необязательно: часть факта, у которой дата восстановлена по правилу, а не взята из журнала
      var restored = o.restored && o.restored.some(function (v) { return v > 0; }) ? o.restored : null;
      var n = fact.length + fc.length;
      var W = o.width || 640, H = o.height || 220, padL = 44, padB = 26, padT = 18;
      var max = o.max || Math.ceil(Math.max.apply(null, fact.concat(fc)) / 20) * 20;
      var step = (W - padL) / n, bw = Math.max(6, step * 0.62);
      var id = 'ro-hatch-' + (++hatchN);
      host.classList.add('ro-chart', 'is-pending');
      var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(o.alt || 'Отток по дням: факт слева от линии «сегодня», прогноз справа') + '"><defs><pattern id="' + id + '" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#FFFFFF"/><rect width="2.2" height="6" fill="#6E6158"/></pattern></defs>';
      [0, 0.5, 1].forEach(function (k) {
        var y = padT + (H - padT - padB) * (1 - k);
        svg += '<line class="ro-chart__grid" x1="' + padL + '" x2="' + W + '" y1="' + y + '" y2="' + y + '"/><text class="ro-chart__axis" x="0" y="' + (y + 4) + '">' + esc(o.unit === 'count' ? String(Math.round(max * k * 10) / 10).replace('.', ',') : eur(max * k, true)) + '</text>';
      });
      fact.concat(fc).forEach(function (v, i) {
        var isFc = i >= fact.length, bh = (H - padT - padB) * v / max, x = padL + i * step + (step - bw) / 2, y = H - padB - bh;
        var rv = !isFc && restored ? Math.min(restored[i] || 0, v) : 0, rh = (H - padT - padB) * rv / max;
        svg += '<rect class="ro-chart__bar ' + (isFc ? 'ro-chart__bar--fc' : 'ro-chart__bar--fact') + '" x="' + x.toFixed(1) + '" y="' + (y + rh).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + (bh - rh).toFixed(1) + '" rx="2"' + (isFc ? ' fill="url(#' + id + ')"' : '') + '/>';
        if (rh > 0) svg += '<rect class="ro-chart__bar ro-chart__bar--restored" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + rh.toFixed(1) + '" rx="2"/>';
        if (labels && labels[i]) svg += '<text class="ro-chart__axis" x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle">' + esc(labels[i]) + '</text>';
      });
      var tx = padL + fact.length * step;
      svg += '<line class="ro-chart__today" x1="' + tx + '" x2="' + tx + '" y1="4" y2="' + (H - padB) + '"/><text class="ro-chart__todaylabel" x="' + (tx + 6) + '" y="12">сегодня</text></svg>';
      host.innerHTML = (o.legend === false ? '' : '<div class="ro-chart__legend"><span><i class="ro-sw-fact"></i>' + (restored ? 'факт из журнала' : 'факт, сплошные') + '</span>' +
        (restored ? '<span><i class="ro-sw-restored"></i>факт, дата по правилу «срок + 5 дней»</span>' : '') +
        '<span><i class="ro-sw-fc"></i>прогноз, штриховка</span></div>') + svg +
        '<p class="ro-sr">' + esc((function (fmt) { return 'Факт: ' + fact.map(fmt).join(', ') + '. Прогноз: ' + fc.map(fmt).join(', ') + '.'; })(o.unit === 'count' ? function (v) { return String(v).replace('.', ','); } : function (v) { return eur(v, true); })) + '</p>';
      var bars = $$(host, '.ro-chart__bar');
      function grow() {
        host.classList.remove('is-pending');
        if (reduced() || !Element.prototype.animate) return;
        bars.forEach(function (b, i) { b.animate([{ transform: 'scaleY(0)' }, { transform: 'scaleY(1)' }], { duration: 450, delay: i * 30, easing: EASE, fill: 'backwards' }); });
      }
      var un = visible(host, function () { grow(); un(); }, null, 0.3);
      return { destroy: un };
    }
  };

  /* =================== ИНТЕРАКТИВНАЯ ФОРМУЛА =================== */
  var F_CLIENTS = OD.formula;
  var F_FEATS = [
    { key: 'wait', name: 'Ждёт оплаты', short: 'Ждёт', w: OD.weights.wait },
    { key: 'funds', name: 'Не хватает средств', short: 'Средства', w: OD.weights.funds },
    { key: 'exp', name: 'Скоро истекает без автопродления', short: 'Истекает', w: OD.weights.exp }
  ];
  var Formula = {
    mount: function (host, o) {
      o = o || {};
      var w = F_FEATS.map(function (f) { return f.w; });
      host.classList.add('ro-formula');
      var uid = Math.random().toString(36).slice(2, 7);
      host.innerHTML =
        '<div class="ro-card ro-card--lift ro-weights" role="group" aria-label="Веса признаков">' +
        '<span class="ro-label">Веса признаков</span>' +
        F_FEATS.map(function (f, i) {
          return '<div class="ro-weight"><div class="ro-weight__top"><label for="w-' + uid + i + '">' + esc(f.name) + '</label><output for="w-' + uid + i + '" data-o="' + i + '">' + f.w + '</output></div>' +
            '<input class="ro-range" id="w-' + uid + i + '" type="range" min="0" max="100" step="1" value="' + f.w + '" data-i="' + i + '" aria-describedby="wd-' + uid + '"></div>';
        }).join('') +
        '<div class="ro-weights__fixed" id="wd-' + uid + '"><span class="ro-small">Остальные признаки — с постоянным весом:</span>' + OD.weights.others.map(function (o) { return '<div><span>' + esc(o[0]) + '</span><b>' + o[1] + '</b></div>'; }).join('') + '</div>' +
        '</div>' +
        '<div class="ro-card ro-card--lift ro-ftable"><div class="ro-ftable__head" aria-hidden="true"><span>Клиент</span>' + F_FEATS.map(function (f) { return '<span class="sig">' + f.short + '</span>'; }).join('') + '<span>Score</span><span>Уровень</span><span class="eur">€ / 30 дн</span></div>' +
        '<div class="ro-ftable__rows" role="list" aria-label="Клиенты, отсортированные по score"></div>' +
        '<div class="ro-ftable__foot"><span class="ro-small">Под угрозой у этих шести клиентов</span><span class="ro-ftable__sum" data-swap data-sum><span></span></span></div></div>' +
        '<div class="ro-card ro-formula__eq"><span class="ro-label">Формула</span><code>score = Σ вес × сила сигнала, не больше 100</code><span class="ro-small">· сила сигнала от 0 до 1 · потеря = выручка за 30 дней × вероятность непродления</span></div>';
      var rowsBox = $(host, '.ro-ftable__rows');
      var rows = F_CLIENTS.map(function (c) {
        var r = h('div', 'ro-ftable__row'); r.setAttribute('role', 'listitem'); r.setAttribute('data-id', c.id);
        r.innerHTML = '<span class="ro-mono">' + c.id + '</span>' + c.s.map(function (v) { return '<span class="sig">' + dec(v) + '</span>'; }).join('') + '<span class="sc" data-sc></span><span class="lvl" data-lv></span><span class="eur" data-eur></span>';
        rowsBox.appendChild(r); c.row = r; return c;
      });
      var sumEl = $(host, '[data-sum]');
      function calc(first) {
        rows.forEach(function (c) {
          var sc = Math.min(100, Math.round(c.s[0] * w[0] + c.s[1] * w[1] + c.s[2] * w[2] + c.other));
          var lv = levelOf(sc); c.score = sc;
          c.threat = Math.round(c.rev * (sc >= 100 ? 1 : sc / 100) * 100) / 100;
          $(c.row, '[data-sc]').textContent = sc;
          var lvEl = $(c.row, '[data-lv]');
          if (lvEl.getAttribute('data-level') !== lv) { lvEl.setAttribute('data-level', lv); lvEl.innerHTML = badge(lv); }
          $(c.row, '[data-eur]').textContent = eur(c.threat);
          c.row.setAttribute('aria-label', c.id + ': score ' + sc + ', ' + LEVEL_NAME[lv] + ' риск, ' + eur(c.threat));
        });
        var sorted = rows.slice().sort(function (a, b) { return b.score - a.score || b.threat - a.threat; });
        var cur = $$(rowsBox, '.ro-ftable__row');
        var changed = sorted.some(function (c, i) { return cur[i] !== c.row; });
        if (changed) flip(rowsBox, sorted.map(function (c) { return c.row; }), 450);
        var total = rows.reduce(function (a, c) { return a + c.threat; }, 0);
        if (first) sumEl.firstChild.textContent = eur(total); else swap(sumEl, eur(total));
      }
      $$(host, '.ro-range').forEach(function (inp) {
        var paint = function () { inp.style.setProperty('--p', inp.value + '%'); };
        paint();
        inp.addEventListener('input', function () {
          var i = +inp.getAttribute('data-i'); w[i] = +inp.value; paint();
          $(host, '[data-o="' + i + '"]').textContent = inp.value;
          calc();
        });
      });
      calc(true);
      return { destroy: function () { host.innerHTML = ''; } };
    }
  };

  /* =================== СРАВНЕНИЕ =================== */
  var CompareTable = {
    mount: function (host) {
      var cols = ['Таблица в Excel', 'ML-модель', 'Радар оттока'];
      var rows = [
        ['Объясняет причину риска', ['no', 'Нет', 'только цифры'], ['part', 'Частично', 'нужна интерпретация'], ['yes', 'Да', 'баллы по каждому признаку']],
        ['Считает деньги', ['part', 'Вручную', 'формулы ломаются'], ['no', 'Обычно нет', 'отдаёт вероятность'], ['yes', 'Да', '€ под угрозой за 30 дней']],
        ['Меняется без программиста', ['part', 'Да', 'но правки теряются'], ['no', 'Нет', 'нужно переобучение'], ['yes', 'Да', 'веса в панели с предпросмотром']],
        ['Работает без истории за годы', ['yes', 'Да', ''], ['no', 'Нет', 'нужна длинная история'], ['yes', 'Да', 'с первого дня']],
        ['Обновляется сам', ['no', 'Нет', 'выгрузка раз в неделю'], ['part', 'По расписанию', ''], ['yes', 'Да', 'в реальном времени']]
      ];
      function cell(c, us, col) { return '<td data-col="' + col + '"' + (us ? ' class="is-us"' : '') + '><span class="ro-verdict ro-verdict--' + c[0] + '">' + I[c[0]] + '<span><b>' + c[1] + '</b>' + (c[2] ? ' — ' + esc(c[2]) : '') + '</span></span></td>'; }
      host.innerHTML = '<table class="ro-compare"><caption class="ro-sr">Сравнение: таблица в Excel, ML-модель и Радар оттока</caption><thead><tr><td></td>' + cols.map(function (c, i) { return '<th scope="col"' + (i === 2 ? ' class="is-us"' : '') + '>' + c + '</th>'; }).join('') + '</tr></thead><tbody>' +
        rows.map(function (r) { return '<tr><th scope="row">' + r[0] + '</th>' + cell(r[1], false, cols[0]) + cell(r[2], false, cols[1]) + cell(r[3], true, cols[2]) + '</tr>'; }).join('') + '</tbody></table>';
      return { destroy: function () { host.innerHTML = ''; } };
    }
  };

  /* =================== ПОТОК ДАННЫХ =================== */
  var DataFlow = {
    mount: function (host) {
      host.classList.add('ro-flow');
      var svg, paths = {}, core = null, flash = null, timer = 0, raf = 0, packets = [], layout = '', running = false;
      var NODES = [
        { k: 'bill', t: 'Биллинг', s: 'клиенты, услуги, счета' },
        { k: 'api', t: 'API снимка и событий', s: 'PUT /api/v1/source/snapshot' },
        { k: 'core', t: 'Радар оттока', s: 'score · деньги · прогноз' },
        { k: 'panel', t: 'Панель маркетолога', s: '/login' },
        { k: 'prom', t: 'Prometheus', s: 'GET /metrics' },
        { k: 'sso', t: 'SSO', s: 'вход по учётке компании' },
        { k: 'tg', t: 'Telegram-бот', s: 'уведомления маркетологу' }
      ];
      function build() {
        var wide = host.clientWidth >= 760;
        var L = wide ? 'h' : 'v';
        if (L === layout) return; layout = L;
        var pos, W, H;
        if (wide) {
          W = 1120; H = 360;
          pos = { bill: [0, 148, 200, 64], api: [260, 143, 250, 74], core: [580, 138, 200, 84], panel: [880, 0, 240, 64], prom: [880, 98, 240, 64], sso: [880, 196, 240, 64], tg: [880, 294, 240, 64] };
        } else {
          W = 340; H = 734;
          pos = { bill: [20, 0, 300, 62], api: [20, 110, 300, 70], core: [20, 230, 300, 76], panel: [64, 372, 256, 62], prom: [64, 466, 256, 62], sso: [64, 560, 256, 62], tg: [64, 654, 256, 62] };
        }
        var out = '<svg viewBox="-6 -6 ' + (W + 12) + ' ' + (H + 12) + '" role="img" aria-label="Схема: биллинг отправляет данные в API снимка и событий, оттуда в Радар оттока; Радар отдаёт результат в панель маркетолога, метрики Prometheus, уведомления в Telegram и использует SSO для входа">';
        function c(k) { var p = pos[k]; return { l: p[0], r: p[0] + p[2], t: p[1], b: p[1] + p[3], cx: p[0] + p[2] / 2, cy: p[1] + p[3] / 2 }; }
        var d = {};
        if (wide) {
          d.a = 'M' + c('bill').r + ' ' + c('bill').cy + ' L' + c('api').l + ' ' + c('api').cy;
          d.b = 'M' + c('api').r + ' ' + c('api').cy + ' L' + c('core').l + ' ' + c('core').cy;
          ['panel', 'prom', 'sso', 'tg'].forEach(function (k) { var a = c('core'), z = c(k), mx = (a.r + z.l) / 2; d[k] = 'M' + a.r + ' ' + a.cy + ' C' + mx + ' ' + a.cy + ' ' + mx + ' ' + z.cy + ' ' + z.l + ' ' + z.cy; });
        } else {
          d.a = 'M' + c('bill').cx + ' ' + c('bill').b + ' L' + c('api').cx + ' ' + c('api').t;
          d.b = 'M' + c('api').cx + ' ' + c('api').b + ' L' + c('core').cx + ' ' + c('core').t;
          ['panel', 'prom', 'sso', 'tg'].forEach(function (k) { var a = c('core'), z = c(k), x = 40; d[k] = 'M' + x + ' ' + a.b + ' L' + x + ' ' + (z.cy - 14) + ' Q' + x + ' ' + z.cy + ' ' + (x + 14) + ' ' + z.cy + ' L' + z.l + ' ' + z.cy; });
        }
        for (var k in d) out += '<path class="ro-flow__path" data-p="' + k + '" d="' + d[k] + '"/>';
        NODES.forEach(function (n) {
          var p = pos[n.k];
          out += '<g class="ro-flow__node' + (n.k === 'core' ? ' ro-flow__node--core' : '') + '"><rect x="' + p[0] + '" y="' + p[1] + '" width="' + p[2] + '" height="' + p[3] + '" rx="' + (n.k === 'core' ? 16 : 12) + '"/>' +
            '<text class="t" x="' + (p[0] + 16) + '" y="' + (p[1] + p[3] / 2 - 3) + '">' + esc(n.t) + '</text><text class="s" x="' + (p[0] + 16) + '" y="' + (p[1] + p[3] / 2 + 15) + '">' + esc(n.s) + '</text></g>';
        });
        var cp = pos.core;
        out += '<rect class="ro-flow__flash" x="' + (cp[0] - 5) + '" y="' + (cp[1] - 5) + '" width="' + (cp[2] + 10) + '" height="' + (cp[3] + 10) + '" rx="20"/><g data-pk></g></svg>';
        host.innerHTML = out;
        svg = $(host, 'svg'); flash = $(host, '.ro-flow__flash');
        $$(host, '[data-p]').forEach(function (p) { paths[p.getAttribute('data-p')] = p; });
        packets.forEach(function (pk) { pk.el.remove(); }); packets = [];
      }
      function spawn() {
        var outs = ['panel', 'prom', 'sso', 'tg'];
        var el = s('circle', { 'class': 'ro-flow__pkt', r: 5 });
        $(host, '[data-pk]').appendChild(el);
        packets.push({ el: el, route: ['a', 'b', outs[Math.floor(Math.random() * 3)]], seg: 0, t: 0, dur: [700, 700, 800] });
      }
      var last = 0;
      function frame(t) {
        if (!running && !packets.length) { raf = 0; return; }
        var dt = last ? Math.min(t - last, 64) : 16; last = t;
        packets = packets.filter(function (pk) {
          pk.t += dt;
          var segDur = pk.dur[pk.seg];
          if (pk.t >= segDur) {
            pk.t -= segDur; pk.seg++;
            if (pk.seg === 2 && flash && flash.animate) flash.animate([{ opacity: 0.9 }, { opacity: 0 }], { duration: 600, easing: EASE });
            if (pk.seg === 2) pk.el.setAttribute('class', 'ro-flow__pkt ro-flow__pkt--out');
            if (pk.seg >= pk.route.length) { pk.el.remove(); return false; }
          }
          var p = paths[pk.route[pk.seg]], L = p.getTotalLength(), pt = p.getPointAtLength(L * Math.min(1, pk.t / pk.dur[pk.seg]));
          pk.el.setAttribute('cx', pt.x.toFixed(1)); pk.el.setAttribute('cy', pt.y.toFixed(1));
          return true;
        });
        raf = requestAnimationFrame(frame);
      }
      build();
      var ro = global.ResizeObserver ? new ResizeObserver(function () { build(); }) : null;
      if (ro) ro.observe(host);
      function start() { if (running || reduced()) return; running = true; spawn(); timer = setInterval(spawn, 900); last = 0; if (!raf) raf = requestAnimationFrame(frame); }
      function stop() { running = false; clearInterval(timer); }
      var un = visible(host, start, stop, 0.2);
      return { destroy: function () { stop(); un(); if (ro) ro.disconnect(); } };
    }
  };

  /* =================== ВОПРОСЫ (аккордеон) =================== */
  var FAQ = {
    mount: function (host, items) {
      host.classList.add('ro-faq');
      host.innerHTML = items.map(function (q) { return '<details><summary>' + esc(q[0]) + I.plus + '</summary><div class="ro-faq__a"><p>' + esc(q[1]) + '</p></div></details>'; }).join('');
      $$(host, 'details').forEach(function (d) {
        var sum = $(d, 'summary'), body = $(d, '.ro-faq__a');
        sum.addEventListener('click', function (e) {
          if (reduced() || !body.animate) return;
          e.preventDefault();
          if (!d.open) {
            d.open = true; var hgt = body.scrollHeight;
            body.animate([{ height: '0px', opacity: 0 }, { height: hgt + 'px', opacity: 1 }], { duration: 320, easing: EASE });
          } else {
            var a = body.animate([{ height: body.scrollHeight + 'px', opacity: 1 }, { height: '0px', opacity: 0 }], { duration: 260, easing: EASE });
            a.onfinish = function () { d.open = false; };
          }
        });
      });
      return { destroy: function () { host.innerHTML = ''; } };
    }
  };

  /* =================== ШАПКА =================== */
  var SiteHeader = {
    mount: function (host, o) {
      o = o || {};
      var links = o.links || [['#features', 'Возможности'], ['#how', 'Как считаем'], ['#api', 'API'], ['#faq', 'Вопросы']];
      host.classList.add('ro-header');
      host.innerHTML = '<div class="ro-header__in"><a class="ro-wordmark" href="#top" aria-label="Радар оттока — наверх">' + WORDMARK_SVG + '<span>Радар оттока</span>' + (o.tag ? '<span class="ro-wordmark__tag">' + esc(o.tag) + '</span>' : '') + '</a>' +
        '<nav class="ro-nav" aria-label="Разделы">' + links.map(function (l) { return '<a href="' + l[0] + '">' + l[1] + '</a>'; }).join('') + '</nav>' +
        '<a class="ro-btn ro-btn--sm ro-btn--header" href="/login">Войти в панель' + I.arrow + '</a>' +
        '<button class="ro-burger" type="button" aria-expanded="false" aria-controls="ro-drawer">' + I.menu + 'Меню</button></div>';
      var drawer = h('div', 'ro-drawer'); drawer.id = 'ro-drawer';
      drawer.innerHTML = '<div class="ro-drawer__scrim"></div><div class="ro-drawer__sheet" role="dialog" aria-modal="true" aria-label="Меню"><div class="ro-drawer__top"><span class="ro-wordmark">' + WORDMARK_SVG + '<span>Радар оттока</span></span><button class="ro-burger" type="button" data-close style="display:inline-flex" aria-label="Закрыть меню">' + I.close + '</button></div>' +
        '<nav aria-label="Разделы">' + links.map(function (l) { return '<a href="' + l[0] + '">' + l[1] + '</a>'; }).join('') + '<a href="/api/v1/docs">Документация API</a></nav><a class="ro-btn" href="/login">Войти в панель' + I.arrow + '</a></div>';
      host.parentNode.insertBefore(drawer, host.nextSibling);
      var burger = $(host, '.ro-burger');
      function open() { drawer.classList.add('is-open'); requestAnimationFrame(function () { drawer.classList.add('is-shown'); }); burger.setAttribute('aria-expanded', 'true'); $(drawer, '[data-close]').focus(); }
      function close(back) { drawer.classList.remove('is-shown'); burger.setAttribute('aria-expanded', 'false'); setTimeout(function () { drawer.classList.remove('is-open'); }, reduced() ? 0 : 320); if (back !== false) burger.focus(); }
      burger.addEventListener('click', open);
      $(drawer, '[data-close]').addEventListener('click', function () { close(); });
      $(drawer, '.ro-drawer__scrim').addEventListener('click', function () { close(); });
      $$(drawer, 'nav a').forEach(function (a) { a.addEventListener('click', function () { close(false); }); });
      drawer.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') close();
        if (e.key === 'Tab') { var f = $$(drawer, 'a, button'), first = f[0], lastF = f[f.length - 1]; if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); lastF.focus(); } else if (!e.shiftKey && doc.activeElement === lastF) { e.preventDefault(); first.focus(); } }
      });
      // залипание и активный пункт
      var onScroll = function () { host.classList.toggle('is-stuck', global.scrollY > 8); };
      global.addEventListener('scroll', onScroll, { passive: true }); onScroll();
      var navLinks = $$(host, '.ro-nav a');
      var targets = navLinks.map(function (a) { return doc.querySelector(a.getAttribute('href')); }).filter(Boolean);
      if ('IntersectionObserver' in global && targets.length) {
        var io = new IntersectionObserver(function (es) {
          es.forEach(function (e) {
            if (!e.isIntersecting) return;
            navLinks.forEach(function (a) { if (a.getAttribute('href') === '#' + e.target.id) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); });
          });
        }, { rootMargin: '-45% 0px -50% 0px' });
        targets.forEach(function (t) { io.observe(t); });
      }
      return { destroy: function () { global.removeEventListener('scroll', onScroll); drawer.remove(); } };
    }
  };

  /* =================== ТЕКСТЫ ВАРИАНТОВ =================== */
  var BEATS_BASE = [
    ['День 0', 'Срок оплаты подходит, а на счёте ' + eur(OD.story.balance) + ' из нужных ' + eur(OD.story.need) + ' — на продление не хватает.'],
    ['Дни 1–4', 'Статус «Ждёт оплаты». Письма-напоминания уходят автоматически и теряются среди остальных.'],
    ['День 5', 'Услуга удалена вместе с данными. Клиент ушёл молча — а с ним выручка за год.']
  ];
  var FAQ_BASE = [
    ['Почему не нейросеть?', 'Нейросети нужна длинная история и программист для переобучения, а её ответ трудно объяснить. Здесь каждый балл виден и меняется в панели.'],
    ['Откуда берутся данные?', 'Из биллинга: он присылает снимок клиентов и события в API. Радар пересчитывает оценку сразу, как только данные пришли.'],
    ['Что если данных по клиенту мало?', 'Клиент получает статус «Нет оценки» и не смешивается с низким риском. Как только появятся сигналы, оценка посчитается сама.'],
    ['Как добавить новый признак?', 'Признак — это один файл с правилом, которое считает силу сигнала от 0 до 1. Вес нового признака сам появится в панели, API и метриках — подберите его с предпросмотром.'],
    ['Как узнать о новом риске, не открывая панель?', 'Бот в Telegram присылает клиентов, перешедших в высокий риск: причину, сумму и что сделать. Команды /top и /summary дают сводку за секунду.'],
    ['Как часто обновляется?', 'Данные приходят в реальном времени: рейтинг и суммы пересчитываются сразу, без перезагрузки страницы.'],
    ['Можно ли поменять формулу?', 'Да: веса признаков меняются прямо в панели. До сохранения видно, сколько клиентов сменят уровень и как изменится сумма.']
  ];
  var COPY = {
    A: {
      tag: 'Aéza',
      heroTitle: 'Мы видим, кто из клиентов не продлит сервер. За дни, а не после удаления',
      heroSub: ['Радар оттока каждую минуту пересчитывает риск по всей базе Aéza и показывает маркетологу, ', 'кому написать сегодня', ' и сколько это стоит.'],
      heroNote: 'это ' + OD.pinned.id + ' — удалится через ' + OD.pinned.hours + ' ч, если не позвонить',
      facts: [[String(OD.clients.length), 'клиентов в базе'], [String(OD.counts.high), 'высокого риска'], [eur(OD.total), 'под угрозой за 30 дней']],
      storyLead: 'Так клиент уходил до Радара: без жалобы и без звонка. Прокрутите — увидите, что изменилось.',
      beats: BEATS_BASE.concat([['С Радаром', 'В день 0 клиент уже был в списке маркетолога — с причиной и готовой рекомендацией, что предложить.']]),
      featTitle: 'Что маркетолог видит каждое утро',
      featLead: 'Пять вещей, ради которых открывают панель. Всё ниже — живые фрагменты интерфейса, а не картинки.',
      feats: {
        rank: ['Рейтинг с причинами', 'Клиенты по убыванию риска, и у каждого — почему он здесь. Не «так решила модель», а «не хватает средств на продление».'],
        money: ['Деньги под угрозой', 'Сколько выручки за 30 дней мы можем потерять: ожидаемо и в худшем случае.'],
        tune: ['Настройка модели с предпросмотром', 'Меняете вес признака — и до сохранения видите, сколько клиентов сменят уровень и как изменится сумма.'],
        live: ['Живое обновление', 'Биллинг присылает события, рейтинг пересчитывается сам, а о каждом новом клиенте высокого риска бот сразу пишет в Telegram.'],
        chart: ['Факт и прогноз', 'Сколько выручки ушло по дням и сколько уйдёт, если ничего не делать.']
      },
      featNote: { on: 'tune', text: OD.tune.highFrom + ' → ' + OD.tune.highTo + ': столько звонков прибавится' },
      howTitle: 'Как мы считаем риск',
      howLead: 'У каждого клиента score от 0 до 100 — сумма баллов по признакам. Балл признака = вес × сила сигнала. Подвигайте веса: строки пересортируются, уровни и деньги пересчитаются прямо здесь.',
      howNote: 'подвигайте — список пересоберётся',
      cmpTitle: 'Почему не Excel и не нейросеть',
      cmpLead: 'Три способа ответить на вопрос «кто уйдёт» — и что умеет каждый из них.',
      apiTitle: 'Как Радар подключён к биллингу Aéza',
      apiLead: 'Биллинг отдаёт снимок клиентов и события в API — Радар сразу пересчитывает оценку и отдаёт результат панели и метрикам.',
      relTitle: 'Работает как остальная инфраструктура',
      faqTitle: 'Частые вопросы',
      finalSub: 'В списке ' + OD.counts.high + ' клиентов высокого риска. Начните с первого.'
    },
    B: {
      tag: '',
      heroTitle: 'Ваши клиенты уходят молча. Радар показывает кто, почему и сколько это стоит',
      heroSub: ['Подключите биллинг — и через минуту увидите ', 'сумму под угрозой на 30 дней', ' и список клиентов, с которыми нужно связаться первыми.'],
      heroNote: 'здесь ваша сумма под угрозой',
      heroNoteTarget: 'total',
      facts: [['1 день', 'на подключение'], ['5', 'признаков риска'], ['0', 'чёрных ящиков']],
      storyLead: 'Так клиент уходит у большинства хостингов: молча, по расписанию биллинга. Прокрутите — увидите, где его можно было удержать.',
      beats: BEATS_BASE.concat([['С Радаром', 'В день 0 ваш маркетолог видит этого клиента в списке — с причиной и рекомендацией, что сказать.']]),
      featTitle: 'Что вы получаете в первый же день',
      featLead: 'Пять инструментов вместо выгрузок и догадок. Всё ниже — живые фрагменты интерфейса.',
      feats: {
        rank: ['Кому звонить первым', 'Список клиентов по риску, и у каждого — конкретная причина. Маркетолог знает, что сказать, ещё до звонка.'],
        money: ['Сколько вы теряете, в евро', 'Ожидаемая потеря и худший случай за 30 дней на одной карточке — цифра, которую можно принести на планёрку.'],
        tune: ['Модель под ваш бизнес', 'Меняете вес признака — и до сохранения видите, сколько клиентов сменят уровень и как изменится сумма.'],
        live: ['Сам себя обновляет', 'Ваш биллинг присылает события — рейтинг пересчитывается сам. Без выгрузок и ночных скриптов.'],
        chart: ['Факт и прогноз', 'Сколько денег ушло по дням и сколько уйдёт, если ничего не делать.']
      },
      featNote: { on: 'money', text: 'эта цифра — первое, что вы увидите' },
      howTitle: 'Как считается риск — по шагам, без чёрного ящика',
      howLead: 'У каждого клиента score от 0 до 100 — сумма баллов по признакам. Балл признака = вес × сила сигнала. Подвигайте веса: строки пересортируются, уровни и деньги пересчитаются прямо здесь.',
      howNote: 'каждая цифра объяснима',
      cmpTitle: 'Чем Радар отличается от того, что у вас уже есть',
      cmpLead: 'Таблица считает, но не объясняет. Модель предсказывает, но не говорит почему. Радар делает и то, и другое.',
      apiTitle: 'Подключается к биллингу за день',
      apiLead: 'Ваш биллинг отправляет снимок клиентов и события в API. Всё остальное — оценка, деньги, панель, метрики — уже на стороне Радара.',
      relTitle: 'Спокойно для безопасности и эксплуатации',
      faqTitle: 'Частые вопросы',
      finalSub: 'Это демо-база из 200 клиентов. Свою сумму вы увидите в день подключения.'
    }
  };

  /* =================== ЛЕНДИНГ =================== */
  function mountLanding(root, variant) {
    var C = COPY[variant] || COPY.A;
    var small = global.innerWidth < 640;
    root.classList.add('ro', 'ro-js');
    if (embedded()) root.classList.add('ro-embedded');
    root.innerHTML =
      '<a class="ro-sr" href="#main">К содержанию</a><header data-header></header>' +
      '<main id="main">' +
      '<section class="ro-hero" id="top"><div class="ro-wrap ro-hero__grid">' +
      '<div class="ro-hero__copy"><h1 class="ro-h1" data-reveal="title">' + esc(C.heroTitle) + '</h1>' +
      '<p class="ro-lead" data-reveal="body">' + esc(C.heroSub[0]) + '<span class="ro-mark" data-draw="pending">' + esc(C.heroSub[1]) + '</span>' + esc(C.heroSub[2]) + '</p>' +
      '<div class="ro-hero__actions" data-reveal="body"><a class="ro-btn" href="/login">Войти в панель' + I.arrow + '</a><a class="ro-link" href="/api/v1/docs">Документация API</a></div>' +
      '<ul class="ro-hero__facts" data-reveal="body">' + C.facts.map(function (f) { return '<li><b>' + esc(f[0]) + '</b>' + esc(f[1]) + '</li>'; }).join('') + '</ul></div>' +
      '<div class="ro-hero__stage ro-panel ro-panel--deep" data-reveal="body"><div class="ro-dots" style="position:absolute;inset:0;border-radius:inherit;opacity:.9;z-index:-1"></div><div class="ro-card ro-card--lift" data-radar></div></div>' +
      '</div></section>' +
      '<section id="problem" data-story></section>' +
      '<section class="ro-section" id="features"><div class="ro-wrap"><div class="ro-section__head"><h2 class="ro-h2" data-reveal="title">' + esc(C.featTitle) + '</h2><p class="ro-lead" data-reveal="body">' + esc(C.featLead) + '</p></div>' +
      '<div class="ro-feats">' +
      feat('rank', 'ro-card', C) + feat('money', 'ro-panel', C) + feat('tune', 'ro-panel', C) + feat('live', 'ro-card', C) + feat('chart', 'ro-panel ro-panel--deep', C) +
      '</div></div></section>' +
      '<section class="ro-section ro-paper-texture" id="how" style="position:relative;background:var(--kraft-100)"><div class="ro-wrap"><div class="ro-section__head ro-section__head--wide"><h2 class="ro-h2" data-reveal="title">' + esc(C.howTitle) + '</h2><p class="ro-text" data-reveal="body">' + esc(C.howLead) + '</p></div>' +
      '<div style="position:relative" data-reveal="body"><div data-formula></div><div class="ro-note" data-hownote style="right:8px;top:-46px">' + esc(C.howNote) + '<svg width="60" height="46" style="right:-6px;top:22px" aria-hidden="true"><path d="M8 4 C 30 8, 44 18, 46 38 M38 31 L46 39 L52 30"/></svg></div></div></div></section>' +
      '<section class="ro-section" id="compare"><div class="ro-wrap"><div class="ro-section__head"><h2 class="ro-h2" data-reveal="title">' + esc(C.cmpTitle) + '</h2><p class="ro-lead" data-reveal="body">' + esc(C.cmpLead) + '</p></div><div data-compare data-reveal="body"></div></div></section>' +
      '<section class="ro-section" id="api" style="padding-top:0"><div class="ro-wrap"><div class="ro-section__head"><h2 class="ro-h2" data-reveal="title">' + esc(C.apiTitle) + '</h2><p class="ro-lead" data-reveal="body">' + esc(C.apiLead) + '</p></div>' +
      '<div class="ro-dash-box" data-reveal="body"><div data-flow></div></div>' +
      '<p class="ro-small" style="margin-top:16px">Эндпоинты: <span class="ro-code">PUT /api/v1/source/snapshot</span> <span class="ro-code">GET /metrics</span> · <a class="ro-link" href="/api/v1/docs" style="min-height:0">Документация API</a></p></div></section>' +
      '<section class="ro-section" id="reliability" style="padding-top:0"><div class="ro-wrap"><div class="ro-section__head"><h2 class="ro-h2" data-reveal="title">' + esc(C.relTitle) + '</h2></div>' +
      '<ul class="ro-facts" data-reveal="body">' +
      fact(I.lock, 'Вход по паролю или через SSO', 'Маркетологи заходят под своей учётной записью компании.') +
      fact(I.shield, 'Без входа данные недоступны', 'Ни панель, ни API не отдают данные клиентов без авторизации.') +
      fact(I.gauge, 'Метрики для Prometheus', 'Состояние сервиса и пересчётов — на <span class="ro-code">GET /metrics</span>, в ваших дашбордах.') +
      fact(I.term, 'Запуск одной командой', '<span class="ro-code">docker compose up</span> — и панель работает на вашем сервере.') +
      '</ul></div></section>' +
      '<section class="ro-section" id="faq" style="padding-top:0"><div class="ro-wrap"><div class="ro-section__head"><h2 class="ro-h2" data-reveal="title">' + esc(C.faqTitle) + '</h2></div><div data-faq data-reveal="body"></div></div></section>' +
      '<section class="ro-wrap" aria-labelledby="final-t"><div class="ro-panel ro-final"><svg class="ro-final__rings" viewBox="0 0 520 520" aria-hidden="true"><circle cx="260" cy="260" r="80"/><circle cx="260" cy="260" r="150"/><circle cx="260" cy="260" r="220"/><circle cx="260" cy="260" r="258"/></svg>' +
      '<p class="ro-final__sum" id="final-t" data-reveal="title"><span class="ro-mark" data-draw="pending">' + eur(OD.total) + '</span> под угрозой прямо сейчас</p>' +
      '<p class="ro-lead" data-reveal="body">' + esc(C.finalSub) + '</p><a class="ro-btn" href="/login" data-reveal="body">Войти в панель' + I.arrow + '</a></div></section>' +
      '</main>' +
      '<footer class="ro-footer"><div class="ro-wrap"><span class="ro-wordmark" style="font-size:17px">' + WORDMARK_SVG + 'Радар оттока</span><a class="ro-link" href="/api/v1/docs">Документация API</a><a class="ro-link" href="/privacy">Политика обработки персональных данных</a><span>© 2026</span></div></footer>';

    var parts = [];
    parts.push(SiteHeader.mount($(root, '[data-header]'), { tag: C.tag }));
    var stage = $(root, '.ro-hero__stage');
    parts.push(Radar.mount($(root, '[data-radar]'), { mode: 'hero', small: small, note: C.heroNote, noteTarget: C.heroNoteTarget, noteHost: stage }));
    var hn = $(stage, '.ro-note');
    if (hn && C.heroNoteTarget) { hn.classList.add('ro-note--b'); hn.style.cssText = 'right:8px;bottom:-56px;transform:rotate(-1.5deg)'; }
    else if (hn) hn.style.cssText = 'left:-34px;bottom:-58px;max-width:250px;transform:rotate(-2deg)';
    parts.push(ScrollStory.mount($(root, '[data-story]'), { title: '5 дней до удаления', lead: C.storyLead, beats: C.beats }));
    parts.push(RankingDemo.mount($(root, '[data-demo="rank"]')));
    parts.push(MoneyDemo.mount($(root, '[data-demo="money"]'), { mark: false }));
    parts.push(TuningDemo.mount($(root, '[data-demo="tune"]')));
    parts.push(LiveDemo.mount($(root, '[data-demo="live"]')));
    parts.push(ForecastChart.mount($(root, '[data-demo="chart"]'), { fact: OD.chart.fact, forecast: OD.chart.forecast, labels: OD.chart.labels, alt: 'Отток по дням, € в день: 9 дней факта до даты расчёта и 5 дней прогноза после' }));
    parts.push(Formula.mount($(root, '[data-formula]')));
    parts.push(CompareTable.mount($(root, '[data-compare]')));
    parts.push(DataFlow.mount($(root, '[data-flow]')));
    parts.push(FAQ.mount($(root, '[data-faq]'), FAQ_BASE));
    revealAll(root);
    return { destroy: function () { parts.forEach(function (p) { p && p.destroy && p.destroy(); }); root.innerHTML = ''; } };

    function feat(k, cls, C) {
      var f = C.feats[k];
      var note = C.featNote && C.featNote.on === k ? '<div class="ro-note" style="right:58px;top:0;text-align:right">' + esc(C.featNote.text) + '<svg width="44" height="54" style="right:-52px;top:4px" aria-hidden="true"><path d="M2 10 C 22 6, 34 16, 32 44 M25 37 L32 46 L39 37"/></svg></div>' : '';
      return '<article class="ro-feat ro-feat--' + k + ' ' + cls + '"><div class="ro-feat__text"><h3 class="ro-h3" data-reveal="title">' + esc(f[0]) + '</h3><p class="ro-text" data-reveal="body">' + esc(f[1]) + '</p></div>' +
        '<div class="ro-feat__demo' + (note ? ' ro-feat__demo--noted' : '') + '" data-reveal="body">' + note + '<div data-demo="' + k + '"></div></div></article>';
    }
    function fact(icon, t, d) { return '<li>' + icon + '<div><h3>' + esc(t) + '</h3><p class="ro-text">' + d + '</p></div></li>'; }
  }

  /* =================== Простые компоненты (для витрины) =================== */
  var Button = { html: function (text, o) { o = o || {}; return '<a class="ro-btn' + (o.variant ? ' ro-btn--' + o.variant : '') + (o.size === 'sm' ? ' ro-btn--sm' : '') + '" href="' + (o.href || '/login') + '">' + esc(text) + (o.arrow === false ? '' : I.arrow) + '</a>'; } };
  var RiskBadge = { html: badge };
  var Marker = { html: function (text) { return '<span class="ro-mark">' + esc(text) + '</span>'; } };
  var HandNote = { arrow: function (w, hh, d) { return '<svg width="' + w + '" height="' + hh + '" aria-hidden="true"><path d="' + d + '"/></svg>'; } };

  global.Ottok = {
    version: '1.0.0',
    data: data, eur: eur, levelOf: levelOf, icons: I, wordmark: WORDMARK_SVG, copy: COPY, faq: FAQ_BASE,
    Button: Button, RiskBadge: RiskBadge, Marker: Marker, HandNote: HandNote,
    Radar: Radar, ScrollStory: ScrollStory, RankingDemo: RankingDemo, MoneyDemo: MoneyDemo, TuningDemo: TuningDemo, LiveDemo: LiveDemo,
    ForecastChart: ForecastChart, Formula: Formula, CompareTable: CompareTable, DataFlow: DataFlow, FAQ: FAQ, SiteHeader: SiteHeader,
    LandingA: { mount: function (root) { return mountLanding(root, 'A'); } },
    LandingB: { mount: function (root) { return mountLanding(root, 'B'); } },
    mountLanding: mountLanding, revealAll: revealAll
  };
})(window);

