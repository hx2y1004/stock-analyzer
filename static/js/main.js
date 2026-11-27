// main.js build: pf-tabs-v2 (compact card mode)
console.log('[StockAnalyzer] main.js loaded — build: pf-tabs-v2');
let chartData = null;
let currentInterval = '1d';
let currentUser = null;
let currentAnalysisTicker = null;
let currentPositionData = null;
let pfCollapsed = true;       // 포트폴리오 기본 상태: 접힘
let pfAllHoldings = [];       // 마지막으로 불러온 전체 보유 종목

// ── 페이지 모드: 홈(/) = 시황 대시보드, /stock/<ticker> = 분석 전용 ──
function _pathTicker() {
  const m = location.pathname.match(/^\/stock\/(.+)$/);
  return m ? decodeURIComponent(m[1]).trim().toUpperCase() : null;
}
const PAGE_TICKER = _pathTicker();   // null이면 홈 모드

// 종목 페이지에서는 검색창에 종목명을 보여주고 실제 티커는 data-ticker 에 둔다.
// 사용자가 글자를 고치면(input 이벤트) data-ticker 를 지워 입력값을 그대로 쓴다.
function _inputTicker() {
  const inp = document.getElementById('tickerInput');
  if (!inp) return '';
  const v = inp.value.trim();
  if (inp.dataset.ticker && v === inp.dataset.label) return inp.dataset.ticker;
  return v;
}
function _showInputName(ticker, name) {
  const inp = document.getElementById('tickerInput');
  if (!inp || !name || name === ticker) return;
  inp.value = name;
  inp.dataset.ticker = ticker;
  inp.dataset.label = name;
}

function gotoStock(ticker) {
  location.href = '/stock/' + encodeURIComponent(String(ticker).trim().toUpperCase());
}

// ── 앱 초기화 ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  checkAuth();

  // 구형 링크 호환: /?ticker=XXX → /stock/XXX 로 통일
  const _t = new URLSearchParams(location.search).get('ticker');
  if (!PAGE_TICKER && _t) { gotoStock(_t); return; }
  // 알림 푸시(요약·테스트)에서 온 /?tab=alerts → 홈 '내 알림' 탭
  if (!PAGE_TICKER && new URLSearchParams(location.search).get('tab') === 'alerts') {
    setTimeout(() => { switchPfTab('alerts'); document.getElementById('pfTabAlerts')?.closest('section')?.scrollIntoView({ block: 'start' }); }, 0);
  }

  if (PAGE_TICKER) {
    // 분석 모드: 대시보드·포트폴리오 탭 숨기고(body 클래스, 비동기 로드에도 유지) 자동 분석
    document.body.classList.add('stock-mode');
    document.getElementById('backToHome')?.classList.remove('hidden');
    const inp = document.getElementById('tickerInput');
    if (inp) inp.value = PAGE_TICKER;
    analyze();
  } else {
    // 홈 모드: 섹션 순서 재배치 (검색 → 시황 → 추세감지/포트폴리오 탭)
    const search = document.querySelector('.search-section');
    const dash = document.getElementById('marketDashboard');
    const pf = document.getElementById('portfolioSection');
    if (search && dash && pf) {
      document.body.insertBefore(search, pf);   // 검색을 맨 위로
      search.after(dash);                        // 그 아래 시황
    }
    document.body.classList.add('home-mode');    // 검색 컴팩트 스타일
    dash?.classList.remove('hidden');
    initDashboard();
  }
});

// ── 시황 대시보드 ────────────────────────────────────────────
let _dashMarket = 'KR';        // 히트맵·급등락 공용 시장 (기본값은 장 상태로 결정)
let _dashMarketUserSet = false;
let _moversData = null;

async function initDashboard() {
  // 지수 먼저 → 열려있는 시장을 기본 선택 (KR 장중 → KR, US 장중 → US,
  // 둘 다 마감이면 KST 낮(06~17시)엔 KR, 밤엔 US)
  const ov = await loadMarketOverview();
  if (!_dashMarketUserSet) {
    if (ov && ov.kr_open) _dashMarket = 'KR';
    else if (ov && ov.us_open) _dashMarket = 'US';
    else {
      const h = new Date().getHours();
      _dashMarket = (h >= 6 && h < 17) ? 'KR' : 'US';
    }
  }
  _syncDashToggle();
  loadSectorMap();
  loadMovers();
  setInterval(loadMarketOverview, 60000);   // 지수 1분 자동 갱신
}

function _syncDashToggle() {
  document.querySelectorAll('#dashMarketToggle .ah-period-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.dm === _dashMarket));
  const lb = document.getElementById('sectorMapMarketLabel');
  if (lb) lb.textContent = _dashMarket === 'KR' ? '🇰🇷' : '🇺🇸';
  if (_lastOverview) _renderLeadRow(_lastOverview);          // 시장 폭 칸을 활성 시장 토글과 맞춤 (초기 로드 포함)
}

function setDashMarket(mkt, btn) {
  _dashMarket = mkt;
  _dashMarketUserSet = true;
  _syncDashToggle();
  loadSectorMap();
  renderMovers();
}

// ── 선행지표 행 (6단계): 10Y-2Y 금리차 | 하이일드 스프레드 | 구리/금 비율 | 시장 폭 추세(선택한 시장) ──
let _lastOverview = null;
const _LD_TONE = { inverted: 'bad', alert: 'bad', slowdown: 'bad', caution: 'warn', normal: 'neu', neutral: 'neu', stable: 'good', expansion: 'good' };
const _LD_TIP = {
  t10y2y: '미국 10년물과 2년물 국채 금리 차이예요. 0 아래로 내려가는 "역전"은 과거 경기 침체 1~2년 전에 자주 나타났어요. 0~0.25%p는 평탄(주의).',
  hy: '신용등급이 낮은 회사채(하이일드)가 국채보다 더 받는 금리예요. 벌어지면 시장이 부도 위험을 걱정한다는 뜻 — 3.5% 미만 안정, 4.5% 이상이나 20일 새 +0.5%p 급등이면 경계.',
  copper_gold: '경기에 민감한 구리를 안전자산인 금으로 나눈 값이에요. 오르면 경기 확장 기대, 내리면 둔화 우려 (20거래일 ±3% 기준).',
  breadth: '오른 종목 수 − 내린 종목 수를 날마다 쌓은 A/D 선과 지수를 비교해요. 지수는 오르는데 오르는 종목이 줄면 "고점 다이버전스" — 소수 대형주만 끌어올리는 상태예요.',
};
// 시장 폭 판정 → 카드 배지용 짧은 이름 (전체 문구는 설명 영역에)
const _LD_BREADTH_SHORT = { bearish_divergence: '고점 주의', bullish_divergence: '바닥 신호', weak: '약세 동조', weakening: '폭 약화',
  healthy: '건강', improving: '폭 개선', flat: '보합', collecting: '수집 중' };
function _ldCell(key, label, valHtml, subHtml, badge, tone, extraTip, fullName) {
  const tip = (fullName ? `${fullName} — ` : '') + _LD_TIP[key] + (extraTip ? ` 출처: ${extraTip}.` : '');
  return `
    <div class="ld-cell" data-k="${key}">
      <div class="ld-head"><span class="ld-label">${label}</span>
        <button type="button" class="ld-info" aria-label="${_esc(label)} 설명 보기" aria-expanded="false" aria-controls="ldNote" data-tip="${_esc(tip)}" title="${_esc(tip)}">ⓘ</button>
        ${badge ? `<span class="ld-badge ld-${tone}">${_esc(badge)}</span>` : ''}</div>
      <div class="ld-val">${valHtml}</div>
      <div class="ld-sub">${subHtml}</div>
    </div>`;
}
function _renderLeadRow(d) {
  const el = document.getElementById('mdLead');
  if (!el) return;
  const li = d.leading_indicators || (d.macro && d.macro.leading) || null;
  const sgn = (v, unit, dg = 2) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(dg)}${unit}`);
  const cls = (v, invert) => (v == null || v === 0 ? '' : (v > 0) !== !!invert ? 'up' : 'down');
  const loading = '<span class="ld-dim">불러오는 중…</span>';
  const yc = li && li.t10y2y, hy = li && li.hy, cg = li && li.copper_gold;
  const src = (x) => (x ? `${x.source}${x.as_of ? ` · ${x.as_of.slice(5).replace('-', '.')} 기준` : ''}` : '');
  const c1 = yc ? _ldCell('t10y2y', '10Y-2Y', sgn(yc.value, '%p'), `20일 <span class="${cls(yc.change_20d)}">${sgn(yc.change_20d, '%p')}</span>`, yc.badge, _LD_TONE[yc.state] || 'neu', src(yc), '장단기 금리차')
    : _ldCell('t10y2y', '10Y-2Y', '—', li ? '<span class="ld-dim">자료 없음</span>' : loading, '', 'neu', '', '장단기 금리차');
  const c2 = hy ? (hy.value != null
      ? _ldCell('hy', '하이일드', `${hy.value.toFixed(2)}%`, `20일 <span class="${cls(hy.change_20d, true)}">${sgn(hy.change_20d, '%p')}</span>`, hy.badge, _LD_TONE[hy.state] || 'neu', src(hy), '하이일드 스프레드')
      : _ldCell('hy', '하이일드', `<span class="${cls(hy.change_20d)}">${sgn(hy.change_20d, '%')}</span>`, 'HYG/LQD 20일', hy.badge, _LD_TONE[hy.state] || 'neu', src(hy), '하이일드 스프레드'))
    : _ldCell('hy', '하이일드', '—', li ? '<span class="ld-dim">자료 없음</span>' : loading, '', 'neu', '', '하이일드 스프레드');
  const c3 = cg ? _ldCell('copper_gold', '구리/금', cg.value.toFixed(2), `20일 <span class="${cls(cg.change_20d)}">${sgn(cg.change_20d, '%')}</span> · ×1000`, cg.badge, _LD_TONE[cg.state] || 'neu', src(cg), '구리/금 비율')
    : _ldCell('copper_gold', '구리/금', '—', li ? '<span class="ld-dim">자료 없음</span>' : loading, '', 'neu', '', '구리/금 비율');
  const mk = _dashMarket === 'KR' ? 'KR' : 'US';
  const b = (d.breadth || {})[mk];
  const bTone = b ? ({ up: 'good', down: 'bad' }[b.tone] || 'neu') : 'neu';
  const mkName = mk === 'KR' ? '한국' : '미국';
  const c4 = b ? _ldCell('breadth', `시장 폭 · ${mkName}`,
      b.advancers != null ? `<span class="up">${b.advancers}</span><small> 상승</small> <span class="down">${b.decliners}</span><small> 하락</small>` : '—',
      b.state === 'collecting' ? `<span class="ld-dim">${_esc(b.label)}</span>`
        : `최근 ${b.window_days}일 순상승 ${sgn(b.ad_net_pct, '%', 1)}${b.index_change_pct != null ? ` · 지수 ${sgn(b.index_change_pct, '%')}` : ''}`,
      _LD_BREADTH_SHORT[b.state] || b.label, b.state === 'collecting' ? 'neu' : bTone,
      mk === 'KR' ? '섹터맵 한국 대표 약 350종목 · KOSPI' : '섹터맵 미국 대표 약 540종목 · S&P 500', `시장 폭 추세(${mkName}): ${b.label}`)
    : _ldCell('breadth', `시장 폭 · ${mkName}`, '—', '<span class="ld-dim">자료 없음</span>', '', 'neu', '', `시장 폭 추세(${mkName})`);
  const openKey = el.querySelector('.ld-cell.tip-on')?.dataset.k;          // 1분 자동 갱신 때 열어 둔 설명 유지
  el.innerHTML = `<div class="ld-title">선행지표 <small>6시간마다 갱신 · 국면 판정에 반영</small></div><div class="ld-grid">${c1 + c2 + c3 + c4}</div>`
    + '<p class="ld-note" id="ldNote" role="note" hidden></p>';
  el.classList.remove('hidden');
  // 설명은 4칸 아래 공용 영역 하나에 (옆 카드 높이가 늘어나지 않게). 다시 누르거나 바깥을 누르면 닫힘
  const note = el.querySelector('#ldNote');
  const closeAll = () => {
    el.querySelectorAll('.ld-cell.tip-on').forEach((c) => { c.classList.remove('tip-on'); c.querySelector('.ld-info').setAttribute('aria-expanded', 'false'); });
    note.hidden = true;
  };
  const openTip = (btn) => {
    closeAll();
    btn.closest('.ld-cell').classList.add('tip-on'); btn.setAttribute('aria-expanded', 'true');
    note.textContent = btn.dataset.tip; note.hidden = false;
  };
  el.querySelectorAll('.ld-info').forEach((btn) => {
    btn.onclick = (e) => { e.stopPropagation(); btn.getAttribute('aria-expanded') === 'true' ? closeAll() : openTip(btn); };
  });
  if (openKey) { const b = el.querySelector(`.ld-cell[data-k="${openKey}"] .ld-info`); if (b) openTip(b); }
  if (!_renderLeadRow._outside) {
    _renderLeadRow._outside = true;
    document.addEventListener('click', (e) => {
      const box = document.getElementById('mdLead');
      if (box && !e.target.closest('.ld-info')) {
        box.querySelectorAll('.ld-cell.tip-on').forEach((c) => { c.classList.remove('tip-on'); c.querySelector('.ld-info').setAttribute('aria-expanded', 'false'); });
        const n = box.querySelector('#ldNote'); if (n) n.hidden = true;
      }
    });
  }
}

// 홈 시장 현황 아래 1줄: 미 10년 국채 | 금 | 달러 인덱스 | 매크로 국면
function _renderMacroStrip(m) {
  const el = document.getElementById('mdMacro');
  if (!el) return;
  if (!m) { el.classList.add('hidden'); return; }
  const chgCls = (v) => (v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '');
  const pct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`);
  const cell = (label, val, chgHtml, extra = '') => `
    <div class="md-mac ${extra}"><div class="md-idx-label">${label}</div><div class="md-mac-val">${val}</div><div class="md-idx-chg">${chgHtml}</div></div>`;
  const t = m.tnx, g = m.gold, x = m.dxy, r = m.regime || {};
  const tnx = t ? cell('미 10년 국채', `${t.value.toFixed(2)}%`,
    t.change_pp == null ? '—' : `<span class="${chgCls(t.change_pp)}">${t.change_pp > 0 ? '+' : ''}${t.change_pp.toFixed(2)}%p</span>`) : cell('미 10년 국채', '—', '—');
  const gold = g ? cell('금 (GC=F)', '$' + g.value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
    `<span class="${chgCls(g.change_pct)}">${pct(g.change_pct)}</span>`) : cell('금 (GC=F)', '—', '—');
  const dxy = x ? cell('달러 인덱스', x.value.toFixed(2), `<span class="${chgCls(x.change_pct)}">${pct(x.change_pct)}</span>`) : cell('달러 인덱스', '—', '—');
  const tone = r.regime === 'risk_on' ? 'on' : r.regime === 'risk_off' ? 'off' : 'neu';
  const reg = `
    <div class="md-mac md-mac-regime tone-${tone}" title="${_esc(r.summary || '')}">
      <div class="md-idx-label">매크로 국면</div>
      <div class="md-mac-val">${r.available === false ? '—' : _esc(r.label || '중립')}<small>${r.available === false ? '' : (r.regime === 'risk_on' ? ' Risk-On' : r.regime === 'risk_off' ? ' Risk-Off' : '')}</small></div>
      <div class="md-mac-why">${_esc((r.reasons || []).slice(0, 2).join(' · ') || (r.available === false ? '자료 없음' : '뚜렷한 부담·우호 요인 없음'))}</div>
    </div>`;
  el.innerHTML = tnx + gold + dxy + reg;
  el.classList.remove('hidden');
}

async function loadMarketOverview() {
  const el = document.getElementById('mdIndices');
  if (!el) return null;
  try {
    const r = await fetch('/api/market/overview');
    if (!r.ok) return null;
    const d = await r.json();
    if (!d.indices || !d.indices.length) return null;
    el.innerHTML = d.indices.map(ix => {
      const chg = ix.change_pct;
      const cls = chg == null ? '' : chg > 0 ? 'up' : chg < 0 ? 'down' : '';
      const isVix = ix.kind === 'vix';
      const val = ix.kind === 'fx'
        ? '₩' + ix.value.toLocaleString('ko-KR', { maximumFractionDigits: 1 })
        : ix.value.toLocaleString('en-US', { maximumFractionDigits: 2 });
      return `
        <div class="md-idx ${isVix ? 'md-idx-vix' : ''}">
          <div class="md-idx-label">${ix.label}</div>
          <div class="md-idx-val">${val}</div>
          <div class="md-idx-chg ${cls}">${chg == null ? '—' : (chg > 0 ? '+' : '') + chg.toFixed(2) + '%'}</div>
        </div>`;
    }).join('');
    _renderMacroStrip(d.macro);
    _lastOverview = d;
    _renderLeadRow(d);
    // 서버가 막 켜져 선행지표를 백그라운드로 받는 중이면 한 번만 다시 확인 (6시간 캐시라 이후엔 바로 나옴)
    if (!d.leading_indicators && !loadMarketOverview._retried) { loadMarketOverview._retried = true; setTimeout(loadMarketOverview, 8000); }
    const st = document.getElementById('mdStatus');
    if (st) {
      // 장중 / 마감 / 휴장(사유) · 미국 조기폐장일은 '조기폐장' 표시
      const lab = (s, open) => {
        if (!s) return open ? '장중' : '마감';
        if (s.holiday) return `휴장 (${_esc(s.holiday)})`;
        return s.open ? (s.early_close ? '장중 · 13시 조기폐장' : '장중') : (s.early_close ? '마감 · 조기폐장' : '마감');
      };
      st.innerHTML =
        `<span class="md-dot ${d.kr_open ? 'on' : ''}"></span>한국 ${lab(d.kr_status, d.kr_open)} · ` +
        `<span class="md-dot ${d.us_open ? 'on' : ''}"></span>미국 ${lab(d.us_status, d.us_open)}`;
    }
    return d;
  } catch (e) { /* 다음 주기에 재시도 */ }
  return null;
}

// ── 섹터맵: 전체 유니버스 시총가중 (색 = 등락률, 크기 = 섹터 시총 비중) ──
// 서버가 백그라운드로 만든 스냅샷을 즉시 받는다. 두 시장 모두 받아 두어 토글 전환이 바로 된다.
let _sectorMapData = null;
const _sectorMapCache = {};
let _sectorSort = 'cap';

function _fmtCap(v, mkt) {
  if (v == null || !v) return '';
  if (mkt === 'KR') return v >= 1e12 ? (v / 1e12).toFixed(v >= 1e14 ? 0 : 1) + '조' : Math.round(v / 1e8).toLocaleString('ko-KR') + '억';
  return v >= 1e12 ? '$' + (v / 1e12).toFixed(2) + 'T' : '$' + (v / 1e9).toFixed(1) + 'B';
}

// 스퀘어리파이드 트리맵: 면적 = 값, 가능한 한 정사각형에 가깝게 (Bruls et al.)
function _squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((a, b) => a + b.v, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest = items.map(it => ({ ...it, a: it.v * scale }));
  const worst = (row, side) => {
    const s = row.reduce((a, b) => a + b.a, 0);
    let mx = 0, mn = Infinity;
    row.forEach(r => { mx = Math.max(mx, r.a); mn = Math.min(mn, r.a); });
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  while (rest.length) {
    const side = Math.min(w, h);
    let row = [rest[0]], i = 1;
    while (i < rest.length && worst(row.concat([rest[i]]), side) <= worst(row, side)) { row.push(rest[i]); i++; }
    const s = row.reduce((a, b) => a + b.a, 0);
    if (w >= h) {                          // 왼쪽에 세로 줄
      const cw = s / h; let cy = y;
      row.forEach(r => { const rh = r.a / cw; out.push({ ...r, x, y: cy, w: cw, h: rh }); cy += rh; });
      x += cw; w -= cw;
    } else {                               // 위쪽에 가로 줄
      const rh = s / w; let cx = x;
      row.forEach(r => { const cw = r.a / rh; out.push({ ...r, x: cx, y, w: cw, h: rh }); cx += cw; });
      y += rh; h -= rh;
    }
    rest = rest.slice(i);
  }
  return out;
}

function _sectorHint(d) {
  const hint = document.getElementById('mdHeatmapHint');
  if (!hint) return;
  if (!d || d.warming) { hint.textContent = '섹터맵을 처음 집계하고 있어요 (1~2분). 잠시 후 자동으로 표시됩니다.'; return; }
  const t = d.updated_at ? new Date(d.updated_at) : null;
  const hm = t ? t.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false }) : '';
  const md = t ? `${t.getMonth() + 1}/${t.getDate()} ` : '';
  const today = t && t.toDateString() === new Date().toDateString();
  const kind = d.reason === 'close' ? '장 마감' : d.reason === 'intraday' ? '장중' : '';
  hint.innerHTML = `주요 <b>${d.covered_count}</b>종목 시총가중 · ${today ? '' : md}${hm} 기준${kind ? ` (${kind})` : ''}`
    + ` · 상승 <span class="up">${d.advancing_count}</span> · 하락 <span class="down">${d.declining_count}</span>`
    + `<span class="md-hint-sub"> · 색 = 등락률, 크기 = 섹터 시가총액 비중, 클릭하면 구성 종목</span>`;
}

async function _fetchSectorMap(mkt) {
  const r = await fetch(`/api/market/sectormap?market=${mkt}`);
  if (!r.ok) throw 0;
  const d = await r.json();
  if (!d.warming) _sectorMapCache[mkt] = d;
  return d;
}

let _sectorRetry = null;
async function loadSectorMap() {
  const el = document.getElementById('mdHeatmap');
  if (!el) return;
  const mkt = _dashMarket;
  clearTimeout(_sectorRetry);
  if (_sectorMapCache[mkt]) renderSectorMap(_sectorMapCache[mkt]);   // 받아 둔 것 즉시 표시
  else el.innerHTML = '<div class="pf-loading">불러오는 중...</div>';
  try {
    const d = await _fetchSectorMap(mkt);
    if (mkt !== _dashMarket) return;
    if (d.warming) {
      _sectorHint(d);
      if (!_sectorMapCache[mkt]) el.innerHTML = '<div class="pf-loading">섹터 집계 준비 중...</div>';
      _sectorRetry = setTimeout(loadSectorMap, 15000);
      return;
    }
    renderSectorMap(d);
    const other = mkt === 'KR' ? 'US' : 'KR';                       // 다른 시장도 미리 받아 둠
    if (!_sectorMapCache[other]) _fetchSectorMap(other).catch(() => {});
  } catch (e) {
    if (!_sectorMapCache[mkt]) el.innerHTML = '<div class="empty-state">불러오기 실패 — 잠시 후 다시 시도해주세요</div>';
  }
}

function renderSectorMap(d) {
  const el = document.getElementById('mdHeatmap');
  if (!el) return;
  _sectorMapData = d;
  _sectorHint(d);
  const groups = (d.groups || []).filter(g => g.market_cap > 0);
  if (!groups.length) { el.innerHTML = '<div class="empty-state">데이터 없음</div>'; return; }
  // .hm-treemap 은 좌우로 2px 씩 넓혀(바깥 테두리 상쇄) 그린다 → 계산 폭 = 카드 안쪽 폭 + 4
  const W = (el.clientWidth || 600) + 4;
  const H = W < 520 ? Math.round(W * 0.95) : Math.max(300, Math.round(W * 0.56));
  const colorCls = (c) =>
    c >= 2 ? 'hm-up3' : c >= 1 ? 'hm-up2' : c > 0.15 ? 'hm-up1'
    : c <= -2 ? 'hm-dn3' : c <= -1 ? 'hm-dn2' : c < -0.15 ? 'hm-dn1' : 'hm-flat';
  const rects = _squarify(groups.map(g => ({ g, v: g.market_cap })), 0, 0, W, H);
  const MOB_HM = window.matchMedia('(max-width: 767px)').matches;
  // 좌표를 정수 픽셀로: 이웃 칸이 같은 경계값을 반올림하므로 빈틈·겹침 없이 맞닿고, 2px 테두리 두 개 = 모든 틈 4px
  const snapped = rects.map(r => {
    const x0 = Math.round(r.x), y0 = Math.round(r.y), x1 = Math.round(r.x + r.w), y1 = Math.round(r.y + r.h);
    return { g: r.g, x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }).filter(r => r.w > 0 && r.h > 0);
  el.innerHTML = `<div class="hm-treemap" style="height:${H}px">${snapped.map(({ g, x, y, w, h }) => {
    const c = g.change_pct;
    // 모바일(≤767px)은 좁은 칸에서 등락률이 테두리 밖으로 잘리므로 기준을 넓힌다 (라벨 숨김 → 누르면 구성 종목 창에 이름·등락률)
    const tiny = MOB_HM ? (w < 56 || h < 36) : (w < 40 || h < 30);
    const big = w >= 150 && h >= 96, mid = w >= 74 && h >= 56, narrow = !tiny && w < 84;
    const breadth = mid ? `<span class="hm-br">${g.advancing_count}↑ ${g.declining_count}↓</span>` : '';
    const nPrev = big ? Math.max(0, Math.min(3, Math.floor((h - 78) / 18))) : 0;   // 높이에 들어가는 만큼만
    const preview = nPrev ? g.members.slice(0, nPrev).map(m =>
      `<span class="hm-mini"><span>${escapeHtmlMain(m.name)}</span><span>${fmtPct(m.change_pct)}</span></span>`).join('') : '';
    const label = `${g.name} ${fmtPct(c)} · 상승 ${g.advancing_count} 하락 ${g.declining_count} 보합 ${g.unchanged_count} · 시총 비중 ${g.cap_share}%`;
    return `<button type="button" class="hm-tile hm-abs ${colorCls(c)}${big ? ' hm-big' : ''}${tiny ? ' hm-tiny' : ''}${narrow ? ' hm-narrow' : ''}"
        style="left:${x}px;top:${y}px;width:${w}px;height:${h}px"
        onclick="toggleSectorDrill('${g.key}')" title="${escapeHtmlMain(label)}" aria-label="${escapeHtmlMain(label)}">
        <span class="hm-in">${tiny ? '' : `<span class="hm-name">${escapeHtmlMain(g.name)}</span><span class="hm-chg">${fmtPct(c)}</span>`}${breadth}${preview}</span>
      </button>`;
  }).join('')}</div>`;
  if (MOB_HM) {
    // 모바일: 그래도 이름·등락률이 칸 밖으로 넘치는 블록은 라벨을 숨긴다
    el.querySelectorAll('.hm-abs:not(.hm-tiny)').forEach((t) => {
      const chg = t.querySelector('.hm-chg');
      if (!chg) return;
      const tb = t.getBoundingClientRect(), cb = chg.getBoundingClientRect();
      if (cb.bottom > tb.bottom - 2 || cb.right > tb.right - 2) {
        t.classList.add('hm-tiny');
        const inn = t.querySelector('.hm-in');
        if (inn) inn.innerHTML = '';
      }
    });
  }
}

let _smResize = null;
window.addEventListener('resize', () => {
  clearTimeout(_smResize);
  _smResize = setTimeout(() => { if (_sectorMapData && !_sectorMapData.warming) renderSectorMap(_sectorMapData); }, 200);
});

// 타일 클릭 → 구성 종목 모달 (스냅샷에 이미 포함돼 있어 추가 요청 없음)
function toggleSectorDrill(key) {
  if (!_sectorMapData) return;
  const g = (_sectorMapData.groups || []).find(x => x.key === key);
  if (!g) return;
  const title = document.getElementById('sectorModalTitle');
  const desc = document.getElementById('sectorModalDesc');
  if (!title) return;
  const c = g.change_pct;
  const mkt = _sectorMapData.market;
  title.textContent = `${mkt === 'KR' ? '🇰🇷' : '🇺🇸'} ${g.name}`;
  const n = g.total_count || 1;
  const pa = g.advancing_count / n * 100, pu = g.unchanged_count / n * 100, pd = g.declining_count / n * 100;
  desc.innerHTML = `
    <div class="sm-summary">
      <span class="sm-chg ${c > 0 ? 'up' : c < 0 ? 'down' : ''}">${fmtPct(c)}</span>
      <span class="sm-basis">${g.basis === 'simple' ? '단순 평균' : '시총가중'} · ${g.total_count}종목 · 시총 ${_fmtCap(g.market_cap, mkt)} (시장의 ${g.cap_share}%)</span>
    </div>
    <div class="sm-bar" role="img" aria-label="상승 ${g.advancing_count}, 보합 ${g.unchanged_count}, 하락 ${g.declining_count}">
      <i class="sm-adv" style="width:${pa}%"></i><i class="sm-unch" style="width:${pu}%"></i><i class="sm-dec" style="width:${pd}%"></i>
    </div>
    <div class="sm-bar-legend"><span class="up">상승 ${g.advancing_count}</span><span>보합 ${g.unchanged_count}</span><span class="down">하락 ${g.declining_count}</span></div>
    <div class="sm-sort" role="group" aria-label="정렬">
      ${[['cap', '시총순'], ['up', '상승순'], ['down', '하락순']].map(([k, l]) =>
        `<button type="button" class="sm-sort-btn${_sectorSort === k ? ' active' : ''}" data-sort="${k}" aria-pressed="${_sectorSort === k}">${l}</button>`).join('')}
      <span class="sm-sort-hint">종목을 누르면 분석 페이지로</span>
    </div>`;
  desc.querySelectorAll('.sm-sort-btn').forEach(b => b.onclick = () => {
    _sectorSort = b.dataset.sort;
    desc.querySelectorAll('.sm-sort-btn').forEach(x => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-pressed', on); });
    _renderSectorMembers(g, mkt);
  });
  _renderSectorMembers(g, mkt);
  document.getElementById('sectorModal').classList.remove('hidden');
}

function _renderSectorMembers(g, mkt) {
  const list = document.getElementById('sectorModalList');
  const rows = g.members.slice();
  if (_sectorSort === 'up') rows.sort((a, b) => b.change_pct - a.change_pct);
  else if (_sectorSort === 'down') rows.sort((a, b) => a.change_pct - b.change_pct);
  else rows.sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0));
  list.innerHTML = rows.map(m => {
    const cls = m.change_pct > 0 ? 'up' : m.change_pct < 0 ? 'down' : '';
    const cur = mkt === 'KR'
      ? Math.round(m.price).toLocaleString('ko-KR') + '원'
      : '$' + Number(m.price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `<button class="sector-modal-item" onclick="gotoStock('${m.ticker}')">
        <span class="smi-name"><span class="smi-nm">${escapeHtmlMain(m.name)}</span><span class="smi-cap">${_fmtCap(m.market_cap, mkt)}</span></span>
        <span class="smi-price">${cur}</span>
        <span class="smi-chg ${cls}">${fmtPct(m.change_pct)}</span>
      </button>`;
  }).join('');
  list.scrollTop = 0;
}

function closeSectorModal(event) {
  if (!event || event.target === document.getElementById('sectorModal')) {
    document.getElementById('sectorModal').classList.add('hidden');
  }
}
document.addEventListener('keydown', (e) => {
  const m = document.getElementById('sectorModal');
  if (e.key === 'Escape' && m && !m.classList.contains('hidden')) m.classList.add('hidden');
});

async function loadMovers() {
  try {
    const r = await fetch('/api/market/movers');
    if (!r.ok) return;
    _moversData = await r.json();
    renderMovers();
  } catch (e) { /* 무시 */ }
}

function renderMovers() {
  if (!_moversData) return;
  const d = _moversData[_dashMarket];
  if (!d) return;
  const row = (s) => {
    const cls = s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : '';
    return `<button class="md-mover" onclick="gotoStock('${s.ticker}')">
        <span class="md-mover-name">${escapeHtmlMain(s.name)}</span>
        <span class="md-mover-chg ${cls}">${fmtPct(s.change_pct)}</span>
      </button>`;
  };
  const g = document.getElementById('mdGainers');
  const l = document.getElementById('mdLosers');
  if (g) g.innerHTML = (d.gainers || []).map(row).join('') || '<div class="empty-state">—</div>';
  if (l) l.innerHTML = (d.losers || []).map(row).join('') || '<div class="empty-state">—</div>';
  // 하락장에서 '급등' 열에 마이너스 종목이 섞이면 '상대 강세'로 (반대로 '급락' 열에 플러스가 섞이면 '상대 약세')
  const gt = document.getElementById('mdGainTitle'), lt = document.getElementById('mdLoseTitle');
  const gRel = (d.gainers || []).some(s => s.change_pct <= 0), lRel = (d.losers || []).some(s => s.change_pct >= 0);
  if (gt) { gt.textContent = gRel ? '상대 강세' : '급등'; gt.classList.toggle('rel', gRel); gt.title = gRel ? '오늘 오른 종목이 적어 덜 내린 순서로 보여 줍니다' : ''; }
  if (lt) { lt.textContent = lRel ? '상대 약세' : '급락'; lt.classList.toggle('rel', lRel); lt.title = lRel ? '오늘 내린 종목이 적어 덜 오른 순서로 보여 줍니다' : ''; }
}

async function checkAuth() {
  try {
    const res  = await fetch('/api/me');
    const data = await res.json();
    currentUser = data.user;
    renderAuthArea();
    _syncTossButton();
    if (currentUser) loadPortfolio();
  } catch (e) {}
}

function renderAuthArea() {
  const area = document.getElementById('authArea');
  if (!currentUser) {
    area.innerHTML = `<button class="login-btn" onclick="openLoginModal()">로그인</button>`;
    return;
  }
  if (window.SAProfile) area.innerHTML = SAProfile.html(currentUser);     // 아바타 → 드롭다운(알림 설정 · 로그아웃)
}

// /#portfolioSection 진입: 위쪽 시황 카드가 늦게 그려지며 섹션을 밀어내므로 몇 번 다시 맞춘다.
// 사용자가 직접 스크롤하면 바로 멈춘다.
function _scrollToPortfolioSection() {
  const sec = document.getElementById('portfolioSection');
  let stopped = false;
  const stop = () => { stopped = true; };
  ['wheel', 'touchstart', 'keydown', 'mousedown'].forEach(ev => addEventListener(ev, stop, { once: true, passive: true }));
  [0, 300, 800, 1500, 2500, 4000].forEach(ms => setTimeout(() => {
    if (stopped) return;
    const hdr = document.querySelector('.header');
    const offset = (hdr ? hdr.getBoundingClientRect().height : 0) + 12;   // 고정 헤더에 가리지 않게
    const delta = sec.getBoundingClientRect().top - offset;
    if (Math.abs(delta) > 4) scrollTo({ top: scrollY + delta, behavior: 'instant' });
  }, ms));
}

// ── 포트폴리오 ───────────────────────────────────────────────────────────────
async function loadPortfolio() {
  document.getElementById('portfolioSection').classList.remove('hidden');
  if (location.hash === '#portfolioSection' && !window._pfHashDone) {
    window._pfHashDone = true;
    switchPfTab('portfolio');
    _scrollToPortfolioSection();
  }
  const cards = document.getElementById('portfolioCards');
  cards.innerHTML = '<div class="pf-loading">불러오는 중...</div>';
  try {
    const res  = await fetch('/api/portfolio?summary=1');
    const data = await res.json();
    // 로그인 안 됨 → 서버가 [] 를 준다 / 로그인 → {holdings, summary}
    const holdings = Array.isArray(data) ? data : (data.holdings || []);
    renderPortfolioSummary(Array.isArray(data) ? null : data.summary);
    renderPortfolioCards(holdings);
    const nEl = document.getElementById('pfHoldCount');
    if (nEl) nEl.textContent = holdings.length ? holdings.length : '';
    _pfRisk.stale = true;                                   // 보유 종목이 바뀌었을 수 있음 → 리스크 탭은 다음에 열 때 다시
    if (_pfRisk.sub === 'risk') loadPfRisk();
  } catch (e) {
    cards.innerHTML = '<div class="pf-loading">불러오기 실패</div>';
  }
}

// 토스 계좌 불러오기 버튼: 연동 권한이 있는 소유자 계정에서만 (일반 사용자는 '+ 종목 추가'만)
function _syncTossButton() {
  const b = document.getElementById('pfTossBtn');
  if (b) b.classList.toggle('hidden', !(currentUser && currentUser.can_import_toss === true));
}

// 토스 계좌 보유종목 → 실제 포트폴리오 동기화
async function importTossPortfolio(btn) {
  if (!confirm('토스증권 계좌의 보유종목을 불러옵니다.\n현재 포트폴리오는 토스 계좌 기준으로 덮어쓰여집니다. 계속할까요?')) return;
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '불러오는 중...'; }
  try {
    const res = await fetch('/api/portfolio/import-toss', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || '불러오기 실패');
      return;
    }
    alert(`토스 계좌에서 ${data.imported}개 종목을 불러왔습니다.`);
    await loadPortfolio();
  } catch (e) {
    alert('네트워크 오류로 불러오지 못했습니다.');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
}

