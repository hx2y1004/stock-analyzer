/* 종목 상세 기술적 분석 차트 (Lightweight Charts 4.1)
 *
 * - 모바일: 세로로 쓸면 페이지가 스크롤되고(vertTouchDrag 끔), 가로로 쓸면 차트 이동, 터치를 떼면 크로스헤어 해제
 * - 메인(캔들/라인 + 거래량 + MA/BB/일목) · RSI · MACD 세 차트는 같은 날짜 칸(빈 값 포함)을 가져
 *   논리 범위(봉 번호)와 크로스헤어를 그대로 맞춘다
 * - 상단 고정 범례 바: 크로스헤어가 가리키는 봉(없으면 마지막 봉)의 날짜 · 시고저종 · 등락률 · 거래량
 * - 설정(지표 on/off, 보조 차트, 색상, 기간)은 localStorage 에 저장
 *
 * main.js 가 renderCharts(data) · setChartType · setChartInterval 을 부른다. data 는 /api/chart 의 chart 객체.
 */
(function () {
  const LWC = window.LightweightCharts;
  const RIGHT_OFFSET = 5;
  const RANGE_DAYS = { '1M': 31, '3M': 92, '6M': 183, '1Y': 366 };
  const TRADING_DAYS = { '1D': 1, '3D': 3, '5D': 5 };            // 분·시간봉: 최근 N 거래일
  // 봉 단위별 기간 버튼과 기본 범위 ('BARS' = 최근 INTRA_BARS 봉, 버튼 강조 없음)
  const RANGE_KEYS = { '10m': ['1D', '3D', '5D', '1M', 'ALL'], '1h': ['5D', '1M', '3M', 'ALL'], day: ['1M', '3M', '6M', '1Y', 'ALL'] };
  const DEFAULT_RANGE = { '10m': 'BARS', '1h': 'BARS', '1d': '6M', '1wk': '1Y', '1mo': 'ALL' };
  const INTRA_BARS = 100;
  const TZ_LABEL = { 'Asia/Seoul': 'KST', 'America/New_York': 'ET' };
  const COLORS = {
    us: { up: '#3fb950', down: '#f85149' },          // 초록 상승 / 빨강 하락
    kr: { up: '#f04452', down: '#3182f6' },          // 빨강 상승 / 파랑 하락
  };
  const store = {
    get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  const isMobile = () => window.matchMedia('(max-width: 640px)').matches;
  // main.js 의 최상위 let currentInterval (window 속성이 아니라 이름으로 읽는다)
  // eslint-disable-next-line no-undef
  const curInterval = () => (typeof currentInterval !== 'undefined' ? currentInterval : '1d');

  // ── 상태 ────────────────────────────────────────────────────────────────
  const S = {
    type: 'candlestick',
    ind: Object.assign({ ma: true, bb: false, ichimoku: false }, store.get('chartInd', {})),
    subs: null,                  // { rsi, macd } — 저장값 없으면 폭에 따라 기본값
    color: store.get('chartColor', 'auto'),   // 'auto' | 'kr' | 'us'
    range: null,                 // 현재 기간 프리셋 (사용자가 직접 확대·이동하면 null)
    data: null, charts: [], main: null, fs: false,
  };
  const savedSubs = store.get('chartSubs', null);
  S.subs = savedSubs || (isMobile() ? { rsi: false, macd: false } : { rsi: true, macd: true });

  const $ = (id) => document.getElementById(id);
  const stock = () => window._lastStock || {};
  const isKRW = () => stock().currency === 'KRW' || /\.(KS|KQ)$/i.test(stock().ticker || '');
  const isUSD = () => !isKRW() && (stock().currency || 'USD') === 'USD';
  const scheme = () => (S.color === 'auto' ? (isKRW() ? 'kr' : 'us') : S.color);
  const palette = () => COLORS[scheme()];

  // ── 분·시간봉 시간 ─────────────────────────────────────────────────────────
  // LWC 는 Unix 초를 UTC 시계로 그린다 → 거래소 현지 시계(한국 KST, 미국 ET)로 보이도록
  // '현지 벽시계 시각을 UTC 로 본 값'으로 옮겨 그린다 (서머타임도 봉마다 Intl 로 계산). 범례도 같은 값을 쓴다.
  const _dtf = {};
  function tzShift(ts, tz) {
    if (!tz) return ts;
    const f = _dtf[tz] || (_dtf[tz] = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23',
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    const p = Object.fromEntries(f.formatToParts(new Date(ts * 1000)).map((x) => [x.type, x.value]));
    return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second) / 1000;
  }
  const isoOf = (t) => new Date(t * 1000).toISOString();           // 옮긴 값 → 'YYYY-MM-DDTHH:MM…' (현지 시계)
  function barLabel(i) {
    const t = S.data.dates[i];
    if (!S.intra) return t;
    return isoOf(t).slice(0, 16).replace('T', ' ') + (TZ_LABEL[S.tz] ? ' ' + TZ_LABEL[S.tz] : '');
  }

  // ── 숫자 형식 ────────────────────────────────────────────────────────────
  function fmtPrice(v) {
    if (v == null || !isFinite(v)) return '—';
    if (isKRW()) return Math.round(v).toLocaleString('ko-KR');
    const s = Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return isUSD() ? '$' + s : s;
  }
  function fmtNum(v) {           // MACD 등 통화 단위지만 기호 없이
    if (v == null || !isFinite(v)) return '—';
    return isKRW() ? Math.round(v).toLocaleString('ko-KR')
      : Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtVol(v) {
    if (v == null || !isFinite(v)) return '—';
    if (isKRW()) {
      if (v >= 1e8) return (v / 1e8).toFixed(2) + '억';
      if (v >= 1e4) return Math.round(v / 1e4).toLocaleString('ko-KR') + '만';
      return Math.round(v).toLocaleString('ko-KR');
    }
    if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return String(Math.round(v));
  }
  const priceFormat = () => (isKRW() ? { type: 'price', precision: 0, minMove: 1 } : { type: 'price', precision: 2, minMove: 0.01 });

  // ── 일목 구름대: 선행스팬 A·B 사이를 칠하는 시리즈 프리미티브 ─────────────────
  class CloudPrimitive {
    constructor(points, up, down) { this.pts = points; this.up = up; this.down = down; this._views = [new CloudView(this)]; }
    attached(p) { this.chart = p.chart; this.series = p.series; }
    detached() { this.chart = this.series = null; }
    updateAllViews() {}
    paneViews() { return this._views; }
  }
  class CloudView {
    constructor(src) { this.src = src; }
    zOrder() { return 'bottom'; }
    renderer() {
      const src = this.src;
      return {
        draw(target) {
          if (!src.chart || !src.series) return;
          const ts = src.chart.timeScale();
          const lr = ts.getVisibleLogicalRange();
          if (!lr) return;
          const pts = src.pts;
          const i0 = Math.max(0, Math.floor(lr.from) - 1), i1 = Math.min(pts.length - 1, Math.ceil(lr.to) + 1);
          target.useBitmapCoordinateSpace((scope) => {
            const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
            for (let i = i0; i < i1; i++) {
              const p = pts[i], q = pts[i + 1];
              if (!p || !q) continue;
              const x1 = ts.logicalToCoordinate(i), x2 = ts.logicalToCoordinate(i + 1);
              const a1 = src.series.priceToCoordinate(p.a), b1 = src.series.priceToCoordinate(p.b);
              const a2 = src.series.priceToCoordinate(q.a), b2 = src.series.priceToCoordinate(q.b);
              if ([x1, x2, a1, b1, a2, b2].some((v) => v == null)) continue;
              ctx.fillStyle = p.a >= p.b ? src.up : src.down;
              ctx.beginPath();
              ctx.moveTo(x1 * hr, a1 * vr); ctx.lineTo(x2 * hr, a2 * vr);
              ctx.lineTo(x2 * hr, b2 * vr); ctx.lineTo(x1 * hr, b1 * vr);
              ctx.closePath(); ctx.fill();
            }
          });
        },
      };
    }
  }

  // ── 차트 생성 ────────────────────────────────────────────────────────────
  function baseOptions(el, height, timeVisible) {
    return {
      width: el.clientWidth || el.parentElement.clientWidth || 600,
      height,
      localization: Object.assign({ locale: 'ko-KR', priceFormatter: fmtNum },
        S.intra ? { timeFormatter: (t) => isoOf(t).slice(0, 16).replace('T', ' ') } : {}),   // 크로스헤어 시간 라벨 YYYY-MM-DD HH:MM
      layout: { background: { color: '#161b22' }, textColor: '#8b949e', fontSize: isMobile() ? 11 : 12 },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: 'rgba(33,38,45,.6)' } },
      crosshair: { mode: LWC.CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#30363d', minimumWidth: isMobile() ? 56 : 70 },
      timeScale: { borderColor: '#30363d', visible: timeVisible, timeVisible: !!S.intra, secondsVisible: false, rightOffset: RIGHT_OFFSET,
        minBarSpacing: 0.02, fixLeftEdge: false, lockVisibleTimeRangeOnResize: true },
      // 모바일 핵심: 세로 드래그는 페이지 스크롤로 넘기고, 가로 드래그만 차트 이동
      handleScroll: { vertTouchDrag: false, horzTouchDrag: true, pressedMouseMove: true, mouseWheel: true },
      handleScale: { axisPressedMouseMove: { time: true, price: true }, mouseWheel: true, pinch: true },
      kineticScroll: { touch: true, mouse: false },
      trackingMode: { exitMode: LWC.TrackingModeExitMode.OnTouchEnd },
    };
  }

  function destroy() {
    S.charts.forEach((c) => { try { c.ro && c.ro.disconnect(); c.chart.remove(); } catch (e) {} });
    S.charts = [];
    S.main = null;
  }

  // 모든 날짜 칸을 가진 시리즈 데이터 (값이 없으면 빈 칸) → 세 차트의 봉 번호가 같아진다
  const withGaps = (dates, values) => dates.map((d, i) => (values && values[i] != null ? { time: d, value: values[i] } : { time: d }));

  function heights() {
    const vh = window.innerHeight;
    if (S.fs) {
      const card = document.querySelector('.chart-card');
      const top = $('mainChart').getBoundingClientRect().top - card.getBoundingClientRect().top;
      const padB = parseFloat(getComputedStyle(card).paddingBottom) || 10;
      const avail = vh - top - padB - 2;
      const nSub = (S.subs.rsi ? 1 : 0) + (S.subs.macd ? 1 : 0);
      const sub = Math.max(84, Math.round(avail * 0.17));
      // 보조 차트마다 위 여백 4 + 구분선 1, MACD(마지막)는 시간축 22 추가
      const extra = nSub * 5 + (S.subs.macd ? 22 : (S.subs.rsi ? 22 : 0));
      return { main: Math.max(200, avail - nSub * sub - extra), sub };
    }
    if (isMobile()) return { main: 340, sub: 104 };
    if (window.innerWidth <= 1024) return { main: 380, sub: 120 };
    return { main: 420, sub: 130 };
  }

  function addChart(el, height, timeVisible, kind) {
    el.innerHTML = '';
    const chart = LWC.createChart(el, baseOptions(el, height, timeVisible));
    if (kind === 'price') chart.applyOptions({ localization: { priceFormatter: fmtPrice } });
    if (kind === 'osc') chart.applyOptions({ localization: { priceFormatter: (v) => v.toFixed(1) } });
    const rec = { el, chart, kind, series: null };
    rec.ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      if (w > 0) { chart.applyOptions({ width: w }); clearTimeout(S._psT); S._psT = setTimeout(syncPriceScaleWidths, 80); }
    });
    rec.ro.observe(el);
    S.charts.push(rec);
    return rec;
  }

  function renderMain(data, h, last) {
    const rec = addChart($('mainChart'), h, last, 'price');
    const chart = rec.chart, pal = palette(), dates = data.dates;
    const ind = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    let main;
    if (S.type === 'candlestick') {
      main = chart.addCandlestickSeries({
        upColor: pal.up, downColor: pal.down, borderUpColor: pal.up, borderDownColor: pal.down,
        wickUpColor: pal.up, wickDownColor: pal.down, priceLineVisible: false, priceFormat: priceFormat(),
      });
      main.setData(dates.map((d, i) => (data.close[i] != null && data.open[i] != null
        ? { time: d, open: data.open[i], high: data.high[i], low: data.low[i], close: data.close[i] } : { time: d })));
    } else {
      main = chart.addLineSeries({ color: '#58a6ff', lineWidth: 2, priceLineVisible: false, priceFormat: priceFormat() });
      main.setData(withGaps(dates, data.close));
    }
    // 캔들은 위 80%, 거래량은 아래 약 18%에 겹쳐 그림
    main.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.2 } });
    const vol = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, priceLineVisible: false,
      lastValueVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
    const upA = hexA(pal.up, 0.45), dnA = hexA(pal.down, 0.45);
    vol.setData(dates.map((d, i) => (data.volume && data.volume[i] != null
      ? { time: d, value: data.volume[i], color: (data.close[i] ?? 0) >= (data.open[i] ?? 0) ? upA : dnA } : { time: d })));

    if (S.ind.ichimoku && data.senkou_a && data.senkou_a.length) {
      const pts = dates.map((_, i) => (data.senkou_a[i] != null && data.senkou_b[i] != null ? { a: data.senkou_a[i], b: data.senkou_b[i] } : null));
      main.attachPrimitive(new CloudPrimitive(pts, 'rgba(63,185,80,0.14)', 'rgba(248,81,73,0.14)'));
      chart.addLineSeries({ ...ind, color: 'rgba(63,185,80,0.35)', lineWidth: 1 }).setData(withGaps(dates, data.senkou_a));
      chart.addLineSeries({ ...ind, color: 'rgba(248,81,73,0.35)', lineWidth: 1 }).setData(withGaps(dates, data.senkou_b));
      chart.addLineSeries({ ...ind, color: '#ff7b72', lineWidth: 1 }).setData(withGaps(dates, data.tenkan));
      chart.addLineSeries({ ...ind, color: '#79c0ff', lineWidth: 1 }).setData(withGaps(dates, data.kijun));
    }
    if (S.ind.bb) {
      chart.addLineSeries({ ...ind, color: 'rgba(188,140,255,0.7)', lineWidth: 1, lineStyle: 2 }).setData(withGaps(dates, data.bb_upper));
      chart.addLineSeries({ ...ind, color: 'rgba(188,140,255,0.4)', lineWidth: 1, lineStyle: 2 }).setData(withGaps(dates, data.bb_mid));
      chart.addLineSeries({ ...ind, color: 'rgba(188,140,255,0.7)', lineWidth: 1, lineStyle: 2 }).setData(withGaps(dates, data.bb_lower));
    }
    if (S.ind.ma) {
      [['ma5', '#f0a500'], ['ma20', '#58a6ff'], ['ma60', '#3fb950'], ['ma120', '#f85149']].forEach(([k, c]) => {
        chart.addLineSeries({ ...ind, color: c, lineWidth: 1 }).setData(withGaps(dates, data[k]));
      });
    }
    rec.series = main;
    S.main = rec;
    return rec;
  }

  function renderRSI(data, h, last) {
    const wrap = $('rsiChart');
    const rec = addChart(wrap, h, last, 'osc');
    const s = rec.chart.addLineSeries({ color: '#bc8cff', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    s.setData(withGaps(data.dates, data.rsi));
    s.createPriceLine({ price: 70, color: 'rgba(248,81,73,0.55)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    s.createPriceLine({ price: 30, color: 'rgba(63,185,80,0.55)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    rec.series = s;
    rec.legend = addLegend(wrap);
    rec.label = (i) => {
      const v = data.rsi[i];
      const zone = v == null ? '' : v >= 70 ? ' <span class="cl-hot">과매수</span>' : v <= 30 ? ' <span class="cl-cold">과매도</span>' : '';
      return `<b>RSI(14)</b> <span class="cl-v" style="color:#bc8cff">${v == null ? '—' : v.toFixed(1)}</span>${zone}`;
    };
    return rec;
  }

  function renderMACD(data, h, last) {
    const wrap = $('macdChart');
    const rec = addChart(wrap, h, last, 'num');
    const pal = palette();
    const hist = rec.chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, priceFormat: priceFormat() });
    hist.setData(data.dates.map((d, i) => (data.macd_hist[i] != null
      ? { time: d, value: data.macd_hist[i], color: hexA(data.macd_hist[i] >= 0 ? pal.up : pal.down, 0.7) } : { time: d })));
    const m = rec.chart.addLineSeries({ color: '#58a6ff', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    m.setData(withGaps(data.dates, data.macd));
    rec.chart.addLineSeries({ color: '#f0a500', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false })
      .setData(withGaps(data.dates, data.macd_signal));
    rec.series = m;
    rec.legend = addLegend(wrap);
    rec.label = (i) => `<b>MACD</b> <span class="cl-v" style="color:#58a6ff">${fmtNum(data.macd[i])}</span>`
      + ` <span class="cl-sub">시그널 <span style="color:#f0a500">${fmtNum(data.macd_signal[i])}</span></span>`;
    return rec;
  }

  function addLegend(el) {
    const lg = document.createElement('div');
    lg.className = 'chart-legend';
    el.appendChild(lg);
    return lg;
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // ── 범례 · 동기화 ────────────────────────────────────────────────────────
  function lastIndex() {
    const c = S.data.close;
    for (let i = c.length - 1; i >= 0; i--) if (c[i] != null) return i;
    return -1;
  }

  function showLegend(i) {
    const d = S.data, el = $('chartLegend');
    if (!el || i == null || i < 0 || i >= d.dates.length || d.close[i] == null) i = lastIndex();
    if (i < 0) { if (el) el.innerHTML = ''; return; }
    let pi = i - 1;
    while (pi >= 0 && d.close[pi] == null) pi--;
    const chg = pi >= 0 && d.close[pi] ? (d.close[i] / d.close[pi] - 1) * 100 : null;
    const pal = palette();
    const col = chg == null ? 'var(--text2)' : chg > 0 ? pal.up : chg < 0 ? pal.down : 'var(--text2)';
    const isLast = i === lastIndex();
    el.innerHTML = `<span class="ol-date">${barLabel(i)}</span>`
      + `<span class="ol-kv"><i>시</i>${fmtPrice(d.open[i])}</span><span class="ol-kv"><i>고</i>${fmtPrice(d.high[i])}</span>`
      + `<span class="ol-kv"><i>저</i>${fmtPrice(d.low[i])}</span><span class="ol-kv"><i>종</i><b style="color:${col}">${fmtPrice(d.close[i])}</b></span>`
      + `<span class="ol-kv" style="color:${col}">${chg == null ? '—' : (chg > 0 ? '+' : '') + chg.toFixed(2) + '%'}</span>`
      + `<span class="ol-kv"><i>거래량</i>${fmtVol(d.volume && d.volume[i])}</span>`;
    el.classList.toggle('is-hover', !isLast);
    S.charts.forEach((r) => { if (r.legend && r.label) r.legend.innerHTML = r.label(i); });
  }

  function indexOfTime(t) {
    if (t == null) return null;
    if (typeof t === 'number') { const v = S.idx.get(t); return v == null ? null : v; }
    const key = typeof t === 'string' ? t : (t.year ? `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}` : null);
    return key ? S.idx.get(key) : null;
  }

  function wireSync() {
    let syncing = false;
    S.charts.forEach((src) => {
      // 시간축: 봉 번호(논리 범위)를 그대로 복사 — 세 차트가 같은 날짜 칸을 가져 정확히 일치
      src.chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
        if (syncing || !r) return;
        syncing = true;
        S.charts.forEach((t) => { if (t !== src) t.chart.timeScale().setVisibleLogicalRange(r); });
        syncing = false;
        // 사용자가 직접 확대·이동하면 프리셋 강조 해제 (LWC 범위 이벤트는 비동기라 '우리가 정한 범위'와 비교)
        const e = S._setRange;
        if (!e || Math.abs(r.from - e.from) > 0.01 || Math.abs(r.to - e.to) > 0.01) { S._setRange = null; if (S.range) markRange(null); }
      });
      // 크로스헤어: 다른 차트에도 같은 봉 위치로
      src.chart.subscribeCrosshairMove((p) => {
        if (syncing) return;
        syncing = true;
        const i = p && p.time != null ? indexOfTime(p.time) : null;
        S.charts.forEach((t) => {
          if (t === src) return;
          if (i == null) { t.chart.clearCrosshairPosition(); return; }
          const v = seriesValue(t, i);
          if (v == null) t.chart.clearCrosshairPosition();
          else t.chart.setCrosshairPosition(v, p.time, t.series);
        });
        syncing = false;
        showLegend(i);
      });
    });
  }

  function seriesValue(rec, i) {
    const d = S.data;
    if (rec === S.main) return d.close[i];
    if (rec.el.id === 'rsiChart') return d.rsi[i];
    if (rec.el.id === 'macdChart') return d.macd[i];
    return null;
  }

  // 가격축 폭을 맞춰 세 차트의 세로 격자를 정렬
  function syncPriceScaleWidths() {
    try {
      const ws = S.charts.map((r) => { try { return r.chart.priceScale('right').width(); } catch (e) { return 0; } });
      const w = Math.max(0, ...ws);
      if (w > 0) S.charts.forEach((r) => { try { r.chart.priceScale('right').applyOptions({ minimumWidth: w }); } catch (e) {} });
    } catch (e) {}
  }

  // ── 기간 프리셋 ──────────────────────────────────────────────────────────
  function markRange(key) {
    S.range = key;
    document.querySelectorAll('#chartRanges [data-range]').forEach((b) => {
      const on = b.dataset.range === key;
      b.classList.toggle('active', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function setRange(r) {
    S._setRange = { from: r.from, to: r.to };
    S.charts.forEach((t) => t.chart.timeScale().setVisibleLogicalRange(r));
  }

  function applyRange(key) {
    const d = S.data;
    if (!d || !S.main) return;
    const n = d.dates.length;
    let from = 0;
    if (S.intra && key !== 'ALL') {
      const day = (i) => isoOf(d.dates[i]).slice(0, 10);
      if (key === 'BARS') from = n - INTRA_BARS;
      else if (TRADING_DAYS[key]) {
        // 최근 N 거래일 (주말·휴장일은 봉이 없으니 날짜가 바뀌는 지점을 센다)
        let days = 0, i = n - 1, cur = day(n - 1);
        for (; i >= 0; i--) { const di = day(i); if (di !== cur) { days++; cur = di; if (days >= TRADING_DAYS[key]) break; } }
        from = i + 1;
      } else {
        const cut = d.dates[n - 1] - RANGE_DAYS[key] * 86400;
        from = d.dates.findIndex((x) => x >= cut);
      }
      from = Math.max(0, Math.min(from < 0 ? 0 : from, n - 12));
    } else if (key !== 'ALL') {
      const last = new Date(d.dates[n - 1] + 'T00:00:00Z');
      const cut = new Date(last.getTime() - RANGE_DAYS[key] * 86400000).toISOString().slice(0, 10);
      from = d.dates.findIndex((x) => x >= cut);
      if (from < 0) from = 0;
      from = Math.max(0, Math.min(from, n - 12));        // 월봉 1M 같은 경우에도 최소 12봉
    }
    setRange({ from: from - 0.5, to: n - 1 + RIGHT_OFFSET });
    markRange(key);
  }

  function resetView() {
    S.charts.forEach((r) => {
      r.chart.priceScale('right').applyOptions({ autoScale: true });
      r.chart.timeScale().applyOptions({ rightOffset: RIGHT_OFFSET });
    });
    applyRange(defaultRange());
  }
  const defaultRange = () => DEFAULT_RANGE[S.intra ? S.interval : curInterval()] || '6M';

  // 봉 단위에 맞는 기간 버튼 (10분: 1D·3D·5D·1M·전체 / 1시간: 5D·1M·3M·전체 / 일·주·월: 1M·3M·6M·1Y·전체)
  function paintRangeButtons() {
    const box = $('chartRanges');
    if (!box) return;
    const keys = RANGE_KEYS[S.intra ? S.interval : 'day'] || RANGE_KEYS.day;
    if (box.dataset.keys === keys.join(',')) return;
    box.dataset.keys = keys.join(',');
    box.innerHTML = keys.map((k) => `<button type="button" data-range="${k}" aria-pressed="false">${k === 'ALL' ? '전체' : k}</button>`).join('');
  }

  // 한국 종목 분·시간봉이 순수 Yahoo 폴백(chart_source='yfinance')일 때만 '지연 시세' 안내.
  // 'toss' · 'toss_hybrid'(최신 구간이 토스 1분봉으로 채워짐)이면 숨김
  function paintSourceNote() {
    const el = $('chartDelay');
    if (!el) return;
    const show = S.intra && S.source === 'yfinance' && isKRW();
    el.hidden = !show;
    if (show) el.title = '토스 실시간 분봉을 받지 못해 Yahoo Finance 시세로 표시 중입니다. 실시간보다 늦고 장 막판(14:50 이후) 봉이 빠질 수 있어요.';
  }

  // ── 렌더 ────────────────────────────────────────────────────────────────
  function render(data, keepView) {
    if (!data || !data.dates || !LWC) return;
    const prevRange = keepView && S.main ? S.main.chart.timeScale().getVisibleLogicalRange() : null;
    const prevKey = S.range;
    S.intra = !!data.intraday;
    S.tz = data.tz || null;
    S.source = data.source || null;
    S.interval = S.intra ? (curInterval() === '1h' ? '1h' : '10m') : curInterval();
    // 분·시간봉: Unix 초 → 거래소 현지 시계 기준 값 (같은 배열을 세 차트·범례·기간 계산이 함께 쓴다)
    if (S.intra && !data._shifted) {
      data = Object.assign({}, data, { dates: data.dates.map((t) => tzShift(t, S.tz)), _unix: data.dates, _shifted: true });
    }
    S.data = data;
    S.idx = new Map(data.dates.map((d, i) => [d, i]));
    paintRangeButtons();
    paintSourceNote();
    destroy();
    $('rsiChart').hidden = !S.subs.rsi;
    $('macdChart').hidden = !S.subs.macd;
    const h = heights();
    const lastIsMain = !S.subs.rsi && !S.subs.macd;
    renderMain(data, h.main, lastIsMain);
    if (S.subs.rsi) renderRSI(data, h.sub + (S.subs.macd ? 0 : 22), !S.subs.macd);   // 맨 아래면 시간축 높이만큼 더
    if (S.subs.macd) renderMACD(data, h.sub + 22, true);   // 마지막 차트는 시간축 높이만큼 더
    wireSync();
    paintToolbar();
    showLegend(null);
    if (prevRange && keepView) {
      setRange(prevRange);
      markRange(prevKey);
    } else {
      applyRange(defaultRange());
    }
    requestAnimationFrame(() => {
      // 다른 차트들도 메인과 같은 범위로 (구독 연결 전에 그려진 범위 보정)
      const r = S._setRange || (S.main && S.main.chart.timeScale().getVisibleLogicalRange());
      if (r) S.charts.forEach((t) => t.chart.timeScale().setVisibleLogicalRange(r));
      syncPriceScaleWidths();
      setTimeout(syncPriceScaleWidths, 250);
    });
  }

  // ── 툴바 ────────────────────────────────────────────────────────────────
  function paintToolbar() {
    const press = (el, on) => { if (!el) return; el.classList.toggle('active', on); el.setAttribute('aria-pressed', on ? 'true' : 'false'); };
    document.querySelectorAll('[data-ctype]').forEach((b) => press(b, b.dataset.ctype === S.type));
    ['ma', 'bb', 'ichimoku'].forEach((k) => press(document.querySelector(`[data-ind="${k}"]`), !!S.ind[k]));
    ['rsi', 'macd'].forEach((k) => press(document.querySelector(`[data-sub="${k}"]`), !!S.subs[k]));
    const cb = $('chartColorBtn');
    if (cb) {
      const sc = scheme();
      cb.innerHTML = sc === 'kr' ? '🇰🇷 <span>빨강↑</span>' : '🇺🇸 <span>초록↑</span>';
      cb.title = `캔들 색상: ${sc === 'kr' ? '한국식(빨강 상승·파랑 하락)' : '미국식(초록 상승·빨강 하락)'}${S.color === 'auto' ? ' · 시장에 맞춰 자동' : ' · 직접 고정'} (눌러서 전환)`;
      cb.setAttribute('aria-label', cb.title);
    }
  }

  function setFullscreen(on) {
    const card = document.querySelector('.chart-card');
    if (!card) return;
    S.fs = on;
    card.classList.toggle('is-fullscreen', on);
    document.body.classList.toggle('chart-fs-open', on);
    const b = $('chartFsBtn');
    if (b) {
      b.innerHTML = on ? `${ICON_CLOSE}<span>닫기</span>` : `${ICON_EXPAND}<span>확대</span>`;
      b.setAttribute('aria-label', on ? '전체화면 닫기' : '차트 전체화면으로 보기');
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    if (S.data) render(S.data, true);   // 높이 다시 계산 + resize
    if (on) b && b.focus();
  }
  const ICON_EXPAND = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_CLOSE = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false"><path d="M18 6 6 18M6 6l12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';

  function bindToolbar() {
    const tb = $('chartToolbar');
    if (!tb || tb.dataset.bound) return;
    tb.dataset.bound = '1';
    tb.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.ctype) { S.type = b.dataset.ctype; render(S.data, true); }
      else if (b.dataset.ind) { S.ind[b.dataset.ind] = !S.ind[b.dataset.ind]; store.set('chartInd', S.ind); render(S.data, true); }
      else if (b.dataset.sub) { S.subs[b.dataset.sub] = !S.subs[b.dataset.sub]; store.set('chartSubs', S.subs); render(S.data, true); }
      else if (b.dataset.range) applyRange(b.dataset.range);
      else if (b.id === 'chartResetBtn') resetView();
      else if (b.id === 'chartFsBtn') setFullscreen(!S.fs);
      else if (b.id === 'chartColorBtn') { S.color = scheme() === 'kr' ? 'us' : 'kr'; store.set('chartColor', S.color); render(S.data, true); }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.fs) setFullscreen(false); });
    let rt;
    window.addEventListener('resize', () => {
      clearTimeout(rt);
      rt = setTimeout(() => {
        if (!S.data || !S.main) return;
        const h = heights();
        const lastSub = S.charts[S.charts.length - 1];
        S.charts.forEach((r) => r.chart.applyOptions({ height: r === S.main ? h.main : (r === lastSub ? h.sub + 22 : h.sub) }));
      }, 150);
    });
  }

  // ── main.js 가 부르는 전역 함수 ───────────────────────────────────────────
  window.renderCharts = function (data) { bindToolbar(); render(data, false); };
  window.setChartType = function (type) { S.type = type; if (S.data) render(S.data, true); };
  window.SAChart = { state: S, applyRange, resetView, setFullscreen, render: (d) => render(d || S.data, true) };
  if (document.readyState !== 'loading') bindToolbar(); else document.addEventListener('DOMContentLoaded', bindToolbar);
})();
