// 모의투자 페이지 (/trading) — 1차 리뉴얼: 빠른 매수 자동완성 → 1클릭 매수 창, 실전형 매수/매도 모달,
// 내 자산(비중 바·MDD·닉네임/새로고침/계좌 초기화), 자산 추이(MM/DD), 거래 내역 필터·검색
console.log('[trading] loaded');

let currentUser = null;
let dashboardData = null;
let qbACItems = [];
const FEE_RATE = 0.001;                         // 모의투자 수수료 0.1% (서버 PAPER_FEE_RATE 와 같음)
const COACH_MIN_TX = 5, REVIEW_MIN_HOLD = 2;

// ── 공통 ───────────────────────────────────────────────────────────────────
const fmtKRW = (v) => v == null ? '—' : Math.round(v).toLocaleString('ko-KR') + '원';
const fmtNative = (v, cur) => {
  if (v == null) return '—';
  if (cur === 'KRW') return Math.round(v).toLocaleString('ko-KR') + '원';
  return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const fmtPct = (v) => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
const isKR = (t) => /\.(KS|KQ)$/i.test(t || '');
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const _escName = escapeHtml;                    // (예전 이름 호환)
// 서버 시각은 시간대 없는 UTC(datetime.utcnow) → 'Z' 를 붙여 브라우저 현지 시각으로 바꾼다 (예전엔 9시간 이르게 보였다)
function parseServerTime(s) {
  if (!s) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z';
  const d = new Date(iso);
  return isNaN(d) ? null : d;
}
const pad2 = (n) => String(n).padStart(2, '0');
const fmtDateTime = (d) => d ? `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}` : '—';
const fx = () => (dashboardData && dashboardData.exchange_rate) || 1380;

let _toastT;
function toast(msg, kind) {
  const el = document.getElementById('trToast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'tr-toast show' + (kind ? ' ' + kind : '');
  clearTimeout(_toastT);
  _toastT = setTimeout(() => { el.className = 'tr-toast'; }, 2600);
}

// 열린 모달 하나만 ESC 로 닫는다 (확인 → 닉네임 → 매수/매도 순)
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const open = (id) => { const m = document.getElementById(id); return m && !m.classList.contains('hidden'); };
  if (open('confirmModal')) closeConfirm(false);
  else if (open('exitModal')) closeExitModal();
  else if (open('nicknameModal')) cancelNicknameModal();
  else if (open('tradeBuyModal')) closeBuyModal();
  else if (open('tradeSellModal')) closeSellModal();
});

// ── 보유종목: 정렬(공통 hsort.js) · 표시 통화 토글(원/$). 카드 오른쪽 큰 숫자는 항상 평가금액 ──
let _posCur  = 'KRW';
const _POS_SORT_KEY = 'sa_sort_trading_pos';
const _posSort = window.SAHoldSort ? SAHoldSort.load(_POS_SORT_KEY) : { key: 'value', dir: 'desc' };
function _setupPosSort() {
  const slot = document.getElementById('posSortSlot');
  if (!slot || !window.SAHoldSort) return;
  slot.innerHTML = SAHoldSort.controlHTML('posSort', _posSort, '보유 종목');
  SAHoldSort.bind(document.getElementById('posSort'), _posSort, _POS_SORT_KEY, () => { if (dashboardData) renderPositions(dashboardData.positions || []); });
}
function _convCur(val, fromCur, toCur, rate) {
  if (val == null) return null;
  if (fromCur === toCur) return val;
  if (fromCur === 'USD' && toCur === 'KRW') return val * rate;
  if (fromCur === 'KRW' && toCur === 'USD') return val / rate;
  return val;
}
function _fmtCur(val, cur) {
  if (val == null) return '—';
  if (cur === 'KRW') return Math.round(val).toLocaleString('ko-KR') + '원';
  return '$' + Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function _pressGroup(btn) {
  btn.parentElement.querySelectorAll('.ah-period-btn').forEach(b => { b.classList.remove('active'); b.setAttribute('aria-pressed', 'false'); });
  btn.classList.add('active'); btn.setAttribute('aria-pressed', 'true');
}
function setPosCur(c, btn) { _posCur = c; _pressGroup(btn); if (dashboardData) renderPositions(dashboardData.positions || []); }

// ── 시작 ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  _setupPosSort();
  setupQuickBuyAC();
  setupTradeInputs();
  setupTxTools();
  await checkAuthAndLoad();
});

async function checkAuthAndLoad() {
  try {
    const r = await fetch('/api/me');
    const data = await r.json();
    const u = data && data.user;
    if (!u || !u.id) {
      document.getElementById('loginRequired').classList.remove('hidden');
      document.getElementById('tradingDashboard').classList.add('hidden');
      return;
    }
    currentUser = u;
    if (window.SAProfile) document.getElementById('authArea').innerHTML = SAProfile.html(u);
    document.getElementById('loginRequired').classList.add('hidden');
    document.getElementById('tradingDashboard').classList.remove('hidden');
    await loadDashboard();
    await openBuyFromUrl();
  } catch (e) {
    console.warn('auth check failed', e);
    document.getElementById('loginRequired').classList.remove('hidden');
  }
}