// ── 내 포트폴리오: PC 는 증권사식 1줄 표, 모바일(≤768px)은 컴팩트 카드 (같은 마크업을 CSS grid 로 전환) ──
// 숫자 표기: 부호는 통화 기호 앞(-$23.40), 달러 단가·금액은 소수 2자리, 원화는 정수 쉼표, 수량은 소수 최대 2자리
function pfMoney(v, cur, signed = false) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v), sign = signed ? (n > 0 ? '+' : n < 0 ? '-' : '') : (n < 0 ? '-' : '');
  const a = Math.abs(n);
  return cur === 'KRW'
    ? sign + Math.round(a).toLocaleString('ko-KR') + '원'
    : sign + '$' + a.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pfQty(q) {
  if (q == null) return '—';
  return Number(q).toLocaleString('en-US', { maximumFractionDigits: 2 }) + '주';      // 10.225972 → 10.23, 6 → 6
}
const _PF_EDIT_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false"><path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const _PF_DEL_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false"><path d="M18 6 6 18M6 6l12 12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" fill="none"/></svg>';

// 정렬 (공통 hsort.js): 드롭다운 · 방향 버튼 · PC 표 머리글 클릭이 같은 상태를 쓴다
const _PF_SORT_KEY = 'sa_sort_home_pf';
const _pfSort = window.SAHoldSort ? SAHoldSort.load(_PF_SORT_KEY) : { key: 'value', dir: 'desc' };
// 표 머리글 → 정렬 기준 (현재가는 정렬 없음). 손익·수익률 칸은 평가손익·수익률 둘 다 이 칸으로 표시
const _PF_HEAD = [
  { label: '종목', key: 'name', on: ['name'] },
  { label: '수량 · 매입가', key: 'cost', on: ['cost'] },
  { label: '현재가', key: null },
  { label: '평가금액', key: 'value', on: ['value'] },
  { label: '손익 · 수익률', key: 'pl', on: ['pl', 'ret'] },
];
function _pfSortAcc() {
  const fx = (window._pfFx || 1400);
  const toKrw = (v, cur) => (v == null ? null : (cur || 'USD') === 'USD' ? v * fx : v);
  const q = (h) => Number(h.quantity) || 0;
  const has = (h) => h.current_price != null;
  return {
    value: (h) => (has(h) ? toKrw(h.current_price * q(h), h.currency) : null),
    ret: (h) => (h.return_pct == null ? null : h.return_pct),
    pl: (h) => (has(h) ? toKrw((h.current_price - h.purchase_price) * q(h), h.currency) : null),
    cost: (h) => toKrw(h.purchase_price * q(h), h.currency),
    name: (h) => h.name || h.ticker,
  };
}
function _pfHeadHTML() {
  return _PF_HEAD.map((c) => {
    if (!c.key) return `<span role="columnheader">${c.label}</span>`;
    const active = c.on.includes(_pfSort.key);
    const sortName = active && _pfSort.key === 'ret' ? ' (수익률)' : '';
    const aria = active ? (_pfSort.dir === 'asc' ? 'ascending' : 'descending') : 'none';
    return `<span role="columnheader" aria-sort="${aria}"><button type="button" class="pft-sort${active ? ' on' : ''}" data-k="${c.key}"`
      + ` title="${c.label}${sortName} 기준 정렬">${c.label}<i aria-hidden="true">${active ? SAHoldSort.arrow(_pfSort.dir) : ''}</i></button></span>`;
  }).join('') + '<span class="pft-h-acts" role="columnheader">관리</span>';
}
function _pfResort() { if (pfAllHoldings) renderPortfolioCards(pfAllHoldings); }

function _pfRowHTML(h) {
  const cur = h.currency || 'USD';
  const q = Number(h.quantity) || 0;
  const hasPx = h.current_price != null;
  const value = hasPx ? h.current_price * q : null;              // 평가금액은 원본 정밀 수량으로
  const pl = h.return_amount, pct = h.return_pct;
  const cls = pct == null ? 'flat' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
  const tk = escapeHtmlMain(h.ticker), nm = escapeHtmlMain(h.name || h.ticker);
  const pctTxt = pct == null ? '' : `${pct > 0 ? '+' : pct < 0 ? '-' : ''}${Math.abs(pct).toFixed(2)}%`;
  const logo = window.renderStockLogo ? renderStockLogo(h.ticker, h.name, 'md') : '';
  return `
    <div class="pft-row pf-${cls}" role="link" tabindex="0" data-t="${tk}" aria-label="${nm} 종목 분석 보기">
      <div class="pft-stock">${logo}<div class="pft-id"><div class="pft-name">${nm}</div><div class="pft-tk">${tk}</div></div></div>
      <div class="pft-qty"><div>${pfQty(q)}</div><div class="pft-sub">평균 ${pfMoney(h.purchase_price, cur)}</div></div>
      <div class="pft-px">${hasPx ? pfMoney(h.current_price, cur) : '<span class="pf-loading-price">로딩중</span>'}</div>
      <div class="pft-val">${pfMoney(value, cur)}</div>
      <div class="pft-pl ${cls}"><div>${pfMoney(pl, cur, true)}</div><div class="pft-sub">${pctTxt}</div></div>
      <div class="pft-meta">${pfQty(q)} · ${pfMoney(h.purchase_price, cur)} → ${hasPx ? pfMoney(h.current_price, cur) : '—'}</div>
      <div class="pft-acts">
        ${window.SAAlerts ? `<button type="button" class="pft-btn al-pft" onclick="event.stopPropagation(); SAAlerts.open('${tk}', this.dataset.n, ${hasPx ? h.current_price : 'null'}, '${cur}')" data-n="${nm}" title="알림 설정" aria-label="${nm} 알림 설정">${SAAlerts.bellSvg(15)}</button>` : ''}
        <button type="button" class="pft-btn" onclick="openEditModal(event, ${h.id})" title="매입가·수량 수정" aria-label="${nm} 매입가·수량 수정">${_PF_EDIT_SVG}</button>
        <button type="button" class="pft-btn del" onclick="deleteHolding(event, ${h.id})" title="삭제" aria-label="${nm} 삭제">${_PF_DEL_SVG}</button>
      </div>
    </div>`;
}

function renderPortfolioCards(holdings) {
  pfAllHoldings = holdings;
  const cards  = document.getElementById('portfolioCards');
  const footer = document.getElementById('pfMoreFooter');
  const btn    = document.getElementById('pfToggleBtn');
  if (footer) footer.classList.add('hidden');
  if (btn)    btn.classList.add('hidden');
  cards.classList.remove('collapsed');

  const bar = document.getElementById('pfSortBar');
  if (!holdings.length) {
    if (bar) bar.classList.add('hidden');
    cards.innerHTML = `<div class="pf-empty">보유 종목이 없습니다. 종목을 추가해보세요!</div>`;
    return;
  }
  // 정렬 컨트롤 (처음 한 번 그리고, 이후엔 상태만 다시 칠한다)
  if (bar && window.SAHoldSort) {
    const slot = document.getElementById('pfSortSlot');
    if (!slot.firstChild) {
      slot.innerHTML = SAHoldSort.controlHTML('pfSort', _pfSort, '내 포트폴리오');
      SAHoldSort.bind(document.getElementById('pfSort'), _pfSort, _PF_SORT_KEY, _pfResort);
    } else SAHoldSort.paint(document.getElementById('pfSort'), _pfSort);
    document.getElementById('pfSortCount').textContent = `보유 ${holdings.length}종목`;
    bar.classList.remove('hidden');
  }
  // 금액 기준은 원화 환산(달러 × 현재 환율)으로 비교
  const rows = window.SAHoldSort ? SAHoldSort.sort(holdings, _pfSort, _pfSortAcc()) : holdings;
  cards.innerHTML = `
    <div class="pft" role="table" aria-label="내 포트폴리오 보유 종목">
      <div class="pft-head" role="row">${window.SAHoldSort ? _pfHeadHTML() : ''}</div>
      ${rows.map(_pfRowHTML).join('')}
    </div>`;
  // PC 표 머리글 클릭: 다른 칸 → 그 기준의 기본 방향, 같은 칸 → 방향 뒤집기. 드롭다운과 같은 상태
  cards.querySelectorAll('.pft-sort').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.k;
    const same = _PF_HEAD.find((c) => c.key === k).on.includes(_pfSort.key);
    if (same) _pfSort.dir = _pfSort.dir === 'asc' ? 'desc' : 'asc';
    else SAHoldSort.setKey(_pfSort, k, false);
    SAHoldSort.save(_PF_SORT_KEY, _pfSort);
    _pfResort();
    const again = cards.querySelector(`.pft-sort[data-k="${k}"]`);
    if (again) again.focus();
  }));
  cards.querySelectorAll('.pft-row').forEach((r) => {
    const go = () => gotoStock(r.dataset.t);
    r.addEventListener('click', (e) => { if (!e.target.closest('button, a')) go(); });
    r.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === r) { e.preventDefault(); go(); } });
  });
}

// 실제 포트폴리오 요약 헤더: 총 평가금액·투자금·손익, 통화/종목 비중
function renderPortfolioSummary(s) {
  const el = document.getElementById('pfSummary');
  if (!el) return;
  if (!s || !s.count) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  if (s.fx_usd_krw) window._pfFx = s.fx_usd_krw;
  const won = (v) => (v < 0 ? '-' : '') + Math.round(Math.abs(v)).toLocaleString('ko-KR') + '원';
  const plCls = s.pl_krw > 0 ? 'up' : s.pl_krw < 0 ? 'down' : '';
  const sign = s.pl_krw > 0 ? '+' : '';
  const curLabel = { KRW: '원화', USD: '달러' };
  const curBar = Object.entries(s.currency_weights || {}).map(([c, p]) =>
    `<span class="pf-sum-chip">${curLabel[c] || c} ${p}%</span>`).join('');
  const top = (s.top_weights || []).map(t =>
    `<div class="pf-sum-w"><span class="pf-sum-w-name">${escapeHtmlMain(t.name)}</span>
       <span class="pf-sum-w-bar"><span style="width:${Math.min(100, t.pct)}%"></span></span>
       <span class="pf-sum-w-pct">${t.pct}%</span></div>`).join('');
  const note = s.priced_count < s.count
    ? `<div class="pf-sum-note">현재가를 못 가져온 ${s.count - s.priced_count}개 종목은 손익 계산에서 뺐어요.</div>` : '';
  el.innerHTML = `
    <div class="pf-sum-main">
      <div class="pf-sum-label">총 평가금액</div>
      <div class="pf-sum-value">${won(s.total_value_krw)}</div>
      <div class="pf-sum-pl ${plCls}">${sign}${won(s.pl_krw)}${s.pl_pct != null ? ` (${sign}${s.pl_pct.toFixed(2)}%)` : ''}</div>
    </div>
    <div class="pf-sum-side">
      <div class="pf-sum-kv"><span>총 투자금</span><strong>${won(s.total_cost_krw)}</strong></div>
      <div class="pf-sum-kv"><span>종목 수</span><strong>${s.count}개</strong></div>
      <div class="pf-sum-chips">${curBar}</div>
    </div>
    ${top ? `<div class="pf-sum-weights"><div class="pf-sum-sub">비중 상위</div>${top}</div>` : ''}
    <div class="pf-sum-note">달러 종목은 현재 환율(1달러 ${s.fx_usd_krw.toLocaleString('ko-KR')}원)로 환산했어요. 매입 당시 환율은 반영되지 않아요.</div>
    ${note}`;
  el.classList.remove('hidden');
}

// 레거시 호환: 펼치기/접기 토글 → 더 이상 사용 안 함
function togglePortfolio() {
  // 내부 스크롤로 전체 표시 중이라 noop
  pfCollapsed = false;
  renderPortfolioCards(pfAllHoldings);
}

// ── 포트폴리오 / 관심종목 탭 전환 (추세 셋업은 /screener?tab=trends 로 이동) ──
function switchPfTab(tab) {
  if (tab !== 'watch' && tab !== 'alerts') tab = 'portfolio';
  document.querySelectorAll('.pf-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  const pfPanel  = document.getElementById('pfTabPortfolio');
  const wtPanel  = document.getElementById('pfTabWatch');
  if (pfPanel) pfPanel.classList.toggle('hidden', tab !== 'portfolio');
  if (wtPanel) wtPanel.classList.toggle('hidden', tab !== 'watch');
  const alPanel = document.getElementById('pfTabAlerts');
  if (alPanel) alPanel.classList.toggle('hidden', tab !== 'alerts');
  if (tab === 'alerts' && window.SAAlerts) SAAlerts.renderHome(alPanel);
  if (tab === 'watch' && window.SAWatch) {
    SAWatch.renderList(document.getElementById('watchList'), (t) => gotoStock(t));
  }
}

async function deleteHolding(event, hid) {
  event.stopPropagation();
  if (!confirm('이 종목을 포트폴리오에서 삭제할까요?')) return;
  await fetch(`/api/portfolio/${hid}`, { method: 'DELETE' });
  loadPortfolio();
  currentPositionData = null;
  renderPositionCard(null, null);
}

// ── 포지션 수정 모달 ──────────────────────────────────────
function openEditModal(event, hid) {
  if (event) event.stopPropagation();
  if (!currentUser) { openLoginModal(); return; }
  // pfAllHoldings 에서 해당 종목 찾기
  const h = (pfAllHoldings || []).find(x => x.id === hid);
  if (!h) { alert('종목 정보를 찾을 수 없습니다.'); return; }

  document.getElementById('editHoldingId').value = h.id;
  document.getElementById('editCurrency').value  = h.currency || 'USD';
  document.getElementById('editPrice').value     = h.purchase_price;
  document.getElementById('editQty').value       = h.quantity;
  document.getElementById('editCurrencyLabel').textContent = `(${h.currency || 'USD'})`;
  document.getElementById('editModalDesc').textContent =
    `${h.name} (${h.ticker}) 의 매입가·수량을 수정합니다`;
  document.getElementById('editModal').classList.remove('hidden');
  setTimeout(() => document.getElementById('editPrice').focus(), 50);
}

function closeEditModal(event) {
  if (!event || event.target === document.getElementById('editModal')) {
    document.getElementById('editModal').classList.add('hidden');
  }
}

// 수정 모달 전용 step (통화 기준)
function _stepEditNum(inputId, direction) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const isKRW = (document.getElementById('editCurrency').value || 'USD') === 'KRW';
  let step = 1;
  if (inputId === 'editPrice') step = isKRW ? 100 : 1;
  else if (inputId === 'editQty') step = 1;

  let cur = parseFloat(input.value);
  if (isNaN(cur)) cur = 0;
  let next = cur + direction * step;
  if (next < 0) next = 0;
  if ((inputId === 'editPrice' && isKRW) || inputId === 'editQty') {
    next = Math.round(next);
  } else {
    next = Math.round(next * 100) / 100;
  }
  input.value = next;
}

