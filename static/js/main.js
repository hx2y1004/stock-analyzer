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
}

function setDashMarket(mkt, btn) {
  _dashMarket = mkt;
  _dashMarketUserSet = true;
  _syncDashToggle();
  loadSectorMap();
  renderMovers();
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
    const st = document.getElementById('mdStatus');
    if (st) {
      st.innerHTML =
        `<span class="md-dot ${d.kr_open ? 'on' : ''}"></span>한국 ${d.kr_open ? '장중' : '마감'} · ` +
        `<span class="md-dot ${d.us_open ? 'on' : ''}"></span>미국 ${d.us_open ? '장중' : '마감'}`;
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
  // 좌표를 정수 픽셀로: 이웃 칸이 같은 경계값을 반올림하므로 빈틈·겹침 없이 맞닿고, 2px 테두리 두 개 = 모든 틈 4px
  const snapped = rects.map(r => {
    const x0 = Math.round(r.x), y0 = Math.round(r.y), x1 = Math.round(r.x + r.w), y1 = Math.round(r.y + r.h);
    return { g: r.g, x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }).filter(r => r.w > 0 && r.h > 0);
  el.innerHTML = `<div class="hm-treemap" style="height:${H}px">${snapped.map(({ g, x, y, w, h }) => {
    const c = g.change_pct;
    const big = w >= 150 && h >= 96, mid = w >= 74 && h >= 56, tiny = w < 40 || h < 30, narrow = !tiny && w < 84;
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
  } catch (e) {
    cards.innerHTML = '<div class="pf-loading">불러오기 실패</div>';
  }
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

function _pfCardHTML(h, rank, compact = false) {
  const hasPrice = h.current_price != null;
  const retPct   = h.return_pct;
  const retAmt   = h.return_amount;
  const retClass = retPct == null ? 'neutral' : retPct > 0 ? 'up' : retPct < 0 ? 'down' : 'neutral';
  const retIcon  = retPct > 0 ? '▲' : retPct < 0 ? '▼' : '';
  const cur      = h.currency || 'USD';
  const fmtPrice = (v, noRound = false) => v == null ? '—' : cur === 'KRW'
    ? (noRound ? Math.round(Number(v)) : Math.round(v / 10) * 10).toLocaleString('ko-KR') + '원'
    : '$' + Number(v).toLocaleString();

  // 순위 + 메달 (수익률 양수일 때만 메달)
  const medals = ['🥇', '🥈', '🥉'];
  const showMedal = rank <= 3 && retPct != null && retPct > 0;
  const rankBlock = showMedal
    ? `<div class="trend-rank trend-rank-medal">${medals[rank - 1]}</div>`
    : `<div class="trend-rank trend-rank-num">#${rank}</div>`;
  const tierClass = showMedal ? `trend-tier-${rank}` : '';

  // 수익률 뱃지 색상 — 방향 우선, 진하기는 절댓값
  let retBadgeColor;
  if (retPct == null)        retBadgeColor = 'flat';
  else if (retPct >= 15)     retBadgeColor = 'gain-strong';
  else if (retPct > 0)       retBadgeColor = 'gain';
  else if (retPct === 0)     retBadgeColor = 'flat';
  else if (retPct > -15)     retBadgeColor = 'loss';
  else                       retBadgeColor = 'loss-strong';

  // 카드 자체 방향 클래스 (좌측 보더/배경 틴트용)
  const dirClass =
    retPct == null   ? 'pf-dir-flat' :
    retPct >  0      ? 'pf-dir-gain' :
    retPct === 0     ? 'pf-dir-flat' : 'pf-dir-loss';

  // 수익률 뱃지 콘텐츠 (% 큰 글씨, 변화 없으면 dash)
  const retBadgeText = retPct != null
    ? `${retIcon} ${Math.abs(retPct).toFixed(1)}<span class="score-suffix">%</span>`
    : '—';

  // 가격 / 손익금액 (방향에 따라 강조)
  const retAmtStr = retAmt != null
    ? (retAmt >= 0 ? '+' : '') + fmtPrice(Math.round(retAmt))
    : '';
  const priceBlock = `
    <div class="trend-price-block">
      <div class="trend-price-val">${hasPrice ? fmtPrice(h.current_price) : '<span class="pf-loading-price">로딩중</span>'}</div>
      <div class="trend-chg pf-${retClass} pf-amount">${retAmtStr}</div>
    </div>`;

  // 컴팩트: 한 줄 (순위, 종목명, 현재가, 수익률뱃지)
  if (compact) {
    return `
    <div class="pf-card trend-card-v2 ${tierClass} ${dirClass}" onclick="quickSearch('${h.ticker}')">
      <div class="trend-card-row">
        ${rankBlock}
        <div class="trend-name-block">
          <div class="pf-stock-name">${h.name}</div>
          <div class="pf-ticker">${h.ticker}</div>
        </div>
        <div class="trend-spark-wrap"></div>
        ${priceBlock}
        <div class="trend-score-v2 score-${retBadgeColor}">${retBadgeText}</div>
      </div>
    </div>`;
  }

  // 풀: 보유수량 + 매입가 + 삭제 버튼 추가
  return `
  <div class="pf-card trend-card-v2 ${tierClass} ${dirClass}" onclick="quickSearch('${h.ticker}')">
    <div class="trend-card-row">
      ${rankBlock}
      <div class="trend-name-block">
        <div class="pf-stock-name">${h.name}</div>
        <div class="pf-ticker">${h.ticker}</div>
      </div>
      <div class="trend-spark-wrap"></div>
      ${priceBlock}
      <div class="trend-score-v2 score-${retBadgeColor}">${retBadgeText}</div>
    </div>
    <div class="pf-meta-row">
      <span class="pf-meta-item">📌 매입 ${fmtPrice(h.purchase_price, true)}</span>
      <span class="pf-meta-item">📦 ${h.quantity}주</span>
      <button class="pf-edit-btn" onclick="openEditModal(event, ${h.id})" title="매입가/수량 수정">✏️</button>
      <button class="pf-delete-btn" onclick="deleteHolding(event, ${h.id})" title="삭제" aria-label="삭제"><svg class="icon-x" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><path d="M18 6 6 18M6 6l12 12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" fill="none"/></svg></button>
    </div>
  </div>`;
}

function renderPortfolioCards(holdings) {
  pfAllHoldings = holdings;
  const cards  = document.getElementById('portfolioCards');
  const footer = document.getElementById('pfMoreFooter');
  const btn    = document.getElementById('pfToggleBtn');

  // 토글/푸터 사용 안 함 (전체 항상 렌더 + 내부 스크롤로 표시)
  if (footer) footer.classList.add('hidden');
  if (btn)    btn.classList.add('hidden');
  cards.classList.remove('collapsed');

  if (!holdings.length) {
    cards.innerHTML = `<div class="pf-empty">보유 종목이 없습니다. 종목을 추가해보세요!</div>`;
    return;
  }

  // 수익률 내림차순 정렬 → 전체 풀모드로 렌더
  const byReturn = [...holdings].sort((a, b) => (b.return_pct ?? -9999) - (a.return_pct ?? -9999));
  cards.innerHTML = byReturn.map((h, idx) => _pfCardHTML(h, idx + 1, false)).join('');
}

// 실제 포트폴리오 요약 헤더: 총 평가금액·투자금·손익, 통화/종목 비중
function renderPortfolioSummary(s) {
  const el = document.getElementById('pfSummary');
  if (!el) return;
  if (!s || !s.count) { el.classList.add('hidden'); el.innerHTML = ''; return; }
  const won = (v) => Math.round(v).toLocaleString('ko-KR') + '원';
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
  if (tab !== 'watch') tab = 'portfolio';
  document.querySelectorAll('.pf-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  const pfPanel  = document.getElementById('pfTabPortfolio');
  const wtPanel  = document.getElementById('pfTabWatch');
  if (pfPanel) pfPanel.classList.toggle('hidden', tab !== 'portfolio');
  if (wtPanel) wtPanel.classList.toggle('hidden', tab !== 'watch');
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
  _balanceStockColumns();
}

// ── 좌우 컬럼 길이 맞추기: '다가오는 이벤트'를 기본은 좌측(내 포지션 아래)에 두고,
// PC 2열에서는 이벤트를 뺀 두 컬럼 높이를 재서 합쳤을 때 차이가 더 작은 쪽에 붙인다.
// (AI 봇 카드·보유 정보로 좌측이 길어지는 종목은 우측으로) 1열(≤1024px)에서는 우측 컬럼 끝 = 맨 아래.
const _BALANCE_FILL_MAX = 96;   // 이 이하로 차이 나면 짧은 쪽 마지막 카드를 살짝 늘려 아래 끝을 맞춘다
function _balanceStockColumns() {
  const ev = document.getElementById('eventsCard');
  const L = document.querySelector('.col-chart'), R = document.querySelector('.col-side');
  if (!ev || !L || !R) return;
  document.querySelectorAll('.main-grid .card[data-fill]').forEach((c) => { c.style.minHeight = ''; delete c.dataset.fill; });
  if (innerWidth <= 1024) {                 // 1열: 기존처럼 맨 아래 (우측 컬럼 끝)
    if (ev.parentElement !== R || R.lastElementChild !== ev) R.appendChild(ev);
    return;
  }
  if (!ev.classList.contains('hidden')) {
    const evH = ev.getBoundingClientRect().height + 16;       // 카드 + --card-gap
    const lH = L.getBoundingClientRect().height - (ev.parentElement === L ? evH : 0);
    const rH = R.getBoundingClientRect().height - (ev.parentElement === R ? evH : 0);
    const target = Math.abs(lH + evH - rH) <= Math.abs(rH + evH - lH) ? L : R;
    if (ev.parentElement !== target) target.appendChild(ev);
  }
  // 남은 차이가 작으면 짧은 컬럼의 마지막 카드만 그만큼 늘려 두 컬럼 아래 끝을 맞춘다 (큰 차이는 그대로 둠)
  const diff = L.getBoundingClientRect().height - R.getBoundingClientRect().height;
  if (Math.abs(diff) >= 1 && Math.abs(diff) <= _BALANCE_FILL_MAX) {
    const col = diff < 0 ? L : R;
    const last = [...col.children].filter((c) => c.classList.contains('card') && !c.classList.contains('hidden')).pop();
    if (last) {
      last.style.minHeight = Math.round(last.getBoundingClientRect().height + Math.abs(diff)) + 'px';
      last.dataset.fill = '1';
    }
  }
}
let _balanceRaf = 0;
function _scheduleBalance() {
  cancelAnimationFrame(_balanceRaf);
  _balanceRaf = requestAnimationFrame(_balanceStockColumns);
}
if (window.ResizeObserver) {
  document.addEventListener('DOMContentLoaded', () => {
    const ro = new ResizeObserver(_scheduleBalance);
    document.querySelectorAll('.col-chart > .card:not(#eventsCard), .col-side > .card:not(#eventsCard)').forEach((c) => ro.observe(c));
  });
}
addEventListener('resize', _scheduleBalance);

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
  const profitStr = profitAmt != null
    ? `${profitAmt >= 0 ? '+' : ''}${fmtP(Math.round(profitAmt))}`
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
    renderDetail(data.analysis.details || []);
    renderFundamental(data.analysis.fundamental_details || [], data.stock);
    renderScorecard(data.analysis.scorecard, data.stock);
    renderMetrics(data.stock, data.analysis);
    renderPositionCard(data.position || null, data.stock);
    renderStockBots(data.stock);
    renderMarketContext(data.stock.market_context, data.stock.currency === 'KRW');
    renderUpcomingEvents(data.stock.upcoming_events);
    renderZones(data.analysis, data.stock);
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

async function loadAnalyzeExtras(ticker, data, refill = 0) {
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
  document.getElementById('stockTicker').textContent = stock.ticker;
  if (document.getElementById('notifyBell') && window.SAWatch) SAWatch.bindStar(document.getElementById('watchStar'), stock.ticker, stock.name);
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
  document.getElementById('verdictPaperBuySub').textContent = withPlan ? '목표·손절 자동 채움' : '';
  a.title = withPlan ? '분석의 목표가·손절가를 채운 모의 매수 창을 엽니다' : '모의 매수 창을 엽니다';
  a.classList.remove('hidden');
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
      immediate: { dot: '🟢', txt: '즉시 진입 가능' },
      wait:      { dot: '🟡', txt: `약 ${diff.toFixed(1)}% 풀백까지 limit 주문` },
      patient:   { dot: '🟠', txt: `약 ${diff.toFixed(1)}% 풀백 대기 (시간 필요)` },
    };
    const cInfo = confMap[conf] || null;

    let strongDesc = '추세 강세';
    if (cInfo) strongDesc = `${cInfo.dot} ${cInfo.txt}`;

    let neutralDesc = 'MA50 또는 풀백까지 대기';
    if (conf === 'patient') neutralDesc = `${confMap.patient.dot} 풀백 대기 필요 (${diff.toFixed(1)}%)`;
    else if (conf === 'wait') neutralDesc = `${confMap.wait.dot} ${diff.toFixed(1)}% 풀백까지 limit`;
    else if (conf === 'immediate') neutralDesc = `${confMap.immediate.dot} 현재 가격대 매수 가능`;

    const recMap = {
      strong:  { cls: 'strong',  icon: '🔥', label: '강한 매수 신호', desc: strongDesc },
      neutral: { cls: 'neutral', icon: '⚖️', label: '풀백 대기 권장', desc: neutralDesc },
      avoid:   { cls: 'avoid',   icon: '⚠️', label: '진입 비추천',
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
  if (rr != null)  metaItems.push(`📊 R:R <strong>1:${rr.toFixed(2)}</strong>`);
  if (sdp != null) metaItems.push(`📉 손절 거리 <strong>${sdp}%</strong>`);
  if (sdp != null && sdp > 15) metaItems.push(`<span class="meta-warn">⚠ 변동성 큼</span>`);
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
function renderDcf(stock) {
  const card = document.getElementById('dcfCard');
  const tabBtn = document.getElementById('valuationTabBtn');
  if (!card) return;
  const d = stock.dcf;
  // 종목마다 DCF 가능 여부가 다르므로 탭 자체를 조건부 노출, 탭은 항상 기술적 분석으로 초기화
  switchDetailTab('technical');
  if (!d || !d.fair_value) {
    card.classList.add('hidden');
    tabBtn?.classList.add('hidden');
    return;
  }
  tabBtn?.classList.remove('hidden');
  const cur = stock.currency;

  // 1) 주인공 — 역산 (성장률 우선, 불가 시 영업이익률로 전환)
  const labEl = document.getElementById('dcfHeroLabel');
  const impEl = document.getElementById('dcfImplied');
  const cmpEl = document.getElementById('dcfImpliedCmp');
  const altEl = document.getElementById('dcfImpliedAlt');
  const a = d.assumptions || {};
  altEl.textContent = '';

  if (d.implied_growth_pct != null) {
    // 마진을 현재 수준으로 고정했을 때, 시장이 전제하는 매출성장률
    labEl.textContent = '현재 주가가 반영 중인 매출성장률';
    impEl.textContent = `연 ${d.implied_growth_pct}%`;
    impEl.className = 'dcf-hero-val';
    const parts = [];
    if (a.consensus_growth_pct != null) parts.push(`컨센서스 ${a.consensus_growth_pct}%`);
    if (a.hist_cagr_pct != null) parts.push(`과거 ${a.hist_cagr_pct}%`);
    let verdict = '';
    if (a.consensus_growth_pct != null) {
      const gap = d.implied_growth_pct - a.consensus_growth_pct;
      verdict = gap > 10 ? ' · 컨센서스보다 공격적인 기대'
              : gap < -5 ? ' · 컨센서스보다 보수적인 기대'
              : ' · 컨센서스와 비슷한 수준';
    }
    cmpEl.textContent = (parts.length ? parts.join(' · ') : '') + verdict;
    if (d.implied_margin_pct != null && a.ebit_margin_pct != null) {
      altEl.textContent =
        `또는 성장률이 컨센서스 수준이라면, 영업이익률이 ${a.ebit_margin_pct}% → ${d.implied_margin_pct}%로 올라야 합니다`;
    }
  } else if (d.implied_margin_pct != null) {
    // 성장률만으로는 설명 불가 → 시장이 전제하는 '수익성 개선'으로 해석
    labEl.textContent = '현재 주가가 반영 중인 영업이익률';
    impEl.textContent = `${d.implied_margin_pct}%`;
    impEl.className = 'dcf-hero-val';
    cmpEl.textContent =
      `현재 ${a.ebit_margin_pct}% → 필요 ${d.implied_margin_pct}%` +
      (a.base_growth_pct != null ? ` (매출 연 ${a.base_growth_pct}% 성장 가정 시)` : '');
    altEl.textContent =
      '성장률만으로는 현재 주가를 설명할 수 없어, 수익성 개선을 전제로 역산한 값입니다';
  } else {
    // 성장·마진 어느 쪽으로도 설명 불가 — '실패'가 아니라 그 자체가 신호
    labEl.textContent = '현재 주가가 반영 중인 기대';
    impEl.textContent = '재무 범위 밖';
    impEl.className = 'dcf-hero-val dcf-na';
    cmpEl.textContent =
      `매출 연 60% 성장 또는 영업이익률 70%를 가정해도 현재가에 도달하지 않습니다` +
      (a.ebit_margin_pct != null ? ` (현재 영업이익률 ${a.ebit_margin_pct}%)` : '');
    altEl.textContent =
      '시장이 재무제표에 아직 없는 요인(신사업·시장 재편 기대)을 반영하고 있다는 뜻입니다';
  }

  // 2) 3개 기준점
  document.getElementById('dcfPrice').textContent = fmt(d.current_price, cur);

  const fairEl = document.getElementById('dcfFair');
  fairEl.textContent = fmt(d.fair_value, cur);
  fairEl.className = 'dcf-ref-val ' +
    (d.upside_pct > 0 ? 'dcf-up' : d.upside_pct < 0 ? 'dcf-down' : '');
  const s = d.scenarios || {};
  document.getElementById('dcfFairRange').textContent =
    (s.bear && s.bull && s.bear.fair_value && s.bull.fair_value)
      ? `${d.upside_pct > 0 ? '+' : ''}${d.upside_pct}% · 범위 ${fmt(s.bear.fair_value, cur)}~${fmt(s.bull.fair_value, cur)}`
      : `${d.upside_pct > 0 ? '+' : ''}${d.upside_pct}%`;

  const tEl = document.getElementById('dcfTarget');
  const tSub = document.getElementById('dcfTargetSub');
  if (d.analyst_target) {
    tEl.textContent = fmt(d.analyst_target, cur);
    const up = (d.analyst_target / d.current_price - 1) * 100;
    tEl.className = 'dcf-ref-val ' + (up > 0 ? 'dcf-up' : up < 0 ? 'dcf-down' : '');
    tSub.textContent = `${up > 0 ? '+' : ''}${up.toFixed(1)}%` +
      (d.analyst_count ? ` · ${d.analyst_count}명` : '') +
      ' · 글로벌 집계';
  } else {
    tEl.textContent = '—'; tEl.className = 'dcf-ref-val'; tSub.textContent = '';
  }

  // 3) 신뢰도 뱃지
  const relEl = document.getElementById('dcfReliability');
  const relMap = {
    high:   ['신뢰도 높음', 'rel-high'],
    medium: ['참고용', 'rel-mid'],
    low:    ['해석 주의', 'rel-low'],
  };
  const [relTxt, relCls] = relMap[d.reliability] || relMap.medium;
  relEl.textContent = relTxt;
  relEl.className = 'dcf-rel ' + relCls;

  // 4) 경고
  const warnEl = document.getElementById('dcfWarnings');
  warnEl.innerHTML = (d.warnings || [])
    .map(w => `<div class="dcf-warn">⚠️ ${escapeHtmlMain(w)}</div>`).join('');

  // 5) 가정
  const rows = [
    ['WACC (할인율)', a.wacc_pct != null ? a.wacc_pct + '%' : '—'],
    ['자기자본비용', a.cost_equity_pct != null ? a.cost_equity_pct + '%' : '—'],
    ['베타 (조정)', a.beta != null ? `${a.beta}${a.beta_raw ? ` (원본 ${a.beta_raw})` : ''}` : '—'],
    ['무위험수익률', a.risk_free_pct != null ? a.risk_free_pct + '%' : '—'],
    ['적용 매출성장률', a.base_growth_pct != null ? a.base_growth_pct + '%' : '—'],
    ['영업이익률', a.ebit_margin_pct != null ? a.ebit_margin_pct + '%' : '—'],
    ['영구성장률', a.terminal_g_pct != null ? a.terminal_g_pct + '%' : '—'],
    ['예측 기간', a.years ? a.years + '년' : '—'],
    ['법인세율', a.tax_rate_pct != null ? a.tax_rate_pct + '%' : '—'],
    ['터미널 비중', a.terminal_share_pct != null ? a.terminal_share_pct + '%' : '—'],
  ];
  document.getElementById('dcfAssumptions').innerHTML = rows.map(([k, v]) =>
    `<div class="dcf-as-item"><span class="dcf-as-k">${k}</span><span class="dcf-as-v">${v}</span></div>`
  ).join('');

  card.classList.remove('hidden');

  // 6) AI 해설은 종목 인사이트(통합 1회 호출)와 함께 /api/analyze/extra 로 온다 → loadAnalyzeExtras 에서 채움
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
  el.innerHTML = `<div class="dcf-comment-label">🤖 AI 해설</div>` +
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
    el.innerHTML = `<div class="dcf-comment-label">🤖 AI 해설</div>` +
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
  'RSI (상대강도지수)': {
    bullish: '💚 RSI 30 이하 "과매도" 구간이에요. 너무 많이 떨어진 상태로 반등 가능성이 높아요.',
    bearish: '🔴 RSI 70 이상 "과매수" 구간이에요. 너무 많이 오른 상태로 조정이 올 수 있어요.',
    neutral: '✅ RSI가 적정 범위(30~70)에 있어요. 과열도 과냉도 아닌 안정적인 상태예요.'
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

// ── 기술적 분석 카드 ──────────────────────────────────────
function renderDetail(details) {
  document.getElementById('detailList').innerHTML = _buildCards(details);
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
  // 한국 전용
  kr_foreign:   '외국인이 들고있는 비율. 한국 시장은 외국인이 주가에 큰 영향.\n👍 30%+ = 우량주 신호 / 변화율이 더 중요',
  kr_for_net:   '최근 5거래일 외국인 누적 순매수 주식수. 한국 증시에서 외국인 매매는 가장 강력한 단기 시그널.\n👍 + (사고있음) / 👎 − (팔고있음)',
  kr_inst_net:  '최근 5거래일 기관 누적 순매수. 외국인+기관 둘 다 +면 가장 강한 매수세.\n👍 + 강세 / 👎 − 약세',
  kr_short_bal: '전체 유통주식 중 공매도 잔고 비율. 누적 베어 포지션의 크기.\n⚠️ 3%+ 주의 / 5%+ 위험',
  kr_short_5d:  '최근 5일 평균 일일 공매도 비중. 단기 공매도 트렌드.\n⚠️ 10%+ 지속 매도 압력',
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
function renderScorecard(sc, stock) {
  const panel = document.getElementById('scorecardPanel');
  if (!panel) return;
  if (!sc || !sc.factors || !sc.metrics) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
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

  // 한국 종목 전용 보충 데이터 (네이버 스크래핑)
  const krShortBal = M.kr_short_balance_pct;
  const krShort5d  = M.kr_short_5d_avg_pct;
  const krForRatio = M.kr_foreign_ratio_pct;
  const krForNet5d = M.kr_foreign_net_5d;
  const krInstNet5d = M.kr_inst_net_5d;
  const krSupplyDate = M.kr_supply_date;
  const krDartUrl    = M.kr_dart_url;
  const hasKrSupply = krForRatio != null || krForNet5d != null || krInstNet5d != null;

  // 거래대금 표기 (단위: 주 → 천주/만주/억주)
  const _fmtShares = (v) => {
    if (v == null || isNaN(v)) return '—';
    const sign = v >= 0 ? '+' : '';
    const av = Math.abs(v);
    if (av >= 100000000) return sign + (v/100000000).toFixed(1) + '억';
    if (av >= 10000)     return sign + (v/10000).toFixed(0) + '만';
    if (av >= 1000)      return sign + (v/1000).toFixed(0) + '천';
    return sign + v.toLocaleString();
  };

  // 한국 전용 섹션 (네이버에서 가져온 데이터)
  const krSupplySection = isKR ? `
    <div class="sc-metric-section sc-kr-section">
      <div class="sc-section-title">🇰🇷 한국 시장 시그널 <small>네이버 금융 · ${krSupplyDate || '최근'}</small></div>
      <div class="sc-metric-row"><span>외국인 보유율 ${_helpIcon('kr_foreign')}</span><span>${krForRatio != null ? krForRatio.toFixed(2) + '%' : '<span class="m-na">—</span>'}</span></div>
      <div class="sc-metric-row"><span>외국인 순매수 (5일) ${_helpIcon('kr_for_net')}</span><span class="${krForNet5d != null ? (krForNet5d > 0 ? 'good' : (krForNet5d < 0 ? 'bad' : '')) : ''}">${_fmtShares(krForNet5d)}주</span></div>
      <div class="sc-metric-row"><span>기관 순매수 (5일) ${_helpIcon('kr_inst_net')}</span><span class="${krInstNet5d != null ? (krInstNet5d > 0 ? 'good' : (krInstNet5d < 0 ? 'bad' : '')) : ''}">${_fmtShares(krInstNet5d)}주</span></div>
      <div class="sc-metric-row"><span>공매도 잔고 비중 ${_helpIcon('kr_short_bal')}</span><span class="${krShortBal != null ? (krShortBal > 3 ? 'bad' : '') : ''}">${krShortBal != null ? krShortBal.toFixed(2) + '%' : '<span class="m-na">—</span>'}</span></div>
      <div class="sc-metric-row"><span>공매도 비중 (5일 평균) ${_helpIcon('kr_short_5d')}</span><span class="${krShort5d != null ? (krShort5d > 10 ? 'bad' : '') : ''}">${krShort5d != null ? krShort5d.toFixed(2) + '%' : '<span class="m-na">—</span>'}</span></div>
      ${krDartUrl ? `<div class="sc-metric-row sc-dart-link"><span>내부자 매매</span><a href="${krDartUrl}" target="_blank" rel="noopener" class="sc-link">DART 공시 보러가기 ↗</a></div>` : ''}
    </div>
  ` : '';

  // 한국 종목 안내 문구
  const krNotice = isKR ? `
    <div class="sc-kr-notice">
      ⓘ 한국 종목은 yfinance에서 <strong>내부자 매매</strong>·<strong>F-Score</strong>가 비어있는 경우가 많아,
      위 <strong>🇰🇷 한국 시장 시그널</strong>(네이버 스크래핑)을 참고하세요.
      외국인 순매수와 기관 순매수가 동시에 양(+)이면 강한 매수세입니다.
    </div>` : '';

  const metricsHTML = `
    <div class="sc-metrics">
      <div class="sc-metric-section">
        <div class="sc-section-title">💎 Quality <small>품질 — 진짜 수익성</small></div>
        <div class="sc-metric-row"><span>FCF Margin ${_helpIcon('fcf_margin')}</span><span class="${_gradeFmt(M.fcf_margin, 15, 0)}">${_fmt(M.fcf_margin, '%')}</span></div>
        <div class="sc-metric-row"><span>FCF Yield ${_helpIcon('fcf_yield')}</span><span class="${_gradeFmt(M.fcf_yield, 5, 0)}">${_fmt(M.fcf_yield, '%')}</span></div>
        <div class="sc-metric-row"><span>영업이익률 ${_helpIcon('op_margin')}</span><span class="${_gradeFmt(M.operating_margin, 15, 0)}">${_fmt(M.operating_margin, '%')}</span></div>
        <div class="sc-metric-row"><span>매출총이익률 ${_helpIcon('gross_margin')}</span><span class="${_gradeFmt(M.gross_margin, 30, 0)}">${_fmt(M.gross_margin, '%')}</span></div>
        <div class="sc-metric-row"><span>Piotroski F-Score ${_helpIcon('f_score')}</span><span class="${_gradeFmt(M.f_score, 7, 3)}">${M.f_score != null ? M.f_score + '/9' : '<span class="m-na">—</span>'}</span></div>
        <div class="sc-metric-row"><span>Net Debt / EBITDA ${_helpIcon('net_debt')}</span><span class="${M.net_debt_to_ebitda != null ? (M.net_debt_to_ebitda < 2 ? 'good' : (M.net_debt_to_ebitda > 4 ? 'bad' : '')) : ''}">${_fmt(M.net_debt_to_ebitda, 'x')}</span></div>
      </div>

      <div class="sc-metric-section">
        <div class="sc-section-title">💰 Value <small>밸류에이션 — 비싼가/싼가</small></div>
        <div class="sc-metric-row"><span>PEG Ratio ${_helpIcon('peg')}</span><span class="${M.peg != null ? (M.peg < 1 ? 'good' : (M.peg > 2 ? 'bad' : '')) : ''}">${_fmt(M.peg, 'x')}</span></div>
        <div class="sc-metric-row"><span>EV / EBITDA ${_helpIcon('ev_ebitda')}</span><span class="${M.ev_ebitda != null ? (M.ev_ebitda < 12 ? 'good' : (M.ev_ebitda > 25 ? 'bad' : '')) : ''}">${_fmt(M.ev_ebitda, 'x')}</span></div>
        <div class="sc-metric-row"><span>FCF Yield ${_helpIcon('fcf_yield')}</span><span class="${_gradeFmt(M.fcf_yield, 5, 0)}">${_fmt(M.fcf_yield, '%')}</span></div>
      </div>

      <div class="sc-metric-section">
        <div class="sc-section-title">📈 Growth <small>성장 — 마진 추세</small></div>
        <div class="sc-metric-row"><span>마진 추세 (영업 YoY) ${_helpIcon('op_yoy')}</span><span class="${_signClass(M.operating_margin_yoy)}">${_fmt(M.operating_margin_yoy, 'pp')}</span></div>
        <div class="sc-metric-row"><span>마진 추세 (매출총 YoY) ${_helpIcon('gm_yoy')}</span><span class="${_signClass(M.gross_margin_yoy)}">${_fmt(M.gross_margin_yoy, 'pp')}</span></div>
      </div>

      <div class="sc-metric-section">
        <div class="sc-section-title">⚡ Momentum & Signals <small>수급 시그널</small></div>
        <div class="sc-metric-row"><span>EPS Revision (30일) ${_helpIcon('eps_rev')}</span><span class="${_signClass(rev.score_pct)}">${rev.score_pct != null ? ((rev.score_pct>=0?'+':'') + rev.score_pct + '%') : '<span class="m-na">—</span>'}${rev.up_30d != null ? ` <small>(↑${rev.up_30d} ↓${rev.down_30d})</small>` : ''}</span></div>
        <div class="sc-metric-row"><span>${isKR ? '공매도 비중 (당일)' : 'Short % of Float'} ${_helpIcon('short_float')}</span><span class="${M.short_pct_of_float != null ? (M.short_pct_of_float > 10 ? 'bad' : '') : ''}">${_fmt(M.short_pct_of_float, '%')}</span></div>
        ${!isKR ? `<div class="sc-metric-row"><span>내부자 매매 (6M) ${_helpIcon('insider')}</span><span class="${ins.net > 0 ? 'good' : (ins.net < 0 ? 'bad' : '')}">${ins.net != null ? `${ins.net >= 0 ? '+' : ''}${ins.net}건 <small>(↑${ins.buys} ↓${ins.sells})</small>` : '<span class="m-na">—</span>'}</span></div>` : ''}
        <div class="sc-metric-row"><span>주주환원 수익률 ${_helpIcon('shareholder')}</span><span class="${_gradeFmt(M.shareholder_yield_pct, 4, 0)}">${_fmt(M.shareholder_yield_pct, '%')}${M.buyback_yield_pct != null ? ` <small>(배당 ${(M.dividend_yield_pct||0).toFixed(1)}+자사주 ${M.buyback_yield_pct.toFixed(1)})</small>` : ''}</span></div>
      </div>
      ${krSupplySection}
    </div>
    ${krNotice}
  `;

  const _gradeHelp = 'A+ ~ A: 우수 (매수 관점)\nB+ ~ B: 양호 (적정 수준)\nC+ ~ C: 보통 (다른 시그널 확인 필요)\nD ~ F: 주의/위험 (펀더멘탈 약함)\n\n각 팩터 점수는 0~100, 등급은 그 점수의 변환입니다.';

  panel.innerHTML = `
    <div class="sc-header">
      <div class="sc-header-left">
        <div class="sc-title">📊 펀더멘탈 스코어카드 ${_helpInline(_gradeHelp)}</div>
        <div class="sc-subtitle">월가 표준 4팩터 (Quality · Value · Growth · Momentum) 종합 등급</div>
      </div>
      <div class="sc-overall sc-${ovCls}" title="${_gradeHelp}" onclick="toggleScHelp(this.parentElement.querySelector('.m-help-inline'), event)">
        <div class="sc-overall-grade">${ovGrade}</div>
        <div class="sc-overall-score">${ovScore}<small>/100</small></div>
      </div>
    </div>
    <div class="sc-factors">
      ${factorCard('Quality',  '💎', F.quality)}
      ${factorCard('Value',    '💰', F.value)}
      ${factorCard('Growth',   '📈', F.growth)}
      ${factorCard('Momentum', '⚡', F.momentum)}
    </div>
    <details class="sc-details">
      <summary>📋 10개 지표 상세 보기 <small>(각 지표 옆 ⓘ 마우스 올리면 설명)</small></summary>
      ${metricsHTML}
    </details>
  `;
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
          <span class="co-icon">🏢</span>
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
            <span class="co-icon">🏢</span>
            <span class="co-title">회사 소개</span>
            ${stock.sector ? `<span class="co-sector">${sectorKo(stock.sector)}${stock.industry ? ' · ' + stock.industry : ''}</span>` : ''}
          </div>
          <p class="co-desc">${formatted}</p>
        </div>`;
    }
  }

  // ── 2. 분기 실적 추이 (매출 + EPS/영업이익, 발표치 + 추정치) ──────────
  const revQ = (stock && stock.revenue_quarters) ? stock.revenue_quarters : [];
  const epsQ = (stock && stock.eps_quarters)     ? stock.eps_quarters     : [];
  const oiQ  = (stock && stock.oi_quarters)      ? stock.oi_quarters      : [];
  const cur2    = (stock && stock.currency) || 'USD';
  const isKRW2  = cur2 === 'KRW';
  // 한국 주식이면 영업이익, 아니면 EPS
  const secQ  = isKRW2 ? oiQ : epsQ;

  if (revQ.length >= 2 || secQ.length >= 2) {
    const cur   = cur2;
    const isKRW = isKRW2;

    // period "YYYY-MM" → "Q? 'YY"
    function toQLabel(p) {
      if (!p) return p;
      const [y, m] = p.split('-').map(Number);
      const q = Math.ceil(m / 3);
      return `Q${q} '${String(y).slice(2)}`;
    }

    // 매출/영업이익 포맷 (억원/조원 or $B/$M)
    function fmtRev(v) {
      if (v == null) return '—';
      if (isKRW) {
        if (Math.abs(v) >= 1e12) return (v / 1e12).toFixed(1) + '조';
        if (Math.abs(v) >= 1e8)  return Math.round(v / 1e8) + '억';
        return v.toLocaleString('ko-KR');
      }
      if (Math.abs(v) >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
      if (Math.abs(v) >= 1e6) return '$' + (v / 1e6).toFixed(0) + 'M';
      return '$' + v.toLocaleString();
    }

    // EPS 포맷 (미국 주식 전용)
    function fmtEps(v) {
      if (v == null) return '—';
      return '$' + Number(v).toFixed(2);
    }

    // beat/miss 뱃지
    function beatBadge(actual, estimate) {
      if (actual == null || estimate == null || estimate === 0) return '';
      const diff = ((actual - estimate) / Math.abs(estimate) * 100).toFixed(1);
      const isBeat = parseFloat(diff) >= 0;
      const sign   = isBeat ? '+' : '';
      return `<span class="beat-badge ${isBeat ? 'beat' : 'miss'}">${isBeat ? '✅' : '❌'} ${sign}${diff}%</span>`;
    }

    // ── 공통: 바 1개 렌더링 (추정치 = 노란 점선 오버레이, 발표치 앞에 표시) ──
    function makeBar(q, actPct, estPct, fmtFn, badge, isNeg) {
      return `
        <div class="qc-col">
          <div class="qc-bar-wrap">
            <div class="qc-bar-actual${isNeg ? ' neg' : ''}" style="height:${actPct}%"></div>
            ${estPct != null ? `<div class="qc-bar-estimate" style="height:${estPct}%"></div>` : ''}
          </div>
          <div class="qc-actual">${fmtFn(q.actual)}</div>
          ${q.estimate != null
            ? `<div class="qc-estimate">▶ ${fmtFn(q.estimate)}</div>`
            : '<div class="qc-estimate"></div>'}
          ${badge ? `<div class="qc-beat-row">${badge}</div>` : '<div class="qc-beat-row"></div>'}
          <div class="qc-period">${toQLabel(q.period)}</div>
        </div>`;
    }

    // ── 매출 차트 ──
    let revHtml = '';
    if (revQ.length >= 2) {
      const revList = [...revQ].reverse();
      const maxAct  = Math.max(...revList.map(q => q.actual || 0));
      const maxEst  = Math.max(...revList.map(q => q.estimate || 0));
      const maxVal  = Math.max(maxAct, maxEst, 1);

      // 발표 전 분기(실적 null, 추정치만 있음)는 비교에서 제외 — null 이 0 으로 계산돼 '감소'로 오판했었다
      const revActuals = revList.map(q => q.actual).filter(v => v != null);
      const oldest = revActuals[0], newest = revActuals[revActuals.length - 1];
      const hasTrend = revActuals.length >= 2;
      const revTrendLbl = !hasTrend ? '➡️ 보합' : newest > oldest * 1.05 ? '📈 증가' : newest < oldest * 0.95 ? '📉 감소' : '➡️ 보합';
      const revTrendCls = !hasTrend ? 'rev-trend-flat' : newest > oldest * 1.05 ? 'rev-trend-up' : newest < oldest * 0.95 ? 'rev-trend-down' : 'rev-trend-flat';

      const revBars = revList.map(q => {
        const actPct = Math.round((q.actual || 0) / maxVal * 100);
        const estPct = q.estimate != null ? Math.round(q.estimate / maxVal * 100) : null;
        return makeBar(q, actPct, estPct, fmtRev, beatBadge(q.actual, q.estimate), false);
      }).join('');

      revHtml = `
        <div class="qc-section">
          <div class="qc-section-hd">
            <span class="qc-section-title">💰 매출</span>
            <span class="rev-trend ${revTrendCls}">${revTrendLbl}</span>
          </div>
          <div class="qc-bars">${revBars}</div>
        </div>`;
    }

    // ── EPS (미국) / 영업이익 (한국) 차트 ──
    let secHtml = '';
    if (secQ.length >= 2) {
      const secList  = [...secQ].reverse();
      const actVals  = secList.map(q => q.actual).filter(v => v != null);
      const estVals  = secList.map(q => q.estimate).filter(v => v != null);
      const maxSec   = Math.max(...[...actVals, ...estVals].map(Math.abs), 1);
      const fmtSec   = isKRW ? fmtRev : fmtEps;
      const secTitle = isKRW ? '📊 영업이익' : '📊 주당순이익 (EPS)';

      const secBars = secList.map(q => {
        const actPct = q.actual != null ? Math.round(Math.abs(q.actual) / maxSec * 100) : 0;
        const estPct = q.estimate != null ? Math.round(Math.abs(q.estimate) / maxSec * 100) : null;
        const isNeg  = q.actual != null && q.actual < 0;
        const badge  = (!isKRW && q.surprise_pct != null)
          ? `<span class="beat-badge ${q.surprise_pct >= 0 ? 'beat' : 'miss'}">${q.surprise_pct >= 0 ? '✅' : '❌'} ${q.surprise_pct >= 0 ? '+' : ''}${q.surprise_pct.toFixed(1)}%</span>`
          : beatBadge(q.actual, q.estimate);
        return makeBar(q, actPct, estPct, fmtSec, badge, isNeg);
      }).join('');

      const secActuals = secList.map(q => q.actual).filter(v => v != null);
      const secOld = secActuals[0], secNew = secActuals[secActuals.length - 1];
      const secTrendLbl = secNew > secOld * 1.05 ? '📈 증가' : secNew < secOld * 0.95 ? '📉 감소' : '➡️ 보합';
      const secTrendCls = secNew > secOld * 1.05 ? 'rev-trend-up' : secNew < secOld * 0.95 ? 'rev-trend-down' : 'rev-trend-flat';

      secHtml = `
        <div class="qc-section qc-section--eps">
          <div class="qc-section-hd">
            <span class="qc-section-title">${secTitle}</span>
            <span class="rev-trend ${secTrendCls}">${secTrendLbl}</span>
          </div>
          <div class="qc-bars">${secBars}</div>
        </div>`;
    }

    html += `
      <div class="quarterly-card">
        <div class="qc-header">📋 분기 실적 추이</div>
        <div class="qc-sections-wrap">
          ${revHtml}
          ${secHtml}
        </div>
      </div>`;
  }

  // ── 3. 기존 투자 판단 항목들 ──────────────────────────
  html += _buildCards(details);

  el.innerHTML = html;
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

// ── 매수/매도 구간 탭 ─────────────────────────────────────
function _getZoneAdvice(zone, analysis) {
  // 주요 신호 컨텍스트 문자열 생성
  const a = analysis || {};
  const sigParts = [];
  if (a.new_52w_high)       sigParts.push('52주 신고가 달성');
  else if (a.near_52w_high) sigParts.push('52주 신고가 근방');
  if (a.near_52w_low)       sigParts.push('52주 신저가 근방');
  if (a.recent_golden)      sigParts.push('골든크로스 발생');
  if (a.recent_dead)        sigParts.push('데드크로스 발생');
  if (a.vol_up_confirm)     sigParts.push('거래량 동반 상승 확인');
  else if (a.vol_down_confirm) sigParts.push('거래량 동반 하락 확인');
  if (a.bb_expanding)       sigParts.push('밴드 확장 중');
  else if (a.bb_contracting)sigParts.push('밴드 수축 중');
  const sigCtx = sigParts.length ? ` (${sigParts.join(' · ')})` : '';

  const isStrong = a.trend === 'strong-uptrend' || a.trend === 'strong-downtrend';

  const map = {
    '손절 구간':
      `손절선 아래로 내려왔어요${sigCtx}. 최근 저점 지지가 깨진 신호로 추가 하락 리스크가 있으니 포지션 정리를 고려하세요.`,
    '눌림목 매수 구간':
      `${isStrong ? '강한 ' : ''}상승 추세 속 일시적 조정 구간이에요${sigCtx}. 추세가 유효하다면 분할 매수 기회가 될 수 있어요.`,
    '상승 추세 (보유)':
      `${isStrong ? '강한 ' : ''}상승 추세가 살아있어요${sigCtx}. 매수·매도보다 현 포지션 보유를 유지하는 게 유리해요.`,
    '고점 부근 (일부 익절)':
      `${isStrong ? '강한 ' : ''}상승 추세 고점 부근이에요${sigCtx}. 전량 매도보다 보유량 일부(20~30%)를 익절하며 리스크를 줄여보세요.`,
    '매수 추천 구간':
      `횡보 구간 지지선 부근이에요${sigCtx}. 지지선 역할을 하는 구간으로 분할 매수를 고려해보세요.`,
    '매도 추천 구간':
      `횡보 구간 저항선 부근이에요${sigCtx}. 저항선 역할을 하는 구간으로 분할 매도를 고려해보세요.`,
    '하락 추세 저점 (관망)':
      `${isStrong ? '강한 ' : ''}하락 추세 저점 부근이에요${sigCtx}. 반등 가능성은 있지만 추세가 꺾이지 않았으니 관망을 추천해요.`,
    '하락 추세 반등 (매도)':
      `${isStrong ? '강한 ' : ''}하락 추세 속 반등 구간이에요${sigCtx}. 반등 시 일부 매도로 리스크를 줄이는 전략이 유효해요.`,
    '관망 구간':
      `가격이 매수·매도 구간 사이에 있어요${sigCtx}. 뚜렷한 방향성이 나올 때까지 현 포지션을 유지하며 관망하세요.`,
  };
  return map[zone] || `현재 가격 위치를 분석 중이에요${sigCtx}.`;
}

function renderZones(analysis, stock) {
  const el = document.getElementById('zonesList');
  if (!el || !analysis) return;

  const cur    = stock.currency || 'USD';
  const isKRW  = cur === 'KRW';
  const price  = stock.current_price;
  const entry  = analysis.entry_price;
  const target = analysis.target_price;
  const stop   = analysis.stop_loss;
  const rsi    = analysis.rsi ? analysis.rsi.toFixed(1) : '—';

  const fmt = (v) => {
    if (v == null) return '—';
    return isKRW
      ? (Math.round(v / 10) * 10).toLocaleString('ko-KR') + '원'
      : '$' + Number(v).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
  };

  // 1개월 범위 + 추세 기반 현재 위치 판단
  const entryLow   = analysis.entry_low   || entry;
  const entryHigh  = analysis.entry_high  || entry * 1.02;
  const targetLow  = analysis.target_low  || target * 0.97;
  const targetHigh = analysis.target_high || target;
  const monthHigh  = analysis.month_high  || target;
  const monthLow   = analysis.month_low   || stop;
  const trend      = analysis.trend       || 'sideways';
  // 종합 판단이 '진입 비추천'이면 매수 구간을 추천처럼 보여주지 않는다 (상단 판단과 모순 방지)
  const isAvoid    = analysis.trade_recommendation === 'avoid';
  const monthRange = monthHigh - monthLow || 1;
  const rangePct   = (price - monthLow) / monthRange; // 0~1 (1개월 범위 내 위치)

  let priceZone = '관망 구간', priceColor = 'neutral';

  if (price <= stop) {
    priceZone = '손절 구간'; priceColor = 'bearish';

  } else if (trend === 'strong-uptrend' || trend === 'uptrend') {
    // 상승 추세
    if (rangePct <= 0.35)      { priceZone = '눌림목 매수 구간';      priceColor = 'bullish'; }
    else if (rangePct >= 0.80) { priceZone = '고점 부근 (일부 익절)'; priceColor = 'neutral'; }
    else                       { priceZone = '상승 추세 (보유)';       priceColor = 'bullish'; }

  } else if (trend === 'strong-downtrend' || trend === 'downtrend') {
    // 하락 추세
    if (rangePct <= 0.30)      { priceZone = '하락 추세 저점 (관망)'; priceColor = 'neutral'; }
    else if (rangePct >= 0.65) { priceZone = '하락 추세 반등 (매도)'; priceColor = 'bearish'; }
    else                       { priceZone = '관망 구간';              priceColor = 'neutral'; }

  } else {
    // 횡보
    if (rangePct <= 0.30)      { priceZone = '매수 추천 구간'; priceColor = 'bullish'; }
    else if (rangePct >= 0.70) { priceZone = '매도 추천 구간'; priceColor = 'bearish'; }
    else                       { priceZone = '관망 구간';      priceColor = 'neutral'; }
  }

  let rsiMsg = '';
  const rsiNum = parseFloat(rsi);
  if (rsiNum < 30)      rsiMsg = '⚠️ RSI 과매도 — 반등 가능성';
  else if (rsiNum > 70) rsiMsg = '⚠️ RSI 과매수 — 조정 가능성';
  else                  rsiMsg = '✅ RSI 정상 범위';

  const trendLabel = {
    'strong-uptrend':   { text: '강한 상승 추세 ↑↑', cls: 'trend-up' },
    'uptrend':          { text: '상승 추세 ↑',        cls: 'trend-up' },
    'sideways':         { text: '횡보',               cls: 'trend-side' },
    'downtrend':        { text: '하락 추세 ↓',        cls: 'trend-down' },
    'strong-downtrend': { text: '강한 하락 추세 ↓↓', cls: 'trend-down' },
  }[trend] || { text: '횡보', cls: 'trend-side' };

  const new52wHigh    = analysis.new_52w_high;
  const near52wHigh   = analysis.near_52w_high;
  const near52wLow    = analysis.near_52w_low;
  const recentGolden  = analysis.recent_golden;
  const recentDead    = analysis.recent_dead;
  const bbExpanding   = analysis.bb_expanding;
  const bbContracting = analysis.bb_contracting;
  const volUp         = analysis.vol_up_confirm;
  const volDown       = analysis.vol_down_confirm;

  const highBadge   = new52wHigh  ? `<span class="badge-52w high">🏆 52주 신고가</span>`
                    : near52wHigh ? `<span class="badge-52w near-high">📈 신고가 근방</span>`
                    : near52wLow  ? `<span class="badge-52w low">📉 52주 신저가 근방</span>`
                    : '';
  const crossBadge  = recentGolden ? `<span class="badge-trend golden">✨ 골든크로스</span>`
                    : recentDead   ? `<span class="badge-trend dead">💀 데드크로스</span>`
                    : '';
  const bbBadge     = bbExpanding   ? `<span class="badge-trend bb-exp">↔ 밴드 확장</span>`
                    : bbContracting ? `<span class="badge-trend bb-con">→← 밴드 수축</span>`
                    : '';
  const volBadge    = volUp   ? `<span class="badge-trend vol-up">📊 거래량 동반 상승</span>`
                    : volDown ? `<span class="badge-trend vol-down">📊 거래량 동반 하락</span>`
                    : '';

  el.innerHTML = `
  <div class="zones-wrap">
    <div class="zones-header">
      <span class="zones-current-price">현재가 <strong>${fmt(price)}</strong></span>
      <span class="trend-badge ${trendLabel.cls}">${trendLabel.text}</span>
      ${highBadge}${crossBadge}${bbBadge}${volBadge}
      <span class="zones-rsi-badge">${rsiMsg}</span>
    </div>

    <div class="zones-list">
      <div class="zone-row zone-stop">
        <div class="zone-icon-col">🛑</div>
        <div class="zone-content">
          <div class="zone-title-row">
            <span class="zone-name">손절 구간</span>
            <span class="zone-price-range">${fmt(stop)} 이하</span>
          </div>
          <div class="zone-explanation">이 가격 아래로 떨어지면 더 큰 손실을 막기 위해 매도를 고려하세요. 원칙을 지키는 게 중요해요.</div>
        </div>
      </div>

      <div class="zone-row zone-buy">
        <div class="zone-icon-col">${isAvoid ? '⚪' : '🟢'}</div>
        <div class="zone-content">
          <div class="zone-title-row">
            <span class="zone-name">${isAvoid ? '1개월 저점 구간 (참고)' : '매수 추천 구간'}</span>
            <span class="zone-price-range">${fmt(entryLow)} ~ ${fmt(entryHigh)}</span>
          </div>
          <div class="zone-explanation">${isAvoid
            ? `최근 한 달 저점 부근이에요. 다만 지금은 종합 판단이 <strong>진입 비추천</strong>이라, 이 구간에 있어도 바로 사기보다 추세 전환 신호(MA 골든크로스, RSI 회복 등)를 먼저 확인하세요.`
            : `최근 한 달 저점 부근이에요. 이 구간으로 주가가 내려오면 분할 매수를 고려해보세요. 한 번에 전액 투자하기보다 2~3번에 나눠 사면 리스크를 줄일 수 있어요.`}</div>
        </div>
      </div>

      <div class="zone-row zone-current ${priceColor}">
        <div class="zone-icon-col">📍</div>
        <div class="zone-content">
          <div class="zone-title-row">
            <span class="zone-name">현재 위치 <span class="zone-badge ${priceColor}">${priceZone}</span></span>
            <span class="zone-price-range">${fmt(price)}</span>
          </div>
          <div class="zone-explanation">${_getZoneAdvice(priceZone, analysis)}${isAvoid && priceColor === 'bullish'
            ? ' 다만 종합 판단은 <strong>진입 비추천</strong>이라 신규 매수는 신중하게 접근하세요.' : ''}</div>
        </div>
      </div>

      <div class="zone-row zone-sell">
        <div class="zone-icon-col">💰</div>
        <div class="zone-content">
          <div class="zone-title-row">
            <span class="zone-name">매도 추천 구간 (목표가)</span>
            <span class="zone-price-range">${fmt(targetLow)} ~ ${fmt(targetHigh)}</span>
          </div>
          <div class="zone-explanation">목표가 근처에서 보유 물량의 절반씩 나눠 파는 것을 추천해요. 한 번에 전량 매도하면 추가 상승을 놓칠 수 있어요.</div>
        </div>
      </div>
    </div>

    ${(() => {
      // 신규 매수자 관점 계산
      let nba = null;
      if (price <= stop) {
        nba = { action: '신규 매수 비추천', color: 'bearish',
          reason: `손절선(${fmt(stop)}) 아래로 내려왔어요. 반등 후 지지선 회복을 확인하고 진입을 고려하세요.` };
      } else if (trend === 'strong-uptrend' || trend === 'uptrend') {
        if (rangePct <= 0.35)
          nba = { action: '신규 매수 적기 ✓', color: 'bullish',
            reason: `상승 추세 속 눌림목 구간이에요. 분할 매수하기 좋은 타이밍이에요. 목표가 ${fmt(targetLow)} 부근에서 절반 익절, 손절은 ${fmt(stop)} 이탈 시 적용하세요.` };
        else if (rangePct >= 0.80)
          nba = { action: '눌림목 대기 추천', color: 'neutral',
            reason: `상승 추세이지만 1개월 고점 부근이에요. ${fmt(entryLow)}~${fmt(entryHigh)} 구간으로 내려오면 분할 매수를 고려하세요.` };
        else
          nba = { action: '소량 선진입 가능', color: 'bullish',
            reason: `상승 추세가 유효해요. 추격 매수보다 투자금의 1/3씩 분할 진입을 추천해요. ${fmt(entryHigh)} 이하 눌림목에서 추가 매수 기회를 노리세요.` };
      } else if (trend === 'strong-downtrend' || trend === 'downtrend') {
        if (rangePct <= 0.30)
          nba = { action: '관망 후 전환 신호 확인', color: 'neutral',
            reason: `하락 추세 저점 부근이에요. 추세 전환 신호(MA 골든크로스, RSI 30 이상 회복 등)가 나올 때 진입하는 게 안전해요.` };
        else
          nba = { action: '신규 진입 비추천', color: 'bearish',
            reason: `하락 추세가 이어지고 있어요. 저점 확인 전 진입은 손실 위험이 높아요. 추세가 전환될 때까지 관망을 강력히 추천해요.` };
      } else {
        if (rangePct <= 0.30)
          nba = { action: '분할 매수 추천', color: 'bullish',
            reason: `횡보 구간 지지선 부근이에요. ${fmt(entryLow)}~${fmt(entryHigh)}에서 분할 매수하고 ${fmt(targetLow)} 부근에서 익절하는 전략을 추천해요.` };
        else if (rangePct >= 0.70)
          nba = { action: '눌림목 대기 추천', color: 'neutral',
            reason: `횡보 구간 저항선 부근이에요. ${fmt(entryLow)}~${fmt(entryHigh)} 지지선 근처로 내려올 때 진입하는 전략이 유리해요.` };
        else
          nba = { action: '매수 구간 대기', color: 'neutral',
            reason: `횡보 구간 중반이에요. 매수 추천 구간(${fmt(entryLow)}~${fmt(entryHigh)})까지 내려오면 분할 매수를 고려하세요.` };
      }
      if (isAvoid && nba.color === 'bullish') {
        nba = { action: '관망 권장', color: 'neutral',
          reason: `가격 위치만 보면 매수를 고려할 만한 자리지만, 종합 판단은 진입 비추천이에요(추세가 약하거나 손익비 부족). 추세가 강해지거나 손익비가 개선될 때까지 기다리는 게 안전해요.` };
      }
      const cls = nba.color === 'bullish' ? 'nba-bullish' : nba.color === 'bearish' ? 'nba-bearish' : 'nba-neutral';
      return `
    <div class="new-buyer-advice ${cls}">
      <div class="nba-header">📊 신규 매수자 관점 — 지금 진입한다면?</div>
      <div class="nba-action">${nba.action}</div>
      <div class="nba-reason">${nba.reason}</div>
    </div>`;
    })()}

    <div class="zones-guide">
      <h4 class="zones-guide-title">📚 초보자를 위한 매매 가이드</h4>
      <div class="zones-guide-grid">
        <div class="guide-card buy-guide">
          <div class="guide-card-title">✅ 언제 살까요?</div>
          <div class="guide-card-body">${isAvoid
            ? `지금은 종합 판단이 <strong>진입 비추천</strong>이에요. 추세 전환 신호(MA 골든크로스, RSI 30 이상 회복 등)가 확인된 뒤 <strong>${fmt(entryLow)} ~ ${fmt(entryHigh)}</strong> 구간(최근 1개월 저점 부근)에서 분할 매수를 고려하세요.`
            : `현재가가 <strong>${fmt(entryLow)} ~ ${fmt(entryHigh)}</strong> 구간(최근 1개월 저점 부근)에 오거나 RSI가 40 이하로 내려오면 분할 매수를 고려하세요. 급등하는 종목을 쫓아가며 사는 건 위험해요.`}</div>
        </div>
        <div class="guide-card sell-guide">
          <div class="guide-card-title">💰 언제 팔까요?</div>
          <div class="guide-card-body">현재가가 <strong>${fmt(targetLow)} ~ ${fmt(targetHigh)}</strong> 구간(최근 1개월 고점 부근)에 오면 절반씩 분할 매도를 추천해요. RSI가 70을 넘으면 추가 상승보다 조정 가능성을 고려하세요.</div>
        </div>
        <div class="guide-card stop-guide">
          <div class="guide-card-title">🛑 손절 기준은?</div>
          <div class="guide-card-body"><strong>${fmt(stop)}</strong>(최근 저점과 변동폭(ATR)으로 계산한 손절선) 아래로 떨어지면 지지선이 무너진 신호예요. 추가 하락 가능성이 높으니 손실이 커지기 전에 매도하는 게 현명해요.</div>
        </div>
        <div class="guide-card split-guide">
          <div class="guide-card-title">⚖️ 분할 매매란?</div>
          <div class="guide-card-body">한 번에 전액 투자하지 말고 2~3번에 나눠 매수·매도하는 방법이에요. 평균 단가를 낮추고 리스크를 줄이는 가장 기본적인 투자 전략이에요.</div>
        </div>
      </div>
    </div>
  </div>`;
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
function renderMarketContext(market, isKRW) {
  const card = document.getElementById('marketContextCard');
  const list = document.getElementById('marketContextList');
  if (!card || !list) return;
  if (!market || !Object.keys(market).length) {
    card.classList.add('hidden');
    return;
  }
  // 값 포맷 (지수는 정수 천단위, 환율은 원, VIX는 소수점)
  const fmtVal = (label, v) => {
    if (v == null) return '—';
    if (label === '환율') return Math.round(v).toLocaleString() + '원';
    if (label === 'VIX')  return v.toFixed(2);
    return v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  };
  const rows = Object.entries(market).map(([label, m]) => {
    const chg = m.change_pct ?? 0;
    const cls  = chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat';
    const icon = chg > 0 ? '▲' : chg < 0 ? '▼' : '·';
    // VIX 는 낮은 게 좋고 높은 게 위험 → 반전 색상
    const isVix = label === 'VIX';
    const vixHint = isVix
      ? (m.value < 20 ? ' <span class="mc-tag safe">안정</span>'
                      : m.value < 30 ? ' <span class="mc-tag mid">경계</span>'
                                     : ' <span class="mc-tag danger">위험</span>')
      : '';
    return `
      <div class="mc-row">
        <span class="mc-label">${label}</span>
        <div class="mc-right">
          <span class="mc-val">${fmtVal(label, m.value)}</span>
          <span class="mc-chg ${cls}">${icon} ${Math.abs(chg).toFixed(2)}%</span>${vixHint}
        </div>
      </div>`;
  }).join('');
  list.innerHTML = rows;
  card.classList.remove('hidden');
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
    _scheduleBalance();
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
  _scheduleBalance();
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