async function loadDashboard() {
  try {
    const r = await fetch('/api/trading/dashboard');
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'load failed');
    dashboardData = data;
    renderSummary(data);
    renderPositions(data.positions || []);
    renderAiGates();
    await loadTransactions();
    loadAssetHistory(_ahCurrentDays || 30);        // 오늘 스냅샷이 저장된 뒤에 차트
    if (data.newly_earned_badges && data.newly_earned_badges.length) _showBadgeToasts(data.newly_earned_badges);
    loadBadges();
    loadCoachCached();
    loadReviewCached(_reviewMode);
    if (!data.nickname && !_nicknamePromptShown && !_nickPromptSnoozed()) {
      _nicknamePromptShown = true;
      setTimeout(() => openNicknameModal(true), 800);
    }
    const up = document.getElementById('psUpdated');
    if (up) { const d = new Date(); up.textContent = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())} 갱신`; }
    return true;
  } catch (e) {
    console.error('loadDashboard', e);
    return false;
  }
}

// ── 내 자산 ────────────────────────────────────────────────────────────────
async function refreshDashboard(btn) {
  if (btn) { btn.classList.add('spinning'); btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
  const t0 = Date.now();
  const ok = await loadDashboard();
  const wait = Math.max(0, 500 - (Date.now() - t0));   // 너무 빠르면 깜빡임만 보여 최소 0.5초
  setTimeout(() => {
    if (btn) { btn.classList.remove('spinning'); btn.disabled = false; btn.removeAttribute('aria-busy'); }
    toast(ok ? '시세와 자산을 새로 불러왔어요' : '불러오지 못했습니다. 잠시 후 다시 시도해 주세요', ok ? '' : 'err');
  }, wait);
}

function renderSummary(d) {
  const ret = d.total_return_pct || 0;
  const retCls = ret > 0 ? 'up' : ret < 0 ? 'down' : 'flat';
  const retIcon = ret > 0 ? '▲' : ret < 0 ? '▼' : '·';
  document.getElementById('psTotal').textContent = fmtKRW(d.total_assets_krw);
  document.getElementById('psReturn').innerHTML = `
    <span class="ps-return-pct ${retCls}">${retIcon} ${Math.abs(ret).toFixed(2)}%</span>
    <span class="ps-return-amt">${d.total_assets_krw >= d.initial_capital_krw ? '+' : '-'}${fmtKRW(Math.abs(d.total_assets_krw - d.initial_capital_krw))}</span>
    <span class="ps-return-base">초기 ${fmtKRW(d.initial_capital_krw)}</span>`;
  document.getElementById('psCash').textContent = fmtKRW(d.cash_krw);
  document.getElementById('psPositions').textContent = fmtKRW(d.positions_value_krw);
  const setPnl = (id, v) => {
    const el = document.getElementById(id);
    el.textContent = (v >= 0 ? '+' : '') + fmtKRW(v);
    el.className = 'ps-val ' + (v > 0 ? 'up' : v < 0 ? 'down' : '');
  };
  setPnl('psUnrealized', d.unrealized_pnl_krw || 0);
  setPnl('psRealized', d.realized_pnl_krw || 0);

  // 현금 · 원화 주식 · 달러 주식 비중 바
  const pos = d.positions || [];
  const krw = pos.filter(p => p.currency === 'KRW').reduce((a, p) => a + (p.value_krw || 0), 0);
  const usd = pos.filter(p => p.currency !== 'KRW').reduce((a, p) => a + (p.value_krw || 0), 0);
  const cash = Math.max(0, d.cash_krw || 0);
  const tot = cash + krw + usd;
  const seg = [['cash', '현금', cash], ['krw', '원화 주식', krw], ['usd', '달러 주식', usd]].filter(s => s[2] > 0);
  const pctOf = (v) => tot > 0 ? v / tot * 100 : 0;
  document.getElementById('psAlloc').innerHTML = tot > 0 ? `
    <div class="ps-alloc-bar" role="img" aria-label="${seg.map(s => `${s[1]} ${pctOf(s[2]).toFixed(1)}%`).join(', ')}">
      ${seg.map(s => `<i class="a-${s[0]}" style="width:${pctOf(s[2]).toFixed(2)}%"></i>`).join('')}
    </div>
    <div class="ps-alloc-legend">
      ${seg.map(s => `<span><i class="a-${s[0]}" aria-hidden="true"></i>${s[1]} <b>${pctOf(s[2]).toFixed(1)}%</b></span>`).join('')}
    </div>` : '';

  const s = d.stats || {};
  const mdd = s.max_drawdown_pct;
  document.getElementById('psStats').innerHTML = `
    <span>총 거래 <strong>${s.total_trades || 0}회</strong></span>
    <span>매도 승률 <strong>${s.win_rate || 0}%</strong> <em>(${s.wins || 0}/${s.total_sells || 0})</em></span>
    <span title="일별 자산 기준, 고점 대비 가장 크게 내려간 비율">최대 낙폭(MDD) <strong class="${mdd < 0 ? 'down' : ''}">${mdd == null ? '—' : mdd.toFixed(2) + '%'}</strong></span>
    <span>환율 <strong>${Number(d.exchange_rate).toLocaleString('ko-KR')}원/$</strong></span>`;
}

// ── 자산 변화 차트 ─────────────────────────────────────────────────────────
let _ahChart = null;
let _ahSeriesMe = null, _ahSeriesKospi = null, _ahSeriesSp = null;
let _ahCurrentDays = 30;
const _bdToStr = (t) => typeof t === 'string' ? t : (t && t.year ? `${t.year}-${pad2(t.month)}-${pad2(t.day)}` : '');

async function loadAssetHistory(days, btn) {
  _ahCurrentDays = days;
  document.querySelectorAll('.asset-history-card .ah-period-btn').forEach(b => b.classList.remove('active'));
  const t = btn || document.querySelector(`.asset-history-card .ah-period-btn[data-days="${days}"]`);
  if (t) t.classList.add('active');
  try {
    const r = await fetch(`/api/trading/history?days=${days}`);
    if (!r.ok) throw new Error('history load failed');
    renderAssetHistoryChart(await r.json());
  } catch (e) { console.error('loadAssetHistory', e); }
}

const _ahFmtPct = (p) => (p == null || isNaN(p)) ? '—' : (p > 0 ? '+' : '') + p.toFixed(2) + '%';

function renderAssetHistoryChart(data) {
  const wrap = document.getElementById('assetHistoryChart');
  const empty = document.getElementById('ahEmpty');
  const summary = document.getElementById('ahSummary');
  if (!wrap || typeof LightweightCharts === 'undefined') return;
  const snaps = data.snapshots || [];
  const bm = data.benchmarks || {};
  const kospi = bm.kospi || [], sp500 = bm.sp500 || [];
  if (snaps.length < 2 && kospi.length < 2 && sp500.length < 2) {
    wrap.style.display = 'none';
    if (empty) empty.classList.remove('hidden');
    if (summary) summary.textContent = '';
    return;
  }
  wrap.style.display = '';
  if (empty) empty.classList.add('hidden');
  if (!_ahChart) {
    const pctFmt = { type: 'custom', formatter: (p) => (p >= 0 ? '+' : '') + p.toFixed(2) + '%' };
    _ahChart = LightweightCharts.createChart(wrap, {
      autoSize: true,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#9aa4b2', fontSize: 11 },
      rightPriceScale: { borderColor: '#2a3340' },
      timeScale: {
        borderColor: '#2a3340', timeVisible: false, rightOffset: 2,
        // X축: '19일' 대신 '11/19' (90D·전체에서도 월을 바로 알 수 있게)
        tickMarkFormatter: (t) => { const s = _bdToStr(t); return s ? `${+s.slice(5, 7)}/${+s.slice(8, 10)}` : ''; },
      },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      localization: {
        locale: 'ko-KR',
        priceFormatter: (p) => (p >= 0 ? '+' : '') + p.toFixed(2) + '%',
        timeFormatter: (t) => _bdToStr(t),
      },
      handleScroll: { vertTouchDrag: false },
    });
    _ahSeriesMe    = _ahChart.addLineSeries({ color: '#22c55e', lineWidth: 2, title: '', priceFormat: pctFmt });
    _ahSeriesKospi = _ahChart.addLineSeries({ color: '#3b82f6', lineWidth: 1, title: '', lastValueVisible: false, priceLineVisible: false, priceFormat: pctFmt });
    _ahSeriesSp    = _ahChart.addLineSeries({ color: '#f59e0b', lineWidth: 1, title: '', lastValueVisible: false, priceLineVisible: false, priceFormat: pctFmt });
    _ahSeriesMe.createPriceLine({ price: 0, color: '#5a6675', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
  }
  const toSeries = (arr, key) => arr.filter(p => p && p.date && p[key] != null).map(p => ({ time: p.date, value: p[key] }));
  _ahSeriesMe.setData(toSeries(snaps, 'return_pct'));
  _ahSeriesKospi.setData(toSeries(kospi, 'return_pct'));
  _ahSeriesSp.setData(toSeries(sp500, 'return_pct'));
  _ahChart.timeScale().fitContent();
  const last = (arr) => arr.length ? arr[arr.length - 1].return_pct : null;
  const me = last(snaps), ks = last(kospi), sp = last(sp500);
  if (summary) {
    const parts = [];
    if (me != null) parts.push(`<span style="color:#22c55e">내 ${_ahFmtPct(me)}</span>`);
    if (ks != null) parts.push(`<span style="color:#3b82f6">KOSPI ${_ahFmtPct(ks)}</span>`);
    if (sp != null) parts.push(`<span style="color:#f59e0b">S&amp;P ${_ahFmtPct(sp)}</span>`);
    summary.innerHTML = parts.join(' &nbsp;·&nbsp; ');
  }
}

// ── 보유 종목 ──────────────────────────────────────────────────────────────
function renderPositions(positions) {
  const el = document.getElementById('positionsList');
  // .card-title 이 flex(gap 8px)라 숫자를 따로 span 에 넣으면 '( 0 개)'처럼 벌어졌다 → 제목을 한 덩어리 글자로
  document.getElementById('holdingTitle').textContent = `보유 종목 (${positions.length}개)`;
  if (!positions.length) {
    el.innerHTML = '<div class="empty-state">보유 종목이 없습니다. 위에서 매수해보세요!</div>';
    return;
  }
  const rate = fx();
  // 금·은·동 강조는 표시 순서와 상관없이 '수익률 상위 3개(수익 중)' 에만
  const tier = new Map([...positions].filter((p) => (p.pnl_pct || 0) > 0)
    .sort((a, b) => b.pnl_pct - a.pnl_pct).slice(0, 3).map((p, i) => [p, i + 1]));
  const toKrw = (v, cur) => (v == null ? null : cur === 'USD' ? v * rate : v);
  const hasPx = (p) => p.current_price != null;
  const sorted = window.SAHoldSort ? SAHoldSort.sort(positions, _posSort, {
    value: (p) => (hasPx(p) ? toKrw(p.current_price * p.quantity, p.currency) : null),
    ret: (p) => (p.pnl_pct == null ? null : p.pnl_pct),
    pl: (p) => (hasPx(p) ? toKrw((p.current_price - p.purchase_price) * p.quantity, p.currency) : null),
    cost: (p) => toKrw(p.purchase_price * p.quantity, p.currency),
    name: (p) => p.name || p.ticker,
  }) : positions;
  const cardsHTML = sorted.map((p) => {
    // 순위 메달·번호 대신 회사 로고 (홈 '내 포트폴리오'와 같은 원형 로고 · 실패 시 이니셜)
    const rank = `<div class="trend-rank trend-rank-logo">${window.renderStockLogo ? renderStockLogo(p.ticker, p.name, 'md') : ''}</div>`;
    const cls = p.pnl_pct > 0 ? 'up' : p.pnl_pct < 0 ? 'down' : 'flat';
    const icon = p.pnl_pct > 0 ? '▲' : p.pnl_pct < 0 ? '▼' : '·';
    let badgeCls;
    if (p.pnl_pct == null) badgeCls = 'score-flat';
    else if (p.pnl_pct >= 15) badgeCls = 'score-gain-strong';
    else if (p.pnl_pct > 0) badgeCls = 'score-gain';
    else if (p.pnl_pct === 0) badgeCls = 'score-flat';
    else if (p.pnl_pct > -15) badgeCls = 'score-loss';
    else badgeCls = 'score-loss-strong';
    const dirClass = p.pnl_pct == null || p.pnl_pct === 0 ? 'pf-dir-flat' : p.pnl_pct > 0 ? 'pf-dir-gain' : 'pf-dir-loss';
    const tierClass = tier.has(p) ? `trend-tier-${tier.get(p)}` : '';
    const valueNative = (p.current_price || 0) * p.quantity;
    const mainConv = _convCur(valueNative, p.currency, _posCur, rate);
    const pnlConv = _convCur(((p.current_price || 0) - p.purchase_price) * p.quantity, p.currency, _posCur, rate);
    const buyConv = _convCur(p.purchase_price, p.currency, _posCur, rate);
    const tk = escapeHtml(p.ticker), nm = escapeHtml(p.name);
    const href = `/stock/${encodeURIComponent(p.ticker)}`;
    // 줄 구성 고정: 단가 줄 → (손절↔익절 바 | 하나만 예약됐을 때 익절/손절 배지) → 버튼 3개 줄
    const bar = _exitBar(p);                 // 익절·손절 둘 다 있으면 바 양 끝에 숫자가 있어 배지는 생략
    const exitBadge = bar ? '' : _exitBadge(p);
    return `
      <div class="pf-card trend-card-v2 tr-pos-card ${tierClass} ${dirClass}" data-t="${tk}" data-href="${href}" title="${nm} 종목 분석 보기">
        <div class="trend-card-row">
          ${rank}
          <div class="trend-name-block">
            <a class="pf-stock-name tr-pos-link" href="${href}">${nm}</a>
            <div class="pf-ticker">${tk}</div>
          </div>
          <div class="trend-spark-wrap"></div>
          <div class="trend-price-block">
            <div class="trend-price-val" title="평가금액">${_fmtCur(mainConv, _posCur)}</div>
            <div class="trend-chg pf-${cls} pf-amount">${(pnlConv >= 0 ? '+' : '') + _fmtCur(pnlConv, _posCur)}</div>
          </div>
          <div class="trend-score-v2 ${badgeCls}">${icon} ${Math.abs(p.pnl_pct || 0).toFixed(1)}<span class="score-suffix">%</span></div>
        </div>
        <div class="pf-meta-row tr-unit-row">
          <span class="pf-meta-item tr-unit" title="1주 단가 (종목 통화 기준 — 익절·손절과 같은 단위)">매입 ${fmtNative(p.purchase_price, p.currency)} → 현재 <b class="pf-${cls}">${fmtNative(p.current_price, p.currency)}</b> · ${p.quantity}주</span>
        </div>
        ${bar || (exitBadge ? `<div class="tr-exit-row">${exitBadge}</div>` : '')}
        <div class="tr-actions-row">
          <button type="button" class="trade-action-btn exit" data-act="exit" data-t="${tk}" aria-label="${nm} 목표가·손절가 예약">목표/손절</button>
          <button type="button" class="trade-action-btn buy" data-act="buy" data-t="${tk}">추가매수</button>
          <button type="button" class="trade-action-btn sell" data-act="sell" data-t="${tk}">매도</button>
        </div>
        ${p.note ? `<div class="pf-note">메모 · ${escapeHtml(p.note)}</div>` : ''}
      </div>`;
  }).join('');
  // PC(> 768px): 홈 '내 포트폴리오'와 같은 증권사식 1줄 표 / 모바일: 위 카드. 같은 정렬·통화 상태를 쓴다
  el.innerHTML = `
    <div class="pft tpt" role="table" aria-label="모의투자 보유 종목">
      <div class="pft-head tpt-head" role="row">
        <span role="columnheader">종목</span><span role="columnheader">수량 · 매입→현재</span><span role="columnheader">평가금액</span>
        <span role="columnheader">손익 · 수익률</span><span role="columnheader">손절 ↔ 익절</span><span role="columnheader" class="pft-h-acts">관리</span>
      </div>
      ${sorted.map((p) => _tptRowHTML(p, rate)).join('')}
    </div>
    <div class="tr-cards">${cardsHTML}</div>`;
  // 표의 행도 누르면 종목 상세로 (버튼은 각자 동작)
  el.querySelectorAll('.tpt-row').forEach((row) => {
    const go = () => { location.href = row.dataset.href; };
    row.addEventListener('click', (e) => {
      if (e.target.closest('button, a, input, select, textarea')) return;
      if (window.getSelection && String(window.getSelection())) return;
      go();
    });
    row.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === row) { e.preventDefault(); go(); } });
  });
  // 카드 아무 곳이나 누르면 종목 상세로 (안의 버튼·링크는 각자 동작, 텍스트를 드래그해 고를 때는 이동 안 함)
  el.querySelectorAll('.tr-pos-card').forEach((card) => card.addEventListener('click', (e) => {
    if (e.target.closest('button, a, input, select, textarea')) return;
    if (window.getSelection && String(window.getSelection())) return;
    location.href = card.dataset.href;
  }));
  el.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const p = (dashboardData.positions || []).find(x => x.ticker === b.dataset.t);
    if (!p) return;
    if (b.dataset.act === 'buy') openBuyModalForHolding(p.ticker, p.name, p.currency, p.current_price || 0);
    else if (b.dataset.act === 'exit') openExitModal(p);
    else openSellModal(p.ticker, p.name, p.currency, p.current_price || 0, p.quantity);
  }));
}

// PC 표 한 줄: 종목 | 수량·매입→현재 | 평가금액 | 손익·수익률 | 손절↔익절 | 관리
const _NOTE_SVG = '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3 2.5h10v8.5l-3 3H3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M5.5 6h5M5.5 8.5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>';

function _tptExit(p) {
  const lo = p.stop_loss_price, hi = p.target_price, cur = p.current_price;
  const f = (v) => fmtNative(v, p.currency);
  if (lo != null && hi != null && hi > lo && cur != null) {
    const pos = Math.max(0, Math.min(100, (cur - lo) / (hi - lo) * 100));
    const zone = pos >= 66 ? 'near-t' : pos <= 33 ? 'near-s' : 'mid';
    return `<div class="tpt-x ${zone}" title="손절 ${f(lo)} ↔ 익절 ${f(hi)}">
      <span class="tpt-x-track"><span class="tpt-x-fill" style="width:${pos.toFixed(1)}%"></span><span class="tpt-x-dot" style="left:${pos.toFixed(1)}%"></span></span>
      <span class="tpt-x-lab"><span class="s">${((lo / cur - 1) * 100).toFixed(1)}%</span><span class="t">+${((hi / cur - 1) * 100).toFixed(1)}%</span></span>
    </div>`;
  }
  if (hi != null) return `<span class="tpt-x-one t">익절 ${f(hi)}</span>`;
  if (lo != null) return `<span class="tpt-x-one s">손절 ${f(lo)}</span>`;
  return '<span class="tpt-none">—</span>';
}

function _tptRowHTML(p, rate) {
  const cls = p.pnl_pct > 0 ? 'up' : p.pnl_pct < 0 ? 'down' : 'flat';
  const tk = escapeHtml(p.ticker), nm = escapeHtml(p.name);
  const href = `/stock/${encodeURIComponent(p.ticker)}`;
  const value = _convCur((p.current_price || 0) * p.quantity, p.currency, _posCur, rate);
  const pnl = _convCur(((p.current_price || 0) - p.purchase_price) * p.quantity, p.currency, _posCur, rate);
  const logo = window.renderStockLogo ? renderStockLogo(p.ticker, p.name, 'sm') : '';
  const note = p.note ? `<span class="tpt-note" title="메모 · ${escapeHtml(p.note)}" aria-label="메모 있음">${_NOTE_SVG}</span>` : '';
  return `
    <div class="pft-row tpt-row pf-${cls}" role="row" tabindex="0" data-t="${tk}" data-href="${href}" aria-label="${nm} 종목 분석 보기">
      <div class="pft-stock">${logo}<div class="pft-id"><div class="pft-name">${nm}${note}</div><div class="pft-tk">${tk}</div></div></div>
      <div class="pft-qty"><div>${p.quantity}주</div><div class="pft-sub">${fmtNative(p.purchase_price, p.currency)} → <b class="pf-${cls}">${fmtNative(p.current_price, p.currency)}</b></div></div>
      <div class="pft-val">${_fmtCur(value, _posCur)}</div>
      <div class="pft-pl ${cls}"><div>${(pnl >= 0 ? '+' : '') + _fmtCur(pnl, _posCur)}</div><div class="pft-sub">${p.pnl_pct == null ? '—' : (p.pnl_pct > 0 ? '+' : '') + p.pnl_pct.toFixed(2) + '%'}</div></div>
      <div class="tpt-exit">${_tptExit(p)}</div>
      <div class="tpt-acts">
        <button type="button" class="trade-action-btn exit" data-act="exit" data-t="${tk}" aria-label="${nm} 목표가·손절가 예약">목표/손절</button>
        <button type="button" class="trade-action-btn buy" data-act="buy" data-t="${tk}" aria-label="${nm} 추가매수">추매</button>
        <button type="button" class="trade-action-btn sell" data-act="sell" data-t="${tk}" aria-label="${nm} 매도">매도</button>
      </div>
      ${p.note ? `<div class="tpt-note-line">메모 · ${escapeHtml(p.note)}</div>` : ''}
    </div>`;
}

// ── 거래 내역 (필터 · 검색) ────────────────────────────────────────────────
let _txAll = [], _txFilter = 'all', _txQuery = '';

async function loadTransactions() {
  try {
    const r = await fetch('/api/trading/transactions?limit=200');
    _txAll = (await r.json()) || [];
    renderTransactions();
  } catch (e) { console.warn('loadTransactions', e); }
}

function setupTxTools() {
  document.querySelectorAll('#txFilter [data-f]').forEach((b) => b.addEventListener('click', () => {
    _txFilter = b.dataset.f; _pressGroup(b); renderTransactions();
  }));
  const s = document.getElementById('txSearch');
  if (s) s.addEventListener('input', () => { _txQuery = s.value.trim().toLowerCase(); renderTransactions(); });
}

function renderTransactions() {
  const el = document.getElementById('transactionsList');
  const cnt = document.getElementById('txCount');
  const rows = _txAll.filter(t => (_txFilter === 'all' || t.type === _txFilter)
    && (!_txQuery || (t.name || '').toLowerCase().includes(_txQuery) || (t.ticker || '').toLowerCase().includes(_txQuery)));
  if (cnt) cnt.textContent = _txAll.length ? `${rows.length}${rows.length !== _txAll.length ? ' / ' + _txAll.length : ''}건` : '';
  if (!_txAll.length) { el.innerHTML = '<div class="empty-state">거래 내역이 없습니다</div>'; return; }
  if (!rows.length) { el.innerHTML = '<div class="empty-state">조건에 맞는 거래가 없습니다</div>'; return; }
  el.innerHTML = `
    <div class="tx-grid tx-grid-head" aria-hidden="true">
      <span>체결 시각</span><span>구분</span><span>종목</span><span class="num">수량 × 단가</span>
      <span class="num">금액</span><span class="num">수수료</span><span class="num">실현손익</span>
    </div>
    ${rows.map(t => {
      const sell = t.type === 'sell';
      const pnl = t.realized_pnl_krw || 0;
      const pnlCls = pnl > 0 ? 'up' : pnl < 0 ? 'down' : '';
      return `<div class="tx-row">
        <div class="tx-grid">
          <span class="tx-time">${fmtDateTime(parseServerTime(t.timestamp))}</span>
          <span><span class="tx-type ${sell ? 'sell' : 'buy'}">${sell ? '매도' : '매수'}</span></span>
          <span class="tx-stock-cell lg-id">${(window.renderStockLogo ? renderStockLogo(t.ticker, t.name, 'sm') : '')}<span class="lg-id-text"><a class="tx-stock" href="/stock/${encodeURIComponent(t.ticker)}">${escapeHtml(t.name)}</a><span class="tx-ticker">${escapeHtml(t.ticker)}</span></span></span>
          <span class="num" data-l="수량 × 단가">${t.quantity}주 × ${fmtNative(t.price, t.currency)}</span>
          <span class="num" data-l="금액">${fmtKRW(t.amount_krw)}</span>
          <span class="num muted" data-l="수수료">${fmtKRW(t.fee_krw)}</span>
          <span class="num ${sell ? pnlCls : 'muted'}" data-l="실현손익">${sell ? (pnl >= 0 ? '+' : '') + fmtKRW(pnl) : '—'}</span>
        </div>
        ${t.note ? `<div class="tx-memo">메모 · ${escapeHtml(t.note)}</div>` : ''}
      </div>`;
    }).join('')}`;
}

// ── 빠른 매수: 실시간 자동완성 → 고르면 바로 매수 창 ───────────────────────
let _qbActive = -1, _qbSeq = 0, _qbTimer = null;
function _qbMarket(sym) { return isKR(sym) ? 'KR' : 'US'; }
// 목록 닫기 + 아직 안 온 검색 응답 무효화 (늦게 온 응답이 닫은 목록을 다시 열지 않게)
function _qbCloseList() {
  clearTimeout(_qbTimer); _qbSeq++;
  const list = document.getElementById('qbAcList'), input = document.getElementById('qbTicker');
  if (list) list.classList.add('hidden');
  if (input) { input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); }
  _qbActive = -1;
}

function setupQuickBuyAC() {
  const input = document.getElementById('qbTicker');
  const list = document.getElementById('qbAcList');
  if (!input) return;
  const close = _qbCloseList;
  const paint = () => list.querySelectorAll('.qb-ac-item').forEach((el, i) => {
    const on = i === _qbActive; el.classList.toggle('active', on); el.setAttribute('aria-selected', on ? 'true' : 'false');
    if (on) { el.scrollIntoView({ block: 'nearest' }); input.setAttribute('aria-activedescendant', el.id); }
  });
  input.addEventListener('input', () => {
    _qbHint('종목을 고르면 바로 매수 창이 열립니다.');
    delete input.dataset.symbol; delete input.dataset.name;
    const q = input.value.trim();
    clearTimeout(_qbTimer);
    if (!q) { close(); return; }
    _qbTimer = setTimeout(async () => {
      const my = ++_qbSeq;
      try {
        const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
        const items = (await r.json()) || [];
        if (my !== _qbSeq) return;                            // 늦게 온 이전 검색 결과·이미 닫은 목록은 무시
        qbACItems = items.slice(0, 8);
        if (!qbACItems.length) {
          list.innerHTML = '<div class="qb-ac-empty">검색 결과가 없습니다</div>';
        } else {
          list.innerHTML = qbACItems.map((it, i) => `
            <div class="autocomplete-item qb-ac-item" id="qbOpt${i}" role="option" aria-selected="false" data-i="${i}">
              ${(window.renderStockLogo ? renderStockLogo(it.symbol, it.name, 'xs') : '')}
              <span class="qb-mkt ${_qbMarket(it.symbol).toLowerCase()}">${_qbMarket(it.symbol)}</span>
              <span class="ac-name">${escapeHtml(it.name)}</span>
              <span class="ac-symbol">${escapeHtml(it.symbol)}</span>
            </div>`).join('');
          list.querySelectorAll('.qb-ac-item').forEach((el) => el.addEventListener('mousedown', (e) => {
            e.preventDefault();                               // 입력창 blur 로 목록이 먼저 닫히지 않게
            selectQbAC(+el.dataset.i);
          }));
        }
        _qbActive = -1;
        list.classList.remove('hidden'); input.setAttribute('aria-expanded', 'true');
      } catch (e) { close(); }
    }, 150);
  });
  input.addEventListener('blur', () => setTimeout(close, 150));
  input.addEventListener('keydown', (e) => {
    const n = list.classList.contains('hidden') ? 0 : list.querySelectorAll('.qb-ac-item').length;
    if (e.key === 'ArrowDown' && n) { e.preventDefault(); _qbActive = (_qbActive + 1) % n; paint(); }
    else if (e.key === 'ArrowUp' && n) { e.preventDefault(); _qbActive = (_qbActive - 1 + n) % n; paint(); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (_qbActive >= 0) selectQbAC(_qbActive); else { close(); openQuickBuy(); }
    } else if (e.key === 'Escape') close();
  });
}

function _qbHint(msg, isErr) {
  const el = document.getElementById('qbBadge');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('err', !!isErr);
}

function _qbLoading(on) {
  const inp = document.getElementById('qbTicker'), btn = document.getElementById('qbBtn'), sp = document.getElementById('qbSpin');
  inp.disabled = on; btn.disabled = on;
  btn.textContent = on ? '불러오는 중…' : '매수';
  btn.setAttribute('aria-busy', on ? 'true' : 'false');
  if (sp) sp.classList.toggle('hidden', !on);
}

function selectQbAC(i) {
  const item = qbACItems[i];
  if (!item) return;
  const inp = document.getElementById('qbTicker');
  inp.value = `${item.name} (${item.symbol})`;
  inp.dataset.symbol = item.symbol; inp.dataset.name = item.name;
  _qbCloseList();
  openQuickBuy();                                        // 고르자마자 매수 창
}

async function _qbResolve(q) {
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const items = (await r.json()) || [];
    const norm = (s) => (s || '').replace(/\s/g, '').toUpperCase();
    const exact = items.find(it => norm(it.symbol) === norm(q) || norm(it.name) === norm(q));
    return exact || (items.length === 1 ? items[0] : null);
  } catch { return null; }
}

let _qbBusy = false;
async function openQuickBuy() {
  if (_qbBusy) return;                                   // 연타 방지
  const inp = document.getElementById('qbTicker');
  let sym = inp.dataset.symbol, nm = inp.dataset.name;
  _qbCloseList();
  _qbBusy = true; _qbLoading(true);
  try {
    if (!sym) {
      const q = inp.value.trim();
      if (!q) { _qbHint('종목명이나 티커를 입력해 주세요.', true); return; }
      const hit = await _qbResolve(q);
      if (!hit) { _qbHint(`'${q}' 종목을 정확히 찾지 못했어요. 자동완성 목록에서 골라 주세요.`, true); return; }
      sym = hit.symbol; nm = hit.name;
      inp.dataset.symbol = sym; inp.dataset.name = nm;
    }
    const q = await fetchQuote(sym);
    _qbHint(`${nm} (${sym}) · ${isKR(sym) ? '원화' : '달러'} 종목`);
    openBuyModalForHolding(sym, nm, isKR(sym) ? 'KRW' : 'USD', q.price || 0, q.market_open);
  } finally {
    _qbBusy = false; _qbLoading(false);
  }
}

async function fetchQuote(ticker) {
  try {
    const r = await fetch(`/api/realtime-price?ticker=${encodeURIComponent(ticker)}`);
    const d = await r.json();
    if (d && d.price) return d;
  } catch (e) {}
  return { price: await fetchCurrentPrice(ticker), market_open: null };
}

async function fetchCurrentPrice(ticker) {
  try {
    const r = await fetch(`/api/chart?ticker=${encodeURIComponent(ticker)}&interval=1d`);
    const d = await r.json();
    if (d && d.chart && d.chart.close && d.chart.close.length) {
      const closes = d.chart.close.filter(v => v != null);
      return closes[closes.length - 1];
    }
  } catch (e) {}
  return null;
}

// ── 시세 폴링 (체결가는 서버가 그 순간 시세로 정한다 — 화면 값은 참고) ──
let _pricePollTimer = null;
function startPricePolling(kind, ticker) {
  stopPricePolling();
  const apply = async () => {
    try {
      const r = await fetch(`/api/realtime-price?ticker=${encodeURIComponent(ticker)}`);
      const d = await r.json();
      if (d && d.price) _setTradePrice(kind, d.price, d.market_open);
    } catch (e) { /* 무시 */ }
  };
  apply();
  _pricePollTimer = setInterval(apply, 2000);
}
function stopPricePolling() { if (_pricePollTimer) { clearInterval(_pricePollTimer); _pricePollTimer = null; } }

function _setTradePrice(kind, price, marketOpen) {
  const cur = document.getElementById(kind + 'Currency').value;
  document.getElementById(kind + 'Price').value = price || '';
  document.getElementById(kind + 'PriceView').textContent = price ? fmtNative(price, cur) : '—';
  const st = document.getElementById(kind + 'PriceState');
  st.className = 'tt-state ' + (marketOpen === true ? 'live' : marketOpen === false ? 'closed' : '');
  st.textContent = !price ? '불러오는 중' : marketOpen === true ? '실시간' : marketOpen === false ? '장 마감 종가' : '현재가';
  if (kind === 'buy') updateBuySummary(); else updateSellSummary();
}

// ── 매수 모달 ──────────────────────────────────────────────────────────────
function _priceKRW(price, cur) { return cur === 'KRW' ? price : price * fx(); }
function _maxBuyQty() {
  const cur = document.getElementById('buyCurrency').value;
  const price = parseFloat(document.getElementById('buyPrice').value) || 0;
  const cash = dashboardData ? dashboardData.cash_krw : 0;
  const unit = _priceKRW(price, cur) * (1 + FEE_RATE);
  return unit > 0 ? Math.max(0, Math.floor(cash / unit)) : 0;
}

function openBuyModalForHolding(ticker, name, currency, price, marketOpen, prefill) {
  prefill = prefill || {};
  document.getElementById('buyTicker').value = ticker;
  document.getElementById('buyName').value = name;
  document.getElementById('buyCurrency').value = currency;
  document.getElementById('buyModalName').textContent = name;
  document.getElementById('buyModalTicker').textContent = ticker;
  document.getElementById('buyAnalyzeLink').href = '/stock/' + encodeURIComponent(ticker);
  document.getElementById('buyNote').placeholder = currency === 'KRW'
    ? '예: 실적 개선 기대로 매수. 78,000원 도달 시 절반 익절 예정'
    : '예: AI 수요 기대로 매수. $150 도달 시 절반 익절 예정';
  document.getElementById('buyQty').value = '';
  document.getElementById('buyNote').value = prefill.memo || '';
  document.getElementById('buyError').textContent = '';
  document.getElementById('buyTarget').value = prefill.target || '';
  document.getElementById('buyStop').value = prefill.stop || '';
  document.getElementById('buyExit').open = !!(prefill.target || prefill.stop);
  const step = currency === 'KRW' ? '1' : '0.01';
  document.getElementById('buyTarget').step = step; document.getElementById('buyStop').step = step;
  _setTradePrice('buy', price || 0, marketOpen);
  const m = document.getElementById('tradeBuyModal');
  m.classList.remove('hidden');
  startPricePolling('buy', ticker);
  setTimeout(() => document.getElementById('buyQty').focus(), 50);
}

function closeBuyModal(event) {
  const m = document.getElementById('tradeBuyModal');
  if (!event || event.target === m) { m.classList.add('hidden'); stopPricePolling(); }
}

function _validQty(v) { return Number.isInteger(v) && v > 0; }

function updateBuySummary() {
  const cur = document.getElementById('buyCurrency').value || 'USD';
  const price = parseFloat(document.getElementById('buyPrice').value) || 0;
  const raw = document.getElementById('buyQty').value;
  const qty = raw === '' ? 0 : Number(raw);
  const amount = price * qty;
  const amountKrw = _priceKRW(amount, cur);
  const fee = Math.round(amountKrw * FEE_RATE);
  const total = amountKrw + fee;
  const cash = dashboardData ? dashboardData.cash_krw : 0;
  const remain = cash - total;
  const maxQ = _maxBuyQty();
  document.getElementById('buyMaxHint').innerHTML = price > 0
    ? `최대 <b>${maxQ.toLocaleString('ko-KR')}주</b> 가능 <span class="tt-cash">· 현금 ${fmtKRW(cash)}</span>` : '';
  document.querySelectorAll('#tradeBuyModal [data-pct]').forEach((b) => { b.disabled = !(price > 0 && maxQ > 0); });
  document.getElementById('buySummary').innerHTML = `
    <div class="ts-row"><span>매수 금액 (${cur})</span><span>${fmtNative(amount, cur)}</span></div>
    ${cur === 'USD' ? `<div class="ts-row"><span>원화 환산 (1달러 ${fx().toLocaleString('ko-KR')}원)</span><span>${fmtKRW(amountKrw)}</span></div>` : ''}
    <div class="ts-row"><span>수수료 (0.1%)</span><span>${fmtKRW(fee)}</span></div>
    <div class="ts-row total"><span>총 차감액</span><span>${fmtKRW(total)}</span></div>
    <div class="ts-row ${remain < 0 ? 'warn' : ''}"><span>매수 후 현금</span><span>${fmtKRW(remain)}</span></div>`;
  // 유효성: 빈 값 · 0 이하 · 소수 · 현금 초과 · 시세 없음 → 버튼 비활성 + 이유
  const ex = _exitCalc('buy', price, cur, null);
  let why = '';
  if (!(price > 0)) why = '시세를 불러오는 중입니다';
  else if (ex.error) why = ex.error;
  else if (raw === '') why = '';
  else if (!_validQty(qty)) why = '수량은 1주 이상의 정수로 입력해 주세요';
  else if (remain < 0) why = `보유 현금을 ${fmtKRW(-remain)} 넘습니다 (최대 ${maxQ}주)`;
  const ok = price > 0 && _validQty(qty) && remain >= 0 && !ex.error;
  const btn = document.getElementById('buySubmit');
  if (!btn.dataset.busy) btn.disabled = !ok;
  document.getElementById('buyError').textContent = why;
  document.getElementById('buyError').classList.toggle('soft', !(ex.error || (_validQty(qty) && remain < 0)));
}

function _fillPct(pct) {
  const maxQ = _maxBuyQty();
  document.getElementById('buyQty').value = pct >= 100 ? maxQ : Math.floor(maxQ * pct / 100);
  updateBuySummary();
}

async function submitBuy() {
  const btn = document.getElementById('buySubmit');
  if (btn.disabled) return;
  const ticker = document.getElementById('buyTicker').value;
  const name = document.getElementById('buyName').value;
  const qty = Number(document.getElementById('buyQty').value);
  const note = (document.getElementById('buyNote').value || '').trim();
  const ex = _exitValues('buy');
  btn.disabled = true; btn.dataset.busy = '1'; btn.textContent = '매수 중…';
  try {
    const r = await fetch('/api/trading/buy', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      // 체결가는 서버 실시간가 (가격 미전송). 예약은 입력한 것만 보낸다
      body: JSON.stringify(Object.assign({ ticker, name, quantity: qty, note },
        ex.target ? { target_price: ex.target } : {}, ex.stop ? { stop_loss_price: ex.stop } : {})),
    });
    const d = await r.json();
    if (!r.ok) { document.getElementById('buyError').textContent = d.error || '매수하지 못했습니다'; document.getElementById('buyError').classList.remove('soft'); return; }
    closeBuyModal();
    toast(`${name} ${qty}주 매수 완료${ex.target || ex.stop ? ' · 자동 청산 예약' : ''}`);
    await loadDashboard();
  } catch (e) {
    document.getElementById('buyError').textContent = '네트워크 오류: ' + (e.message || e);
  } finally {
    delete btn.dataset.busy; btn.textContent = '매수 실행'; updateBuySummary();
  }
}

// ── 매도 모달 ──────────────────────────────────────────────────────────────
let _sellMaxQty = 0;
function openSellModal(ticker, name, currency, price, maxQty) {
  document.getElementById('sellTicker').value = ticker;
  document.getElementById('sellCurrency').value = currency;
  document.getElementById('sellModalName').textContent = name;
  document.getElementById('sellModalTicker').textContent = ticker;
  document.getElementById('sellAnalyzeLink').href = '/stock/' + encodeURIComponent(ticker);
  document.getElementById('sellQty').value = '';
  document.getElementById('sellNote').value = '';
  document.getElementById('sellError').textContent = '';
  document.getElementById('sellMaxQty').textContent = maxQty;
  _sellMaxQty = maxQty;
  _setTradePrice('sell', price || 0, null);
  document.getElementById('tradeSellModal').classList.remove('hidden');
  startPricePolling('sell', ticker);
  setTimeout(() => document.getElementById('sellQty').focus(), 50);
}

function closeSellModal(event) {
  const m = document.getElementById('tradeSellModal');
  if (!event || event.target === m) { m.classList.add('hidden'); stopPricePolling(); }
}

function setSellMax() { document.getElementById('sellQty').value = Math.floor(_sellMaxQty); updateSellSummary(); }

function updateSellSummary() {
  const cur = document.getElementById('sellCurrency').value || 'USD';
  const price = parseFloat(document.getElementById('sellPrice').value) || 0;
  const raw = document.getElementById('sellQty').value;
  const qty = raw === '' ? 0 : Number(raw);
  const amount = price * qty;
  const amountKrw = _priceKRW(amount, cur);
  const fee = Math.round(amountKrw * FEE_RATE);
  const ticker = document.getElementById('sellTicker').value;
  let pnl = null;
  const pos = dashboardData && (dashboardData.positions || []).find(p => p.ticker === ticker);
  if (pos) pnl = amountKrw - _priceKRW(pos.purchase_price * qty, cur);
  document.getElementById('sellSummary').innerHTML = `
    <div class="ts-row"><span>매도 금액 (${cur})</span><span>${fmtNative(amount, cur)}</span></div>
    ${cur === 'USD' ? `<div class="ts-row"><span>원화 환산 (1달러 ${fx().toLocaleString('ko-KR')}원)</span><span>${fmtKRW(amountKrw)}</span></div>` : ''}
    <div class="ts-row"><span>수수료 (0.1%)</span><span>${fmtKRW(fee)}</span></div>
    <div class="ts-row total"><span>받을 금액</span><span>${fmtKRW(amountKrw - fee)}</span></div>
    ${pnl != null && qty > 0 ? `<div class="ts-row ${pnl >= 0 ? 'up' : 'warn'}"><span>예상 손익</span><span>${pnl >= 0 ? '+' : ''}${fmtKRW(pnl)}</span></div>` : ''}`;
  let why = '';
  if (!(price > 0)) why = '시세를 불러오는 중입니다';
  else if (raw !== '' && !_validQty(qty)) why = '수량은 1주 이상의 정수로 입력해 주세요';
  else if (qty > _sellMaxQty) why = `보유 수량(${_sellMaxQty}주)을 넘습니다`;
  const ok = price > 0 && _validQty(qty) && qty <= _sellMaxQty;
  const btn = document.getElementById('sellSubmit');
  if (!btn.dataset.busy) btn.disabled = !ok;
  document.getElementById('sellError').textContent = why;
}

async function submitSell() {
  const btn = document.getElementById('sellSubmit');
  if (btn.disabled) return;
  const ticker = document.getElementById('sellTicker').value;
  const name = document.getElementById('sellModalName').textContent;
  const qty = Number(document.getElementById('sellQty').value);
  const note = (document.getElementById('sellNote').value || '').trim();
  btn.disabled = true; btn.dataset.busy = '1'; btn.textContent = '매도 중…';
  try {
    const r = await fetch('/api/trading/sell', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, quantity: qty, note }),
    });
    const d = await r.json();
    if (!r.ok) { document.getElementById('sellError').textContent = d.error || '매도하지 못했습니다'; return; }
    closeSellModal();
    const pnl = d.realized_pnl_krw || 0;
    toast(`${name} ${qty}주 매도 완료${pnl ? ` · 실현 손익 ${pnl >= 0 ? '+' : ''}${fmtKRW(pnl)}` : ''}`);
    await loadDashboard();
  } catch (e) {
    document.getElementById('sellError').textContent = '네트워크 오류: ' + (e.message || e);
  } finally {
    delete btn.dataset.busy; btn.textContent = '매도 실행'; updateSellSummary();
  }
}

// ── 매매 모달 입력 ─────────────────────────────────────────────────────────
function _stepTradeNum(modalType, inputId, direction) {
  const input = document.getElementById(inputId);
  if (!input) return;
  let cur = parseFloat(input.value); if (isNaN(cur)) cur = 0;
  input.value = Math.max(0, Math.round(cur + direction));
  if (modalType === 'buy') updateBuySummary(); else updateSellSummary();
}

function setupTradeInputs() {
  [['buyQty', 'buy', updateBuySummary], ['sellQty', 'sell', updateSellSummary]].forEach(([id, type, upd]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', upd);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp')   { e.preventDefault(); _stepTradeNum(type, id, 1); }
      if (e.key === 'ArrowDown') { e.preventDefault(); _stepTradeNum(type, id, -1); }
      if (e.key === 'Enter') { e.preventDefault(); (type === 'buy' ? submitBuy : submitSell)(); }
    });
  });
  document.querySelectorAll('#tradeBuyModal [data-pct]').forEach((b) => b.addEventListener('click', () => _fillPct(+b.dataset.pct)));
  ['buyTarget', 'buyStop'].forEach((id) => document.getElementById(id).addEventListener('input', updateBuySummary));
  ['exitTarget', 'exitStop'].forEach((id) => document.getElementById(id).addEventListener('input', updateExitCalc));
  document.querySelectorAll('#tradeSellModal [data-spct]').forEach((b) => b.addEventListener('click', () => {
    const p = +b.dataset.spct;
    document.getElementById('sellQty').value = p >= 100 ? Math.floor(_sellMaxQty) : Math.max(1, Math.floor(_sellMaxQty * p / 100));
    updateSellSummary();
  }));
}

// ── AI 코치 · 점검: 최소 조건 진행률 ───────────────────────────────────────
function renderAiGates() {
  const d = dashboardData || {};
  const tx = (d.stats && d.stats.total_trades) || 0;
  const cBtn = document.getElementById('coachBtn'), cP = document.getElementById('coachProgress');
  const cOk = tx >= COACH_MIN_TX;
  if (!cBtn.dataset.busy) cBtn.disabled = !cOk;
  cBtn.classList.toggle('locked', !cOk);
  cBtn.title = cOk ? '' : `거래가 ${COACH_MIN_TX}건 이상 쌓이면 분석할 수 있어요`;
  cP.textContent = cOk ? '' : `거래 ${tx}/${COACH_MIN_TX}건`;
  cP.classList.toggle('hidden', cOk);
  const held = _reviewMode === 'real' ? (d.real_holdings_count || 0) : (d.positions || []).length;
  const rBtn = document.getElementById('reviewBtn'), rP = document.getElementById('reviewProgress');
  const rOk = held >= REVIEW_MIN_HOLD;
  if (!rBtn.dataset.busy) rBtn.disabled = !rOk;
  rBtn.classList.toggle('locked', !rOk);
  rBtn.title = rOk ? '' : `보유 종목이 ${REVIEW_MIN_HOLD}개 이상이면 점검할 수 있어요`;
  rP.textContent = rOk ? '' : `보유 ${held}/${REVIEW_MIN_HOLD}종목`;
  rP.classList.toggle('hidden', rOk);
  document.getElementById('reviewRealHint').classList.toggle('hidden', _reviewMode !== 'real');
  // 토스 계좌 언급은 연동 권한이 있는 소유자에게만
  const canToss = !!(currentUser && currentUser.can_import_toss === true);
  const tossHint = document.getElementById('reviewRealHintToss'), plainHint = document.getElementById('reviewRealHintPlain');
  if (tossHint) tossHint.classList.toggle('hidden', !canToss);
  if (plainHint) plainHint.classList.toggle('hidden', canToss);
}

// ── AI 매매 코치 ───────────────────────────────────────────────────────────
let _coachLoaded = false;
async function loadCoachCached() {
  if (_coachLoaded) return;
  try {
    const r = await fetch('/api/trading/coach');
    if (r.status !== 200) return;
    renderCoach(await r.json());
    _coachLoaded = true;
  } catch (e) {}
}

async function requestCoach() {
  const btn = document.getElementById('coachBtn');
  if (btn.disabled) return;
  btn.disabled = true; btn.dataset.busy = '1';
  const orig = btn.textContent;
  btn.textContent = '분석 중…';
  try {
    const r = await fetch('/api/trading/coach', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) {
      document.getElementById('coachBody').innerHTML = `<div class="empty-state">${escapeHtml(d.error || '분석에 실패했습니다')}</div>`;
      document.getElementById('coachMeta').textContent = '';
      return;
    }
    renderCoach(d);
    _coachLoaded = true;
  } catch (e) {
    toast('네트워크 오류: ' + (e.message || e), 'err');
  } finally {
    delete btn.dataset.busy; btn.textContent = orig; renderAiGates();
  }
}

function _feedbackHTML(text) {
  return text.split('\n').map(s => s.trim()).filter(Boolean).map(s => s.replace(/^[-•*]\s*/, ''))
    .map(s => `<div class="coach-item">${escapeHtml(s)}</div>`).join('');
}
function _metaTime(prefix, iso) {
  const dt = parseServerTime(iso);
  return dt ? `${prefix}: ${dt.getMonth() + 1}/${dt.getDate()} ${pad2(dt.getHours())}:${pad2(dt.getMinutes())}` : '';
}

function renderCoach(d) {
  if (!d || !d.feedback) return;
  document.getElementById('coachBody').innerHTML = _feedbackHTML(d.feedback);
  let meta = _metaTime('마지막 분석', d.generated_at);
  if (meta && d.cached) meta += ' · 새 거래가 쌓이면 다시 분석할 수 있어요';
  document.getElementById('coachMeta').textContent = meta;
}

// ── AI 포트폴리오 점검 ─────────────────────────────────────────────────────
let _reviewMode = 'paper';
const _reviewResults = {};

function setReviewMode(mode, btn) {
  _reviewMode = mode;
  document.querySelectorAll('.review-card .ah-period-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderAiGates();
  if (_reviewResults[mode]) renderReview(_reviewResults[mode]);
  else {
    document.getElementById('reviewBody').innerHTML = '<div class="empty-state">「점검 받기」를 누르면 ' +
      (mode === 'paper' ? '모의투자' : '실제') + ' 포트폴리오를 점검해드려요.</div>';
    document.getElementById('reviewMeta').textContent = '';
    loadReviewCached(mode);
  }
}

async function loadReviewCached(mode) {
  const m = mode || _reviewMode;
  try {
    const r = await fetch(`/api/portfolio/review?mode=${m}`);
    if (r.status !== 200) return;
    const d = await r.json();
    _reviewResults[m] = d;
    if (m === _reviewMode) renderReview(d);
  } catch (e) {}
}

async function requestReview() {
  const btn = document.getElementById('reviewBtn');
  if (btn.disabled) return;
  btn.disabled = true; btn.dataset.busy = '1';
  const orig = btn.textContent;
  btn.textContent = '점검 중…';
  const mode = _reviewMode;
  try {
    const r = await fetch('/api/portfolio/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }),
    });
    const d = await r.json();
    if (!r.ok) {
      document.getElementById('reviewBody').innerHTML = `<div class="empty-state">${escapeHtml(d.error || '점검에 실패했습니다')}</div>`;
      document.getElementById('reviewMeta').textContent = '';
      return;
    }
    _reviewResults[mode] = d;
    if (mode === _reviewMode) renderReview(d);
  } catch (e) {
    toast('네트워크 오류: ' + (e.message || e), 'err');
  } finally {
    delete btn.dataset.busy; btn.textContent = orig; renderAiGates();
  }
}

function renderReview(d) {
  if (!d || !d.feedback) return;
  document.getElementById('reviewBody').innerHTML = _feedbackHTML(d.feedback);
  document.getElementById('reviewMeta').textContent = _metaTime('마지막 점검', d.generated_at);
}

// ── 확인 모달 (브라우저 confirm 대신) ─────────────────────────────────────
let _confirmResolve = null, _confirmReturn = null;
function askConfirm({ title, desc, ok = '확인', danger = true }) {
  document.getElementById('confirmTitle').textContent = title;
  document.getElementById('confirmDesc').innerHTML = desc;
  const okBtn = document.getElementById('confirmOk');
  okBtn.textContent = ok;
  okBtn.className = 'tr-btn ' + (danger ? 'tr-btn-danger' : 'tr-btn-primary');
  _confirmReturn = document.activeElement;
  document.getElementById('confirmModal').classList.remove('hidden');
  setTimeout(() => document.getElementById('confirmCancel').focus(), 30);   // 위험한 동작은 '취소'에 먼저 포커스
  return new Promise((res) => { _confirmResolve = res; });
}
function closeConfirm(result) {
  document.getElementById('confirmModal').classList.add('hidden');
  if (_confirmReturn && _confirmReturn.focus) _confirmReturn.focus();
  if (_confirmResolve) { const r = _confirmResolve; _confirmResolve = null; r(!!result); }
}

async function resetTrading() {
  const ok = await askConfirm({
    title: '계좌를 초기화할까요?',
    desc: '보유 종목과 거래 내역이 <strong>모두 삭제</strong>되고 현금이 1억 원으로 돌아갑니다. 되돌릴 수 없어요.',
    ok: '초기화',
  });
  if (!ok) return;
  try {
    const r = await fetch('/api/trading/reset', { method: 'POST' });
    if (!r.ok) { toast('초기화하지 못했습니다', 'err'); return; }
    _coachLoaded = false;
    await loadDashboard();
    toast('계좌를 초기화했어요 (현금 1억 원)');
  } catch (e) { toast('네트워크 오류: ' + (e.message || e), 'err'); }
}

// ── 배지 ───────────────────────────────────────────────────────────────────
let _nicknamePromptShown = false;
let _badgesCache = null;

async function loadBadges() {
  try {
    const r = await fetch('/api/me/badges');
    if (!r.ok) return;
    const data = await r.json();
    _badgesCache = data;
    renderBadges(data);
  } catch (e) { console.warn('badges load failed', e); }
}

const TIER_LABEL = { bronze: '브론즈', silver: '실버', gold: '골드', diamond: '다이아', legend: '전설' };

function renderBadges(data) {
  const wrap = document.getElementById('badgesList');
  const prog = document.getElementById('badgeProgress');
  if (!wrap) return;
  // 개수는 화면에 그리는 배지 목록 기준으로 센다 (서버 earned_count 는 지금은 없는 옛 배지 키까지 셀 수 있음)
  const total = data.badges.length;
  const earned = data.badges.filter(b => b.earned);
  if (prog) prog.textContent = `${earned.length} / ${total}`;
  const byCat = {};
  for (const b of data.badges) (byCat[b.category] = byCat[b.category] || []).push(b);
  const order = ['활동', '수익', '트레이딩', '포트폴리오', '글로벌', '성과', '꾸준함'];
  const cats = Object.keys(byCat).sort((a, b) => ((order.indexOf(a) + 100) % 100) - ((order.indexOf(b) + 100) % 100));
  const pct = total ? Math.round(earned.length / total * 100) : 0;
  const icons = earned.slice(0, 12).map((b, i) =>
    `<button type="button" class="badge-mini tier-${b.tier}" data-bi="${i}" title="${escapeHtml(b.name)} — ${escapeHtml(b.desc)}"
       aria-label="${escapeHtml(b.name)} (${TIER_LABEL[b.tier] || ''}) — ${escapeHtml(b.desc)}">${b.icon}</button>`).join('');
  const more = earned.length > 12 ? `<span class="badge-mini-more">+${earned.length - 12}</span>` : '';
  const expanded = _badgesExpanded();
  wrap.innerHTML = `
    <div class="badge-summary">
      <div class="badge-summary-bar" role="img" aria-label="배지 ${earned.length}개 획득, 전체 ${total}개"><div class="badge-summary-fill" style="width:${pct}%"></div></div>
      <div class="badge-summary-row">
        <div class="badge-summary-icons">${earned.length ? icons + more : '<span class="badge-summary-empty">아직 획득한 배지가 없어요. 첫 매수를 해 보세요!</span>'}</div>
        <button type="button" class="badge-toggle-btn" onclick="toggleBadgesList()" aria-expanded="${expanded}">
          ${expanded ? '접기 ▴' : `전체 ${total}개 보기 ▾`}</button>
      </div>
      <div class="badge-peek" id="badgePeek" aria-live="polite"></div>
    </div>
    <div id="badgesFull" class="badges-full${expanded ? '' : ' hidden'}">
      ${cats.map(cat => `
        <div class="badge-cat">
          <div class="badge-cat-title">${cat} <span class="badge-cat-n">${byCat[cat].filter(b => b.earned).length}/${byCat[cat].length}</span></div>
          <div class="badge-cat-grid">${byCat[cat].map(b => _badgeHTML(b)).join('')}</div>
        </div>`).join('')}
    </div>`;
  // 칩을 누르면(모바일 탭 포함) 아래에 이름·획득 조건
  wrap.querySelectorAll('.badge-mini[data-bi]').forEach((el) => el.addEventListener('click', () => {
    const b = earned[+el.dataset.bi];
    const peek = document.getElementById('badgePeek');
    const same = peek.dataset.k === b.key && peek.classList.contains('show');
    peek.dataset.k = b.key;
    peek.classList.toggle('show', !same);
    peek.innerHTML = same ? '' : `<b>${b.icon} ${escapeHtml(b.name)}</b> <span class="badge-peek-tier">${TIER_LABEL[b.tier] || ''}</span> · ${escapeHtml(b.desc)}`;
  }));
  window._badgesData = data;
}

function _badgesExpanded() { try { return localStorage.getItem('badgesExpanded') === '1'; } catch { return false; } }
function toggleBadgesList() {
  const next = !_badgesExpanded();
  try { localStorage.setItem('badgesExpanded', next ? '1' : '0'); } catch {}
  if (window._badgesData) renderBadges(window._badgesData);
}

function _badgeHTML(b) {
  const cls = b.earned ? `badge-card badge-earned tier-${b.tier}` : 'badge-card badge-locked';
  return `
    <div class="${cls}" title="${escapeHtml(b.name)} — ${escapeHtml(b.desc)}" onclick="_toggleBadgeDetail(this)">
      <div class="badge-icon">${b.earned ? b.icon : '🔒'}</div>
      <div class="badge-name">${escapeHtml(b.name)}</div>
      <div class="badge-tier">${TIER_LABEL[b.tier] || ''}</div>
      <div class="badge-desc">${escapeHtml(b.desc)}</div>
    </div>`;
}
function _toggleBadgeDetail(el) { el.classList.toggle('badge-show-detail'); }

function _showBadgeToasts(keys) {
  if (!_badgesCache || !_badgesCache.badges) { setTimeout(() => _showBadgeToasts(keys), 1200); return; }
  const byKey = {};
  for (const b of _badgesCache.badges) byKey[b.key] = b;
  let delay = 0;
  for (const k of keys) {
    const b = byKey[k];
    if (!b) continue;
    setTimeout(() => _showBadgeToast(b), delay);
    delay += 3200;
  }
}
function _showBadgeToast(b) {
  const t = document.getElementById('badgeToast');
  if (!t) return;
  document.getElementById('badgeToastIcon').textContent = b.icon || '🎉';
  document.getElementById('badgeToastName').textContent = `${b.name} — ${b.desc}`;
  t.classList.remove('hidden'); t.classList.add('show');
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.classList.add('hidden'), 350); }, 3000);
}

// ── 닉네임 ─────────────────────────────────────────────────────────────────
let _nickAuto = false;
function openNicknameModal(isFirstTime) {
  const modal = document.getElementById('nicknameModal');
  if (!modal) return;
  _nickAuto = !!isFirstTime;
  document.getElementById('nicknameError').textContent = '';
  const input = document.getElementById('nicknameInput');
  input.value = (dashboardData && dashboardData.nickname) || '';      // 지금 닉네임을 미리 채움
  modal.classList.remove('hidden');
  setTimeout(() => { input.focus(); input.select(); }, 80);
}
function closeNicknameModal() {
  const modal = document.getElementById('nicknameModal');
  if (modal) modal.classList.add('hidden');
}
// 자동으로 뜬 안내를 닫으면 7일 동안 다시 띄우지 않는다
const _NICK_SNOOZE_KEY = 'nickPromptSnoozedAt';
function _nickPromptSnoozed() {
  try { const t = Number(localStorage.getItem(_NICK_SNOOZE_KEY) || 0); return t > 0 && (Date.now() - t) < 7 * 24 * 3600 * 1000; }
  catch { return false; }
}
function cancelNicknameModal() {
  if (_nickAuto) { try { localStorage.setItem(_NICK_SNOOZE_KEY, String(Date.now())); } catch {} }
  closeNicknameModal();
}
const snoozeNicknamePrompt = cancelNicknameModal;   // (예전 이름 호환)

async function submitNickname() {
  const input = document.getElementById('nicknameInput');
  const err = document.getElementById('nicknameError');
  const nick = (input.value || '').trim();
  err.textContent = '';
  if (!nick) { err.textContent = '닉네임을 입력해주세요'; return; }
  try {
    const r = await fetch('/api/me/nickname', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nickname: nick }),
    });
    const data = await r.json();
    if (!r.ok) { err.textContent = data.error || '저장 실패'; return; }
    closeNicknameModal();
    if (dashboardData) dashboardData.nickname = data.nickname;
    document.querySelectorAll('.profile-name, .pd-name').forEach((el) => { el.textContent = data.nickname; });
    toast(`닉네임을 '${data.nickname}'(으)로 저장했어요`);
  } catch (e) { err.textContent = '네트워크 오류'; }
}


// ── 목표가 · 손절가 자동 청산 예약 ──────────────────────────────────────────
function _exitValues(prefix) {
  const num = (id) => { const v = document.getElementById(id).value.trim(); return v === '' ? null : Number(v); };
  return prefix === 'buy' ? { target: num('buyTarget'), stop: num('buyStop') } : { target: num('exitTarget'), stop: num('exitStop') };
}

// 기준가(price) 대비 예상 수익률·손실률·손익비. avg 가 있으면 매입가 기준 수익률도
function _exitCalc(prefix, price, cur, avg) {
  const { target, stop } = _exitValues(prefix);
  const el = document.getElementById(prefix === 'buy' ? 'buyExitCalc' : 'exitCalc');
  const out = { target, stop, error: '' };
  if (target != null && (!isFinite(target) || target <= 0)) out.error = '목표가는 0보다 큰 숫자로 입력해 주세요';
  else if (stop != null && (!isFinite(stop) || stop <= 0)) out.error = '손절가는 0보다 큰 숫자로 입력해 주세요';
  else if (price > 0 && target != null && !(target > price)) out.error = `목표가는 현재가(${fmtNative(price, cur)})보다 높아야 합니다`;
  else if (price > 0 && stop != null && !(stop < price)) out.error = `손절가는 현재가(${fmtNative(price, cur)})보다 낮아야 합니다`;
  if (!el) return out;
  if (target == null && stop == null) { el.innerHTML = '<span class="ab-muted">비워 두면 예약하지 않아요.</span>'; return out; }
  if (!(price > 0)) { el.innerHTML = ''; return out; }
  const up = target != null ? (target / price - 1) * 100 : null;
  const dn = stop != null ? (stop / price - 1) * 100 : null;
  const rr = up != null && dn != null && dn < 0 && up > 0 ? up / -dn : null;
  const base = avg ? (v) => ` <span class="ab-muted">(매입가 대비 ${fmtPct((v / avg - 1) * 100)})</span>` : () => '';
  el.innerHTML = `
    <div><span>예상 수익률</span><b class="${up > 0 ? 'up' : ''}">${up == null ? '—' : fmtPct(up)}</b>${target != null && avg ? base(target) : ''}</div>
    <div><span>예상 손실률</span><b class="${dn < 0 ? 'down' : ''}">${dn == null ? '—' : fmtPct(dn)}</b>${stop != null && avg ? base(stop) : ''}</div>
    <div><span>손익비</span><b>${rr == null ? '—' : '1 : ' + rr.toFixed(2)}</b></div>`;
  return out;
}

function _exitBadge(p) {
  if (p.target_price == null && p.stop_loss_price == null) return '';
  const parts = [];
  if (p.target_price != null) parts.push(`<span class="xb-t">익절 ${fmtNative(p.target_price, p.currency)}</span>`);
  if (p.stop_loss_price != null) parts.push(`<span class="xb-s">손절 ${fmtNative(p.stop_loss_price, p.currency)}</span>`);
  return `<span class="exit-badge" title="도달하면 전량 자동 매도">${parts.join('<i aria-hidden="true">·</i>')}</span>`;
}

// 손절 ── 현재가 ── 익절 위치 바 (둘 다 예약된 종목만). 매입가는 은은한 눈금. 종목 통화 기준
function _exitBar(p) {
  const lo = p.stop_loss_price, hi = p.target_price, cur = p.current_price;
  if (lo == null || hi == null || !(hi > lo) || cur == null) return '';
  const pos = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
  const c = pos(cur), b = pos(p.purchase_price);
  const toT = (hi / cur - 1) * 100, toS = (lo / cur - 1) * 100;
  const zone = c >= 66 ? 'near-t' : c <= 33 ? 'near-s' : 'mid';
  return `<div class="xbar ${zone}" role="img" aria-label="현재가 ${fmtNative(cur, p.currency)}: 손절 ${fmtNative(lo, p.currency)}까지 ${toS.toFixed(1)}%, 익절 ${fmtNative(hi, p.currency)}까지 +${toT.toFixed(1)}%">
      <span class="xbar-end s">손절 ${fmtNative(lo, p.currency)}</span>
      <span class="xbar-track" aria-hidden="true">
        <span class="xbar-fill" style="width:${c.toFixed(1)}%"></span>
        ${p.purchase_price > lo && p.purchase_price < hi ? `<span class="xbar-buy" style="left:${b.toFixed(1)}%" title="매입 ${fmtNative(p.purchase_price, p.currency)}"></span>` : ''}
        <span class="xbar-dot" style="left:${c.toFixed(1)}%"></span>
      </span>
      <span class="xbar-end t">익절 ${fmtNative(hi, p.currency)}</span>
    </div>`;
}

let _exitPos = null;
function openExitModal(p) {
  _exitPos = p;
  document.getElementById('exitName').textContent = p.name;
  document.getElementById('exitTicker').textContent = p.ticker;
  document.getElementById('exitCurrency').value = p.currency;
  document.getElementById('exitRef').innerHTML =
    `현재가 <b>${fmtNative(p.current_price, p.currency)}</b> · 매입가 ${fmtNative(p.purchase_price, p.currency)} · ${p.quantity}주`;
  const step = p.currency === 'KRW' ? '1' : '0.01';
  const t = document.getElementById('exitTarget'), s = document.getElementById('exitStop');
  t.step = s.step = step;
  t.value = p.target_price != null ? p.target_price : '';
  s.value = p.stop_loss_price != null ? p.stop_loss_price : '';
  document.getElementById('exitClear').disabled = p.target_price == null && p.stop_loss_price == null;
  document.getElementById('exitError').textContent = '';
  updateExitCalc();
  document.getElementById('exitModal').classList.remove('hidden');
  setTimeout(() => t.focus(), 50);
}
function closeExitModal() { document.getElementById('exitModal').classList.add('hidden'); }

function updateExitCalc() {
  if (!_exitPos) return;
  const ex = _exitCalc('exit', _exitPos.current_price, _exitPos.currency, _exitPos.purchase_price);
  document.getElementById('exitError').textContent = ex.error;
  document.getElementById('exitSave').disabled = !!ex.error;
  return ex;
}

async function _patchTargets(body, okMsg) {
  const btn = document.getElementById('exitSave');
  btn.disabled = true; btn.textContent = '저장 중…';
  try {
    const r = await fetch(`/api/trading/holdings/${encodeURIComponent(_exitPos.ticker)}/targets`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) { document.getElementById('exitError').textContent = d.error || '저장하지 못했습니다'; return; }
    closeExitModal();
    toast(okMsg);
    await loadDashboard();
  } catch (e) {
    document.getElementById('exitError').textContent = '네트워크 오류: ' + (e.message || e);
  } finally {
    btn.textContent = '저장'; if (_exitPos) updateExitCalc();
  }
}
function saveExitTargets() {
  const ex = updateExitCalc();
  if (!ex || ex.error) return;
  const { target, stop } = _exitValues('exit');
  const msg = target == null && stop == null ? `${_exitPos.name} 자동 청산 예약을 해제했어요` : `${_exitPos.name} 자동 청산 예약을 저장했어요`;
  _patchTargets({ target_price: target, stop_loss_price: stop }, msg);
}
function clearExitTargets() {
  _patchTargets({ target_price: null, stop_loss_price: null }, `${_exitPos.name} 자동 청산 예약을 해제했어요`);
}

// ── 다른 페이지에서 원클릭: /trading?buy=AAPL&target=380&stop=320&memo=... ──────────
async function openBuyFromUrl() {
  const q = new URLSearchParams(location.search);
  const sym = (q.get('buy') || '').trim().toUpperCase();
  if (!sym) return;
  const num = (k) => { const v = Number(q.get(k)); return q.get(k) && isFinite(v) && v > 0 ? v : null; };
  const prefill = { target: num('target'), stop: num('stop'), memo: (q.get('memo') || '').slice(0, 300) };
  history.replaceState(null, '', location.pathname);            // 새로고침해도 다시 열리지 않게 주소 정리
  if (!/^[A-Z0-9.\-^=]{1,15}$/.test(sym)) { toast('종목 코드를 알 수 없어요', 'err'); return; }
  _qbLoading(true);
  try {
    let name = sym;
    try {
      const items = (await (await fetch(`/api/search?q=${encodeURIComponent(sym)}`)).json()) || [];
      const hit = items.find((it) => (it.symbol || '').toUpperCase() === sym);
      if (hit) name = hit.name;
    } catch (e) {}
    const quote = await fetchQuote(sym);
    openBuyModalForHolding(sym, name, isKR(sym) ? 'KRW' : 'USD', quote.price || 0, quote.market_open, prefill);
  } finally {
    _qbLoading(false);
  }
}