async function submitEditHolding() {
  const hid     = parseInt(document.getElementById('editHoldingId').value);
  const currency = document.getElementById('editCurrency').value || 'USD';
  let   price   = parseFloat(document.getElementById('editPrice').value);
  let   qty     = parseFloat(document.getElementById('editQty').value);

  if (!hid)              { alert('종목 정보를 찾을 수 없습니다'); return; }
  if (!price || !qty)    { alert('매입가와 수량을 입력해주세요'); return; }
  if (price <= 0 || qty <= 0) { alert('매입가와 수량은 0보다 커야 합니다'); return; }

  if (currency === 'KRW') price = Math.round(price);
  qty = Math.floor(qty);

  try {
    const res = await fetch(`/api/portfolio/${hid}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quantity: qty, purchase_price: price }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || '수정 실패'); return; }
    closeEditModal();
    loadPortfolio();
    // 현재 분석 중인 종목이면 포지션 카드도 새로고침
    if (currentAnalysisTicker && currentPositionData &&
        currentPositionData.ticker === currentAnalysisTicker) {
      analyze();
    }
  } catch (e) {
    alert('네트워크 오류: ' + (e.message || e));
  }
}

// 수정 모달 input 검증 (개별 입력 시)
document.addEventListener('DOMContentLoaded', () => {
  const editPrice = document.getElementById('editPrice');
  const editQty   = document.getElementById('editQty');
  if (editPrice) {
    editPrice.addEventListener('input', (e) => {
      const isKRW = (document.getElementById('editCurrency').value || 'USD') === 'KRW';
      let v = e.target.value;
      if (v.startsWith('-')) v = v.replace(/^-+/, '');
      if (isKRW) v = v.replace(/[.,].*$/, '');
      if (v !== e.target.value) e.target.value = v;
    });
    editPrice.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp')   { e.preventDefault(); _stepEditNum('editPrice', 1); }
      if (e.key === 'ArrowDown') { e.preventDefault(); _stepEditNum('editPrice', -1); }
    });
  }
  if (editQty) {
    editQty.addEventListener('input', (e) => {
      let v = e.target.value;
      if (v.startsWith('-')) v = v.replace(/^-+/, '');
      v = v.replace(/[.,].*$/, '');
      if (v !== e.target.value) e.target.value = v;
    });
    editQty.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp')   { e.preventDefault(); _stepEditNum('editQty', 1); }
      if (e.key === 'ArrowDown') { e.preventDefault(); _stepEditNum('editQty', -1); }
    });
  }
});

// ── 종목 추가 모달 ─────────────────────────────────────────────────────────
function _currencyFromTicker(ticker) {
  // 한국 주식: .KS(코스피) 또는 .KQ(코스닥)
  return /\.(KS|KQ)$/i.test(ticker) ? 'KRW' : 'USD';
}

function _updateAddCurrencyLabel(cur) {
  document.getElementById('addCurrencyLabel').textContent = `(${cur})`;
}

// 통화에 따라 매입가 스텝/십진수 정책 결정
function _addModalCurrency() {
  return document.getElementById('addCurrency').value || 'USD';
}

// 스테퍼 버튼(▲/▼) — 통화별 step 적용
function _stepAddNum(inputId, direction) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const isKRW = _addModalCurrency() === 'KRW';
  let step = 1;
  if (inputId === 'addPrice') step = isKRW ? 100 : 1;
  else if (inputId === 'addQty') step = 1;

  let cur = parseFloat(input.value);
  if (isNaN(cur)) cur = 0;
  let next = cur + direction * step;
  if (next < 0) next = 0;

  // KRW 매입가 / 수량 → 정수 강제
  if ((inputId === 'addPrice' && isKRW) || inputId === 'addQty') {
    next = Math.round(next);
  } else {
    // USD 매입가: 소수점 2자리까지
    next = Math.round(next * 100) / 100;
  }
  input.value = next;
}

// 가격 입력: 음수 차단, KRW 시 소수점 제거
function _onAddPriceInput(e) {
  const isKRW = _addModalCurrency() === 'KRW';
  let v = e.target.value;
  // 음수 부호 제거
  if (v.startsWith('-')) v = v.replace(/^-+/, '');
  // KRW: 소수점 이하 제거
  if (isKRW) v = v.replace(/[.,].*$/, '');
  if (v !== e.target.value) e.target.value = v;
}

// 수량: 음수 / 소수점 차단 (정수 주수)
function _onAddQtyInput(e) {
  let v = e.target.value;
  if (v.startsWith('-')) v = v.replace(/^-+/, '');
  v = v.replace(/[.,].*$/, '');
  if (v !== e.target.value) e.target.value = v;
}

// 키보드 화살표도 통화별 step 적용
function _onAddNumKeydown(e) {
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    _stepAddNum(e.target.id, 1);
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    _stepAddNum(e.target.id, -1);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const priceInput = document.getElementById('addPrice');
  const qtyInput   = document.getElementById('addQty');
  if (priceInput) {
    priceInput.addEventListener('input', _onAddPriceInput);
    priceInput.addEventListener('keydown', _onAddNumKeydown);
  }
  if (qtyInput) {
    qtyInput.addEventListener('input', _onAddQtyInput);
    qtyInput.addEventListener('keydown', _onAddNumKeydown);
  }
});

function openAddModal(ticker, name, currency) {
  if (!currentUser) { openLoginModal(); return; }
  const hasTickerFromCtx = !!ticker;
  document.getElementById('addTicker').value   = ticker || '';
  document.getElementById('addName').value     = name   || '';
  document.getElementById('addCurrency').value = currency || (ticker ? _currencyFromTicker(ticker) : 'USD');
  _updateAddCurrencyLabel(document.getElementById('addCurrency').value);
  document.getElementById('addModalDesc').textContent = hasTickerFromCtx
    ? `${name || ticker} 을(를) 포트폴리오에 추가합니다`
    : '종목명 또는 티커를 검색하세요';

  const tickerField = document.getElementById('addTickerField');
  const tickerInput = document.getElementById('addTickerInput');
  const badge       = document.getElementById('addTickerBadge');
  if (hasTickerFromCtx) {
    tickerField.style.display = 'none';
  } else {
    tickerField.style.display = '';
    tickerInput.value = '';
    if (badge) badge.textContent = '';
    hideAddAC();
  }

  document.getElementById('addPrice').value = '';
  document.getElementById('addQty').value   = '';
  document.getElementById('addModal').classList.remove('hidden');
  if (!hasTickerFromCtx) setTimeout(() => tickerInput.focus(), 50);
}
function closeAddModal(event) {
  if (!event || event.target === document.getElementById('addModal')) {
    document.getElementById('addModal').classList.add('hidden');
    hideAddAC();
  }
}
async function submitAddHolding() {
  const ticker   = document.getElementById('addTicker').value;
  const name     = document.getElementById('addName').value || ticker;
  const currency = document.getElementById('addCurrency').value || 'USD';
  let price      = parseFloat(document.getElementById('addPrice').value);
  let qty        = parseFloat(document.getElementById('addQty').value);
  if (!ticker) { alert('종목을 검색해서 선택해주세요'); return; }
  if (!price || !qty) { alert('매입가와 수량을 입력해주세요'); return; }
  if (price <= 0 || qty <= 0) { alert('매입가와 수량은 0보다 커야 합니다'); return; }
  // KRW 매입가는 정수로
  if (currency === 'KRW') price = Math.round(price);
  qty = Math.floor(qty);  // 수량은 정수
  const res = await fetch('/api/portfolio', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker, name, quantity: qty, purchase_price: price, currency }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || '추가 실패'); return; }
  closeAddModal();
  loadPortfolio();
  // 현재 분석창이 같은 종목이면 포지션 카드 새로고침
  if (currentAnalysisTicker === ticker) analyze();
}

// ── 로그인 모달 ──────────────────────────────────────────────────────────────
function openLoginModal() {
  document.getElementById('loginModal').classList.remove('hidden');
}
function closeLoginModal(event) {
  if (!event || event.target === document.getElementById('loginModal'))
    document.getElementById('loginModal').classList.add('hidden');
}

// ── 내 포지션 카드 렌더링 ─────────────────────────────────────────────────
// 내 포지션 보강: 금액·비중 줄, 목표가·손절가 시나리오 줄 (분석 응답의 값으로 계산, 추가 호출 없음)
function _positionExtra(position, analysis, fmtP) {
  const qty = Number(position.quantity) || 0;
  const buy = Number(position.purchase_price) || 0;
  const curP = Number(position.current_price) || 0;
  if (!qty || !buy) return '';
  const cost = buy * qty;
  const value = curP ? curP * qty : null;
  const pf = position.portfolio;
  const w = pf && pf.weight_pct != null ? `${pf.weight_pct.toFixed(1)}%` : '—';
  const wTitle = pf && pf.count > 1
    ? `보유 ${pf.count}종목 평가금 기준${pf.estimated ? ` · ${pf.estimated}종목은 최근 시세가 없어 매입가로 계산` : ''}`
    : '보유 종목이 이 종목 하나';
  const money = (v) => fmtP(Math.round(v), true);
  const signed = (amt, pct) => {
    const cls = amt > 0 ? 'up' : amt < 0 ? 'down' : '';
    const sign = amt > 0 ? '+' : amt < 0 ? '−' : '';
    return `<div class="pe-val ${cls}">${sign}${money(Math.abs(amt))} <span class="pe-sub">(${sign}${Math.abs(pct).toFixed(1)}%)</span></div>`;
  };
  let scenario = '';
  const tgt = analysis ? Number(analysis.target_price) : NaN;
  const stp = analysis ? Number(analysis.stop_loss) : NaN;
  // 유효: 목표가는 현재가 위, 손절가는 현재가 아래 (현재가가 없으면 매입가 기준)
  const ref = curP || buy;
  const okT = tgt > 0 && tgt > ref;
  const okS = stp > 0 && stp < ref && (!okT || stp < tgt);
  if (okT || okS) {
    const item = (label, px, ok) => ok
      ? `<div class="pe-item"><div class="pe-label">${label} ${fmtP(px)}</div>${signed((px - buy) * qty, (px / buy - 1) * 100)}</div>`
      : `<div class="pe-item"><div class="pe-label">${label}</div><div class="pe-val">—</div></div>`;
    scenario = `
      <div class="pe-row scenario" aria-label="목표가·손절가 시나리오">
        ${item('목표가 도달', tgt, okT)}
        ${item('손절가 이탈', stp, okS)}
      </div>`;
  }
  return `
      <div class="position-extra">
        <div class="pe-row" aria-label="금액과 비중">
          <div class="pe-item"><div class="pe-label">총 매입금</div><div class="pe-val">${money(cost)}</div></div>
          <div class="pe-item"><div class="pe-label">현재 평가금</div><div class="pe-val">${value != null ? money(value) : '—'}</div></div>
          <div class="pe-item" title="${wTitle}"><div class="pe-label">포트폴리오 비중</div><div class="pe-val">${w}</div></div>
        </div>${scenario}
      </div>`;
}

// ── 이 종목 AI 봇 현황 (보유 중이거나 최근 7일 신호가 있는 봇만, 없으면 카드 자체를 숨김) ──
let _stockBotsReq = null;   // { ticker, promise }
async function renderStockBots(stock) {
  const card = document.getElementById('botStatusCard');
  const list = document.getElementById('botStatusList');
  if (!card || !list || !stock || !stock.ticker) return;
  const ticker = stock.ticker;
  if (!_stockBotsReq || _stockBotsReq.ticker !== ticker) {
    _stockBotsReq = { ticker, promise: fetch(`/api/stock-bots/${encodeURIComponent(ticker)}`).then(r => r.ok ? r.json() : null).catch(() => null) };
  }
  const data = await _stockBotsReq.promise;
  if (currentAnalysisTicker !== ticker) return;
  const bots = (data && data.bots) || [];
  if (!bots.length) { card.classList.add('hidden'); list.innerHTML = ''; return; }
  const cur = stock.currency || (/\.(KS|KQ)$/i.test(ticker) ? 'KRW' : 'USD');
  const fmt = (v) => v == null ? '—' : cur === 'KRW'
    ? Math.round(v).toLocaleString('ko-KR') + '원'
    : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const price = Number(stock.current_price) || null;
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const DONE = { TARGET_HIT: '익절 청산', STOP_LOSS: '손절 청산', TRAILING_STOP: '추적 손절 청산', TIME_STOP: '기간 만료 청산',
                 TREND_EXIT: '추세 이탈 청산', AI_RISK_EXIT: '위험 감지 청산', CANCELLED: '신호 취소' };
  list.innerHTML = bots.map((b) => {
    const sg = b.signal || {};
    let state, cls;
    if (b.holding) {
      const entry = Number(b.holding.entry_price) || 0;
      const r = entry && price ? (price / entry - 1) * 100 : null;
      cls = r == null ? 'flat' : r > 0 ? 'up' : r < 0 ? 'down' : 'flat';
      state = r == null ? '보유 중' : `보유 중 ${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(1)}%`;
    } else if (sg.status === 'PENDING' || sg.status === 'ACTIVE') {
      cls = 'sig'; state = sg.status === 'PENDING' ? '최근 매수 신호 · 체결 대기' : '최근 매수 신호';
    } else {
      cls = 'muted';
      const pnl = sg.pnl_pct != null ? ` ${sg.pnl_pct > 0 ? '+' : sg.pnl_pct < 0 ? '−' : ''}${Math.abs(sg.pnl_pct).toFixed(1)}%` : '';
      state = `최근 신호 · ${DONE[sg.status] || '종료'}${sg.status === 'CANCELLED' ? '' : pnl}`;
    }
    const lv = [];
    if (sg.target_price) lv.push(`목표 <b>${fmt(sg.target_price)}</b>`);
    if (sg.stop_price) lv.push(`손절 <b>${fmt(sg.stop_price)}</b>`);
    if (!b.holding && sg.signal_date) lv.push(`신호일 ${esc(sg.signal_date.slice(5).replace('-', '/'))}`);
    return `
      <div class="bs-row" style="--bs-color:${esc(b.color || 'var(--accent)')}">
        <div class="bs-main"><span class="bs-badge">${esc(b.emoji || '🤖')} ${esc(b.name)}</span><span class="bs-state ${cls}">${state}</span></div>
        ${lv.length ? `<div class="bs-levels">${lv.join(' · ')}</div>` : ''}
        <a class="bs-link" href="/ai-bots#${encodeURIComponent(b.key)}" aria-label="${esc(b.name)} AI 봇 보기">AI 봇 보기 →</a>
      </div>`;
  }).join('');
  card.classList.remove('hidden');
}

// ── Row 1 높이 맞추기: 차트 카드가 우측(종합 판단 + 주요 지표)과 같은 높이가 되도록 메인 차트 높이를 조정 ──
let _fitRaf = 0;
function _fitRow1() {
  cancelAnimationFrame(_fitRaf);
  _fitRaf = requestAnimationFrame(() => {
    const side = document.querySelector('.col-side');
    if (side && window.SAChart && SAChart.fitTo) SAChart.fitTo(side.getBoundingClientRect().height);
  });
}
addEventListener('resize', () => setTimeout(_fitRow1, 200));   // chart.js 의 리사이즈(150ms) 뒤에
if (window.ResizeObserver) {
  document.addEventListener('DOMContentLoaded', () => {
    const ro = new ResizeObserver(_fitRow1);
    document.querySelectorAll('.col-side > .card').forEach((c) => ro.observe(c));
  });
}

function renderPositionCard(position, stock) {
  const el = document.getElementById('positionContent');
  const manage = document.getElementById('posManageLink');
  if (manage) manage.classList.toggle('hidden', !currentUser);
  currentPositionData = position;

  // 로그인 안 됨
  if (!currentUser) {
    el.innerHTML = `
      <div class="position-empty">
        <div class="position-empty-icon">🔐</div>
        <div class="position-empty-msg">로그인하면 보유 주식 기준<br>매매 타이밍을 분석해 드려요</div>
        <button class="position-login-btn" onclick="openLoginModal()">로그인하기</button>
      </div>`;
    return;
  }

  // 보유 없음 — 진입 추천 정보를 함께 노출
  if (!position) {
    const ticker = currentAnalysisTicker;
    const name   = stock ? stock.name : ticker;
    const cur    = (stock && stock.currency) || 'USD';
    const price  = stock ? (stock.current_price ?? stock.price) : null;
    const fmtP_ = (v) => v == null ? '—' : cur === 'KRW'
      ? Math.round(v / 10) * 10 + '원'
      : '$' + Number(v).toFixed(2);
    // 최근 analysis 가 있으면 진입 추천가 노출 (윈도우 변수에서 가져오거나 stock 안에서)
    const analysis = (window._lastAnalysis) || null;
    let recBlock = '';
    if (analysis && analysis.entry_price != null) {
      const rec = analysis.trade_recommendation;
      const recIcon = { strong: '🔥', neutral: '⚖️', avoid: '⚠️' }[rec] || '⚖️';
      const recText = { strong: '진입 추천', neutral: '풀백 대기', avoid: '진입 비추천' }[rec] || '';
      recBlock = `
        <div class="position-empty-rec rec-${rec || 'neutral'}">
          <span class="rec-mini-icon">${recIcon}</span>
          <span class="rec-mini-text">${recText}</span>
          ${rec !== 'avoid' ? `<span class="rec-mini-price">진입가 ${fmtP_(analysis.entry_price)}</span>` : ''}
        </div>`;
    }
    el.innerHTML = `
      <div class="position-empty">
        ${price != null ? `<div class="position-empty-price">${fmtP_(price)}</div>` : ''}
        ${recBlock}
        <div class="position-empty-msg">이 종목을 보유하고 계신가요?</div>
        <button class="position-add-btn" onclick="openAddModal('${ticker}','${name}','${cur}')">+ 포트폴리오에 추가</button>
      </div>`;
    return;
  }

  // 보유 중 — 비주얼 강조된 카드
  const cur = position.currency || 'USD';
  const fmtP = (v, noRound = false) => v == null ? '—' : cur === 'KRW'
    ? (noRound ? Math.round(Number(v)) : Math.round(v / 10) * 10).toLocaleString('ko-KR') + '원'
    : '$' + Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const rec      = position.recommendation;
  const pct      = rec ? rec.return_pct : null;
  const pctStr   = pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—';
  const pctClass = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
  const pctIcon  = pct > 0 ? '▲' : pct < 0 ? '▼' : '';

  // 손익 금액 계산
  const profitAmt = (position.current_price && position.purchase_price)
    ? (position.current_price - position.purchase_price) * position.quantity
    : null;
  // 부호는 통화 기호 앞에: -$381.00 (예전 '$-381.00')
  const profitStr = profitAmt != null
    ? `${profitAmt >= 0 ? '+' : '-'}${fmtP(Math.abs(cur === 'KRW' ? Math.round(profitAmt) : profitAmt))}`
    : '';

  // 매입→현재→목표 진행률 (analysis 있으면)
  const analysis = window._lastAnalysis || null;
  let progressBar = '';
  if (analysis && analysis.target_price && position.purchase_price && position.current_price) {
    const min  = Math.min(position.purchase_price, analysis.stop_loss || position.purchase_price);
    const max  = Math.max(analysis.target_price, position.current_price);
    const span = max - min || 1;
    const curPct = Math.min(100, Math.max(0, (position.current_price - min) / span * 100));
    const tgtPct = Math.min(100, Math.max(0, (analysis.target_price - min) / span * 100));
    const buyPct = Math.min(100, Math.max(0, (position.purchase_price - min) / span * 100));
    progressBar = `
      <div class="position-progress">
        <div class="pp-track">
          <div class="pp-fill" style="width:${curPct}%"></div>
          <div class="pp-marker pp-buy"     style="left:${buyPct}%" title="매입가 ${fmtP(position.purchase_price)}">매입</div>
          <div class="pp-marker pp-current" style="left:${curPct}%" title="현재가 ${fmtP(position.current_price)}">현재</div>
          <div class="pp-marker pp-target"  style="left:${tgtPct}%" title="목표가 ${fmtP(analysis.target_price)}">목표</div>
        </div>
      </div>`;
  }

  el.innerHTML = `
    <div class="position-holding">
      <!-- 큰 손익 헤드 -->
      <div class="position-head pct-${pctClass}">
        <div class="ph-pct">${pctIcon} ${pctStr === '—' ? '—' : pctStr.replace(/^[+\-]/, '')}</div>
        <div class="ph-amt">${profitStr}</div>
      </div>

      ${progressBar}

      <div class="position-grid">
        <div class="pg-item"><div class="pg-label">보유</div><div class="pg-val">${position.quantity}주</div></div>
        <div class="pg-item"><div class="pg-label">매입</div><div class="pg-val">${fmtP(position.purchase_price, true)}</div></div>
        <div class="pg-item"><div class="pg-label">현재</div><div class="pg-val">${fmtP(position.current_price)}</div></div>
      </div>

      ${_positionExtra(position, analysis, fmtP)}

      ${rec ? `
      <div class="position-rec ${rec.color}">
        <div class="position-rec-action">${rec.action}</div>
        <div class="position-rec-reason">${rec.reason}</div>
      </div>` : ''}
    </div>`;

}

function quickSearch(ticker) {
  document.getElementById('tickerInput').value = ticker;
  analyze();
}

// 종합 분석(/api/analyze)은 일·주·월봉 기준. 10분·1시간봉은 차트(/api/chart)만 바꾼다
function _isIntradayInterval(iv) { return iv === '10m' || iv === '1h'; }
function _analyzeInterval() { return _isIntradayInterval(currentInterval) ? '1d' : currentInterval; }

let _chartReqSeq = 0;
async function setChartInterval(interval) {
  currentInterval = interval;
  const seq = ++_chartReqSeq;            // 빠르게 연속으로 누르면 마지막 요청 결과만 그린다
  document.querySelectorAll('.interval-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.interval === interval);
  });

  const ticker = _inputTicker();
  if (!ticker || document.getElementById('result').classList.contains('hidden')) return;

  // 차트 영역만 로딩 표시
  const chartEl = document.getElementById('mainChart');
  chartEl.style.opacity = '0.4';

  try {
    const res = await fetch(`/api/chart?ticker=${encodeURIComponent(ticker)}&interval=${interval}`);
    const data = await res.json();
    if (seq !== _chartReqSeq) return;
    if (!res.ok) { showError(data.error || '차트 로딩 실패'); return; }

    chartData = data.chart;
    document.getElementById('intervalBadge').textContent = data.interval_label;
    renderCharts(data.chart);
  } catch (e) {
    showError('차트 로딩 오류: ' + e.message);
  } finally {
    if (seq === _chartReqSeq) chartEl.style.opacity = '1';
  }
}

// 한글 등 종목명이 입력되면 검색 API 로 티커를 찾는다 (정확히 같은 이름 우선, 없으면 첫 결과)
async function _resolveName(q) {
  const pick = (items) => {
    if (!items || !items.length) return null;
    const exact = items.find(it => (it.name || '').replace(/\s/g, '') === q.replace(/\s/g, ''));
    return (exact || items[0]).symbol;
  };
  const fromAC = pick(typeof acItems !== 'undefined' ? acItems : null);
  if (fromAC) return fromAC;
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    return pick(await r.json());
  } catch { return null; }
}

async function analyze() {
  let ticker = _inputTicker();
  if (ticker && /[^\x00-\x7F]/.test(ticker)) {          // 티커가 아닌 이름(한글 등)
    const sym = await _resolveName(ticker);
    if (!sym) { showError(`'${ticker}'에 해당하는 종목을 찾지 못했어요. 자동완성 목록에서 골라 주세요.`); return; }
    ticker = sym;
  }
  const period = 'max';
  if (!ticker) return;

  // 홈(시황 대시보드)에서는 인라인 분석 대신 전용 페이지로 이동.
  // 검색·빠른선택·추세카드 등 모든 진입점이 analyze()를 거치므로 여기 한 곳이면 충분.
  if (!PAGE_TICKER) { gotoStock(ticker); return; }
  // 분석 페이지에서 다른 종목 검색 → URL도 그 종목으로
  if (ticker.toUpperCase() !== PAGE_TICKER) { gotoStock(ticker); return; }

  stopStockPricePolling();   // 이전 종목 현재가 폴링 중지
  document.getElementById('loading').classList.remove('hidden');
  document.getElementById('result').classList.add('hidden');
  document.getElementById('errorBox').classList.add('hidden');

  try {
    // 90초 안에 응답이 없으면 끊고 재시도 버튼을 보여준다 (서버 timeout 120초보다 짧게)
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 90000);
    let res, data;
    try {
      res = await fetch(`/api/analyze?ticker=${encodeURIComponent(ticker)}&period=${period}&interval=${_analyzeInterval()}`,
                        { signal: ctrl.signal });
      data = await res.json().catch(() => ({}));
    } catch (err) {
      const timedOut = err && err.name === 'AbortError';
      showError(timedOut ? '분석 응답이 너무 오래 걸려요. 잠시 후 다시 시도해 주세요.'
                         : '서버에 연결하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.',
                { retry: analyze });
      return;
    } finally {
      clearTimeout(timer);
    }

    if (!res.ok) {
      // 5xx(일시 오류)만 재시도 버튼, 4xx(없는 종목 등)는 안내만
      showError(data.error || '알 수 없는 오류', res.status >= 500 ? { retry: analyze } : {});
      return;
    }

    currentAnalysisTicker = data.stock.ticker;
    _showInputName(data.stock.ticker, data.stock.name);
    chartData = data.chart;
    window._lastAnalysis = data.analysis;     // 포지션 카드에서 참조용
    window._lastStock    = data.stock;        // 모의투자 매수/매도에서 참조용
    renderStockInfo(data.stock);
    renderVerdict(data.analysis, data.stock);
    renderDetail(data.analysis.details || [], data.analysis);
    renderFundamental(data.analysis.fundamental_details || [], data.stock);
    renderScorecard(data.analysis.scorecard, data.stock);
    renderMetrics(data.stock, data.analysis);
    renderPositionCard(data.position || null, data.stock);
    renderStockBots(data.stock);
    renderMarketContext(data.stock.market_context, data.stock.currency === 'KRW', data.stock.market_mood);
    renderUpcomingEvents(data.stock.upcoming_events);
    renderStrategy(data.analysis, data.stock);
    if (data.extras_pending) {
      // 뉴스·애널리스트·AI 해설은 /api/analyze/extra 로 나중에 채운다 (첫 화면을 붙잡지 않게)
      _showExtrasLoading();
      loadAnalyzeExtras(data.stock.ticker, data);
    } else {
      renderAnalysts(data.analysts, data.stock);
      renderPriceMoveBanner(data.stock, data.move_reason);
      renderNews(data.news);
    }

    // 봉 뱃지 업데이트
    document.getElementById('intervalBadge').textContent = data.interval_label || '일봉';

    // result를 먼저 보여준 뒤 레이아웃 계산 후 차트 렌더링
    document.getElementById('result').classList.remove('hidden');
    requestAnimationFrame(() => setTimeout(() => {
      renderCharts(data.chart);
      // 분봉·시간봉 보는 중 다시 분석(예: 모의 매수 후)하면 종합 분석은 일봉 기준이므로 차트만 다시 분봉으로
      if (_isIntradayInterval(currentInterval)) setChartInterval(currentInterval);
    }, 50));
  } catch (e) {
    showError('서버 연결 오류: ' + e.message);
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}

// ── 부가 섹션 비동기 로딩 (뉴스·애널리스트·AI 기업 소개·급등락 사유) ──
const _SKELETON = '<div class="sk-wrap"><div class="sk-line"></div><div class="sk-line"></div><div class="sk-line short"></div></div>';

function _showExtrasLoading() {
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set('analystConsensus', _SKELETON);
  set('analystTargets', '');
  set('newsList', _SKELETON);
  document.getElementById('priceMoveBanner')?.classList.add('hidden');
  const db = document.getElementById('discBlock');
  if (db) { db.classList.add('hidden'); db.innerHTML = ''; }
  const title = document.getElementById('analystCardTitle');
  if (title) title.textContent = '애널리스트 의견';
}

function _showExtrasError(ticker, data) {
  const retry = `<button type="button" class="extras-retry-btn" onclick="_retryExtras()">다시 시도</button>`;
  const msg = (what) => `<p class="no-data">${what} 불러오지 못했어요. ${retry}</p>`;
  const a = document.getElementById('analystConsensus'); if (a) a.innerHTML = msg('애널리스트 의견을');
  const n = document.getElementById('newsList'); if (n) n.innerHTML = msg('뉴스를');
  window._extrasRetry = () => { _showExtrasLoading(); loadAnalyzeExtras(ticker, data); };
}
function _retryExtras() { if (window._extrasRetry) window._extrasRetry(); }

// AI(번역·기업 소개·등락 사유)가 동작하지 않을 때 이유를 짧게 알려준다 (키 값·원문 오류는 서버가 보내지 않음)
function _renderAiStatus(st) {
  const news = document.getElementById('newsList');
  if (!news) return;
  let el = document.getElementById('aiStatusNote');
  if (!el) {
    el = document.createElement('p');
    el.id = 'aiStatusNote';
    el.className = 'ai-status-note';
    news.parentNode.insertBefore(el, news);
  }
  const reason = {
    no_key:  '서버에 AI 키가 설정되지 않았어요',
    auth:    'AI 키 인증에 실패했어요 (키 만료·오류)',
    quota:   'AI 무료 사용 한도를 넘었어요',
    model:   '사용할 수 있는 AI 모델을 찾지 못했어요',
    network: 'AI 서버에 연결하지 못했어요',
    unknown: 'AI 호출이 실패했어요',
  }[st && st.state];
  if (!reason) { el.textContent = ''; el.classList.add('hidden'); return; }
  el.textContent = `AI 번역·해설을 쓰지 못하고 있어요 — ${reason}. 원문과 기본 정보로 보여드려요.`;
  el.classList.remove('hidden');
}

// ── 최근 주요 공시 (공시·수주 레이더 DB, 30일 · 최대 5건). 1건 이상일 때만 뉴스 위에 보인다 ──
const _DISC_LABEL = { contract: '수주·계약', buyback: '자사주·소각', insider_buy: '내부자 매수', offering: '유상증자·주의', material_8k: '8-K 주요사항' };
async function loadDisclosures(ticker) {
  const el = document.getElementById('discBlock');
  if (!el) return;
  let items = [];
  try {
    const r = await fetch(`/api/disclosures?ticker=${encodeURIComponent(ticker)}&days=30&limit=5`);
    if (r.ok) items = (await r.json()).items || [];
  } catch (e) { items = []; }
  if (currentAnalysisTicker !== ticker) return;
  renderDisclosures(items);
}
function renderDisclosures(items) {
  const el = document.getElementById('discBlock');
  if (!el) return;
  if (!items || !items.length) { el.classList.add('hidden'); el.innerHTML = ''; return; }   // 0건이면 블록 자체를 숨김
  const esc = escapeHtmlMain;
  const safeUrl = (u) => (/^https:\/\//i.test(u || '') ? esc(u) : '');
  const day = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : `${d.getMonth() + 1}/${String(d.getDate()).padStart(2, '0')}`; };
  el.innerHTML = `<div class="disc-head"><span>최근 주요 공시</span><span class="disc-sub">30일 · ${items.length}건</span></div>
    <ul class="disc-list">${items.map((d) => {
      const tone = d.sentiment === 'positive' ? 'pos' : d.sentiment === 'negative' ? 'neg' : 'neu';
      const label = d.category === 'contract' && d.sentiment === 'negative' ? '계약 해지' : (_DISC_LABEL[d.category] || '공시');
      const url = safeUrl(d.source_url);
      return `<li class="disc-item">
        <span class="disc-badge ${tone}">${esc(label)}</span>
        <div class="disc-main">
          <div class="disc-title">${esc(d.title || '')}</div>
          ${d.summary ? `<div class="disc-sum">${esc(d.summary)}</div>` : ''}
        </div>
        <div class="disc-side">
          <time datetime="${esc(d.disclosed_at || '')}">${esc(day(d.disclosed_at))}</time>
          ${url ? `<a class="disc-src" href="${url}" target="_blank" rel="noopener noreferrer" aria-label="${esc(d.source || '')} 공시 원문 (새 창)">원문 ↗</a>` : ''}
        </div>
      </li>`;
    }).join('')}</ul>`;
  el.classList.remove('hidden');
}

async function loadAnalyzeExtras(ticker, data, refill = 0) {
  if (!refill) loadDisclosures(ticker);              // DB 조회 1번 — 느린 뉴스·AI 를 기다리지 않고 바로
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  try {
    const res = await fetch(`/api/analyze/extra?ticker=${encodeURIComponent(ticker)}&interval=${_analyzeInterval()}`,
                            { signal: ctrl.signal });
    const ex = await res.json();
    if (currentAnalysisTicker !== ticker) return;      // 그 사이 다른 종목으로 이동
    if (!res.ok) throw new Error(ex.error || res.status);
    renderAnalysts(ex.analysts, data.stock);
    renderNews(ex.news);
    // AI 결과를 아직 기다리는 중(ai_pending)이면 실패 배너를 띄우지 않는다 → 다 받은 뒤에만 판단
    _renderAiStatus(ex.ai_pending ? null : ex.ai_status);
    if (ex.valuation_comment) renderDcfComment(ex.valuation_comment);
    renderPriceMoveBanner(data.stock, ex.move_reason);
    if (ex.company_overview) {
      // AI 기업 소개가 오면 투자 판단 탭의 회사 소개만 다시 그린다
      data.stock.company_overview = ex.company_overview;
      renderFundamental(data.analysis.fundamental_details || [], data.stock);
    }
    // 서버 시간 예산(10초) 안에 AI 가 다 못 끝냈으면, 서버가 백그라운드로 마저 만든 결과를 받아 채운다 (최대 2번)
    if (ex.partial && refill < 2) {
      setTimeout(() => { if (currentAnalysisTicker === ticker) loadAnalyzeExtras(ticker, data, refill + 1); }, 15000);
    }
  } catch (e) {
    // 보충 요청 실패는 조용히 무시 (이미 1차 결과가 화면에 있음)
    if (!refill && currentAnalysisTicker === ticker) _showExtrasError(ticker, data);
  } finally {
    clearTimeout(timer);
  }
}

function showError(msg, opts = {}) {
  const box = document.getElementById('errorBox');
  box.textContent = '⚠️ ' + msg;
  if (opts.retry) {
    // 일시적인 오류(시간 초과·서버 오류)는 같은 자리에서 다시 시도할 수 있게
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'error-retry-btn';
    btn.textContent = '다시 시도';
    btn.onclick = () => { box.classList.add('hidden'); opts.retry(); };
    box.appendChild(btn);
  }
  box.classList.remove('hidden');
  document.getElementById('loading').classList.add('hidden');
}

function fmt(num, currency = 'USD', noRound = false) {
  if (num == null) return '—';
  if (currency === 'KRW') {
    const v = noRound ? num : Math.round(num / 10) * 10;
    return v.toLocaleString('ko-KR') + '원';
  }
  return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// yfinance 영문 섹터/거래소 코드 → 화면 표시용 한글
const SECTOR_KO = {
  'Technology': '기술', 'Financial Services': '금융', 'Healthcare': '헬스케어',
  'Consumer Cyclical': '경기소비재', 'Consumer Defensive': '필수소비재',
  'Communication Services': '커뮤니케이션', 'Industrials': '산업재', 'Energy': '에너지',
  'Basic Materials': '소재', 'Real Estate': '부동산', 'Utilities': '유틸리티',
};
const EXCHANGE_KO = { 'KSC': 'KOSPI', 'KOE': 'KOSDAQ', 'NMS': 'NASDAQ', 'NGM': 'NASDAQ', 'NCM': 'NASDAQ', 'NYQ': 'NYSE' };
const sectorKo   = (s) => (s && SECTOR_KO[s]) || s || '';
const exchangeKo = (e) => (e && EXCHANGE_KO[e]) || e || '';

function renderStockInfo(stock) {
  document.getElementById('stockName').textContent = stock.name;
  const _lgSlot = document.getElementById('stockLogo');
  if (_lgSlot) _lgSlot.innerHTML = window.renderStockLogo ? renderStockLogo(stock.ticker, stock.name, 'lg') : '';
  document.getElementById('stockTicker').textContent = stock.ticker;
  if (document.getElementById('notifyBell') && window.SAWatch) SAWatch.bindStar(document.getElementById('watchStar'), stock.ticker, stock.name);
  if (document.getElementById('notifyBell') && window.SAAlerts) SAAlerts.bindBell(document.getElementById('alertBell'), stock);
  document.getElementById('stockSector').textContent = sectorKo(stock.sector);
  document.getElementById('stockExchange').textContent = stock.exchange ? `· ${exchangeKo(stock.exchange)}` : '';

  const cur = stock.currency;
  document.getElementById('currentPrice').textContent = fmt(stock.current_price, cur);

  const changeEl = document.getElementById('priceChange');
  if (stock.price_change != null) {
    const sign = stock.price_change >= 0 ? '+' : '';
    changeEl.innerHTML = `<span class="price-change-label">어제보다</span> ${sign}${fmt(stock.price_change, cur)} (${fmtPct(stock.price_change_pct)})`;
    changeEl.className = 'price-change ' + (stock.price_change >= 0 ? 'up' : 'down');
  }

  // ── 가격 신선도 표시: LIVE 뱃지 / 장 마감 / 데이터 시각 ──
  const freshEl = document.getElementById('priceFreshness');
  if (freshEl) {
    const srcLabel = stock.realtime_source === 'toss'
      ? '토스 실시간'
      : stock.realtime_source === 'naver'
        ? '네이버 ~1분'
        : stock.realtime_source === 'yfinance'
          ? 'Yahoo 15~20분'
          : '';
    if (stock.is_realtime) {
      freshEl.innerHTML =
        `<span class="live-dot"></span> <span class="live-label">LIVE</span>` +
        (srcLabel ? ` · <span class="src-tag">${srcLabel}</span>` : '');
      freshEl.className = 'price-freshness live';
    } else if (stock.is_market_open) {
      freshEl.innerHTML = `🕐 장중 (지연 시세)` + (srcLabel ? ` · ${srcLabel}` : '');
      freshEl.className = 'price-freshness delayed';
    } else {
      const tsLabel = _formatBarTimestamp(stock.data_timestamp);
      freshEl.innerHTML = `🔒 장 마감 · ${tsLabel} 기준`;
      freshEl.className = 'price-freshness closed';
    }
    // 시간외(프리·애프터마켓) 체결가가 반영돼 일봉 정규장 종가와 다를 때만: '시간외 포함' 배지 + 정규장 종가
    if (stock.is_extended_hours_price && stock.regular_close) {
      const rc = stock.currency === 'KRW'
        ? Math.round(stock.regular_close).toLocaleString('ko-KR') + '원'
        : '$' + Number(stock.regular_close).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      freshEl.insertAdjacentHTML('beforeend',
        `<span class="ext-hours" title="현재가는 정규장 이후(또는 이전) 시간외 체결가를 반영했습니다. 차트 일봉은 정규장 종가 기준이라 다를 수 있어요.">` +
        `<span class="ext-badge">시간외 포함</span><span class="ext-close">정규장 종가 ${rc}</span></span>`);
    }
  }

  // 장중이면 현재가·등락을 1초마다 실시간 갱신
  startStockPricePolling(stock);
}

// ── 분석 페이지 헤더 현재가 실시간 폴링 ──
let _stockPriceTimer = null;
let _stockPrevClose = null;
function stopStockPricePolling() {
  if (_stockPriceTimer) { clearInterval(_stockPriceTimer); _stockPriceTimer = null; }
}
function startStockPricePolling(stock) {
  stopStockPricePolling();
  if (!stock || !stock.ticker || !stock.is_market_open) return;  // 장 마감 종목은 갱신 불필요
  const cur = stock.currency;
  // 전일 종가 baseline (등락 재계산용) = 분석 시점 현재가 − 등락
  _stockPrevClose = (stock.current_price != null && stock.price_change != null)
    ? stock.current_price - stock.price_change : null;
  const apply = async () => {
    try {
      const r = await fetch(`/api/realtime-price?ticker=${encodeURIComponent(stock.ticker)}`);
      const d = await r.json();
      if (!d || !d.price) return;
      const priceEl = document.getElementById('currentPrice');
      if (priceEl) priceEl.textContent = fmt(d.price, cur);
      if (_stockPrevClose != null && _stockPrevClose > 0) {
        const change = d.price - _stockPrevClose;
        const pct = change / _stockPrevClose * 100;
        const changeEl = document.getElementById('priceChange');
        if (changeEl) {
          const sign = change >= 0 ? '+' : '';
          changeEl.innerHTML = `<span class="price-change-label">어제보다</span> ${sign}${fmt(change, cur)} (${sign}${pct.toFixed(2)}%)`;
          changeEl.className = 'price-change ' + (change >= 0 ? 'up' : 'down');
        }
      }
      if (window._lastStock) window._lastStock.current_price = d.price;  // 모의매수 모달 초기값에 반영
    } catch (e) { /* 무시 */ }
  };
  apply();
  _stockPriceTimer = setInterval(apply, 1000);
}

// 봉 timestamp(ISO)를 "5/21 (수)" 또는 "2026-05-21" 형식으로
function _formatBarTimestamp(iso) {
  if (!iso) return '데이터 없음';
  // 일봉 시각은 시장 현지 자정(예: 2026-11-20T00:00:00+09:00) → 브라우저 시간대로 바꾸면 하루 밀리므로 문자열의 날짜를 그대로 쓴다
  const m_ = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (m_) {
    const dow = ['일','월','화','수','목','금','토'][new Date(Date.UTC(+m_[1], +m_[2] - 1, +m_[3])).getUTCDay()];
    return `${+m_[2]}/${+m_[3]} (${dow})`;
  }
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso.slice(0, 10);
    const m = d.getMonth() + 1, day = d.getDate();
    const dow = ['일','월','화','수','목','금','토'][d.getDay()];
    return `${m}/${day} (${dow})`;
  } catch (e) { return iso.slice(0, 10); }
}

// 종합 판단 카드의 '모의 매수' → /trading?buy=티커[&target&stop]. 관망이거나 값이 맞지 않으면 목표·손절은 넘기지 않는다
function _setVerdictPaperBuy(analysis, stock) {
  const a = document.getElementById('verdictPaperBuy');
  if (!a || !stock || !stock.ticker) return;
  const q = new URLSearchParams({ buy: stock.ticker });
  const krw = stock.currency === 'KRW';
  const r = (v) => krw ? String(Math.round(v)) : Number(v).toFixed(2);
  const cp = Number(stock.current_price || stock.price || 0);
  const t = Number(analysis && analysis.target_price), s = Number(analysis && analysis.stop_loss);
  const avoid = !analysis || analysis.trade_recommendation === 'avoid';
  let withPlan = false;
  if (!avoid && cp > 0 && isFinite(t) && isFinite(s) && t > cp && s > 0 && s < cp) {
    q.set('target', r(t)); q.set('stop', r(s));
    q.set('memo', `[종합 분석 ${analysis.verdict || ''}] 목표가·손절가 자동 예약`.replace(/\s+\]/, ']'));
    withPlan = true;
  }
  a.href = '/trading?' + q.toString();
  a.classList.toggle('muted', avoid);
  document.getElementById('verdictPaperBuySub').textContent = withPlan ? '목표·손절 자동 채움' : '';
  a.title = withPlan ? '분석의 목표가·손절가를 채운 모의 매수 창을 엽니다' : '모의 매수 창을 엽니다';
  a.classList.remove('hidden');
}

// 종합 판단 근거 1줄: 기술 N점 (강세/중립/약세) · 펀더멘털 N점 (등급)
function _renderScoreBreakdown(analysis) {
  const el = document.getElementById('scoreBreakdown');
  if (!el) return;
  const b = analysis && analysis.score_breakdown;
  if (!b || !b.tech) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  const t = b.tech, f = b.fund, m = b.macro;
  const fBand = f ? (f.score >= 70 ? 'bull' : f.score >= 45 ? 'mid' : 'bear') : 'na';
  const w = b.weights || {};
  el.innerHTML = `
    <div class="sb-item sb-${t.tone}" title="하단 기술적 분석 게이지와 같은 값 · 비중 ${Math.round((w.tech || 0) * 100)}%">
      <span class="sb-k">기술</span><b>${t.score}점</b><span class="sb-tag">${t.label}</span></div>
    <div class="sb-item sb-${fBand}" title="투자 판단 4팩터 종합 · 비중 ${Math.round((w.fund || 0) * 100)}%">
      <span class="sb-k">펀더멘탈</span>${f ? `<b>${f.score}점</b><span class="sb-tag">${f.grade || '—'}</span>` : '<b class="sb-na">자료 없음</b>'}</div>
    <div class="sb-item sb-macro sb-${m ? (m.score > 0 ? 'bull' : m.score < 0 ? 'bear' : 'neu') : 'neu'}" title="${m ? _esc(m.summary || '') : '매크로 자료 없음'} (종합 점수에 ±6 범위로 더함)">
      <span class="sb-k">매크로</span><b>${m ? (m.score > 0 ? '+' : m.score < 0 ? '' : '±') + m.score : '±0'}</b><span class="sb-tag">${m ? _esc(m.label || '중립') : '없음'}</span></div>`;
  el.classList.remove('hidden');
}

function renderVerdict(analysis, stock) {
  const badge = document.getElementById('verdictBadge');
  badge.textContent = analysis.verdict;
  badge.className = `verdict-badge ${analysis.verdict_color}`;

  // 점수 마커 위치 (score: -100~+100 → 0~100%)
  const pct = (analysis.score + 100) / 2;
  document.getElementById('scoreMarker').style.left = `${pct}%`;

  const cur = stock.currency;
  const _noEntry = analysis.trade_recommendation === 'avoid';
  document.getElementById('entryPrice').textContent  =
    (_noEntry && analysis.entry_price == null)  ? '관망' : fmt(analysis.entry_price,  cur);
  document.getElementById('targetPrice').textContent =
    (_noEntry && analysis.target_price == null) ? '관망' : fmt(analysis.target_price, cur);
  document.getElementById('stopLoss').textContent    = fmt(analysis.stop_loss,    cur);
  // 현재가 대비 거리
  const _cp = Number(stock.current_price ?? stock.price);
  const _dist = (v) => (v != null && _cp > 0 ? `${v >= _cp ? '+' : ''}${((v / _cp - 1) * 100).toFixed(1)}%` : '');
  const _sd = document.getElementById('stopDist'), _td = document.getElementById('targetDist');
  if (_sd) { _sd.textContent = _dist(analysis.stop_loss); _sd.title = '현재가 대비'; }
  if (_td) { _td.textContent = analysis.target_price != null ? _dist(analysis.target_price) : ''; _td.title = '현재가 대비'; }
  _renderScoreBreakdown(analysis);
  _setVerdictPaperBuy(analysis, stock);

  // ── Plan D: 추천 등급 + R:R 정보 표시 ──
  const banner = document.getElementById('tradeRecBanner');
  const meta   = document.getElementById('tradeMetaRow');
  const rec    = analysis.trade_recommendation;
  const ts     = analysis.trend_score;
  const rr     = analysis.risk_reward_ratio;
  const sdp    = analysis.stop_distance_pct;

  if (rec) {
    // 진입 신뢰도 기반 동적 설명
    const curPrice = stock && stock.price;
    const conf = analysis.entry_confidence; // immediate / wait / patient
    let diff = 0;
    if (analysis.entry_price != null && curPrice != null) {
      diff = (curPrice - analysis.entry_price) / curPrice * 100;
    }
    const confMap = {
      immediate: { txt: '즉시 진입 가능' },
      wait:      { txt: `약 ${diff.toFixed(1)}% 풀백까지 limit 주문` },
      patient:   { txt: `약 ${diff.toFixed(1)}% 풀백 대기 (시간 필요)` },
    };
    const cInfo = confMap[conf] || null;

    let strongDesc = '추세 강세';
    if (cInfo) strongDesc = cInfo.txt;

    let neutralDesc = 'MA50 또는 풀백까지 대기';
    if (conf === 'patient') neutralDesc = `풀백 대기 필요 (${diff.toFixed(1)}%)`;
    else if (conf === 'wait') neutralDesc = `${diff.toFixed(1)}% 풀백까지 limit`;
    else if (conf === 'immediate') neutralDesc = '현재 가격대 매수 가능';

    // 아이콘: 컬러 이모지 대신 currentColor SVG
    const _ic = {
      up:   '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M3 14l5-5 3 3 6-6M12 6h5v5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      wait: '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 6v4l2.5 2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
      warn: '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M10 3l7.5 13h-15z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M10 8v3.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="10" cy="13.9" r="0.95" fill="currentColor"/></svg>',
    };
    const recMap = {
      strong:  { cls: 'strong',  icon: _ic.up,   label: '강한 매수 신호', desc: strongDesc },
      neutral: { cls: 'neutral', icon: _ic.wait, label: '풀백 대기 권장', desc: neutralDesc },
      avoid:   { cls: 'avoid',   icon: _ic.warn, label: '진입 비추천',
                 desc: '추세가 약하거나 손익비 부족 — 관망 권장' },
    };
    const r = recMap[rec] || recMap.neutral;
    banner.className = `trade-rec-banner ${r.cls}`;
    banner.innerHTML = `
      <div class="rec-icon">${r.icon}</div>
      <div class="rec-body">
        <div class="rec-label">${r.label}</div>
        <div class="rec-desc">${r.desc}${ts != null ? ` · 추세점수 ${ts}/100` : ''}</div>
      </div>`;
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }

  // R:R 비율 + 손절거리 메타
  const metaItems = [];
  if (rr != null)  metaItems.push(`R:R <strong>1:${rr.toFixed(2)}</strong>`);
  if (sdp != null) metaItems.push(`손절 거리 <strong>${sdp}%</strong>`);
  if (sdp != null && sdp > 15) metaItems.push(`<span class="meta-warn">변동성 큼</span>`);
  if (metaItems.length) {
    meta.innerHTML = metaItems.join(' · ');
    meta.classList.remove('hidden');
  } else {
    meta.classList.add('hidden');
  }

  // 진입가 없을 때 (avoid) 회색 처리
  if (rec === 'avoid') {
    document.getElementById('entryPrice').classList.add('disabled');
    document.getElementById('targetPrice').classList.add('disabled');
  } else {
    document.getElementById('entryPrice').classList.remove('disabled');
    document.getElementById('targetPrice').classList.remove('disabled');
  }
}

function renderDrawdowns(stock) {
  const w = document.getElementById('drawdownWidget');
  if (!w) return;
  const dd = stock.drawdowns;
  if (!dd) { w.classList.add('hidden'); return; }
  const cur = stock.currency;

  const fill = (d, valId, highId) => {
    const valEl = document.getElementById(valId);
    const highEl = document.getElementById(highId);
    if (!valEl) return;
    if (!d) { valEl.textContent = '—'; valEl.className = 'dd-val'; if (highEl) highEl.textContent = ''; return; }
    if (d.at_high) {
      valEl.textContent = '🔺 신고가';
      valEl.className = 'dd-val dd-newhigh';
    } else {
      const p = d.pct;                       // 음수
      valEl.textContent = `${p.toFixed(1)}%`;
      // 하락폭 클수록 진하게
      const lvl = p <= -20 ? 'dd-deep' : p <= -8 ? 'dd-mid' : 'dd-soft';
      valEl.className = `dd-val ${lvl}`;
    }
    if (highEl) highEl.textContent = `고점 ${fmt(d.high, cur)}`;
  };
  fill(dd['1w'], 'dd1w', 'dd1wHigh');
  fill(dd['1m'], 'dd1m', 'dd1mHigh');
  fill(dd['all'], 'ddAll', 'ddAllHigh');
  w.classList.remove('hidden');
}

// ── DCF 밸류에이션 ──────────────────────────────────────
function _vlFootball(stock) {
  const vr = stock.valuation_ranges || {};
  const krw = stock.currency === 'KRW';
  const f = (v) => _stFmt(v, krw);
  const cur = vr.current || stock.current_price;
  const rows = [];
  if (vr.dcf) rows.push({ k: vr.dcf.label || 'DCF 적정가', lo: vr.dcf.low, mid: vr.dcf.mid, hi: vr.dcf.high, sub: `보수 ${f(vr.dcf.low)} · 기본 ${f(vr.dcf.mid)} · 낙관 ${f(vr.dcf.high)}` });
  if (vr.analyst) rows.push({ k: '애널리스트 목표', lo: vr.analyst.low, mid: vr.analyst.mid, hi: vr.analyst.high, sub: `${f(vr.analyst.low)} ~ ${f(vr.analyst.high)} · 평균 ${f(vr.analyst.mid)}${vr.analyst.count ? ` · ${vr.analyst.count}명` : ''}` });
  if (vr.week52) rows.push({ k: '52주 가격 범위', lo: vr.week52.low, mid: null, hi: vr.week52.high, sub: `${f(vr.week52.low)} ~ ${f(vr.week52.high)}` });
  if (!rows.length || !cur) return '';
  const all = rows.flatMap((r) => [r.lo, r.hi]).concat([cur]).filter((v) => v != null && isFinite(v));
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.06 || cur * 0.05; lo -= pad; hi += pad;
  const x = (v) => ((v - lo) / (hi - lo) * 100).toFixed(2);
  const cx = x(cur);
  const missing = [!vr.dcf && 'DCF', !vr.analyst && '애널리스트 목표', !vr.week52 && '52주 범위'].filter(Boolean);
  return `
    <section class="vl-ff" aria-label="밸류에이션 범위 비교">
      <div class="vl-ff-h"><h4>밸류에이션 범위 비교</h4><span>점선 = 현재가</span></div>
      <div class="vl-ff-row vl-ff-now"><span class="vl-ff-k"></span><span class="vl-ff-t"><em style="left:${cx}%">현재가 ${f(cur)}</em></span></div>
      ${rows.map((r) => {
        const inRange = cur >= r.lo && cur <= r.hi;
        const pos = cur < r.lo ? '현재가가 범위 아래' : cur > r.hi ? '현재가가 범위 위' : '범위 안';
        return `
      <div class="vl-ff-row">
        <span class="vl-ff-k"><b>${r.k}</b><small>${r.sub}</small></span>
        <span class="vl-ff-t" title="${pos}">
          <i class="vl-ff-bar${inRange ? ' in' : ''}" style="left:${x(r.lo)}%;width:${Math.max(0.6, x(r.hi) - x(r.lo)).toFixed(2)}%"></i>
          ${r.mid != null ? `<i class="vl-ff-mid" style="left:${x(r.mid)}%"></i>` : ''}
          <i class="vl-ff-line" style="left:${cx}%"></i>
        </span>
      </div>`;
      }).join('')}
      ${missing.length ? `<p class="vl-note">${missing.join(' · ')} 자료 없음</p>` : ''}
    </section>`;
}

// 큰 금액: 원화 조·억 / 달러 B·M
function _bigMoney(v, krw) {
  if (v == null || !isFinite(v)) return '—';
  const a = Math.abs(v), sg = v < 0 ? '-' : '';
  if (krw) return a >= 1e12 ? `${sg}${(a / 1e12).toFixed(1)}조원` : `${sg}${Math.round(a / 1e8).toLocaleString('ko-KR')}억원`;
  return a >= 1e9 ? `${sg}$${(a / 1e9).toFixed(1)}B` : `${sg}$${(a / 1e6).toFixed(0)}M`;
}

function renderDcf(stock) {
  const card = document.getElementById('dcfCard');
  const tabBtn = document.getElementById('valuationTabBtn');
  if (!card) return;
  const d = stock.dcf;
  switchDetailTab('technical');
  tabBtn?.classList.remove('hidden');
  const ff = document.getElementById('valFootball');
  if (ff) ff.innerHTML = _vlFootball(stock);
  const empty = document.getElementById('dcfEmpty');
  if (!d || !d.fair_value) {
    card.classList.add('hidden');
    if (empty) {
      empty.textContent = d && d.model === 'pb_roe'
        ? (d.message || 'DCF 미적용 (금융주 — PBR·컨센서스 참고)')
        : '영업적자이거나 현금흐름 자료가 부족해 DCF 적정가를 계산하지 않았습니다. 위 애널리스트 목표가·52주 범위를 참고하세요.';
      empty.classList.remove('hidden');
    }
    return;
  }
  empty?.classList.add('hidden');
  const krw = stock.currency === 'KRW';
  const f = (v) => _stFmt(v, krw);
  const a = d.assumptions || {};
  const pb = d.model === 'pb_roe';
  const up = (v) => (v == null ? '' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);

  // 역DCF 1줄 요약
  let head, sub = '';
  if (pb) {
    head = d.implied_roe_pct != null
      ? `현재 주가는 ROE <b>${d.implied_roe_pct.toFixed(1)}%</b> 가 계속되는 것을 반영 중`
      : '현재 PBR 로 역산한 ROE 를 계산할 수 없습니다';
    sub = `현재 ROE ${a.roe_pct}% · 자기자본비용 ${a.cost_equity_pct}% · 적정 PBR ${a.fair_pb}배 (현재 ${a.pbr_now ?? '—'}배)`;
  } else if (d.implied_growth_pct != null) {
    head = `현재 주가는 향후 ${a.years}년간 연 <b>${d.implied_growth_pct > 0 ? '+' : ''}${d.implied_growth_pct.toFixed(1)}%</b> 매출 성장을 반영 중`;
    const parts = [];
    if (a.consensus_growth_pct != null) parts.push(`애널리스트 내년 전망 ${a.consensus_growth_pct}%`);
    if (a.recent_growth_pct != null) parts.push(`최근 분기 YoY ${a.recent_growth_pct}%`);
    if (a.hist_cagr_pct != null) parts.push(`과거 연평균 ${a.hist_cagr_pct}%`);
    sub = parts.join(' · ') + (parts.length ? ' · ' : '') + `성장률은 ${a.years}년에 걸쳐 영구성장률 ${a.terminal_g_pct}% 로 낮아지는 경로`;
  } else if (d.implied_margin_pct != null) {
    head = `현재 주가는 영업이익률 <b>${d.implied_margin_pct}%</b> 를 반영 중`;
    sub = `최근 ${a.ebit_margin_years || 1}년 평균 ${a.ebit_margin_pct}% · 성장률만으로는 설명되지 않아 수익성 개선을 전제로 역산`;
  } else {
    head = '현재 주가는 이 모델의 재무 범위 밖';
    sub = '매출 연 60% 성장이나 영업이익률 85% 를 가정해도 도달하지 않습니다. 재무제표에 아직 없는 기대를 반영 중입니다.';
  }
  const rel = { high: '신뢰도 높음', medium: '참고용', low: '해석 주의' }[d.reliability] || '참고용';

  // 시나리오 3카드
  const sc = d.scenarios || {};
  const scen = [['bear', '보수'], ['base', '기본'], ['bull', '낙관']].map(([k, n]) => {
    const s = sc[k] || {};
    const basis = pb ? `ROE ${s.roe_pct ?? '—'}% · 적정 PBR ${s.fair_pb ?? '—'}배`
                     : `매출 성장 ${s.start_growth_pct ?? '—'}% 에서 출발`;
    return `<div class="vl-sc${k === 'base' ? ' base' : ''}"><span class="vl-sc-k">${n}</span><b>${f(s.fair_value)}</b>
      <span class="vl-sc-u ${s.upside_pct > 0 ? 'up' : s.upside_pct < 0 ? 'dn' : ''}">${up(s.upside_pct)}</span><small>${basis}</small></div>`;
  }).join('');

  // 민감도 5×5
  const sens = d.sensitivity;
  const sensHTML = sens && sens.grid ? `
    <div class="vl-sens-wrap"><table class="vl-sens">
      <caption>WACC(행) × 영구성장률(열) 별 주당 가치</caption>
      <thead><tr><th>WACC \ g</th>${sens.g_axis.map((g) => `<th>${g.toFixed(1)}%</th>`).join('')}</tr></thead>
      <tbody>${sens.grid.map((row, i) => `<tr><th>${sens.wacc_axis[i].toFixed(1)}%</th>${row.map((v, j) =>
        `<td class="${i === 2 && j === 2 ? 'mid' : ''}${v != null && d.current_price && v >= d.current_price ? ' ge' : ''}">${v == null ? '—' : f(v)}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></div>` : '';

  const rows = pb ? [
    ['ROE', a.roe_pct + '%'], ['자기자본비용', a.cost_equity_pct + '%'], ['영구성장률', a.terminal_g_pct + '%'],
    ['적정 PBR', a.fair_pb + '배'], ['현재 PBR', a.pbr_now != null ? a.pbr_now + '배' : '—'], ['주당순자산', f(a.bvps)],
    ['베타 (조정)', `${a.beta}${a.beta_raw ? ` (원본 ${a.beta_raw})` : ''}`], ['무위험이자율', `${a.risk_free_pct}%${a.rf_source === '미 10년 국채' ? ' (미 10년 국채)' : ' (기본 가정)'}`],
  ] : [
    ['기준 매출', a.base_revenue != null ? `${_bigMoney(a.base_revenue, krw)} (${a.base_revenue_source || '연간'})` : '—'],
    ['WACC (할인율)', a.wacc_pct != null ? a.wacc_pct + '%' : '—'],
    ['자기자본비용', a.cost_equity_pct != null ? a.cost_equity_pct + '%' : '—'],
    ['베타 (조정)', a.beta != null ? `${a.beta}${a.beta_raw ? ` (원본 ${a.beta_raw})` : ''}` : '—'],
    ['무위험이자율', a.risk_free_pct != null ? `${a.risk_free_pct}%${a.rf_source === '미 10년 국채' ? ' (미 10년 국채)' : ' (기본 가정)'}` : '—'],
    ['적용 매출성장률', a.base_growth_pct != null ? a.base_growth_pct + '%' : '—'],
    ['애널리스트 내년 매출 전망', a.consensus_growth_pct != null ? a.consensus_growth_pct + '%' : '—'],
    ['최근 분기 매출 YoY', a.recent_growth_pct != null ? a.recent_growth_pct + '%' : '—'],
    ['과거 연평균 성장률', a.hist_cagr_pct != null ? a.hist_cagr_pct + '%' : '—'],
    ['영업이익률 (출발 → 목표)', a.ebit_margin_pct != null ? `${a.ebit_margin_pct}% → ${a.target_margin_pct ?? a.ebit_margin_pct}% (${a.years}년에 걸쳐 수렴)` : '—'],
    ['설비투자율 (출발 → 목표)', a.capex_pct != null ? `${a.capex_pct}% → ${a.capex_end_pct ?? a.capex_pct}% (감가상각 ${a.da_pct}%)` : '—'],
    ['운전자본 투자율', a.wc_invest_pct != null ? `매출 증가분의 ${a.wc_invest_pct}%` : '—'],
    ['영구성장률', a.terminal_g_pct != null ? a.terminal_g_pct + '%' : '—'],
    ['예측 기간', a.years ? a.years + '년' : '—'],
    ['법인세율', a.tax_rate_pct != null ? a.tax_rate_pct + '%' : '—'],
    ['터미널 비중', a.terminal_share_pct != null ? a.terminal_share_pct + '%' : '—'],
    ['비지배지분 차감', a.minority_interest ? f(a.minority_interest) : '없음'],
    [a.net_debt != null && a.net_debt < 0 ? '순현금' : '순부채', a.net_debt != null ? _bigMoney(Math.abs(a.net_debt), krw) : '—'],
    ['발행주식수', a.shares ? `${Math.round(a.shares).toLocaleString('ko-KR')}주` : '—'],
  ];

  document.getElementById('vlImplied').innerHTML = `
    <div class="vl-im-h"><span class="vl-im-k">${pb ? '역산 ROE' : '역DCF 핵심 해석'}</span><span class="st-tag">${rel}</span>${pb ? '<span class="st-tag">금융주 · P/B-ROE 모델</span>' : ''}</div>
    <p class="vl-im-main">${head}</p><p class="vl-im-sub">${sub}</p>`;
  document.getElementById('vlScen').innerHTML = scen;
  document.getElementById('dcfWarnings').innerHTML = (d.warnings || []).map((w) => `<p class="vl-warn">${escapeHtmlMain(w)}</p>`).join('');
  document.getElementById('dcfSens').innerHTML = sensHTML;
  document.getElementById('dcfAssumptions').innerHTML = rows.map(([k, v]) =>
    `<div class="dcf-as-item"><span class="dcf-as-k">${k}</span><span class="dcf-as-v">${v}</span></div>`).join('');
  card.classList.remove('hidden');
  renderDcfComment(null);
}

// 등락률 표기 통일: 부호 + 소수 둘째 자리 (+2.80%, -1.61%)
function fmtPct(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function escapeHtmlMain(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function renderDcfComment(text) {
  const el = document.getElementById('dcfComment');
  if (!el) return;
  if (!text) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  el.innerHTML = `<div class="dcf-comment-label">AI 해설</div>` +
                 `<p>${escapeHtmlMain(text).replace(/\n/g, '<br>')}</p>`;
  el.classList.remove('hidden');
}

async function loadDcfComment(ticker) {
  const el = document.getElementById('dcfComment');
  if (!el) return;
  el.classList.add('hidden');
  el.innerHTML = '';
  try {
    const r = await fetch(`/api/dcf/comment?ticker=${encodeURIComponent(ticker)}`);
    if (!r.ok) return;
    const d = await r.json();
    if (!d || !d.comment) return;
    el.innerHTML = `<div class="dcf-comment-label">AI 해설</div>` +
                   `<p>${escapeHtmlMain(d.comment).replace(/\n/g, '<br>')}</p>`;
    el.classList.remove('hidden');
  } catch (e) { /* 해설 실패는 조용히 무시 — 숫자는 이미 표시됨 */ }
}

function renderMetrics(stock, analysis) {
  const cur = stock.currency;
  renderDrawdowns(stock);
  renderDcf(stock);
  document.getElementById('yearHigh').textContent = fmt(stock.year_high, cur);
  document.getElementById('yearLow').textContent = fmt(stock.year_low, cur);

  const rsiEl = document.getElementById('rsiValue');
  if (analysis.rsi != null) {
    rsiEl.textContent = analysis.rsi.toFixed(1);
    rsiEl.style.color = analysis.rsi >= 70 ? 'var(--red)' : analysis.rsi <= 30 ? 'var(--green)' : 'var(--text)';
  } else { rsiEl.textContent = '—'; }

  document.getElementById('bbPosition').textContent =
    analysis.bb_position != null ? `${analysis.bb_position.toFixed(1)}%` : '—';

  document.getElementById('peRatio').textContent =
    stock.pe_ratio != null ? stock.pe_ratio.toFixed(1) + 'x' : '—';
  // 기준 표기: 최근 4분기(TTM) — 한국 종목은 TTM 이 없을 때만 직전 연간
  const peLbl = document.getElementById('peLabel');
  if (peLbl) peLbl.textContent = stock.pe_basis === '연간' ? 'PER (연간)' : 'PER (TTM)';
  document.getElementById('peRatio').title = stock.pe_basis === '연간'
    ? '최근 4분기 실적이 없어 직전 확정 연간 EPS 기준' : '최근 4분기 합산(TTM) EPS 기준';

  const fpeEl = document.getElementById('forwardPeRatio');
  fpeEl.textContent =
    stock.forward_pe != null && stock.forward_pe > 0 ? stock.forward_pe.toFixed(1) + 'x' : '—';
  // 한국 종목은 네이버 컨센서스(기준 연도 표기), 그 외는 Yahoo 추정치
  fpeEl.title = stock.forward_pe_basis || '';
  const fpeLbl = document.getElementById('forwardPeLabel');
  if (fpeLbl) fpeLbl.textContent = stock.forward_pe_year ? `선행PER(${stock.forward_pe_year}E)` : '선행PER';

  document.getElementById('pbRatio').textContent =
    stock.pb_ratio != null ? stock.pb_ratio.toFixed(2) + 'x' : '—';

  document.getElementById('epsValue').textContent =
    stock.eps != null ? fmt(stock.eps, cur, true) : '—';  // EPS는 소수점 유지

  document.getElementById('dividendYield').textContent =
    stock.dividend_yield != null ? (stock.dividend_yield * 100).toFixed(2) + '%' : '—';
  _renderShortCell(stock.short_interest);
  if (_krShortEligible(stock)) loadKrShort(stock.ticker);

  if (stock.volume != null && stock.avg_volume != null && stock.avg_volume > 0) {
    const ratio = (stock.volume / stock.avg_volume * 100).toFixed(0);
    const el = document.getElementById('volumeRatio');
    el.textContent = ratio + '%';
    el.style.color = ratio >= 150 ? 'var(--green)' : ratio <= 50 ? 'var(--red)' : 'var(--text)';
  } else {
    document.getElementById('volumeRatio').textContent = '—';
  }
}

function renderMATable(maAnalysis, currentPrice) {
  const container = document.getElementById('maTable');
  if (!maAnalysis || Object.keys(maAnalysis).length === 0) {
    container.innerHTML = '<p style="color:var(--text2)">데이터 부족</p>';
    return;
  }
  container.innerHTML = Object.entries(maAnalysis).map(([key, val]) => `
    <div class="ma-item">
      <div class="ma-label">${key}</div>
      <div class="ma-value">${val.value.toLocaleString()}</div>
      <div class="ma-status ${val.above ? 'above' : 'below'}">
        ${val.above ? '▲ 현재가 위' : '▼ 현재가 아래'}
      </div>
    </div>
  `).join('');
}

// ── 지표별 초보자 팁 ──────────────────────────────────────
const INDICATOR_TIPS = {
  '이동평균선 (MA)': {
    bullish: '📈 단기→중기→장기 순서로 정렬된 "정배열" 상태예요. 주가가 꾸준히 오르는 좋은 흐름이에요.',
    bearish: '📉 장기→중기→단기로 역전된 "역배열" 상태예요. 하락 추세가 이어질 수 있어 신중하게 접근하세요.',
    neutral: '↔️ 이동평균선들이 뒤섞여 있어요. 방향이 불분명해 추세가 확실해질 때까지 기다리는 것도 방법이에요.'
  },
  '볼린저밴드 (BB)': {
    // tip_key(서버가 실제 위치로 지정)가 우선, 없으면 색으로 고른다
    upper_break: '⚠️ 주가가 밴드 위쪽 끝을 넘었어요. 단기 과열 상태로 조정(하락)이 올 수 있어요.',
    lower_break: '📈 주가가 밴드 아래쪽 끝을 벗어났어요. 많이 떨어진 상태라 반등이 나올 수 있지만, 하락 추세가 강하면 더 내려갈 수도 있어요.',
    above_mid:   '↗️ 주가가 밴드 중심선 위에 있어요. 오르는 힘이 조금 더 강한 상태예요.',
    below_mid:   '↘️ 주가가 밴드 중심선 아래에 있어요. 내리는 힘이 조금 더 강한 상태예요.',
    neutral: '↔️ 주가가 밴드 중간에 있어요. 특별한 과매수·과매도 신호는 없어요.'
  },
  '일목균형표': {
    bullish: '☁️ 주가가 구름대 위에 있어요. 상승 추세가 강하며 구름대가 지지선 역할을 해줘요.',
    bearish: '☁️ 주가가 구름대 아래에 있어요. 하락 추세가 강하며 구름대가 저항선 역할을 해요.',
    neutral: '☁️ 주가가 구름대 안에 있어요. 상승·하락 방향이 결정되지 않은 과도기 상태예요.'
  },
  'RSI (14)': {
    bullish: '💚 RSI 30 이하 "과매도" 구간이에요. 너무 많이 떨어진 상태로 반등 가능성이 높아요.',
    bearish: '🔴 RSI 70 이상 "과매수" 구간이에요. 너무 많이 오른 상태라 쉬어 갈 수 있어요. 다만 강하게 오르는 종목은 70 위에서 한동안 버티기도 해요.',
    neutral: '✅ RSI가 적정 범위(30~70)에 있어요. 과열도 과냉도 아닌 안정적인 상태예요.'
  },
  '수급 · ADX': {
    trend:   '🧭 ADX가 25 이상이면 한쪽 방향으로 힘 있게 움직이는 "추세장"이에요. +DI가 크면 오르는 힘, -DI가 크면 내리는 힘이 더 세요. OBV가 오르면 오르는 날 거래가 더 많다는 뜻(돈이 들어옴)이에요.',
    range:   '↔️ ADX가 20 아래면 방향 없이 오르내리는 "횡보장"이에요. 이때는 이동평균·MACD 같은 추세 신호가 자주 틀려요. OBV로 돈이 들어오는지 나가는지를 같이 보세요.',
    neutral: '🧭 ADX는 추세의 "세기"(방향 아님), OBV는 오르는 날·내리는 날 거래량을 누적한 "돈의 흐름"이에요. 둘 다 뚜렷하지 않으면 지켜보는 구간이에요.'
  },
  'MACD': {
    bullish: '📈 MACD가 시그널선 위로 올라왔어요. 상승 모멘텀이 생기는 신호로 매수 타이밍일 수 있어요.',
    bearish: '📉 MACD가 시그널선 아래로 내려갔어요. 하락 압력이 생기는 신호로 매도를 고려할 수 있어요.',
    neutral: '↔️ MACD와 시그널선이 비슷한 수준이에요. 방향 전환을 앞두고 있을 수 있어요.'
  },
  '밸류에이션': {
    bullish: '💚 PER·PBR이 낮아요. 현재 주가가 기업 가치 대비 저렴해 투자 매력이 있어요.',
    bearish: '🔴 PER·PBR이 높아요. 현재 주가가 기업 가치 대비 비싸 가격 부담이 있어요.',
    neutral: '↔️ PER·PBR이 업종 평균 수준이에요. 특별히 싸거나 비싸지 않아요.'
  },
  '수급 & 거래량': {
    vol_high:   '📊 거래량이 평소보다 크게 늘었어요. 관심이 몰리는 중이에요. 오를 때 많으면 사려는 힘, 내릴 때 많으면 팔려는 힘이 강하다는 뜻이에요.',
    vol_normal: '📊 거래량이 평소 수준이에요. 특별한 수급 신호는 없어요.',
    vol_low:    '📊 거래량이 평소보다 적어요. 관심이 줄어든 상태라 지금 움직임의 힘이 약할 수 있어요.',
    neutral: '📊 거래량이 평균 수준이에요. 특별한 수급 신호는 없어요.'
  },
  '시장 위치 & 모멘텀': {
    bullish: '🚀 최근 상승세가 강해요. 52주 고가 근처에 있어 상승 모멘텀이 살아있어요.',
    bearish: '📉 최근 하락세가 강해요. 52주 저가 근처에 있어 모멘텀이 약해요.',
    neutral: '↔️ 52주 범위 중간 정도에 있어요. 특별한 방향성은 없어요.'
  },
  '재무 건전성': {
    bullish: '💪 ROE가 높고 부채가 적어요. 돈을 잘 버는 재무 건강한 기업이에요.',
    bearish: '⚠️ 부채가 많거나 수익성이 낮아요. 재무 리스크에 주의가 필요해요.',
    neutral: '↔️ 재무 지표가 평균 수준이에요. 특별한 문제나 장점은 없어요.'
  }
};

// ── 분석 탭 전환 ────────────────────────────────────────
function switchDetailTab(tab) {
  document.querySelectorAll('.analysis-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  // 기술적 분석 = 지표 + 매수/매도 구간 (한 탭으로 통합)
  const techCont = document.getElementById('technicalContainer');
  if (techCont) techCont.classList.toggle('hidden', tab !== 'technical');
  const fundCont = document.getElementById('fundamentalContainer');
  if (fundCont) fundCont.classList.toggle('hidden', tab !== 'fundamental');
  const valPanel = document.getElementById('valuationPanel');
  if (valPanel) valPanel.classList.toggle('hidden', tab !== 'valuation');
  const stPanel = document.getElementById('strategyPanel');
  if (stPanel) stPanel.classList.toggle('hidden', tab !== 'strategy');
}

function _buildCards(details) {
  if (!details || !details.length) return '<p class="no-data">분석 데이터가 없습니다.</p>';
  return details.map(d => {
    const tipObj = INDICATOR_TIPS[d.indicator];
    let tip = tipObj ? (tipObj[d.tip_key] || tipObj[d.color] || tipObj['neutral'] || '') : '';
    // 장중에는 당일 거래량이 아직 쌓이는 중이라 하루 전체 평균보다 낮게 나온다
    if (d.indicator === '수급 & 거래량' && window._lastStock && window._lastStock.is_market_open) {
      tip += ' (장중이라 오늘 거래량은 아직 집계 중이에요. 장 마감 후 다시 보면 더 정확해요.)';
    }
    return `
    <div class="detail-item open">
      <div class="detail-header" style="cursor:default">
        <span class="detail-indicator">${d.indicator}</span>
        <span class="detail-state ${d.color}">${d.state}</span>
      </div>
      <div class="detail-body">
        <div class="detail-chips">
          ${(d.items || []).map(item => `
            <span class="detail-chip ${item.up === true ? 'up' : item.up === false ? 'down' : ''}">
              ${item.label}
            </span>
          `).join('')}
        </div>
        <div class="detail-desc">${d.desc}</div>
        ${tip ? `<div class="detail-tip">💡 <strong>쉬운 설명</strong> ${tip}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ── 기술적 분석: 종합 게이지 + 6축 레이더 + 무채색 6카드 (ⓘ 설명 토글) ─────────────
const _TA_TONE = { bullish: 'bull', bearish: 'bear', neutral: 'neu' };
const _TA_WORD = { bull: '상승', bear: '하락', neu: '중립' };
const _TA_AXES = ['MA', 'BB', '일목', 'RSI', 'MACD', '수급'];
const _INFO_SVG = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="8" cy="4.9" r="0.95" fill="currentColor"/><path d="M8 7.1v4.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';

// 쉬운 설명에서 앞머리 이모지를 뗀다 (투자 판단 탭과 문구를 같이 쓰기 때문에 원문은 그대로 둔다)
function _plainTip(t) {
  return String(t || '').replace(/^[\p{Extended_Pictographic}←-⇿☀-➿️‍\s]+/u, '').trim();
}

function _taPolar(cx, cy, r, deg) {
  const a = deg * Math.PI / 180;
  return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
}

// 반원 속도계: 왼쪽(0) 매도 · 가운데 중립 · 오른쪽(100) 매수
function _taSpeedo(v) {
  const cx = 110, cy = 104, r = 84, sw = 14;
  const arc = (from, to) => {           // from/to: 0~100
    const [x1, y1] = _taPolar(cx, cy, r, 180 - from * 1.8), [x2, y2] = _taPolar(cx, cy, r, 180 - to * 1.8);
    return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  };
  const val = Math.max(0, Math.min(100, +v || 50));
  const [nx, ny] = _taPolar(cx, cy, r - 22, 180 - val * 1.8);
  return `
  <svg class="tg-speedo" viewBox="0 0 220 124" role="img" aria-label="기술 지표 종합 ${val.toFixed(0)}점 (0 매도 · 100 매수)">
    <path d="${arc(0, 33.3)}"  class="tg-arc tg-arc-bear" stroke-width="${sw}"/>
    <path d="${arc(34.3, 65.7)}" class="tg-arc tg-arc-neu" stroke-width="${sw}"/>
    <path d="${arc(66.7, 100)}" class="tg-arc tg-arc-bull" stroke-width="${sw}"/>
    <line x1="${cx}" y1="${cy}" x2="${nx.toFixed(2)}" y2="${ny.toFixed(2)}" class="tg-needle"/>
    <circle cx="${cx}" cy="${cy}" r="6" class="tg-hub"/>
    <text x="${cx - r}" y="${cy + 18}" text-anchor="middle" class="tg-tick">매도</text>
    <text x="${cx}" y="${cy - r - 8 + 2}" text-anchor="middle" class="tg-tick" dy="-2">중립</text>
    <text x="${cx + r}" y="${cy + 18}" text-anchor="middle" class="tg-tick">매수</text>
  </svg>`;
}

// 6축 레이더: 위(MA)부터 시계 방향
function _taRadar(details) {
  const cx = 130, cy = 112, R = 78;
  const ang = (i) => 90 - i * 60;
  const pt = (i, pct) => _taPolar(cx, cy, R * pct / 100, ang(i));
  const ring = (p) => _TA_AXES.map((_, i) => pt(i, p).map((n) => n.toFixed(1)).join(',')).join(' ');
  const vals = details.slice(0, 6).map((d) => (d.axis == null ? 50 : Math.max(0, Math.min(100, +d.axis))));
  const poly = vals.map((v, i) => pt(i, Math.max(v, 3)).map((n) => n.toFixed(1)).join(',')).join(' ');
  const labels = _TA_AXES.map((name, i) => {
    const [x, y] = _taPolar(cx, cy, R + 20, ang(i));
    const anchor = Math.abs(x - cx) < 4 ? 'middle' : x > cx ? 'start' : 'end';
    return `<text x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="${anchor}" class="tr-lab">${name} <tspan class="tr-val">${Math.round(vals[i])}</tspan></text>`;
  }).join('');
  return `
  <svg class="tg-radar" viewBox="0 0 260 226" role="img" aria-label="6개 지표 점수: ${_TA_AXES.map((n, i) => `${n} ${Math.round(vals[i])}`).join(', ')}">
    ${[25, 50, 75, 100].map((p) => `<polygon points="${ring(p)}" class="tr-ring${p === 50 ? ' tr-mid' : ''}"/>`).join('')}
    ${_TA_AXES.map((_, i) => { const [x, y] = pt(i, 100); return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="tr-spoke"/>`; }).join('')}
    <polygon points="${poly}" class="tr-shape"/>
    ${vals.map((v, i) => { const [x, y] = pt(i, Math.max(v, 3)); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" class="tr-dot"/>`; }).join('')}
    ${labels}
  </svg>`;
}

function _taCard(d, i) {
  const tone = _TA_TONE[d.color] || 'neu';
  const tipObj = INDICATOR_TIPS[d.indicator];
  const tip = _plainTip(tipObj ? (tipObj[d.tip_key] || tipObj[d.color] || tipObj['neutral'] || '') : '');
  const m = d.metrics || [];
  const main = m[0], sub = m[1];
  const subTxt = [sub ? `${sub.label} ${sub.value}` : '', d.overheat ? '단기 과열' : ''].filter(Boolean).join(' · ');
  const name = d.short || d.indicator;
  return `
    <article class="tc-card" id="taCard${i}" data-tone="${tone}">
      <div class="tc-row1">
        <h4 class="tc-name">${name}</h4>
        <span class="tc-stat tc-${tone}"><i></i>${_TA_WORD[tone]}</span>
        <button type="button" class="tc-info" aria-expanded="false" aria-controls="taHelp${i}" aria-label="${name} 설명 보기" onclick="_taToggleHelp(this)">${_INFO_SVG}</button>
      </div>
      <div class="tc-main">${main ? `<b>${main.value}</b><span>${main.label}</span>` : '<b>—</b>'}</div>
      <div class="tc-sub">${subTxt || d.state || ''}</div>
      <div class="tc-help" id="taHelp${i}" hidden>
        <p class="tc-state">${d.state}</p>
        ${tip ? `<p class="tc-tip">${tip}</p>` : ''}
      </div>
    </article>`;
}

function _taToggleHelp(btn) {
  const box = document.getElementById(btn.getAttribute('aria-controls'));
  const open = btn.getAttribute('aria-expanded') !== 'true';
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (box) box.hidden = !open;
}

function renderDetail(details, analysis) {
  const el = document.getElementById('detailList');
  if (!el) return;
  if (!details || !details.length) { el.innerHTML = '<p class="no-data">분석 데이터가 없습니다.</p>'; return; }
  const cards = details.slice(0, 6);
  const g = (analysis && analysis.tech_gauge) || {};
  const cnt = g.counts || cards.reduce((c, d) => { c[_TA_TONE[d.color] || 'neu']++; return c; }, { bull: 0, neu: 0, bear: 0 });
  const val = g.value != null ? g.value : 50;
  el.innerHTML = `
    <div class="tg-panel">
      <section class="tg-left" aria-label="기술 지표 종합">
        ${_taSpeedo(val)}
        <div class="tg-summary">
          <div class="tg-label tg-${g.tone || 'neu'}">${g.label || '중립'}</div>
          <div class="tg-counts">
            <span class="tg-c tc-bull"><i></i>강세 <b>${cnt.bull}</b></span><span class="tg-sep">·</span>
            <span class="tg-c tc-neu"><i></i>중립 <b>${cnt.neu}</b></span><span class="tg-sep">·</span>
            <span class="tg-c tc-bear"><i></i>약세 <b>${cnt.bear}</b></span>
          </div>
          ${g.overheat ? `<div class="tg-warn" title="${(g.overheat_reasons || []).join(', ')}">단기 과열 · ${(g.overheat_reasons || []).join(', ')}</div>` : ''}
          ${g.adx_line ? `<div class="tg-adx">${g.adx_line}</div>` : ''}
        </div>
      </section>
      <section class="tg-right" aria-label="지표별 점수">
        ${_taRadar(cards)}
      </section>
    </div>
    <div class="tc-grid">${cards.map(_taCard).join('')}</div>`;
}

// ── 지표 설명 (툴팁) ──────────────────────────────────────
const _SC_HELP = {
  // Quality
  fcf_margin:   '매출 100원 중 실제 현금으로 남는 비율. EPS는 회계 조작 가능하지만 FCF는 어려워서 \'진짜\' 수익성 지표.\n👍 15% 이상 우수',
  fcf_yield:    '시가총액 대비 연간 잉여현금흐름. 채권 수익률처럼 비교 가능 — 높을수록 저평가.\n👍 5% 이상 매력적',
  op_margin:    '본업으로 100원 팔아 영업단계에서 남기는 돈. 사업 모델의 효율성.\n👍 15% 이상 우수',
  gross_margin: '원가 빼고 남는 비율. 브랜드 파워·가격 결정력의 핵심 지표.\n👍 30% 이상 우수',
  f_score:      'Piotroski F-Score: 재무 9개 시그널(수익성·부채·효율) 종합. Joseph Piotroski 교수 개발.\n👍 7~9 우량 / 👎 0~2 위험',
  net_debt:     '순부채 ÷ EBITDA. 영업이익으로 부채를 갚는 데 걸리는 년수.\n👍 2배 이하 안전 / 👎 4배 초과 위험',
  // Value
  peg:          'PER ÷ EPS 성장률. Peter Lynch 명저. 성장률 대비 비싼지 판단.\n👍 1 미만 저평가 / 👎 2 초과 고평가',
  ev_ebitda:    'EV(부채 포함) ÷ EBITDA. M&A·LBO 관점의 진짜 기업가치. PER이 부채를 무시하는 한계 보완.\n👍 12 이하 매력 / 👎 25 초과 비쌈',
  // Growth
  op_yoy:       '영업이익률 작년 대비 변화(percentage points). +면 경쟁력 강화 / −면 약화.\n👍 +1pp 이상 우수',
  gm_yoy:       '매출총이익률 작년 대비 변화. 가격 결정력 변화 — Buffett이 가장 좋아하는 추세 지표.\n👍 +1pp 이상 우수',
  // Momentum & Signals
  eps_rev:      '최근 30일 애널리스트 EPS 추정치 상향/하향 비율. 헤지펀드 단골 모멘텀 시그널.\n👍 +20% 이상 강세',
  short_float:  '유동주식 중 공매도 비율. 10%+면 베어 컨센서스 강하거나 숏 스퀴즈 가능성.\n⚠️ 10%+ 주의',
  insider:      '최근 6개월 내부자(CEO/CFO 등) 매수 - 매도 건수. CEO 매수는 가장 강력한 매수 신호.\n👍 +5건 이상 강세\n⚠️ 한국 종목/ETF는 데이터 미수집',
  shareholder:  '배당 + 자사주 매입을 시총으로 나눈 값. 주주에게 돌려주는 총 환원율.\n👍 4% 이상 우수',
};

// 인포 아이콘 HTML — 데스크톱은 hover, 모바일은 tap-to-show
// data-help에 텍스트 저장 → toggleScHelp가 인라인 박스로 펼침
const _escAttr = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const _helpIcon = (key) => {
  const text = _SC_HELP[key] || '';
  if (!text) return '';
  const safe = _escAttr(text);
  return `<span class="m-help" data-help="${safe}" onclick="toggleScHelp(this, event)" title="${safe}" aria-label="${safe}" role="button" tabindex="0">ⓘ</span>`;
};
// 인라인 (4팩터·헤더용 — 텍스트 직접 받기)
const _helpInline = (text) => {
  if (!text) return '';
  const safe = _escAttr(text);
  return `<span class="m-help-inline" data-help="${safe}" onclick="toggleScHelp(this, event)" title="${safe}" aria-label="${safe}" role="button" tabindex="0">ⓘ</span>`;
};

// ── 도움말 토글 (모바일 친화) ────────────────────────
function toggleScHelp(iconEl, event) {
  if (event) { event.stopPropagation(); event.preventDefault(); }
  const text = iconEl.getAttribute('data-help');
  if (!text) return;

  // 부모 컨테이너 찾기 (우선순위)
  const anchor = iconEl.closest('.sc-metric-row')
              || iconEl.closest('.sc-factor')
              || iconEl.closest('.sc-header');
  if (!anchor) return;

  // 같은 anchor에 이미 펼친 박스가 있으면 닫기 (토글)
  const existing = anchor.querySelector(':scope > .sc-help-bubble');
  if (existing) {
    existing.remove();
    iconEl.classList.remove('m-help-open');
    return;
  }

  // 다른 모든 박스 닫기
  document.querySelectorAll('.sc-help-bubble').forEach(b => b.remove());
  document.querySelectorAll('.m-help-open').forEach(el => el.classList.remove('m-help-open'));

  // 새 박스 생성
  const bubble = document.createElement('div');
  bubble.className = 'sc-help-bubble';
  bubble.innerHTML = text.replace(/\n/g, '<br>');
  anchor.appendChild(bubble);
  iconEl.classList.add('m-help-open');
}

// 빈 공간 탭/클릭 시 모든 도움말 박스 닫기
document.addEventListener('click', (e) => {
  if (!e.target.closest('.m-help, .m-help-inline, .sc-help-bubble')) {
    document.querySelectorAll('.sc-help-bubble').forEach(b => b.remove());
    document.querySelectorAll('.m-help-open').forEach(el => el.classList.remove('m-help-open'));
  }
});

// ── 월가 스타일 펀더멘탈 스코어카드 (4팩터 + 10지표) ────────
// 세부 지표 표 (Quality·Value·Growth·Momentum 10여 개) — 투자 판단 탭 아래 '세부 지표 보기' 안에 들어간다
// 팩터 점수 배지: 70↑ 초록 · 45~69 파랑 · 45↓ 코랄
function _scBand(v) { return v == null ? 'na' : v >= 70 ? 'hi' : v >= 45 ? 'mid' : 'lo'; }
function _scBadge(f) {
  if (!f) return '';
  const v = f.score;
  const word = v == null ? '' : v >= 70 ? '우수' : v >= 45 ? '보통' : '주의';
  return `<span class="sc-badge sc-b-${_scBand(v)}" title="${f.grade || ''}">${v == null ? '—' : `${word} ${Math.round(v)}`}</span>`;
}
function _sgn(v) { return v == null ? '<span class="m-na">—</span>' : `${v >= 0 ? '+' : ''}${(+v).toFixed(1)}%`; }

function _scDetailHTML(sc, stock) {
  if (!sc || !sc.factors || !sc.metrics) return '';
  const MP = (window._lastAnalysis && window._lastAnalysis.market_position) || {};
  const F = sc.factors, M = sc.metrics;
  const isKR = stock && stock.currency === 'KRW';

  // 등급별 색상 클래스
  const gradeClass = (g) => {
    if (!g || g === '—') return 'na';
    if (g.startsWith('A')) return 'a';
    if (g.startsWith('B')) return 'b';
    if (g.startsWith('C')) return 'c';
    if (g.startsWith('D')) return 'd';
    return 'f';
  };
  const _FACTOR_DESC = {
    Quality:  '돈을 얼마나 잘 버는 사업인가\n(수익성·재무 건전성)',
    Value:    '주가가 적정한가\n(밸류에이션 매력도)',
    Growth:   '성장이 가속화되고 있는가\n(매출/이익/마진 추세)',
    Momentum: '시장이 좋아하기 시작했는가\n(가격·실적 추정치 상향)',
  };
  const factorCard = (label, emoji, f) => {
    const cls = gradeClass(f.grade);
    const score = (f.score != null) ? f.score.toFixed(0) : '—';
    const desc = _FACTOR_DESC[label] || '';
    return `
      <div class="sc-factor sc-${cls}" title="${desc}">
        <div class="sc-factor-head">
          <span class="sc-factor-name">${emoji} ${label} ${_helpInline(desc)}</span>
          <span class="sc-grade">${f.grade}</span>
        </div>
        <div class="sc-factor-score">${score}<small>/100</small></div>
        <div class="sc-factor-bar"><div class="sc-factor-fill" style="width:${score === '—' ? 0 : score}%"></div></div>
      </div>
    `;
  };
  // 종합 등급
  const ovScore = F.overall && F.overall.score != null ? F.overall.score.toFixed(0) : '—';
  const ovGrade = F.overall ? F.overall.grade : '—';
  const ovCls   = gradeClass(ovGrade);

  // 지표 행 헬퍼
  const _fmt = (v, suffix = '', digits = 2) => v == null ? '<span class="m-na">—</span>' : `${(+v).toFixed(digits)}${suffix}`;
  const _signClass = (v, good_pos = true) => v == null ? '' : ((v >= 0) === good_pos ? 'good' : 'bad');
  const _gradeFmt = (s, good_thr, bad_thr) => {
    if (s == null) return '';
    return s >= good_thr ? 'good' : (s <= bad_thr ? 'bad' : '');
  };

  // 10개 지표 카테고리별 정리
  const rev = M.eps_revision || {};
  const ins = M.insider || {};

  const metricsHTML = `
    <div class="sc-metrics">
      <div class="sc-metric-section">
        <div class="sc-section-title">Quality <small>품질 — 진짜 수익성</small>${_scBadge(F.quality)}</div>
        <div class="sc-metric-row"><span>ROE</span><span class="${M.roe_pct == null ? '' : M.roe_pct >= 12 ? 'good' : M.roe_pct < 5 ? 'bad' : ''}">${_fmt(M.roe_pct, '%', 1)}</span></div>
        <div class="sc-metric-row"><span>FCF Margin ${_helpIcon('fcf_margin')}</span><span class="${_gradeFmt(M.fcf_margin, 15, 0)}">${_fmt(M.fcf_margin, '%')}</span></div>
        <div class="sc-metric-row"><span>FCF Yield ${_helpIcon('fcf_yield')}</span><span class="${_gradeFmt(M.fcf_yield, 5, 0)}">${_fmt(M.fcf_yield, '%')}</span></div>
        <div class="sc-metric-row"><span>영업이익률 ${_helpIcon('op_margin')}</span><span class="${_gradeFmt(M.operating_margin, 15, 0)}">${_fmt(M.operating_margin, '%')}</span></div>
        <div class="sc-metric-row"><span>매출총이익률 ${_helpIcon('gross_margin')}</span><span class="${_gradeFmt(M.gross_margin, 30, 0)}">${_fmt(M.gross_margin, '%')}</span></div>
        <div class="sc-metric-row"><span>Piotroski F-Score ${_helpIcon('f_score')}</span><span class="${_gradeFmt(M.f_score, 7, 3)}">${M.f_score != null ? M.f_score + '/9' : '<span class="m-na">—</span>'}</span></div>
        <div class="sc-metric-row"><span>Net Debt / EBITDA ${_helpIcon('net_debt')}</span><span class="${M.net_debt_to_ebitda != null ? (M.net_debt_to_ebitda < 2 ? 'good' : (M.net_debt_to_ebitda > 4 ? 'bad' : '')) : ''}">${_fmt(M.net_debt_to_ebitda, 'x')}</span></div>
      </div>

      <div class="sc-metric-section">
        <div class="sc-section-title">Value <small>밸류에이션 — 비싼가/싼가</small>${_scBadge(F.value)}</div>
        <div class="sc-metric-row"><span>PER (TTM)</span><span class="${M.per == null ? '' : M.per <= 0 ? 'bad' : M.per < 15 ? 'good' : M.per > 40 ? 'bad' : ''}">${M.per == null ? '<span class="m-na">—</span>' : M.per <= 0 ? '적자' : M.per.toFixed(1) + 'x'}</span></div>
        <div class="sc-metric-row"><span>PBR</span><span class="${M.pbr == null ? '' : M.pbr < 1 ? 'good' : M.pbr > 8 ? 'bad' : ''}">${_fmt(M.pbr, 'x')}</span></div>
        <div class="sc-metric-row"><span>PEG Ratio ${_helpIcon('peg')}</span><span class="${M.peg != null ? (M.peg < 1 ? 'good' : (M.peg > 2 ? 'bad' : '')) : ''}">${_fmt(M.peg, 'x')}</span></div>
        <div class="sc-metric-row"><span>EV / EBITDA ${_helpIcon('ev_ebitda')}</span><span class="${M.ev_ebitda != null ? (M.ev_ebitda < 12 ? 'good' : (M.ev_ebitda > 25 ? 'bad' : '')) : ''}">${_fmt(M.ev_ebitda, 'x')}</span></div>
        <div class="sc-metric-row"><span>FCF Yield ${_helpIcon('fcf_yield')}</span><span class="${_gradeFmt(M.fcf_yield, 5, 0)}">${_fmt(M.fcf_yield, '%')}</span></div>
      </div>

      <div class="sc-metric-section">
        <div class="sc-section-title">Growth <small>성장 — 매출·이익·마진</small>${_scBadge(F.growth)}</div>
        <div class="sc-metric-row"><span>매출 성장 (YoY)</span><span class="${_signClass(M.revenue_growth_pct)}">${M.revenue_growth_pct == null ? '<span class="m-na">—</span>' : (M.revenue_growth_pct >= 0 ? '+' : '') + M.revenue_growth_pct.toFixed(1) + '%'}</span></div>
        <div class="sc-metric-row"><span>이익 성장 (YoY)</span><span class="${_signClass(M.earnings_growth_pct)}">${M.earnings_growth_pct == null ? '<span class="m-na">—</span>' : (M.earnings_growth_pct >= 0 ? '+' : '') + M.earnings_growth_pct.toFixed(1) + '%'}</span></div>
        <div class="sc-metric-row"><span>마진 추세 (영업 YoY) ${_helpIcon('op_yoy')}</span><span class="${_signClass(M.operating_margin_yoy)}">${_fmt(M.operating_margin_yoy, 'pp')}</span></div>
        <div class="sc-metric-row"><span>마진 추세 (매출총 YoY) ${_helpIcon('gm_yoy')}</span><span class="${_signClass(M.gross_margin_yoy)}">${_fmt(M.gross_margin_yoy, 'pp')}</span></div>
      </div>

      <div class="sc-metric-section">
        <div class="sc-section-title">Momentum & Signals <small>시장 위치 · 수급</small>${_scBadge(F.momentum)}</div>
        <div class="sc-metric-row"><span>52주 위치</span><span class="${MP.pos_52w == null ? '' : MP.pos_52w >= 70 ? 'good' : MP.pos_52w < 30 ? 'bad' : ''}">${MP.pos_52w == null ? '<span class="m-na">—</span>' : MP.pos_52w.toFixed(0) + '%'}<small> 저점 0 · 고점 100</small></span></div>
        <div class="sc-metric-row"><span>20일 / 60일 수익률</span><span><b class="${_signClass(MP.return_20d)}">${_sgn(MP.return_20d)}</b> <small>/</small> <b class="${_signClass(MP.return_60d)}">${_sgn(MP.return_60d)}</b></span></div>
        <div class="sc-metric-row"><span>6개월 수익률</span><span class="${_signClass(M.return_6m_pct)}">${_sgn(M.return_6m_pct)}</span></div>
        <div class="sc-metric-row"><span>베타 (변동성)</span><span class="${MP.beta == null ? '' : MP.beta > 1.5 ? 'bad' : ''}">${MP.beta == null ? '<span class="m-na">—</span>' : MP.beta.toFixed(2)}<small> ${MP.beta == null ? '' : MP.beta > 1.5 ? '고변동' : MP.beta < 0.7 ? '저변동' : '시장 수준'}</small></span></div>
        <div class="sc-metric-row"><span>EPS Revision (30일) ${_helpIcon('eps_rev')}</span><span class="${_signClass(rev.score_pct)}">${rev.score_pct != null ? ((rev.score_pct>=0?'+':'') + rev.score_pct + '%') : '<span class="m-na">—</span>'}${rev.up_30d != null ? ` <small>(↑${rev.up_30d} ↓${rev.down_30d})</small>` : ''}</span></div>
        ${isKR ? '' : `<div class="sc-metric-row"><span>Short % of Float ${_helpIcon('short_float')}</span><span class="${M.short_pct_of_float != null ? (M.short_pct_of_float > 10 ? 'bad' : '') : ''}">${_fmt(M.short_pct_of_float, '%')}</span></div>`}
        ${!isKR ? `<div class="sc-metric-row"><span>내부자 매매 (6M) ${_helpIcon('insider')}</span><span class="${ins.net > 0 ? 'good' : (ins.net < 0 ? 'bad' : '')}">${ins.net != null ? `${ins.net >= 0 ? '+' : ''}${ins.net}건 <small>(↑${ins.buys} ↓${ins.sells})</small>` : '<span class="m-na">—</span>'}</span></div>` : ''}
        <div class="sc-metric-row"><span>주주환원 수익률 ${_helpIcon('shareholder')}</span><span class="${_gradeFmt(M.shareholder_yield_pct, 4, 0)}">${_fmt(M.shareholder_yield_pct, '%')}${M.buyback_yield_pct != null ? ` <small>(배당 ${(M.dividend_yield_pct||0).toFixed(1)}+자사주 ${M.buyback_yield_pct.toFixed(1)})</small>` : ''}</span></div>
      </div>
    </div>
  `;

  return metricsHTML;
}

const _FJ_DESC = {
  quality:  ['Quality', '돈을 잘 버는 사업인가 — ROE·영업이익률·FCF·F-Score·부채·이자보상배율'],
  value:    ['Value', '주가가 싼가 — PER·PEG·EV/EBITDA·FCF 수익률 (적자·음수 배수는 낮은 점수)'],
  growth:   ['Growth', '성장하는가 — 매출·이익 성장률, 매출총이익률 추세'],
  momentum: ['Momentum', '시장이 좋아하기 시작했는가 — 1·6개월 수익률, EPS 추정치 수정, 마진 추세'],
};

// 투자 판단 상단: 종합 등급 + 4축 팩터 가로 바
function renderScorecard(sc, stock) {
  const panel = document.getElementById('scorecardPanel');
  if (!panel) return;
  if (!sc || !sc.factors) { panel.classList.add('hidden'); panel.innerHTML = ''; return; }
  panel.classList.remove('hidden');
  const F = sc.factors;
  const ov = F.overall || {};
  const rows = ['quality', 'value', 'growth', 'momentum'].map((k) => {
    const f = F[k] || {}, v = f.score;
    return `<div class="fj-row sc-b-${_scBand(v)}">
      <span class="fj-name">${_FJ_DESC[k][0]}</span>
      <span class="fj-track"><i style="width:${v == null ? 0 : Math.max(2, Math.min(100, v))}%"></i></span>
      <b class="fj-score">${v == null ? '—' : Math.round(v)}</b>
      <span class="fj-g">${f.grade || '—'}</span>
    </div>`;
  }).join('');
  panel.innerHTML = `
    <div class="fj-card">
      <div class="fj-top">
        <div class="fj-overall sc-b-${_scBand(ov.score)}"><b>${ov.grade || '—'}</b><span>${ov.score != null ? Math.round(ov.score) + '점' : '—'}</span></div>
        <div class="fj-title">펀더멘털 종합<small>4개 팩터 평균 · 0~100${F.is_financial ? ' · 금융주 기준(ROE·PBR·배당)' : ''}</small></div>
        <button type="button" class="tc-info" aria-expanded="false" aria-controls="fjHelp" aria-label="팩터 설명 보기" onclick="_taToggleHelp(this)">${_INFO_SVG}</button>
      </div>
      <div class="fj-help tc-help" id="fjHelp" hidden>
        ${Object.values(_FJ_DESC).map(([n, d]) => `<p><b>${n}</b> ${d}</p>`).join('')}
        <p class="tc-tip">A+~A 우수 · B+~B 양호 · C+~C 보통 · D~F 주의. 자료가 절반도 없는 팩터는 50점 쪽으로 당겨 계산합니다.</p>
      </div>
      <div class="fj-rows">${rows}</div>
    </div>`;
}

// ── 투자 판단 카드 ────────────────────────────────────────
function renderFundamental(details, stock) {
  const el = document.getElementById('fundamentalList');
  let html = '';

  // ── 1. 회사 소개 (Groq AI 분석 우선, 없으면 영문 요약 폴백) ──────────
  const ov = (stock && stock.company_overview) ? stock.company_overview : null;
  if (ov) {
    const intro    = ov['기업소개'] || '';
    const business = ov['주요사업'] || '';
    const analysis = ov['기업분석'] || '';

    // 주요사업 bullet 처리
    const bizHtml = business
      ? '<ul class="co-biz-list">' +
          business.split('\n')
            .map(l => l.replace(/^[•\-\*]\s*/, '').trim())
            .filter(l => l)
            .map(l => `<li>${l}</li>`)
            .join('') +
        '</ul>'
      : '';

    html += `
      <div class="company-overview-card">
        <div class="co-header">
          <span class="co-title">기업 분석 (AI)</span>
          ${stock.sector ? `<span class="co-sector">${sectorKo(stock.sector)}${stock.industry ? ' · ' + stock.industry : ''}</span>` : ''}
        </div>
        ${intro    ? `<div class="co-section-label">기업 소개</div><p class="co-desc">${intro}</p>` : ''}
        ${bizHtml  ? `<div class="co-section-label">주요 사업</div>${bizHtml}` : ''}
        ${analysis ? `<div class="co-section-label">기업 분석</div><p class="co-desc">${analysis}</p>` : ''}
      </div>`;
  } else {
    // 폴백: 영문/번역된 business_summary — 전체 그대로 표시 (더보기 없음)
    const summary = (stock && stock.business_summary) ? stock.business_summary.trim() : '';
    if (summary) {
      // 줄바꿈 보존
      const formatted = summary.replace(/\n/g, '<br>');
      html += `
        <div class="company-overview-card">
          <div class="co-header">
              <span class="co-title">회사 소개</span>
            ${stock.sector ? `<span class="co-sector">${sectorKo(stock.sector)}${stock.industry ? ' · ' + stock.industry : ''}</span>` : ''}
          </div>
          <p class="co-desc">${formatted}</p>
        </div>`;
    }
  }

  // ── 2. 재무 건전성 | 분기 실적 (2단) ────────────────────────
  const sc = (window._lastAnalysis && window._lastAnalysis.scorecard) || null;
  const top = `<div class="fj-grid">${_fjHealthCard(sc, stock)}${_fjQuarterCard(stock)}</div>`
    + (/\.(KS|KQ)$/i.test((stock && stock.ticker) || '') ? '<div id="fjFlow" class="fj-flow-slot"></div>' : '')
    + (_earnEligible(stock) ? '<div id="fjEarn" class="fj-flow-slot"></div>' : '');

  // ── 3. 세부 지표 · 항목별 판단 (기본 접힘) ─────────────────────
  const detailHTML = _scDetailHTML(sc, stock);
  const more = detailHTML ? `
    <details class="vl-more" open>
      <summary>팩터별 세부 지표</summary>
      ${detailHTML}
    </details>` : '';

  el.innerHTML = top + html + more;
  if (document.getElementById('fjFlow')) loadKrFlow(stock.ticker);
  if (document.getElementById('fjEarn')) loadEarnings(stock.ticker);
}

const _FS_SHORT = {
  roa: 'ROA 흑자', cfo: '영업현금 흑자', d_roa: 'ROA 개선',
  accrual: '현금 > 순이익', d_lever: '레버리지 개선', d_cr: '유동성 개선',
  no_dilute: '신주 발행 없음', d_gm: '마진 개선', d_turn: '자산회전율 개선',
};

// ── 미국 종목 공매도 (Short Interest): 주요 지표 셀 · 투자 판단 1줄 요약. 한국 종목·자료 없음이면 숨김 ──
const _SI_BADGE = { high: 'si-high', elevated: 'si-elev', normal: 'si-norm', caution: 'si-elev', low: 'si-low' };
const _SI_TIP_US = '유동주식 대비 공매도 잔고 비율 · 청산일수(평균 거래량으로 공매도 물량을 다 갚는 데 걸리는 거래일). FINRA 반월 공시 기준이라 2주가량 늦습니다.';
const _SI_TIP_KR = '상장주식수 대비 공매도 순보유잔고 비율(KRX). 잔고는 보고 의무 발생일 기준이라 거래일보다 2영업일(T+2) 늦습니다. 0.5% 미만 낮음 · 0.5~2% 보통 · 2~5% 주의 · 5% 이상 높음. 거래 비중 = 그날 거래대금 중 공매도 비중(KRX+NXT).';
function _siLabel(text, tip) {
  const l = document.getElementById('shortLabel'), i = document.getElementById('shortInfo');
  if (l) l.textContent = text;
  if (i) { i.title = tip; i.setAttribute('aria-label', tip); }
}
function _siMom(v) { return v == null ? '' : `전월 대비 ${v > 0 ? '+' : ''}${v.toFixed(1)}%`; }
function _renderShortCell(si) {
  const cell = document.getElementById('shortCell');
  if (!cell) return;
  if (!si) { cell.classList.add('hidden'); return; }
  cell.classList.remove('hidden');
  _siLabel('공매도 비율', _SI_TIP_US);
  document.getElementById('shortPct').textContent = si.short_pct != null ? `${si.short_pct.toFixed(1)}%` : '—';
  const sub = [si.short_ratio != null ? `청산 ${si.short_ratio.toFixed(1)}일` : '', _siMom(si.mom_change_pct)].filter(Boolean).join(' · ');
  document.getElementById('shortSub').textContent = sub;
  const b = document.getElementById('shortBadge');
  b.className = `si-badge ${_SI_BADGE[si.level] || 'si-norm'}`;
  b.textContent = si.label;
  _fitShortCell();
}
// 주요 지표 그리드는 폭에 따라 열 수가 바뀐다(auto-fill). 공매도 셀이 윗줄의 남은 칸을 채우게:
// 앞 항목 n개, 열 c개 → 나머지 r = n % c. r=0 이면 마지막 줄 전체 폭, 아니면 c-r 칸 (3열 → 2칸, 4열 → 2칸, 2·5열 → 전체 폭)
function _fitShortCell() {
  const cell = document.getElementById('shortCell');
  if (!cell || cell.classList.contains('hidden')) return;
  const grid = cell.parentElement;
  const cols = getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length || 1;
  const n = [...grid.children].filter((e) => e !== cell && e.offsetParent !== null && !e.classList.contains('hidden')).length;
  const r = n % cols;
  cell.style.gridColumn = r === 0 ? '1 / -1' : `span ${cols - r}`;
  if (!grid._siRO && window.ResizeObserver) {
    let last = 0;
    grid._siRO = new ResizeObserver(() => { const w = Math.round(grid.clientWidth); if (w !== last) { last = w; requestAnimationFrame(_fitShortCell); } });
    grid._siRO.observe(grid);
  }
}
// ── 국내 보통주 공매도 순보유잔고 (KRX, /api/stock/<t>/short 2시간 캐시). ETF·우선주·실패면 칸·줄 모두 숨김 ──
const _krShortMem = {};
function _krShortEligible(stock) {
  return !!stock && /^\d{5}0\.(KS|KQ)$/i.test(stock.ticker || '') && stock.quote_type !== 'ETF';
}
async function loadKrShort(ticker) {
  let d = _krShortMem[ticker];
  if (!d) {
    try { d = await (await fetch(`/api/stock/${encodeURIComponent(ticker)}/short`)).json(); } catch (e) { d = null; }
    if (d && d.available) _krShortMem[ticker] = d;
  }
  if (!window._lastStock || window._lastStock.ticker !== ticker) return;   // 그새 다른 종목으로 바뀜
  if (!d || !d.available) return;
  _renderKrShortCell(d);
  const slot = document.getElementById('fjShortKr');
  if (slot) slot.outerHTML = _fjKrShortHTML(d);
}
const _pp = (v) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%p`;
function _renderKrShortCell(d) {
  const cell = document.getElementById('shortCell');
  if (!cell) return;
  cell.classList.remove('hidden');
  _siLabel('공매도 잔고', _SI_TIP_KR);
  document.getElementById('shortPct').textContent = `${d.balance_pct.toFixed(2)}%`;
  document.getElementById('shortSub').textContent =
    [d.trade_pct != null ? `거래 비중 ${d.trade_pct.toFixed(1)}%` : '', `기준 ${d.as_of_label}`].filter(Boolean).join(' · ');
  const b = document.getElementById('shortBadge');
  b.className = `si-badge ${_SI_BADGE[d.level] || 'si-norm'}`;
  b.textContent = d.label;
  _fitShortCell();
}
function _fjKrShortHTML(d) {
  const ch = (v, lab) => (v == null ? '' : `<span class="fj-si ${v > 0 ? 'si-up' : v < 0 ? 'si-dn' : ''}">${lab} ${_pp(v)}</span>`);
  const parts = [
    `<span class="fj-si">상장주식 대비 잔고 <b>${d.balance_pct.toFixed(2)}%</b></span>`,
    d.trade_pct != null ? `<span class="fj-si">거래 비중 <b>${d.trade_pct.toFixed(1)}%</b></span>` : '',
    `<span class="fj-si">기준 ${d.as_of_label} (T+2)</span>`,
    ch(d.change_1d_pp, '전일'), ch(d.change_5d_pp, '5일'),
  ].filter(Boolean);
  return `
      <div class="fj-short">
        <span class="fj-short-h">공매도 현황 <span class="si-info" tabindex="0" title="${_SI_TIP_KR}" aria-label="${_SI_TIP_KR}">ⓘ</span></span>
        <span class="fj-short-v">${parts.join('')}</span>
        <span class="si-badge ${_SI_BADGE[d.level] || 'si-norm'}">${d.label}</span>
      </div>`;
}
function _fjShortRow(stock) {
  if (_krShortEligible(stock)) {
    const d = _krShortMem[stock.ticker];
    return d ? _fjKrShortHTML(d) : '<div id="fjShortKr" hidden></div>';
  }
  const si = stock && stock.short_interest;
  if (!si) return '';
  const parts = [
    si.short_pct != null ? `<span class="fj-si">유동주식 대비 <b>${si.short_pct.toFixed(1)}%</b></span>` : '',
    si.short_ratio != null ? `<span class="fj-si">청산 <b>${si.short_ratio.toFixed(1)}일</b></span>` : '',
    si.mom_change_pct != null ? `<span class="fj-si ${si.mom_change_pct > 0 ? 'si-up' : si.mom_change_pct < 0 ? 'si-dn' : ''}">${_siMom(si.mom_change_pct)}</span>` : '',
  ].filter(Boolean);
  const tip = '공매도 잔고가 유동주식의 15% 이상이거나 청산에 5거래일 이상 걸리면 숏 스퀴즈(공매도 상환 매수로 인한 급등) 가능성과 하락 베팅이 함께 큰 상태입니다. 8%·3일 이상은 높음. FINRA 반월 공시 기준이라 2주가량 늦습니다.';
  return `
      <div class="fj-short">
        <span class="fj-short-h">공매도 현황 <span class="si-info" tabindex="0" title="${tip}" aria-label="${tip}">ⓘ</span></span>
        <span class="fj-short-v">${parts.join('')}</span>
        <span class="si-badge ${_SI_BADGE[si.level] || 'si-norm'}">${si.label}</span>
      </div>`;
}

function _fjHealthCard(sc, stock) {
  const M = (sc && sc.metrics) || {};
  const fd = M.f_score_detail;
  const fsBand = fd ? (fd.score >= 7 ? 'hi' : fd.score >= 4 ? 'mid' : 'lo') : 'na';
  const fsWord = { hi: '우수', mid: '보통', lo: '취약' }[fsBand] || '';
  const fin = !!M.is_financial;
  const dots = fd ? fd.items.map((it) =>
    `<i class="fj-dot ${it.pass === true ? 'on' : it.pass === false ? 'off' : 'na'}" title="${it.label}: ${it.pass === true ? '충족' : it.pass === false ? '미충족' : '자료 없음'}"></i>`).join('') : '';
  const pct = (v, d = 1) => (v == null ? '—' : `${(+v).toFixed(d)}%`);
  const icr = M.interest_coverage != null ? `${M.interest_coverage.toFixed(1)}배` : (M.no_interest_expense ? '이자비용 없음' : '—');
  const shy = M.shareholder_yield_pct != null
    ? `${M.shareholder_yield_pct.toFixed(2)}%<small> 배당 ${(M.dividend_yield_pct || 0).toFixed(2)} + 자사주 ${(M.buyback_yield_pct || 0).toFixed(2)}</small>` : '—';
  const rows = [
    ['ROE', pct(M.roe_pct), M.roe_pct == null ? '' : M.roe_pct >= 15 ? '우수' : M.roe_pct >= 8 ? '보통' : '낮음'],
    ['부채비율', fin ? '—' : pct(M.debt_to_equity_pct, 0), fin ? '금융업은 예금·보험부채라 비교하지 않음' : M.debt_to_equity_pct == null ? '' : M.debt_to_equity_pct < 100 ? '안정' : M.debt_to_equity_pct < 200 ? '보통' : '높음'],
    ['유동비율', fin ? '—' : (M.current_ratio == null ? '—' : `${M.current_ratio.toFixed(2)}배`), fin ? '금융업 해당 없음' : M.current_ratio == null ? '' : M.current_ratio >= 1.5 ? '안정' : M.current_ratio >= 1 ? '보통' : '주의'],
    ['이자보상배율', fin ? '—' : icr, fin ? '금융업 해당 없음' : M.no_interest_expense && M.interest_coverage == null ? '무차입' : M.interest_coverage == null ? '' : M.interest_coverage >= 5 ? '안정' : M.interest_coverage >= 1.5 ? '보통' : '위험'],
    ['주주환원율', shy, M.dilution_pct ? `순발행 희석 ${M.dilution_pct.toFixed(2)}%` : ''],
  ];
  const tone = [
    M.roe_pct == null ? '' : M.roe_pct >= 12 ? 'good' : M.roe_pct < 5 ? 'bad' : '',
    fin || M.debt_to_equity_pct == null ? '' : M.debt_to_equity_pct < 100 ? 'good' : M.debt_to_equity_pct >= 200 ? 'bad' : '',
    fin || M.current_ratio == null ? '' : M.current_ratio >= 1.5 ? 'good' : M.current_ratio < 1 ? 'bad' : '',
    fin ? '' : M.interest_coverage != null ? (M.interest_coverage >= 5 ? 'good' : M.interest_coverage < 1.5 ? 'bad' : '') : (M.no_interest_expense ? 'good' : ''),
    M.dilution_pct ? 'bad' : M.shareholder_yield_pct == null ? '' : M.shareholder_yield_pct >= 3 ? 'good' : '',
  ];
  return `
    <section class="fj-box">
      <div class="fj-box-h"><h4>재무 건전성</h4>
        ${fd ? `<span class="fs-badge fs-${fsBand}" title="Piotroski F-Score">F-Score ${fd.score}/9 · ${fsWord}</span>` : '<span class="fj-fs">F-Score 자료 없음</span>'}</div>
      ${fd ? `<div class="fj-dots" role="img" aria-label="F-Score ${fd.score}/9">${dots}</div>
        <div class="fs-chips" role="list">${fd.items.map((it) => {
          const st = it.pass === true ? 'ok' : it.pass === false ? 'no' : 'na';
          const mark = st === 'ok' ? '✓' : st === 'no' ? '✕' : '–';
          return `<span class="fs-chip fs-${st}" role="listitem" title="${it.label}: ${st === 'ok' ? '충족' : st === 'no' ? '미충족' : '자료 없음'}"><i aria-hidden="true">${mark}</i>${_FS_SHORT[it.key] || it.label}</span>`;
        }).join('')}</div>`
        : `<p class="fj-note">${fin ? '금융업은 유동비율·매출총이익률이 정의되지 않아 F-Score 를 계산하지 않습니다.' : '연간 재무제표가 2년치 이상 없어 계산하지 못했습니다.'}</p>`}
      <div class="fj-table">${rows.map(([k, v, n], i) => `<div class="fj-tr"><span>${k}</span><b class="${tone[i] || ''}">${v}</b><em>${n}</em></div>`).join('')}</div>
      ${_fjShortRow(stock)}
    </section>`;
}

function _fjQuarterCard(stock) {
  const krw = stock && stock.currency === 'KRW';
  const money = (v) => {
    if (v == null) return '—';
    const a = Math.abs(v), sg = v < 0 ? '-' : '';
    if (krw) return a >= 1e12 ? `${sg}${(a / 1e12).toFixed(1)}조` : a >= 1e8 ? `${sg}${Math.round(a / 1e8).toLocaleString()}억` : `${sg}${a.toLocaleString()}`;
    return a >= 1e9 ? `${sg}$${(a / 1e9).toFixed(1)}B` : a >= 1e6 ? `${sg}$${Math.round(a / 1e6)}M` : `${sg}$${a.toLocaleString()}`;
  };
  const qlab = (p) => { if (!p) return ''; const [y, m] = p.split('-').map(Number); return `${String(y).slice(2)}.Q${Math.ceil(m / 3)}`; };
  // 확정 실적만 (발표 전 추정치는 제외)
  const act = (arr) => (arr || []).filter((q) => q && q.actual != null).sort((x, y) => (x.period < y.period ? -1 : 1)).slice(-5);
  const trend = (list) => {
    if (list.length < 2) return null;
    const last = list[list.length - 1].actual;
    const yearAgo = list.length >= 5 ? list[list.length - 5].actual : list[0].actual;
    const prev = list[list.length - 2].actual;
    if (!yearAgo) return null;
    const ch = (last - yearAgo) / Math.abs(yearAgo) * 100;
    const qoq = prev ? (last - prev) / Math.abs(prev) * 100 : 0;
    const basis = list.length >= 5 ? '전년 동기 대비' : `${list.length - 1}분기 전 대비`;
    const lab = ch < 0 ? '감소' : (ch >= 5 && qoq > -5) ? '성장' : '둔화';
    return { lab, cls: { 성장: 'bull', 둔화: 'neu', 감소: 'bear' }[lab], txt: `${basis} ${ch >= 0 ? '+' : ''}${ch.toFixed(1)}%` };
  };
  const chart = (title, list) => {
    if (list.length < 2) return '';
    const t = trend(list);
    const mx = Math.max(...list.map((q) => Math.abs(q.actual)), 1);
    return `
      <div class="fj-q">
        <div class="fj-q-h"><span>${title}</span>${t ? `<span class="tc-stat tc-${t.cls}"><i></i>${t.lab}</span><small>${t.txt}</small>` : ''}</div>
        <div class="fj-bars">${list.map((q) => `
          <div class="fj-bar">
            <span class="fj-bv">${money(q.actual)}</span>
            <span class="fj-bw"><i class="${q.actual < 0 ? 'neg' : ''}" style="height:${Math.max(3, Math.abs(q.actual) / mx * 100).toFixed(1)}%"></i></span>
            <span class="fj-bl">${qlab(q.period)}</span>
          </div>`).join('')}</div>
      </div>`;
  };
  const rev = act(stock && stock.revenue_quarters), oi = act(stock && stock.oi_quarters);
  const body = chart('매출', rev) + chart('영업이익', oi);
  return `
    <section class="fj-box">
      <div class="fj-box-h"><h4>분기 실적 추이</h4><span class="fj-fs">확정 실적 · 최근 ${Math.max(rev.length, oi.length)}개 분기</span></div>
      ${body || '<p class="fj-note">분기 실적 자료가 부족합니다.</p>'}
    </section>`;
}

function toggleCoDesc() {
  const fullText = window._lastCoSummary || '';
  const p   = document.getElementById('coDescText');
  const btn = p ? p.parentElement.querySelector('.co-more-btn') : null;
  if (!p || !fullText) return;
  if (p.dataset.expanded === '1') {
    const sentences = fullText.match(/[^.!?]+[.!?]+/g) || [fullText];
    p.innerHTML = sentences.slice(0, 3).join(' ').slice(0, 320) + '<span>...</span>';
    p.dataset.expanded = '0';
    if (btn) btn.textContent = '더보기 ▼';
  } else {
    // 줄바꿈 보존
    p.innerHTML = fullText.replace(/\n/g, '<br>');
    p.dataset.expanded = '1';
    if (btn) btn.textContent = '접기 ▲';
  }
}

// ── 매매 전략 탭: 가격 구간 맵 · 신규 진입자 / 기존 보유자 대응 ─────────────────
function _stFmt(v, krw) {
  if (v == null || !isFinite(v)) return '—';
  return krw ? Math.round(v).toLocaleString('ko-KR') + '원'
             : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
const _stPct = (p) => (p == null || !isFinite(p) ? '' : `${p > 0 ? '+' : ''}${p.toFixed(1)}%`);
const _TREND_TXT = { 'strong-uptrend': '강한 상승 추세', 'uptrend': '상승 추세', 'sideways': '횡보',
                     'downtrend': '하락 추세', 'strong-downtrend': '강한 하락 추세' };

function _stPaperHref(analysis, stock, plan) {
  const q = new URLSearchParams({ buy: stock.ticker });
  const krw = stock.currency === 'KRW';
  const r = (v) => krw ? String(Math.round(v)) : Number(v).toFixed(2);
  const ok = plan && plan.mode === 'target' && analysis.trade_recommendation !== 'avoid' && !plan.stop.broken;
  if (ok) {
    const cp = plan.current, t = Number(analysis.target_price) > cp ? Number(analysis.target_price) : plan.upper.high;
    q.set('target', r(t)); q.set('stop', r(plan.stop.price));
    q.set('memo', '[매매 전략] 목표가·손절가 자동 예약');
  }
  return { href: '/trading?' + q.toString(), withPlan: ok };
}

function _stMap(plan, krw) {
  const s = plan.stop.price, sl = plan.support.low, sh = plan.support.high, c = plan.current, ul = plan.upper.low, uh = plan.upper.high;
  const lo = Math.min(s, sl), hi = Math.max(uh, c);
  const pad = (hi - lo) * 0.08 || c * 0.02;
  const a = lo - pad, b = hi + pad;
  const x = (v) => ((v - a) / (b - a) * 100);
  const up = plan.mode === 'target' ? '목표가' : '반등 저항';
  const marks = [
    { k: 'stop', v: s, name: '손절가', pct: plan.stop.pct },
    { k: 'sup', v: sh, name: '주요 지지', pct: plan.support.pct },
    { k: 'up', v: ul, name: up, pct: plan.upper.pct_low },
  ];
  // 아래 라벨이 겹치면 두 번째 줄로 내린다
  let lastX = -99, row = 0;
  const lab = marks.map((m) => {
    const px = x(m.v);
    row = (px - lastX < 22 && row === 0) ? 1 : 0;
    lastX = px;
    const align = px < 12 ? 'l' : px > 88 ? 'r' : 'c';
    return `<div class="st-lab st-lab-${m.k} st-row${row} st-al-${align}" style="left:${px.toFixed(2)}%">
      <span class="st-lab-n">${m.name}</span><b>${_stFmt(m.v, krw)}</b><span class="st-lab-p">${_stPct(m.pct)}</span></div>`;
  }).join('');
  const two = /st-row1/.test(lab);
  return `
    <div class="st-map st-m-${plan.mode}${two ? ' st-two' : ''}" role="img" aria-label="손절 ${_stFmt(s, krw)}, 지지 ${_stFmt(sh, krw)}, 현재가 ${_stFmt(c, krw)}, ${up} ${_stFmt(ul, krw)}~${_stFmt(uh, krw)}">
      <div class="st-cur-lab" style="left:${x(c).toFixed(2)}%"><span>현재가</span><b>${_stFmt(c, krw)}</b></div>
      <div class="st-track">
        <i class="st-risk" style="left:${x(s).toFixed(2)}%;width:${(x(c) - x(s)).toFixed(2)}%"></i>
        <i class="st-zone st-zone-sup" style="left:${x(sl).toFixed(2)}%;width:${Math.max(0.8, x(sh) - x(sl)).toFixed(2)}%"></i>
        <i class="st-zone st-zone-up st-${plan.mode}" style="left:${x(ul).toFixed(2)}%;width:${Math.max(0.8, x(uh) - x(ul)).toFixed(2)}%"></i>
        <i class="st-tick st-tick-stop" style="left:${x(s).toFixed(2)}%"></i>
        <i class="st-tick st-tick-sup" style="left:${x(sh).toFixed(2)}%"></i>
        <i class="st-now" style="left:${x(c).toFixed(2)}%"></i>
      </div>
      <div class="st-labs">${lab}</div>
    </div>`;
}

function renderStrategy(analysis, stock) {
  const el = document.getElementById('strategyPanel');
  if (!el) return;
  const plan = analysis && analysis.price_plan;
  if (!plan) { el.innerHTML = '<p class="no-data">가격 구간을 계산할 데이터가 부족합니다.</p>'; return; }
  const krw = stock.currency === 'KRW';
  const f = (v) => _stFmt(v, krw);
  const isRes = plan.mode === 'resistance';
  const trend = analysis.trend || 'sideways';
  const ma20 = analysis.ma_analysis && analysis.ma_analysis.MA20;
  const rec = analysis.trade_recommendation;
  const rr = (v) => (v == null ? '—' : `1 : ${v.toFixed(1)}`);
  const tags = [
    _TREND_TXT[trend] || '횡보',
    rec === 'avoid' ? '진입 비추천' : rec === 'strong' ? '추세 매수' : '분할 접근',
    plan.rr_now != null ? `현재가 손익비 ${rr(plan.rr_now)}<small class="st-basis">${isRes ? '저항 하단 기준' : '목표 하단 기준'}</small>` : null,
    analysis.overheat ? '단기 과열' : null,
    plan.stop.broken ? '손절선 이탈' : null,
  ].filter(Boolean);
  const pb = _stPaperHref(analysis, stock, plan);
  const upName = isRes ? '반등 저항 구간 (매물대)' : '목표가 구간';

  // 신규 진입자
  let nTitle, nTone, nLines = [];
  if (plan.stop.broken) {
    nTitle = '신규 진입 보류'; nTone = 'bear';
    nLines.push(`기존 손절선(${f(plan.stop.original)}) 아래로 내려왔어요. 지지선을 다시 회복하는지 먼저 확인하세요.`);
  } else if (isRes) {
    nTitle = '관망 · 추세 전환 확인'; nTone = 'bear';
    nLines.push(`지금은 ${_TREND_TXT[trend] || '약세'}라 위쪽 ${f(plan.upper.low)} 부근이 반등 때 매물이 나오는 저항이에요.`);
    if (ma20 && ma20.value) nLines.push(`확인 조건 1 · 종가가 20일선(${f(ma20.value)}) 위로 회복`);
    nLines.push(`확인 조건 2 · 거래량을 동반한 반등 (평소의 1.5배 이상)`);
    nLines.push(`지금 사면 손익비 ${rr(plan.rr_now)} <small class="st-basis">저항 하단 기준</small> — ${plan.rr_now != null && plan.rr_now >= 1.5 ? '숫자는 괜찮지만 추세가 받쳐 주지 않아요' : '위험 대비 기대 수익이 작아요'}`);
  } else {
    const ready = analysis.entry_confidence === 'immediate';
    nTitle = ready ? '분할 진입 가능' : '진입 추천가까지 대기'; nTone = ready ? 'bull' : 'neu';
    if (plan.entry) nLines.push(`진입 추천가 ${f(plan.entry)} (현재가 대비 ${_stPct((plan.entry - plan.current) / plan.current * 100)}) · 손익비 ${rr(plan.rr_entry)} <small class="st-basis">목표 하단 기준</small>`);
    nLines.push(`손절 ${f(plan.stop.price)} (${_stPct(plan.stop.pct)}) · 목표 ${f(plan.upper.low)}~${f(plan.upper.high)} (${_stPct(plan.upper.pct_low)})`);
    nLines.push(ready ? '한 번에 사지 말고 2~3번에 나눠 들어가세요.' : `주요 지지 ${f(plan.support.high)}(${plan.support.basis}) 부근까지 눌릴 때 나눠 사는 편이 유리해요.`);
    if (analysis.overheat) nLines.push('단기 과열 상태라 추격 매수는 피하고 눌림을 기다리세요.');
  }

  // 기존 보유자
  let hTitle, hTone, hLines = [];
  if (plan.stop.broken) {
    hTitle = '손절 기준 이탈 · 비중 축소'; hTone = 'bear';
    hLines.push(`종가가 손절선(${f(plan.stop.original)}) 아래예요. 원칙대로라면 정리, 최소한 반등 때 비중을 줄이세요.`);
    hLines.push(`다음 방어선 ${f(plan.stop.price)} (${_stPct(plan.stop.pct)}) 이탈 시 남은 물량도 정리를 고려하세요.`);
  } else if (isRes) {
    hTitle = '반등 시 비중 축소'; hTone = 'neu';
    hLines.push(`${upName} ${f(plan.upper.low)}~${f(plan.upper.high)} (${_stPct(plan.upper.pct_low)}) 에서 일부 매도해 비중을 줄이세요.`);
    hLines.push(`주요 지지 ${f(plan.support.high)} (${plan.support.basis}, ${_stPct(plan.support.pct)}) 이탈 시 추가 축소.`);
    hLines.push(`손절 기준 ${f(plan.stop.price)} (${_stPct(plan.stop.pct)}) 종가 이탈 시 정리.`);
  } else {
    hTitle = '보유 · 분할 익절'; hTone = 'bull';
    hLines.push(`${upName} ${f(plan.upper.low)}~${f(plan.upper.high)} (${_stPct(plan.upper.pct_low)}) 도달 시 절반 익절.`);
    hLines.push(`익절 후 남은 물량은 손절선을 주요 지지 ${f(plan.support.high)} 로 올려 수익을 지키세요.`);
    hLines.push(`손절 기준 ${f(plan.stop.price)} (${_stPct(plan.stop.pct)}) 종가 이탈 시 정리.`);
    if (analysis.overheat) hLines.push('단기 과열 구간이라 일부 차익 실현도 괜찮아요.');
  }

  const rows = [
    ['손절 기준', f(plan.stop.price), _stPct(plan.stop.pct), plan.stop.broken ? `기존 ${f(plan.stop.original)} 이탈` : '종가 기준 이탈 시 정리'],
    ['주요 지지', plan.support.low < plan.support.high ? `${f(plan.support.low)} ~ ${f(plan.support.high)}` : f(plan.support.high), _stPct(plan.support.pct), plan.support.basis],
    ['현재가', f(plan.current), '', _TREND_TXT[trend] || ''],
    [upName, `${f(plan.upper.low)} ~ ${f(plan.upper.high)}`, `${_stPct(plan.upper.pct_low)} ~ ${_stPct(plan.upper.pct_high)}`, isRes ? '반등 때 매물이 나오는 자리' : '분할 익절 구간'],
  ];
  const scen = (title, sub, tone, lines) => `
    <section class="st-card">
      <div class="st-card-h"><span class="st-card-k">${sub}</span><span class="tc-stat tc-${tone}"><i></i>${title}</span></div>
      <ul class="st-list">${lines.map((l) => `<li>${l}</li>`).join('')}</ul>
    </section>`;

  el.innerHTML = `
    <div class="st-head">
      <div class="st-head-l">
        <h3 class="st-title">가격 구간 맵</h3>
        <div class="st-tags">${tags.map((t) => `<span class="st-tag">${t}</span>`).join('')}</div>
      </div>
      <a class="st-buy" href="${pb.href}" title="${pb.withPlan ? '목표가·손절가를 채운 모의 매수 창을 엽니다' : '모의 매수 창을 엽니다'}">
        이 구간으로 모의 매수<span>${pb.withPlan ? '목표·손절 자동 채움' : '진입 비추천 · 목표·손절 없이 열림'}</span></a>
    </div>
    ${_stMap(plan, krw)}
    <div class="st-rows">${rows.map(([k, v, p, n], i) => `
      <div class="st-row${i === 2 ? ' st-row-now' : ''}"><span class="st-k">${k}</span><b class="st-v">${v}</b><span class="st-p ${i === 0 ? 'down' : (i === 3 && !isRes) ? 'up' : ''}">${p}</span><span class="st-n">${n}</span></div>`).join('')}
    </div>
    <div class="st-scen">
      ${scen(nTitle, '신규 진입자 대응', nTone, nLines)}
      ${scen(hTitle, '기존 보유자 대응', hTone, hLines)}
    </div>
    <p class="st-note">최근 1개월 가격·이동평균·변동폭(ATR)으로 계산한 참고용 구간이에요. 투자 판단의 책임은 본인에게 있어요.</p>`;
}

// ── 수익률 트래커 ─────────────────────────────────────────
function renderReturnTracker(stock) {
  const el = document.getElementById('returnTracker');
  if (!el) return;
  const periods = [
    { label: '5일',  key: 'return_5d' },
    { label: '1개월', key: 'return_1m' },
    { label: '3개월', key: 'return_3m' },
    { label: '1년',  key: 'return_1y' },
  ];
  el.innerHTML = periods.map(p => {
    const val = stock[p.key];
    if (val == null) return `
      <div class="return-row">
        <span class="return-label">${p.label}</span>
        <div class="return-bar-wrap"><div class="return-bar-track"><div class="return-bar-fill neutral" style="width:0%"></div></div></div>
        <span class="return-value neutral">—</span>
      </div>`;
    const cls   = val >= 0 ? 'up' : 'down';
    const sign  = val >= 0 ? '+' : '';
    const width = Math.min(Math.abs(val) * 2, 100);
    return `
      <div class="return-row">
        <span class="return-label">${p.label}</span>
        <div class="return-bar-wrap">
          <div class="return-bar-track">
            <div class="return-bar-fill ${cls}" style="width:${width}%"></div>
          </div>
        </div>
        <span class="return-value ${cls}">${sign}${val}%</span>
      </div>`;
  }).join('');
}

// ── 종합 신호 요약 ────────────────────────────────────
function renderSignalSummary(analysis) {
  const el = document.getElementById('signalSummary');
  if (!el) return;

  const rows = (analysis.details || []).map(d => {
    const arrow = d.color === 'bullish' ? '▲' : d.color === 'bearish' ? '▼' : '—';
    return `
      <div class="signal-row">
        <span class="signal-row-name">${d.indicator}</span>
        <span class="signal-row-state ${d.color}">${d.state}</span>
        <span class="signal-row-arrow ${d.color}">${arrow}</span>
      </div>`;
  }).join('');

  const score = analysis.score ?? 0;
  const vcls  = analysis.verdict_color || 'neutral';
  const clsMap = {
    'strong-buy': 'rgba(63,185,80,0.15)', 'buy': 'rgba(63,185,80,0.08)',
    'neutral':    'rgba(210,153,34,0.08)',
    'sell':       'rgba(248,81,73,0.08)', 'strong-sell': 'rgba(248,81,73,0.15)',
  };
  const borderMap = {
    'strong-buy': 'var(--green)', 'buy': 'var(--green)',
    'neutral':    'var(--yellow)',
    'sell':       'var(--red)',   'strong-sell': 'var(--red)',
  };
  const textMap = {
    'strong-buy': 'var(--green)', 'buy': 'var(--green)',
    'neutral':    'var(--yellow)',
    'sell':       'var(--red)',   'strong-sell': 'var(--red)',
  };

  el.innerHTML = rows + `
    <div class="signal-divider"></div>
    <div class="signal-total" style="background:${clsMap[vcls]};border-color:${borderMap[vcls]}">
      <span class="signal-total-label" style="color:${textMap[vcls]}">${analysis.verdict}</span>
      <span class="signal-total-score" style="color:${textMap[vcls]}">${score > 0 ? '+' : ''}${score}</span>
    </div>`;
}

// ── 시장 컨텍스트 ─────────────────────────────────────
function _esc(t) {
  return String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function renderMarketContext(market, isKRW, mood) {
  const card = document.getElementById('marketContextCard');
  const list = document.getElementById('marketContextList');
  const moodEl = document.getElementById('marketMood');
  if (!card || !list) return;
  card.classList.remove('hidden');
  if (moodEl) {
    const tones = { danger: 1, caution: 1, good: 1, info: 1, neutral: 1 };
    if (mood && mood.title) {
      moodEl.className = `mc-mood tone-${tones[mood.tone] ? mood.tone : 'neutral'}`;
      moodEl.innerHTML = `<div class="mc-mood-title">${_esc(mood.title)}</div>${mood.text ? `<div class="mc-mood-text">${_esc(mood.text)}</div>` : ''}`
        + (mood.macro ? `<div class="mc-mood-macro rg-${mood.macro_regime || 'neutral'}">${_esc(mood.macro)}</div>` : '');
    } else {
      moodEl.className = 'mc-mood hidden';
      moodEl.innerHTML = '';
    }
  }
  if (!market || !Object.keys(market).length) {
    list.innerHTML = '<div class="mc-empty">시장 지표를 불러오지 못했습니다.</div>';
    return;
  }
  // 값 포맷 (지수는 천단위, 환율은 원, VIX는 소수점)
  const fmtVal = (label, m) => {
    const v = m.value;
    if (v == null) return '—';
    if (label === '환율' || m.kind === 'fx') return Math.round(v).toLocaleString() + '원';
    if (label === 'VIX') return v.toFixed(2);
    return v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  };
  const chgHtml = (chg) => {
    if (chg == null) return '';
    const cls = chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat';
    const icon = chg > 0 ? '▲' : chg < 0 ? '▼' : '·';
    return `<span class="mc-chg ${cls}">${icon} ${Math.abs(chg).toFixed(2)}%</span>`;
  };
  const rows = Object.entries(market).map(([label, m]) => {
    if (m.kind === 'sector') {
      // 소속 섹터: 시총가중 등락률 + 상승/하락 종목 수 (섹터맵 스냅샷)
      const breadth = (m.advancing != null && m.declining != null)
        ? `<span class="mc-breadth" title="섹터 내 상승·하락 종목 수"><span class="up">${m.advancing}↑</span> <span class="down">${m.declining}↓</span></span>` : '';
      return `
      <div class="mc-row mc-sector" title="섹터맵 시총가중 등락률${m.data_date ? ' · ' + _esc(m.data_date) + ' 기준' : ''}">
        <span class="mc-label">소속 섹터</span>
        <div class="mc-right">
          <span class="mc-val mc-sector-name">${_esc(m.name || '—')}</span>
          ${chgHtml(m.change_pct)}${breadth}
        </div>
      </div>`;
    }
    // VIX 는 낮은 게 좋고 높은 게 위험 → 상태 태그
    const vixHint = label === 'VIX'
      ? (m.value < 20 ? ' <span class="mc-tag safe">안정</span>'
                      : m.value < 30 ? ' <span class="mc-tag mid">경계</span>'
                                     : ' <span class="mc-tag danger">위험</span>')
      : '';
    return `
      <div class="mc-row">
        <span class="mc-label">${_esc(label)}</span>
        <div class="mc-right">
          <span class="mc-val">${fmtVal(label, m)}</span>
          ${chgHtml(m.change_pct)}${vixHint}
        </div>
      </div>`;
  }).join('');
  list.innerHTML = rows;
}

// ── 다가오는 이벤트 ───────────────────────────────────
function renderUpcomingEvents(events) {
  const card = document.getElementById('eventsCard');
  const list = document.getElementById('eventsList');
  if (!card || !list) return;

  if (!events || !events.length) {
    // 빈 상태 표시 (카드는 노출)
    list.innerHTML = `<div class="event-empty">확정된 다가오는 이벤트가 없습니다 (실적·배당)</div>`;
    card.classList.remove('hidden');
    return;
  }
  const rows = events.map(e => {
    const urgent = e.days <= 7 ? ' urgent' : (e.days <= 30 ? ' soon' : '');
    return `
      <div class="event-row${urgent}">
        <div class="event-icon">${e.icon || '📌'}</div>
        <div class="event-body">
          <div class="event-label">${e.label}</div>
          <div class="event-date">${e.date}</div>
        </div>
        <div class="event-days">D-${e.days}</div>
      </div>`;
  }).join('');
  list.innerHTML = rows;
  card.classList.remove('hidden');
}

// ── 애널리스트 ────────────────────────────────────────
function renderAnalysts(analysts, stock) {
  const currency = stock ? (stock.currency || 'USD') : 'USD';
  const isKRW    = currency === 'KRW';

  // 타이틀 동적 변경
  const titleEl = document.getElementById('analystCardTitle');
  if (titleEl) {
    const kr = isKRW || /\.(KS|KQ)$/i.test((stock && stock.ticker) || '');
    titleEl.innerHTML = '애널리스트 의견 <span class="card-title-src">' +
      (kr ? '국내 증권사 최근 리포트 기준' : 'Yahoo Finance 집계') + '</span>';
  }

  if (!analysts) {
    document.getElementById('analystConsensus').innerHTML = '<p class="no-data">애널리스트 데이터가 없습니다.</p>';
    document.getElementById('analystTargets').innerHTML = '';
    return;
  }
  const { targets = [], summary = {} } = analysts;

  // 요약도 목표가 목록도 없으면 '—' 칸을 늘어놓지 않고 한 줄 안내만 보여준다
  const hasSummary = ['recommendation', 'mean', 'high', 'low', 'num_analysts']
    .some(k => summary[k] != null && summary[k] !== '');
  if (!hasSummary && !targets.length) {
    document.getElementById('analystConsensus').innerHTML = '<p class="no-data">애널리스트 데이터가 없습니다.</p>';
    document.getElementById('analystTargets').innerHTML = '';
    return;
  }

  // 컨센서스 요약
  const REC_MAP = {
    'strong_buy': { label: 'Strong Buy', cls: 'rec-buy' },
    'buy':        { label: 'Buy',         cls: 'rec-buy' },
    'hold':       { label: 'Hold',        cls: 'rec-hold' },
    'neutral':    { label: 'Neutral',     cls: 'rec-hold' },
    'sell':       { label: 'Sell',        cls: 'rec-sell' },
    'underperform': { label: 'Underperform', cls: 'rec-sell' },
  };
  const rec = REC_MAP[summary.recommendation] || { label: summary.recommendation || '—', cls: 'rec-hold' };

  document.getElementById('analystConsensus').innerHTML = `
    <div class="consensus-item">
      <div class="consensus-label">컨센서스</div>
      <div class="consensus-value"><span class="consensus-rec ${rec.cls}">${rec.label}</span></div>
    </div>
    <div class="consensus-item">
      <div class="consensus-label">평균 목표가</div>
      <div class="consensus-value">${summary.mean ? fmt(summary.mean, currency) : '—'}</div>
    </div>
    <div class="consensus-item">
      <div class="consensus-label">최고 목표가</div>
      <div class="consensus-value" style="color:var(--green)">${summary.high ? fmt(summary.high, currency) : '—'}</div>
    </div>
    <div class="consensus-item">
      <div class="consensus-label">최저 목표가</div>
      <div class="consensus-value" style="color:var(--red)">${summary.low ? fmt(summary.low, currency) : '—'}</div>
    </div>
    <div class="consensus-item">
      <div class="consensus-label">참여 애널리스트</div>
      <div class="consensus-value">${summary.num_analysts ?? '—'}명</div>
    </div>
  `;

  if (!targets.length) {
    document.getElementById('analystTargets').innerHTML = '<p class="no-data">최근 애널리스트 데이터가 없습니다.</p>';
    return;
  }

  document.getElementById('analystTargets').innerHTML = targets.map(t => {
    const diff = t.prior_target ? t.target - t.prior_target : 0;
    const arrow = diff > 0 ? '▲' : diff < 0 ? '▼' : '─';
    const cls   = diff > 0 ? 'analyst-target-up' : diff < 0 ? 'analyst-target-down' : 'analyst-target-same';
    const prior = t.prior_target ? `<span class="analyst-prior">(이전 ${fmt(t.prior_target, currency)})</span>` : '';
    return `
      <div class="analyst-row">
        <div class="analyst-firm">${t.firm}</div>
        <div class="analyst-grade grade-${t.score}">${t.grade}</div>
        <div class="analyst-target ${cls}">${arrow} ${fmt(t.target, currency)}${prior}</div>
        <div class="analyst-date">${t.date}</div>
      </div>
    `;
  }).join('');
}

// ── 뉴스 ──────────────────────────────────────────────
function renderPriceMoveBanner(stock, moveReason) {
  const el = document.getElementById('priceMoveBanner');
  if (!el) return;

  const pct = stock.price_change_pct;
  const ABS = Math.abs(pct || 0);
  if (!pct || ABS < 0.5) { el.classList.add('hidden'); return; }
  if (!moveReason) { el.classList.add('hidden'); return; }

  let kind;
  if      (ABS >= 5 && pct > 0) kind = 'surge';
  else if (ABS >= 5 && pct < 0) kind = 'plunge';
  else if (pct > 0)              kind = 'up';
  else                           kind = 'down';

  const labels = {
    surge:  { emoji:'🚀', title:`급등 +${ABS.toFixed(2)}%`, badge:'급등 이유', cls:'surge'  },
    plunge: { emoji:'📉', title:`급락 -${ABS.toFixed(2)}%`, badge:'급락 이유', cls:'plunge' },
    up:     { emoji:'▲',  title:`상승 +${ABS.toFixed(2)}%`, badge:'상승 이유', cls:'up'     },
    down:   { emoji:'▼',  title:`하락 -${ABS.toFixed(2)}%`, badge:'하락 이유', cls:'down'   },
  };
  const { emoji, title, badge, cls } = labels[kind];

  el.className = `price-move-banner ${cls}`;
  el.innerHTML = `
    <div class="move-banner-head">
      <span class="move-emoji">${emoji}</span>
      <div>
        <span class="move-title">${title}</span>
        <span class="move-badge">${badge}</span>
      </div>
    </div>
    <div class="move-reason-text">${escapeHtmlMain(moveReason).replace(/\n/g, '<br>')}</div>
  `;
  el.classList.remove('hidden');
}

function renderNews(news) {
  const el = document.getElementById('newsList');
  if (!news || !news.length) {
    el.innerHTML = '<p class="no-data">최근 뉴스를 찾을 수 없습니다.</p>';
    return;
  }
  // 외부 기사 텍스트는 이스케이프, 링크는 http(s)만 허용
  const esc = escapeHtmlMain;
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? esc(u) : '#');
  el.innerHTML = news.map(n => `
    <a class="news-item" href="${safeUrl(n.url)}" target="_blank" rel="noopener">
      <div class="news-body">
        <div class="news-title">${esc(n.title || '')}</div>
        ${n.desc ? `<div class="news-desc">${esc(n.desc)}</div>` : ''}
        <div class="news-meta">
          ${n.provider ? `<span class="news-provider">${esc(n.provider)}</span>` : ''}
          ${n.pub ? `<span>${esc(n.pub)}</span>` : ''}
        </div>
      </div>
    </a>
  `).join('');
}

// ── 차트: static/js/chart.js (renderCharts · setChartType · 툴바 · 동기화) ──

// ── 자동완성 ─────────────────────────────────────────
let acItems = [];
let acIndex = -1;
let acTimer = null;

const input = document.getElementById('tickerInput');
const acList = document.getElementById('autocompleteList');

input.addEventListener('input', () => {
  delete input.dataset.ticker;   // 직접 입력 → 종목명 매핑 해제
  delete input.dataset.label;
  clearTimeout(acTimer);
  const q = input.value.trim();
  if (q.length < 1) { hideAC(); return; }
  acTimer = setTimeout(() => fetchAC(q), 280);
});

input.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    if (acIndex >= 0 && acItems[acIndex]) {
      selectAC(acItems[acIndex]);
    } else {
      analyze();
    }
    return;
  }
  if (e.key === 'ArrowDown') { e.preventDefault(); moveAC(1); }
  if (e.key === 'ArrowUp')   { e.preventDefault(); moveAC(-1); }
  if (e.key === 'Escape')    { hideAC(); }
});

document.addEventListener('click', e => {
  if (!e.target.closest('.search-input-wrap')) hideAC();
});

async function fetchAC(q) {
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    acItems = await res.json();
    acIndex = -1;
    renderAC();
  } catch { hideAC(); }
}

function renderAC() {
  if (!acItems.length) { hideAC(); return; }
  acList.innerHTML = acItems.map((item, i) => `
    <div class="autocomplete-item" data-i="${i}" onmousedown="selectAC(acItems[${i}])">
      ${(window.renderStockLogo ? renderStockLogo(item.symbol, item.name, 'xs') : '')}
      <span class="ac-symbol">${item.symbol}</span>
      <span class="ac-name">${item.name}</span>
      <span class="ac-exchange">${item.exchange}</span>
      ${item.type === 'ETF' ? '<span class="ac-type">ETF</span>' : ''}
    </div>
  `).join('');
  acList.classList.remove('hidden');
}

function moveAC(dir) {
  acIndex = Math.max(-1, Math.min(acItems.length - 1, acIndex + dir));
  acList.querySelectorAll('.autocomplete-item').forEach((el, i) => {
    el.classList.toggle('active', i === acIndex);
  });
}

function selectAC(item) {
  input.value = item.symbol;
  hideAC();
  analyze();
}

function hideAC() {
  acList.classList.add('hidden');
  acItems = [];
  acIndex = -1;
}

// ── 모달 자동완성 ──────────────────────────────────────────────────────────
let addAcItems = [];
let addAcIndex = -1;
let addAcTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  const inp  = document.getElementById('addTickerInput');
  const list = document.getElementById('addAcList');
  if (!inp || !list) return;

  inp.addEventListener('input', () => {
    clearTimeout(addAcTimer);
    const q = inp.value.trim();
    if (q.length < 1) { hideAddAC(); return; }
    addAcTimer = setTimeout(() => fetchAddAC(q), 280);
  });

  inp.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveAddAC(1); }
    if (e.key === 'ArrowUp')   { e.preventDefault(); moveAddAC(-1); }
    if (e.key === 'Escape')    { hideAddAC(); }
    if (e.key === 'Enter')     { e.preventDefault(); if (addAcIndex >= 0 && addAcItems[addAcIndex]) selectAddAC(addAcItems[addAcIndex]); }
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('#addTickerField')) hideAddAC();
  });
});

async function fetchAddAC(q) {
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    addAcItems = await res.json();
    addAcIndex = -1;
    renderAddAC();
  } catch { hideAddAC(); }
}

function renderAddAC() {
  const list = document.getElementById('addAcList');
  if (!list || !addAcItems.length) { hideAddAC(); return; }
  list.innerHTML = addAcItems.map((item, i) => `
    <div class="autocomplete-item" data-i="${i}" onmousedown="selectAddAC(addAcItems[${i}])">
      ${(window.renderStockLogo ? renderStockLogo(item.symbol, item.name, 'xs') : '')}
      <span class="ac-symbol">${item.symbol}</span>
      <span class="ac-name">${item.name}</span>
      <span class="ac-exchange">${item.exchange}</span>
      ${item.type === 'ETF' ? '<span class="ac-type">ETF</span>' : ''}
    </div>
  `).join('');
  list.classList.remove('hidden');
}

function moveAddAC(dir) {
  const list = document.getElementById('addAcList');
  addAcIndex = Math.max(-1, Math.min(addAcItems.length - 1, addAcIndex + dir));
  list.querySelectorAll('.autocomplete-item').forEach((el, i) => el.classList.toggle('active', i === addAcIndex));
}

function selectAddAC(item) {
  const cur = _currencyFromTicker(item.symbol);
  document.getElementById('addTicker').value   = item.symbol;
  document.getElementById('addName').value     = item.name;
  document.getElementById('addCurrency').value = cur;
  document.getElementById('addTickerInput').value = `${item.name} (${item.symbol})`;
  _updateAddCurrencyLabel(cur);
  const badge = document.getElementById('addTickerBadge');
  if (badge) badge.textContent = `✅ ${item.symbol} · ${cur === 'KRW' ? '원화(KRW)' : '달러(USD)'}`;
  hideAddAC();
  document.getElementById('addPrice').focus();
}

function hideAddAC() {
  const list = document.getElementById('addAcList');
  if (list) list.classList.add('hidden');
  addAcItems = [];
  addAcIndex = -1;
}

// ── 섹터/테마 강도 ───────────────────────────────────────────
let _sectorsLoadedOnce = false;
let _sectorsMarket = 'US';
let _sectorsExpanded = false;
let _sectorsCache = { US: null, KR: null };

function selectSectorsMarket(btn) {
  document.querySelectorAll('#sectorsMarketToggle .market-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _sectorsMarket = btn.dataset.market;
  _sectorsExpanded = false;
  // 강도 점수 설명 텍스트 갱신
  const t = document.getElementById('sectorsCriteriaText');
  if (t) {
    t.innerHTML = _sectorsMarket === 'KR'
      ? `<strong>1D × 0.45</strong> + <strong>3D × 0.35</strong> + <strong>상승종목폭 × 0.20</strong> (네이버 테마)`
      : `<strong>1D × 0.3</strong> + <strong>1W × 0.4</strong> + <strong>1M × 0.3</strong> + 거래량 로그 보정 (SPDR ETF)`;
  }
  // 캐시 있으면 재사용, 없으면 로드
  if (_sectorsCache[_sectorsMarket]) {
    _renderSectorsList(_sectorsCache[_sectorsMarket]);
  } else {
    loadSectors(false);
  }
}

async function loadSectors(force) {
  const listEl = document.getElementById('sectorsList');
  if (!listEl) return;
  listEl.innerHTML = '<div class="pf-loading">불러오는 중...</div>';
  try {
    const qs = `?market=${_sectorsMarket}` + (force ? '&force=1' : '');
    const r = await fetch('/api/sectors/strength' + qs);
    const data = await r.json();
    const sectors = data.sectors || [];
    _sectorsCache[_sectorsMarket] = sectors;
    _renderSectorsList(sectors);
  } catch (e) {
    listEl.innerHTML = `<div class="pf-empty">데이터를 가져올 수 없습니다: ${e.message || e}</div>`;
  }
}

function _renderSectorsList(sectors) {
  const listEl   = document.getElementById('sectorsList');
  const footerEl = document.getElementById('sectorsFooter');
  if (!listEl) return;
  if (!sectors.length) {
    listEl.innerHTML = '<div class="pf-empty">데이터가 없습니다</div>';
    if (footerEl) footerEl.classList.add('hidden');
    return;
  }
  const TOP_N = 5;
  const limit = _sectorsExpanded ? sectors.length : Math.min(TOP_N, sectors.length);
  const visible = sectors.slice(0, limit);
  const isKR = _sectorsMarket === 'KR';
  listEl.innerHTML = visible.map((s, i) => _sectorCardHTML(s, i, isKR)).join('');
  if (sectors.length > TOP_N && footerEl) {
    footerEl.classList.remove('hidden');
    footerEl.innerHTML = _sectorsExpanded
      ? `접기 ▲`
      : `더 보기 ▼ (${sectors.length - TOP_N}개 더)`;
    footerEl.onclick = () => { _sectorsExpanded = !_sectorsExpanded; _renderSectorsList(sectors); };
  } else if (footerEl) {
    footerEl.classList.add('hidden');
  }
}

function _sectorCardHTML(s, idx, isKR) {
  const scoreClass = s.score >= 0 ? 'up' : 'down';
  const scoreSign  = s.score >= 0 ? '+' : '';
  const _pct = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  const _pctCls = (v) => v >= 0 ? 'up' : 'down';

  // 카드 식별자 (펼침 패널용)
  const cardKey = isKR
    ? `kr-${s.theme_no || s.name.replace(/[^a-zA-Z0-9가-힣]/g,'')}`
    : `us-${s.ticker}`;
  const dataAttr = isKR
    ? `data-market="KR" data-theme-no="${s.theme_no || ''}" data-name="${s.name}"`
    : `data-market="US" data-ticker="${s.ticker}"`;

  if (isKR) {
    const total = (s.up || 0) + (s.flat || 0) + (s.down || 0);
    const leaders = (s.leaders || []).slice(0, 3).join(' · ');
    return `
      <div class="sector-card-wrap">
        <div class="sector-card" onclick="toggleSectorExpand('${cardKey}', this)" ${dataAttr}>
          <div class="sector-rank">${idx + 1}</div>
          <div class="sector-info">
            <div class="sector-name">${s.name}
              <a class="sector-ext" href="${s.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">↗</a>
            </div>
            <div class="sector-meta">
              ${total > 0 ? `<span class="sector-breadth">↑${s.up} -${s.flat} ↓${s.down}</span>` : ''}
              ${leaders ? `<span class="sector-leaders">· ${leaders}</span>` : ''}
            </div>
          </div>
          <div class="sector-stats">
            <div class="sector-score ${scoreClass}">${scoreSign}${s.score.toFixed(1)}</div>
            <div class="sector-chgs">
              <span class="${_pctCls(s.change_1d)}">1D ${_pct(s.change_1d)}</span>
              <span class="${_pctCls(s.change_3d)}">3D ${_pct(s.change_3d)}</span>
            </div>
          </div>
          <div class="sector-expand-arrow">▼</div>
        </div>
        <div class="sector-expand-panel hidden" id="exp-${cardKey}"></div>
      </div>
    `;
  }
  // US
  return `
    <div class="sector-card-wrap">
      <div class="sector-card" onclick="toggleSectorExpand('${cardKey}', this)" ${dataAttr}>
        <div class="sector-rank">${idx + 1}</div>
        <div class="sector-info">
          <div class="sector-name">${s.name}
            <a class="sector-ext" href="javascript:void(0)" onclick="event.stopPropagation();searchAndAnalyze('${s.ticker}')">↗</a>
          </div>
          <div class="sector-meta">${s.ticker} · 거래량 ×${s.vol_ratio.toFixed(2)}</div>
        </div>
        <div class="sector-stats">
          <div class="sector-score ${scoreClass}">${scoreSign}${s.score.toFixed(1)}</div>
          <div class="sector-chgs">
            <span class="${_pctCls(s.change_1d)}">1D ${_pct(s.change_1d)}</span>
            <span class="${_pctCls(s.change_1w)}">1W ${_pct(s.change_1w)}</span>
            <span class="${_pctCls(s.change_1m)}">1M ${_pct(s.change_1m)}</span>
          </div>
        </div>
        <div class="sector-expand-arrow">▼</div>
      </div>
      <div class="sector-expand-panel hidden" id="exp-${cardKey}"></div>
    </div>
  `;
}

// ── 섹터/테마 카드 펼침 (대표 종목 상승률 순) ───────────────
const _sectorExpandCache = {};

async function toggleSectorExpand(cardKey, cardEl) {
  const panel = document.getElementById('exp-' + cardKey);
  if (!panel) return;
  const isOpen = !panel.classList.contains('hidden');
  if (isOpen) {
    panel.classList.add('hidden');
    cardEl.classList.remove('expanded');
    return;
  }
  cardEl.classList.add('expanded');
  panel.classList.remove('hidden');

  // 캐시 있으면 즉시
  if (_sectorExpandCache[cardKey]) {
    panel.innerHTML = _sectorExpandCache[cardKey];
    return;
  }

  panel.innerHTML = '<div class="sector-stocks-loading">종목 불러오는 중...</div>';
  const market = cardEl.dataset.market;
  try {
    let qs;
    if (market === 'KR') {
      const themeNo = cardEl.dataset.themeNo;
      if (!themeNo) { panel.innerHTML = '<div class="sector-stocks-empty">테마 번호를 찾을 수 없습니다</div>'; return; }
      qs = `?market=KR&theme_no=${encodeURIComponent(themeNo)}`;
    } else {
      const ticker = cardEl.dataset.ticker;
      qs = `?market=US&ticker=${encodeURIComponent(ticker)}`;
    }
    const r = await fetch('/api/sectors/constituents' + qs);
    const data = await r.json();
    const stocks = data.stocks || [];
    if (!stocks.length) {
      panel.innerHTML = '<div class="sector-stocks-empty">구성 종목 데이터가 없습니다</div>';
      return;
    }
    const html = _renderSectorStocks(stocks, market);
    _sectorExpandCache[cardKey] = html;
    panel.innerHTML = html;
  } catch (e) {
    panel.innerHTML = `<div class="sector-stocks-empty">데이터를 가져올 수 없습니다: ${e.message || e}</div>`;
  }
}

function _renderSectorStocks(stocks, market) {
  const _pct = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  const _pctCls = (v) => v == null ? '' : (v >= 0 ? 'up' : 'down');
  const _fmtPrice = (v, cur) => {
    if (v == null) return '—';
    if (cur === 'KRW') return Math.round(v).toLocaleString('ko-KR') + '원';
    return '$' + v.toFixed(2);
  };
  const rows = stocks.map((st, i) => {
    const ticker = st.ticker || st.code || '';
    // 한국: "삼성전자 (005930)" / 미국: "Apple (AAPL)" — 이름과 티커가 다를 때만 티커 표시
    const showTicker = (st.ticker || st.code) && st.name && st.name.toUpperCase() !== (st.ticker || '').toUpperCase();
    const tickerLabel = showTicker ? ` <small>(${st.code || st.ticker})</small>` : '';
    return `
      <div class="sector-stock-row" onclick="searchAndAnalyze('${ticker}')">
        <span class="ss-rank">${i + 1}</span>
        <span class="ss-name">${st.name}${tickerLabel}</span>
        <span class="ss-price">${_fmtPrice(st.price, st.currency)}</span>
        <span class="ss-chg ${_pctCls(st.change_1d)}">${_pct(st.change_1d)}</span>
      </div>
    `;
  }).join('');
  return `
    <div class="sector-stocks-header">
      <span>대표 종목 ${stocks.length}개 · 당일 상승률 순</span>
    </div>
    <div class="sector-stocks-list">${rows}</div>
  `;
}

// 카드 클릭 → 검색창에 티커 넣고 분석
function searchAndAnalyze(ticker) {
  const input = document.getElementById('tickerInput');
  if (input) { input.value = ticker; }
  if (typeof analyze === 'function') analyze();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}


// ── 홈 모바일(≤767px): 빠른 선택 칩 끝 흐림 ──
(function initHomeMobile() {
  const qt = document.querySelector('.quick-tickers');
  if (qt) {
    const end = () => qt.classList.toggle('qt-end', qt.scrollLeft + qt.clientWidth >= qt.scrollWidth - 2);
    qt.addEventListener('scroll', end, { passive: true });
    window.addEventListener('resize', end);
    end();
  }
})();


// ── 홈 '내 포트폴리오' 서브 탭: 보유 종목 | 리스크 · 배당 (/api/portfolio/analytics, 처음 열 때 불러옴) ─────────
const _pfRisk = { sub: 'hold', data: null, stale: true, loading: false };
function selectPfSub(sub, focus) {
  _pfRisk.sub = sub;
  document.querySelectorAll('.pf-subtab').forEach((b) => {
    const on = b.dataset.sub === sub;
    b.classList.toggle('active', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
  });
  const hold = document.getElementById('pfSubHold'), risk = document.getElementById('pfSubRisk');
  if (hold) hold.classList.toggle('hidden', sub !== 'hold');
  if (risk) risk.classList.toggle('hidden', sub !== 'risk');
  // 종목 추가·토스 불러오기는 '보유 종목' 탭 전용 → 리스크 · 배당 탭에서는 숨김
  document.querySelector('.pf-top-actions')?.classList.toggle('pf-actions-off', sub === 'risk');
  if (sub === 'risk' && (_pfRisk.stale || !_pfRisk.data)) loadPfRisk();
}
(function initPfSubTabs() {
  const tabs = [...document.querySelectorAll('.pf-subtab')];
  tabs.forEach((b, i) => {
    b.addEventListener('click', () => selectPfSub(b.dataset.sub));
    b.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      selectPfSub(tabs[(i + 1) % tabs.length].dataset.sub, true);
    });
  });
})();

const _krwShort = (v) => {
  if (v == null) return '—';
  const a = Math.abs(v), sg = v < 0 ? '-' : '';
  if (a >= 1e8) return `${sg}${(a / 1e8).toFixed(2)}억원`;
  if (a >= 1e4) return `${sg}${(a / 1e4).toLocaleString('ko-KR', { maximumFractionDigits: a >= 1e6 ? 0 : 1 })}만원`;
  return `${sg}${Math.round(a).toLocaleString('ko-KR')}원`;
};

async function loadPfEarnings() {
  let d = null;
  try { const r = await fetch('/api/portfolio/earnings'); if (r.ok) d = await r.json(); } catch (e) { d = null; }
  const box = document.getElementById('prEarn');
  if (!box) return;
  const up = (d && d.upcoming) || [];
  const head = '<h4 class="pr-h">다가오는 실적 발표 <span class="pr-sub">60일 이내</span></h4>';
  if (!d) { box.innerHTML = `${head}<p class="pr-note">실적 일정을 불러오지 못했습니다.</p>`; return; }
  if (!up.length) { box.innerHTML = `${head}<p class="pr-note">60일 안에 실적을 발표하는 보유 종목이 없어요.</p>`; return; }
  box.innerHTML = `${head}<ul class="pr-ups">${up.map((u) => `
    <li><a href="/stock/${encodeURIComponent(u.ticker)}" class="pr-up">
      ${window.renderStockLogo ? renderStockLogo(u.ticker, u.name, 'sm') : ''}
      <span class="pr-up-id"><b>${_esc(u.name)}</b><small>${_esc(u.ticker)} · ${_mmdd(u.date)}${_ER_SESS[u.session] ? ` ${_ER_SESS[u.session]}` : ''}${u.eps_estimate != null ? ` · 예상 EPS ${_eps(u.eps_estimate, u.currency)}` : ''}</small></span>
      ${u.beat_streak >= 2 ? `<span class="er-badge beat pr-er-badge">${u.beat_streak}연속 Beat</span>` : ''}
      <span class="pr-dday ${u.d_day <= 7 ? 'soon' : ''}">${u.d_day === 0 ? 'D-DAY' : `D-${u.d_day}`}</span></a></li>`).join('')}</ul>
    <p class="pr-note">발표일은 현지 날짜, D-day 는 한국시간 기준이에요. 예상 EPS 는 yfinance 컨센서스.</p>`;
}

async function loadPfRisk() {
  const el = document.getElementById('pfSubRisk');
  if (!el || _pfRisk.loading) return;
  if (!currentUser) { el.innerHTML = '<div class="pf-empty pr-empty">로그인하면 보유 종목의 섹터 편중 · 환율 노출 · 예상 배당을 볼 수 있어요.</div>'; return; }
  _pfRisk.loading = true;
  if (!_pfRisk.data) el.innerHTML = '<div class="pf-loading">진단하는 중... (처음엔 배당 정보를 모으느라 몇 초 걸릴 수 있어요)</div>';
  else el.classList.add('is-loading');
  try {
    const r = await fetch('/api/portfolio/analytics');
    if (!r.ok) throw new Error(r.status);
    _pfRisk.data = await r.json();
    _pfRisk.stale = false;
    renderPfRisk(_pfRisk.data);
  } catch (e) {
    el.innerHTML = '<div class="pf-empty pr-empty">진단을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</div>';
  } finally {
    _pfRisk.loading = false;
    el.classList.remove('is-loading');
  }
}

// 섹터 막대 순위별 색 (기타·ETF 는 회색)
const _PR_SEC_COLORS = ['#38BDF8', '#34D399', '#A78BFA', '#FBBF24'];
const _prSecColor = (name, i) => (name === '기타' ? '#94A3B8' : _PR_SEC_COLORS[i] || '#94A3B8');

function renderPfRisk(d) {
  const el = document.getElementById('pfSubRisk');
  if (!el) return;
  if (!d || !d.count) {
    el.innerHTML = '<div class="pf-empty pr-empty">보유 종목을 추가하면 섹터 편중 · 환율 노출 · 월별 예상 배당을 진단해 드려요.</div>';
    return;
  }
  const dv = d.dividends || {};
  const curName = { KRW: '원화', USD: '달러' };
  const curCls = (c) => ({ KRW: 'krw', USD: 'usd' }[c] || 'etc');
  const pctTxt = (v) => (v == null ? '—' : `${(+v).toFixed(1)}%`);
  const usd = (d.currencies || []).find((c) => c.currency === 'USD');
  // 1행: KPI
  const kpis = `
    <div class="pr-kpis">
      <div class="pr-kpi">
        <span class="pr-k">최대 섹터 비중</span>
        <b class="pr-v">${pctTxt(d.max_sector_pct)}</b>
        <span class="pr-s">${_esc(d.max_sector_name || '업종 정보 없음')}${(d.sectors || []).some((s) => s.name === 'ETF') ? ' · ETF 제외' : ''}${d.concentrated ? ' <span class="pr-badge warn">편중 주의</span>' : ''}</span>
      </div>
      <div class="pr-kpi">
        <span class="pr-k">외화(환율) 노출</span>
        <b class="pr-v">${pctTxt(d.fx_exposure_pct)}</b>
        <span class="pr-s">${(d.currencies || []).map((c) => `${_esc(c.currency)} ${c.pct.toFixed(0)}%`).join(' · ')}</span>
      </div>
      <div class="pr-kpi pr-kpi-div">
        <span class="pr-k">연간 예상 배당금</span>
        <b class="pr-v pr-green">${dv.annual_dividend_krw ? _krwShort(dv.annual_dividend_krw) : '—'}</b>
        <span class="pr-s">${dv.annual_dividend_krw
          ? `<span class="pr-s-pc">세전 · 월 평균 약 ${_krwShort(dv.monthly_avg_krw)} (${(dv.portfolio_yield_pct || 0).toFixed(2)}%)</span><span class="pr-s-m">세전 · 월 약 ${_krwShort(dv.monthly_avg_krw)} · ${(dv.portfolio_yield_pct || 0).toFixed(2)}%</span>`
          : '배당 종목 없음'}</span>
      </div>
    </div>`;
  // 2행: 섹터 · 통화
  const sectors = `
    <section class="pr-box">
      <h4 class="pr-h">섹터별 비중</h4>
      <div class="pr-bars">${(d.sectors || []).map((s, i) => `
        <div class="pr-bar-row"><span class="pr-bar-name">${_esc(s.name)}</span>
          <span class="pr-bar-track"><i style="width:${Math.max(1.5, s.pct)}%;background:${_prSecColor(s.name, i)}"></i></span>
          <span class="pr-bar-pct">${s.pct.toFixed(1)}%</span></div>`).join('')}</div>
    </section>`;
  const curr = `
    <section class="pr-box">
      <h4 class="pr-h">통화(환율) 노출</h4>
      <div class="pr-stack" role="img" aria-label="${(d.currencies || []).map((c) => `${c.currency} ${c.pct}%`).join(', ')}">${(d.currencies || []).map((c) =>
        `<i class="pr-cur-${curCls(c.currency)}" style="width:${c.pct}%"></i>`).join('')}</div>
      <div class="pr-legend">${(d.currencies || []).map((c) => `
        <span><i class="pr-cur-${curCls(c.currency)}"></i>${_esc(curName[c.currency] || c.currency)} <b>${c.pct.toFixed(1)}%</b> <em>${_krwShort(c.value_krw)}</em></span>`).join('')}</div>
      <p class="pr-note">${usd ? `달러 자산 ${usd.pct.toFixed(1)}%는 원/달러 환율이 1% 움직이면 평가금액이 약 ${_krwShort(usd.value_krw * 0.01)} 달라집니다 (적용 환율 ${Math.round(d.fx_usd_krw).toLocaleString('ko-KR')}원).` : '외화 자산이 없어 환율 변동 영향이 없습니다.'}</p>
    </section>`;
  // 3행: 월별 배당 · 다가오는 배당락
  const months = dv.months || [];
  const mx = Math.max(1, ...months);
  const monthChart = `
    <section class="pr-box">
      <h4 class="pr-h">월별 예상 배당금 <span class="pr-sub">배당락 월 기준 · 세전</span></h4>
      ${dv.annual_dividend_krw ? `
      <div class="pr-months" role="list">${months.map((v, i) => `
        <button type="button" class="pr-mcol" role="listitem" data-m="${i + 1}" data-v="${v}" title="${i + 1}월 ${v.toLocaleString('ko-KR')}원" aria-label="${i + 1}월 예상 배당 ${v.toLocaleString('ko-KR')}원">
          <span class="pr-mbar"><i style="height:${v ? Math.max(4, v / mx * 100) : 0}%"></i></span><span class="pr-mlab">${i + 1}</span></button>`).join('')}</div>
      <p class="pr-mcap" id="prMcap">막대를 누르면 그 달 금액이 보여요.</p>`
      : '<div class="pf-empty pr-empty">배당을 주는 보유 종목이 없어요.</div>'}
    </section>`;
  const up = dv.upcoming || [];
  const upcoming = `
    <section class="pr-box">
      <h4 class="pr-h">다가오는 배당락일</h4>
      ${up.length ? `<ul class="pr-ups">${up.map((u) => `
        <li><a href="/stock/${encodeURIComponent(u.ticker)}" class="pr-up">
          ${window.renderStockLogo ? renderStockLogo(u.ticker, u.name, 'sm') : ''}
          <span class="pr-up-id"><b>${_esc(u.name)}</b><small>${_esc(u.ticker)} · ${_esc(u.ex_date.slice(5).replace('-', '.'))}${u.estimated ? ' (추정)' : ''}</small></span>
          <span class="pr-dday ${u.d_day <= 7 ? 'soon' : ''}">${u.d_day === 0 ? 'D-DAY' : `D-${u.d_day}`}</span>
          <span class="pr-up-amt">${_krwShort(u.est_payout_krw)}</span></a></li>`).join('')}</ul>
        <p class="pr-note">배당락일 전날까지 보유해야 배당을 받습니다. '추정'은 지난 배당 간격으로 계산한 날짜예요.</p>`
      : '<div class="pf-empty pr-empty">400일 안에 예정된 배당락일이 없어요.</div>'}
    </section>`;
  el.innerHTML = `${kpis}<div class="pr-row">${sectors}${curr}</div><div class="pr-row">${monthChart}${upcoming}</div>`
    + '<section class="pr-box pr-earn" id="prEarn"><h4 class="pr-h">다가오는 실적 발표 <span class="pr-sub">60일 이내</span></h4><p class="pr-note">불러오는 중...</p></section>';
  loadPfEarnings();
  const cap = document.getElementById('prMcap');
  el.querySelectorAll('.pr-mcol').forEach((b) => {
    const show = () => {
      el.querySelectorAll('.pr-mcol.on').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      if (cap) cap.textContent = `${b.dataset.m}월 예상 배당 ${(+b.dataset.v).toLocaleString('ko-KR')}원`;
    };
    b.addEventListener('click', show);
    b.addEventListener('mouseenter', show);
    b.addEventListener('focus', show);
  });
}


// ── 국내 종목 외국인 · 기관 수급 카드 (투자 판단 탭, /api/stock/<ticker>/flow · 서버 30분 캐시) ─────────
// ── 어닝 서프라이즈 · 실적 일정 (/api/stock/<t>/earnings, yfinance 6시간 캐시). ETF·우선주·자료 없음이면 카드 숨김 ──
const _earnMem = {};
function _earnEligible(stock) {
  const t = (stock && stock.ticker) || '';
  if (!t || stock.quote_type === 'ETF') return false;
  return /\.(KS|KQ)$/i.test(t) ? /^\d{5}0\./.test(t) : true;
}
function _eps(v, cur) {
  if (v == null) return '—';
  return cur === 'KRW' ? `${Math.round(v).toLocaleString('ko-KR')}원` : `$${(+v).toFixed(2)}`;
}
const _ER_SESS = { BMO: '장전', AMC: '장후' };
const _mmdd = (iso) => (iso ? iso.slice(5).replace('-', '.') : '');
const _sp = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
async function loadEarnings(ticker) {
  const slot = document.getElementById('fjEarn');
  if (!slot) return;
  let d = _earnMem[ticker];
  if (!d) {
    try { d = await (await fetch(`/api/stock/${encodeURIComponent(ticker)}/earnings`)).json(); } catch (e) { d = null; }
    if (d && d.available) _earnMem[ticker] = d;
  }
  const cur = document.getElementById('fjEarn');
  if (!cur || !window._lastStock || window._lastStock.ticker !== ticker) return;
  if (!d || !d.available) { cur.innerHTML = ''; return; }
  cur.innerHTML = _earnHTML(d);
  _bindEarnChart(cur, d);
}
function _earnHTML(d) {
  const n = d.next_earnings, s = d.summary || {}, h = d.history || [];
  const badges = [];
  if (n && n.d_day <= 14) badges.push(`<span class="er-badge soon">실적 발표 ${n.d_day === 0 ? 'D-DAY' : `D-${n.d_day}`}</span>`);
  if (s.beat_streak >= 2) badges.push(`<span class="er-badge beat">${s.beat_streak}분기 연속 Beat</span>`);
  else if (s.miss_streak >= 2) badges.push(`<span class="er-badge miss">${s.miss_streak}분기 연속 Miss</span>`);
  const kr = d.currency === 'KRW';
  const nextSub = n ? [
    _ER_SESS[n.session] ? `${kr ? '' : '미국 '}${_ER_SESS[n.session]}` : '',
    n.time_kst && n.date_kst ? `한국시간 ${_mmdd(n.date_kst)} ${n.time_kst}` : '',
    n.eps_estimate != null ? `예상 EPS ${_eps(n.eps_estimate, d.currency)}` : '',
  ].filter(Boolean).join(' · ') : '';
  const kpiNext = n ? `
      <div class="er-kpi">
        <span class="er-k">다음 실적 발표</span>
        <b class="er-v">${_mmdd(n.date)} <em class="${n.d_day <= 14 ? 'soon' : ''}">${n.d_day === 0 ? 'D-DAY' : `D-${n.d_day}`}</em></b>
        <span class="er-s">${nextSub || '시간 미정'}</span>
      </div>` : '';
  const kpiRec = s.quarters ? `
      <div class="er-kpi">
        <span class="er-k">최근 ${s.quarters}분기 성적</span>
        <b class="er-v">${s.beat_ratio} <em>Beat</em></b>
        <span class="er-s">평균 서프라이즈 <span class="${s.avg_surprise_pct > 0 ? 'up' : s.avg_surprise_pct < 0 ? 'down' : ''}">${_sp(s.avg_surprise_pct)}</span></span>
      </div>` : '';
  const mx = Math.max(1, ...h.map((x) => Math.abs(x.surprise_pct || 0)));
  // 부호가 한쪽뿐이면 기준선을 아래(모두 Beat)·위(모두 Miss)로 옮겨 막대를 2배 높이로
  const hasPos = h.some((x) => (x.surprise_pct || 0) > 0), hasNeg = h.some((x) => (x.surprise_pct || 0) < 0);
  const mode = hasPos && hasNeg ? 'mid' : hasNeg ? 'neg' : 'pos';
  const half = mode === 'mid' ? 50 : 100;
  const pos = (v) => (mode === 'pos' ? 'bottom:0' : mode === 'neg' ? 'top:0' : v >= 0 ? 'bottom:50%' : 'top:50%');
  const chart = h.length ? `
      <div class="kf-chart-wrap er-chart-wrap">
        <div class="kf-chart-h"><span>분기별 어닝 서프라이즈 <small>(회계 분기 말 기준)</small></span>
          <span class="kf-legend"><i class="eb"></i>Beat<i class="em"></i>Miss</span></div>
        <div class="er-chart er-${mode}" role="list" style="grid-template-columns:repeat(${h.length},minmax(0,1fr))">${h.map((x, i) => {
          const v = x.surprise_pct || 0, ht = v ? Math.max(3, Math.abs(v) / mx * half) : 2;
          return `
          <button type="button" class="er-col" role="listitem" data-i="${i}" aria-label="${x.label} 분기 ${x.result} 서프라이즈 ${_sp(x.surprise_pct)}">
            <span class="er-bars"><i class="er-b ${x.result}" style="height:${ht}%;${pos(v)}"></i></span>
            <span class="er-x">${_esc(x.label)}</span></button>`; }).join('')}</div>
        <p class="kf-tip er-tip" id="erTip"></p>
      </div>` : '';
  return `
    <section class="fj-box er-card" aria-label="어닝 서프라이즈 · 실적 일정">
      <div class="kf-head er-head"><h4>어닝 서프라이즈 · 실적 일정</h4>${badges.length ? `<div class="kf-badges">${badges.join('')}</div>` : ''}</div>
      ${kpiNext || kpiRec ? `<div class="er-kpis">${kpiNext}${kpiRec}</div>` : ''}
      ${chart}
      <p class="fj-note kf-note">EPS 컨센서스·실제 EPS는 yfinance(Yahoo) 기준 · 차이 1% 미만은 부합(Inline)으로 봅니다.</p>
    </section>`;
}
function _bindEarnChart(root, d) {
  const tip = root.querySelector('#erTip');
  const h = d.history || [];
  const cols = root.querySelectorAll('.er-col');
  const word = { beat: 'Beat', miss: 'Miss', inline: '부합' };
  const show = (b) => {
    cols.forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    const x = h[+b.dataset.i];
    if (tip && x) tip.innerHTML = `<b>${_esc(x.label)} 분기${x.report_date ? ` (${_mmdd(x.report_date)} 발표)` : ''}</b>`
      + `예상 ${_eps(x.eps_estimate, d.currency)} → 실제 ${_eps(x.eps_actual, d.currency)} · <span class="er-r ${x.result}">${_sp(x.surprise_pct)} ${word[x.result] || ''}</span>`;
  };
  cols.forEach((b) => { b.addEventListener('click', () => show(b)); b.addEventListener('mouseenter', () => show(b)); b.addEventListener('focus', () => show(b)); });
  if (cols.length) show(cols[cols.length - 1]);                 // 처음엔 가장 최근 분기
}

const _flowMem = {};
function _eok(v, sign = true) {
  if (v == null) return '—';
  const a = Math.abs(v), sg = v > 0 ? (sign ? '+' : '') : v < 0 ? '-' : '';
  if (a >= 1e12) return `${sg}${(a / 1e12).toFixed(a >= 1e13 ? 1 : 2)}조`;
  if (a >= 1e8) return `${sg}${Math.round(a / 1e8).toLocaleString('ko-KR')}억`;
  if (a >= 1e4) return `${sg}${Math.round(a / 1e4).toLocaleString('ko-KR')}만`;
  return `${sg}${Math.round(a).toLocaleString('ko-KR')}`;
}
async function loadKrFlow(ticker) {
  const slot = document.getElementById('fjFlow');
  if (!slot) return;
  let d = _flowMem[ticker];
  if (!d) {
    slot.innerHTML = '<section class="fj-box kf-card"><div class="fj-box-h"><h4>외국인 · 기관 수급</h4></div><p class="fj-note">불러오는 중...</p></section>';
    try { d = await (await fetch(`/api/stock/${encodeURIComponent(ticker)}/flow`)).json(); } catch (e) { d = null; }
    if (d && d.available) _flowMem[ticker] = d;
  }
  const cur = document.getElementById('fjFlow');                    // 그새 다른 종목으로 바뀌었으면 그리지 않음
  if (!cur || !window._lastStock || window._lastStock.ticker !== ticker) return;
  if (!d || !d.available) { cur.innerHTML = ''; return; }
  cur.innerHTML = _krFlowHTML(d);
  _bindKrFlowChart(cur, d);
}
function _krFlowHTML(d) {
  const tone = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '');
  const kpi = (label, k) => `
    <div class="kf-kpi">
      <span class="kf-k">${label}${k === 'retail' && d.retail_estimated ? ' <em title="외국인·기관 순매수의 반대로 추정">추정</em>' : ''}</span>
      <b class="kf-v ${tone(d.sum_5d[`${k}_krw`])}">${_eok(d.sum_5d[`${k}_krw`])}</b>
      <span class="kf-s">최근 5일 · 20일 <span class="${tone(d.sum_20d[`${k}_krw`])}">${_eok(d.sum_20d[`${k}_krw`])}</span></span>
    </div>`;
  const mx = Math.max(1, ...d.daily.flatMap((x) => [Math.abs(x.foreign_krw), Math.abs(x.inst_krw)]));
  const bar = (v, cls) => {
    const h = v ? Math.max(2, Math.abs(v) / mx * 50) : 0;
    return `<i class="kf-b ${cls} ${v >= 0 ? 'pos' : 'neg'}" style="height:${h}%;${v >= 0 ? 'bottom:50%' : 'top:50%'}"></i>`;
  };
  return `
    <section class="fj-box kf-card" aria-label="외국인 · 기관 수급">
      <div class="kf-head">
        <h4>외국인 · 기관 수급</h4>
        <div class="kf-badges">${d.badges.map((b) => `<span class="kf-badge ${b.tone}">${_esc(b.text)}</span>`).join('')}</div>
        ${d.foreign_hold_ratio != null ? `<span class="kf-hold">외국인 보유율 <b>${d.foreign_hold_ratio.toFixed(1)}%</b></span>` : ''}
      </div>
      <div class="kf-kpis">${kpi('외국인 순매수', 'foreign')}${kpi('기관 순매수', 'inst')}${kpi('개인 순매수', 'retail')}</div>
      <div class="kf-chart-wrap">
        <div class="kf-chart-h"><span>최근 ${d.daily.length}거래일 수급 추이</span>
          <span class="kf-legend"><i class="f"></i>외국인<i class="i"></i>기관</span></div>
        <div class="kf-chart" role="list">${d.daily.map((x, i) => `
          <button type="button" class="kf-col" role="listitem" data-i="${i}" aria-label="${x.label} 외국인 ${_eok(x.foreign_krw)}원, 기관 ${_eok(x.inst_krw)}원">
            <span class="kf-bars">${bar(x.foreign_krw, 'f')}${bar(x.inst_krw, 'i')}</span>
            <span class="kf-x">${x.label}</span></button>`).join('')}</div>
        <p class="kf-tip" id="kfTip">막대를 누르면 그날 금액이 보여요.</p>
      </div>
      <p class="fj-note kf-note">순매수 주수 × 그날 종가로 계산한 추정 금액 · ${d.as_of.slice(5).replace('-', '.')} 기준 (네이버 증권)</p>
    </section>`;
}
function _bindKrFlowChart(root, d) {
  const tip = root.querySelector('#kfTip');
  root.querySelectorAll('.kf-col').forEach((b) => {
    const show = () => {
      root.querySelectorAll('.kf-col.on').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      const x = d.daily[+b.dataset.i];
      if (tip) tip.innerHTML = `<b>${x.label}</b> 외국인 <span class="${x.foreign_krw >= 0 ? 'up' : 'down'}">${_eok(x.foreign_krw)}원</span> · 기관 <span class="${x.inst_krw >= 0 ? 'up' : 'down'}">${_eok(x.inst_krw)}원</span> · 종가 ${Math.round(x.close).toLocaleString('ko-KR')}원`;
    };
    b.addEventListener('click', show); b.addEventListener('mouseenter', show); b.addEventListener('focus', show);
  });
}
