// 모의투자 페이지 (/trading)
console.log('[trading] loaded');

let currentUser = null;
let dashboardData = null;
let qbACItems = [];

const fmtKRW = (v) => v == null ? '—' : Math.round(v).toLocaleString('ko-KR') + '원';
const fmtNative = (v, cur) => {
  if (v == null) return '—';
  if (cur === 'KRW') return Math.round(v).toLocaleString('ko-KR') + '원';
  return '$' + Number(v).toFixed(2);
};
const fmtPct = (v) => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
const _ko = (t) => /[가-힯]/.test(t);

// ── 초기화 ──────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await checkAuthAndLoad();
  setupQuickBuyAC();
  setupTradeInputs();
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
    // 인라인 style 로 강제 크기 (CSS 캐시 등 우회)
    const imgStyle = 'width:28px;height:28px;border-radius:50%;object-fit:cover;background:var(--border);flex-shrink:0;';
    const img = u.profile_image
      ? `<img class="profile-img" src="${u.profile_image}" style="${imgStyle}" />`
      : `<div class="profile-img" style="${imgStyle};display:flex;align-items:center;justify-content:center;font-size:14px">👤</div>`;
    document.getElementById('authArea').innerHTML = `
      <div class="profile-area">
        ${img}
        <span class="profile-name">${u.name || '사용자'}</span>
      </div>
      <a href="/auth/logout" class="logout-btn">로그아웃</a>`;
    document.getElementById('loginRequired').classList.add('hidden');
    document.getElementById('tradingDashboard').classList.remove('hidden');
    await loadDashboard();
  } catch (e) {
    console.warn('auth check failed', e);
    document.getElementById('loginRequired').classList.remove('hidden');
  }
}

// ── 대시보드 데이터 로드 ────────────────────────────────────
async function loadDashboard() {
  try {
    const r = await fetch('/api/trading/dashboard');
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'load failed');
    dashboardData = data;
    renderSummary(data);
    renderPositions(data.positions || []);
    await loadTransactions();
    // 차트는 dashboard 호출 후 (오늘 스냅샷이 저장된 다음) 로드
    loadAssetHistory(_ahCurrentDays || 30);

    // 새 배지 획득 알림
    if (data.newly_earned_badges && data.newly_earned_badges.length) {
      _showBadgeToasts(data.newly_earned_badges);
    }
    // 배지 목록 새로고침
    loadBadges();
    // 닉네임 미설정 → 자동 안내 모달
    if (!data.nickname && !_nicknamePromptShown) {
      _nicknamePromptShown = true;
      setTimeout(() => openNicknameModal(true), 800);
    }
  } catch (e) {
    console.error('loadDashboard', e);
  }
}

// ─── 자산 변화 차트 ─────────────────────────────────────────
let _ahChart = null;
let _ahSeriesMe = null, _ahSeriesKospi = null, _ahSeriesSp = null;
let _ahCurrentDays = 30;

async function loadAssetHistory(days, btn) {
  _ahCurrentDays = days;
  // 버튼 active 토글
  document.querySelectorAll('.ah-period-btn').forEach(b => b.classList.remove('active'));
  if (btn) {
    btn.classList.add('active');
  } else {
    const t = document.querySelector(`.ah-period-btn[data-days="${days}"]`);
    if (t) t.classList.add('active');
  }
  try {
    const r = await fetch(`/api/trading/history?days=${days}`);
    if (!r.ok) throw new Error('history load failed');
    const data = await r.json();
    renderAssetHistoryChart(data);
  } catch (e) {
    console.error('loadAssetHistory', e);
  }
}

function _ahFmtPct(p) {
  if (p == null || isNaN(p)) return '—';
  const sign = p > 0 ? '+' : '';
  return sign + p.toFixed(2) + '%';
}

function renderAssetHistoryChart(data) {
  const wrap = document.getElementById('assetHistoryChart');
  const empty = document.getElementById('ahEmpty');
  const summary = document.getElementById('ahSummary');
  if (!wrap || typeof LightweightCharts === 'undefined') return;

  const snaps = data.snapshots || [];
  const bm = data.benchmarks || {};
  const kospi = bm.kospi || [];
  const sp500 = bm.sp500 || [];

  // 데이터가 1개 이하면 차트 의미 없음
  if (snaps.length < 2 && kospi.length < 2 && sp500.length < 2) {
    wrap.style.display = 'none';
    if (empty) empty.classList.remove('hidden');
    if (summary) summary.textContent = '';
    return;
  }
  wrap.style.display = '';
  if (empty) empty.classList.add('hidden');

  // 차트 인스턴스 lazy 생성
  if (!_ahChart) {
    _ahChart = LightweightCharts.createChart(wrap, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#9aa4b2',
        fontSize: 11,
      },
      rightPriceScale: {
        borderColor: '#2a3340',
      },
      timeScale: {
        borderColor: '#2a3340',
        timeVisible: false,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
      },
      localization: {
        priceFormatter: (p) => (p >= 0 ? '+' : '') + p.toFixed(2) + '%',
      },
    });
    _ahSeriesMe    = _ahChart.addLineSeries({ color: '#22c55e', lineWidth: 2, title: '내 자산',
                                              priceFormat: { type: 'custom', formatter: (p) => (p>=0?'+':'')+p.toFixed(2)+'%' } });
    _ahSeriesKospi = _ahChart.addLineSeries({ color: '#3b82f6', lineWidth: 1, title: 'KOSPI',
                                              priceFormat: { type: 'custom', formatter: (p) => (p>=0?'+':'')+p.toFixed(2)+'%' } });
    _ahSeriesSp    = _ahChart.addLineSeries({ color: '#f59e0b', lineWidth: 1, title: 'S&P500',
                                              priceFormat: { type: 'custom', formatter: (p) => (p>=0?'+':'')+p.toFixed(2)+'%' } });

    // baseline 0% 가로선 추가
    _ahSeriesMe.createPriceLine({
      price: 0, color: '#5a6675', lineWidth: 1, lineStyle: 2, axisLabelVisible: false,
    });
  }

  // 시리즈 데이터 셋
  const toSeries = (arr, key) => arr
    .filter(p => p && p.date && p[key] != null)
    .map(p => ({ time: p.date, value: p[key] }));

  _ahSeriesMe.setData(toSeries(snaps, 'return_pct'));
  _ahSeriesKospi.setData(toSeries(kospi, 'return_pct'));
  _ahSeriesSp.setData(toSeries(sp500, 'return_pct'));

  _ahChart.timeScale().fitContent();

  // 헤더 summary: 최신 값 비교
  const last = (arr) => arr.length ? arr[arr.length - 1].return_pct : null;
  const me = last(snaps), ks = last(kospi), sp = last(sp500);
  if (summary) {
    const parts = [];
    if (me != null) parts.push(`<span style="color:#22c55e">내 ${_ahFmtPct(me)}</span>`);
    if (ks != null) parts.push(`<span style="color:#3b82f6">KOSPI ${_ahFmtPct(ks)}</span>`);
    if (sp != null) parts.push(`<span style="color:#f59e0b">S&P ${_ahFmtPct(sp)}</span>`);
    summary.innerHTML = parts.join(' &nbsp;·&nbsp; ');
  }
}

// 자산 새로고침 — 시세/평가금액 다시 fetch
async function refreshDashboard(btn) {
  if (btn) {
    btn.classList.add('spinning');
    btn.disabled = true;
  }
  try {
    await loadDashboard();
  } finally {
    if (btn) {
      // 너무 빠르면 깜빡임만 되니까 최소 400ms 보여주기
      setTimeout(() => {
        btn.classList.remove('spinning');
        btn.disabled = false;
      }, 400);
    }
  }
}

function renderSummary(d) {
  const ret = d.total_return_pct;
  const retCls = ret > 0 ? 'up' : ret < 0 ? 'down' : 'flat';
  const retIcon = ret > 0 ? '▲' : ret < 0 ? '▼' : '·';

  document.getElementById('psTotal').textContent  = fmtKRW(d.total_assets_krw);
  document.getElementById('psReturn').innerHTML   = `
    <span class="ps-return-pct ${retCls}">${retIcon} ${Math.abs(ret).toFixed(2)}%</span>
    <span class="ps-return-amt">${ret >= 0 ? '+' : '-'}${fmtKRW(Math.abs(d.total_assets_krw - d.initial_capital_krw))}</span>
    <span class="ps-return-base">초기 ${fmtKRW(d.initial_capital_krw)}</span>
  `;
  document.getElementById('psCash').textContent       = fmtKRW(d.cash_krw);
  document.getElementById('psPositions').textContent  = fmtKRW(d.positions_value_krw);

  const unr = d.unrealized_pnl_krw;
  const unrEl = document.getElementById('psUnrealized');
  unrEl.textContent = (unr >= 0 ? '+' : '') + fmtKRW(unr);
  unrEl.className = 'ps-val ' + (unr > 0 ? 'up' : unr < 0 ? 'down' : '');

  const rea = d.realized_pnl_krw;
  const reaEl = document.getElementById('psRealized');
  reaEl.textContent = (rea >= 0 ? '+' : '') + fmtKRW(rea);
  reaEl.className = 'ps-val ' + (rea > 0 ? 'up' : rea < 0 ? 'down' : '');

  const s = d.stats || {};
  document.getElementById('psStats').innerHTML = `
    <span>📊 총 거래 <strong>${s.total_trades || 0}회</strong></span>
    <span>🎯 매도 승률 <strong>${s.win_rate || 0}%</strong> (${s.wins || 0}/${s.total_sells || 0})</span>
    <span>💱 환율 <strong>${d.exchange_rate}원/$</strong></span>
  `;
}

function renderPositions(positions) {
  const el = document.getElementById('positionsList');
  document.getElementById('holdingCount').textContent = positions.length;
  if (!positions.length) {
    el.innerHTML = '<div class="empty-state">보유 종목이 없습니다. 위에서 매수해보세요!</div>';
    return;
  }
  // 수익률 내림차순
  positions.sort((a, b) => (b.pnl_pct || 0) - (a.pnl_pct || 0));

  el.innerHTML = positions.map((p, idx) => {
    const medals = ['🥇', '🥈', '🥉'];
    const rank = idx < 3 && p.pnl_pct > 0 ? `<div class="trend-rank trend-rank-medal">${medals[idx]}</div>`
                                          : `<div class="trend-rank trend-rank-num">#${idx + 1}</div>`;
    const cls = p.pnl_pct > 0 ? 'up' : p.pnl_pct < 0 ? 'down' : 'flat';
    const icon = p.pnl_pct > 0 ? '▲' : p.pnl_pct < 0 ? '▼' : '·';
    // 뱃지: 방향 우선, 진하기는 절댓값
    let badgeCls;
    if (p.pnl_pct == null)      badgeCls = 'score-flat';
    else if (p.pnl_pct >= 15)   badgeCls = 'score-gain-strong';
    else if (p.pnl_pct > 0)     badgeCls = 'score-gain';
    else if (p.pnl_pct === 0)   badgeCls = 'score-flat';
    else if (p.pnl_pct > -15)   badgeCls = 'score-loss';
    else                        badgeCls = 'score-loss-strong';
    // 카드 좌측 보더/배경용 방향 클래스
    const dirClass =
      p.pnl_pct == null   ? 'pf-dir-flat' :
      p.pnl_pct >  0      ? 'pf-dir-gain' :
      p.pnl_pct === 0     ? 'pf-dir-flat' : 'pf-dir-loss';
    const tierClass = (idx < 3 && p.pnl_pct > 0) ? `trend-tier-${idx + 1}` : '';
    return `
      <div class="pf-card trend-card-v2 ${tierClass} ${dirClass}">
        <div class="trend-card-row">
          ${rank}
          <div class="trend-name-block">
            <div class="pf-stock-name">${p.name}</div>
            <div class="pf-ticker">${p.ticker}</div>
          </div>
          <div class="trend-spark-wrap"></div>
          <div class="trend-price-block">
            <div class="trend-price-val">${fmtNative(p.current_price, p.currency)}</div>
            <div class="trend-chg pf-${cls} pf-amount">${(p.pnl_krw >= 0 ? '+' : '') + fmtKRW(p.pnl_krw)}</div>
          </div>
          <div class="trend-score-v2 ${badgeCls}">
            ${icon} ${Math.abs(p.pnl_pct).toFixed(1)}<span class="score-suffix">%</span>
          </div>
        </div>
        <div class="pf-meta-row">
          <span class="pf-meta-item">📌 매입 ${fmtNative(p.purchase_price, p.currency)}</span>
          <span class="pf-meta-item">📦 ${p.quantity}주</span>
          <button class="trade-action-btn buy" onclick="openBuyModalForHolding('${p.ticker}','${p.name}','${p.currency}',${p.current_price || 0})">💰 추가매수</button>
          <button class="trade-action-btn sell" onclick="openSellModal('${p.ticker}','${p.name}','${p.currency}',${p.current_price || 0},${p.quantity})">💸 매도</button>
        </div>
      </div>`;
  }).join('');
}

async function loadTransactions() {
  try {
    const r = await fetch('/api/trading/transactions?limit=30');
    const txs = await r.json();
    renderTransactions(txs || []);
  } catch (e) { console.warn('loadTransactions', e); }
}

function renderTransactions(txs) {
  const el = document.getElementById('transactionsList');
  if (!txs.length) {
    el.innerHTML = '<div class="empty-state">거래 내역이 없습니다</div>';
    return;
  }
  el.innerHTML = `
    <table class="tx-table">
      <thead>
        <tr>
          <th>일시</th><th>구분</th><th>종목</th><th>가격</th>
          <th>수량</th><th>금액(원)</th><th>손익</th>
        </tr>
      </thead>
      <tbody>
        ${txs.map(t => {
          const d = new Date(t.timestamp);
          const dStr = `${(d.getMonth()+1).toString().padStart(2,'0')}/${d.getDate().toString().padStart(2,'0')} ${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`;
          const isSell = t.type === 'sell';
          const pnl = t.realized_pnl_krw || 0;
          const pnlCls = pnl > 0 ? 'up' : pnl < 0 ? 'down' : '';
          return `
            <tr>
              <td>${dStr}</td>
              <td><span class="tx-type ${t.type}">${isSell ? '매도' : '매수'}</span></td>
              <td>
                <div class="tx-stock">${t.name}</div>
                <div class="tx-ticker">${t.ticker}</div>
              </td>
              <td>${fmtNative(t.price, t.currency)}</td>
              <td>${t.quantity}주</td>
              <td>${fmtKRW(t.amount_krw)}</td>
              <td class="${pnlCls}">${isSell ? ((pnl >= 0 ? '+' : '') + fmtKRW(pnl)) : '—'}</td>
            </tr>`;
        }).join('')}
      </tbody>
    </table>
  `;
}

// ── 빠른 매수 자동완성 ──────────────────────────────────────
function setupQuickBuyAC() {
  const input = document.getElementById('qbTicker');
  const list  = document.getElementById('qbAcList');
  if (!input) return;

  let timer = null;
  let selected = null;

  input.addEventListener('input', () => {
    document.getElementById('qbBadge').textContent = '';
    selected = null;
    const q = input.value.trim();
    if (timer) clearTimeout(timer);
    if (q.length < 1) { list.classList.add('hidden'); return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
        const items = await r.json();
        qbACItems = items || [];
        if (!qbACItems.length) { list.classList.add('hidden'); return; }
        list.innerHTML = qbACItems.slice(0, 8).map((it, i) => `
          <div class="autocomplete-item" onmousedown="selectQbAC(${i})">
            <div class="ac-name">${it.name}</div>
            <div class="ac-symbol">${it.symbol}</div>
          </div>
        `).join('');
        list.classList.remove('hidden');
      } catch (e) { list.classList.add('hidden'); }
    }, 200);
  });
  input.addEventListener('blur', () => setTimeout(() => list.classList.add('hidden'), 200));
}

function selectQbAC(i) {
  const item = qbACItems[i];
  if (!item) return;
  document.getElementById('qbTicker').value = `${item.name} (${item.symbol})`;
  document.getElementById('qbTicker').dataset.symbol = item.symbol;
  document.getElementById('qbTicker').dataset.name   = item.name;
  document.getElementById('qbAcList').classList.add('hidden');
  const cur = /\.(KS|KQ)$/i.test(item.symbol) ? 'KRW' : 'USD';
  document.getElementById('qbBadge').innerHTML =
    `✅ <strong>${item.symbol}</strong> · ${cur === 'KRW' ? '원화(KRW)' : '달러(USD)'}`;
}

async function openQuickBuy() {
  const inp = document.getElementById('qbTicker');
  const sym = inp.dataset.symbol;
  const nm  = inp.dataset.name;
  if (!sym) { alert('종목을 검색해서 선택해주세요'); return; }
  // 현재가 fetch
  const price = await fetchCurrentPrice(sym);
  const cur = /\.(KS|KQ)$/i.test(sym) ? 'KRW' : 'USD';
  openBuyModalForHolding(sym, nm, cur, price || 0);
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

// ── 매수 모달 ───────────────────────────────────────────────
function openBuyModalForHolding(ticker, name, currency, price) {
  document.getElementById('buyTicker').value   = ticker;
  document.getElementById('buyName').value     = name;
  document.getElementById('buyCurrency').value = currency;
  document.getElementById('buyCurrencyLabel').textContent = `(${currency})`;
  document.getElementById('buyModalDesc').textContent = `${name} (${ticker})`;
  document.getElementById('buyPrice').value = currency === 'KRW' ? Math.round(price) : (price ? price.toFixed(2) : '');
  document.getElementById('buyQty').value = '';
  updateBuySummary();
  document.getElementById('tradeBuyModal').classList.remove('hidden');
  setTimeout(() => document.getElementById('buyQty').focus(), 50);
}

function closeBuyModal(event) {
  if (!event || event.target === document.getElementById('tradeBuyModal')) {
    document.getElementById('tradeBuyModal').classList.add('hidden');
  }
}

function updateBuySummary() {
  const cur = document.getElementById('buyCurrency').value || 'USD';
  const price = parseFloat(document.getElementById('buyPrice').value) || 0;
  const qty   = parseFloat(document.getElementById('buyQty').value) || 0;
  const amount = price * qty;
  const fx = dashboardData ? dashboardData.exchange_rate : 1380;
  const amount_krw = cur === 'KRW' ? amount : amount * fx;
  const fee = Math.round(amount_krw * 0.001);
  const total = amount_krw + fee;
  const cash = dashboardData ? dashboardData.cash_krw : 0;
  const remain = cash - total;

  document.getElementById('buySummary').innerHTML = `
    <div class="ts-row"><span>매수 금액 (${cur})</span><span>${cur === 'KRW' ? fmtKRW(amount) : '$' + amount.toFixed(2)}</span></div>
    ${cur === 'USD' ? `<div class="ts-row"><span>KRW 환산 (₩${fx})</span><span>${fmtKRW(amount_krw)}</span></div>` : ''}
    <div class="ts-row"><span>수수료 (0.1%)</span><span>${fmtKRW(fee)}</span></div>
    <div class="ts-row total"><span>총 차감액</span><span>${fmtKRW(total)}</span></div>
    <div class="ts-row ${remain < 0 ? 'warn' : ''}"><span>매수 후 현금</span><span>${fmtKRW(remain)}</span></div>
  `;
}

async function submitBuy() {
  const ticker = document.getElementById('buyTicker').value;
  const name   = document.getElementById('buyName').value;
  const price  = parseFloat(document.getElementById('buyPrice').value);
  const qty    = parseFloat(document.getElementById('buyQty').value);
  if (!price || !qty || price <= 0 || qty <= 0) { alert('가격/수량을 입력해주세요'); return; }
  try {
    const r = await fetch('/api/trading/buy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, name, price, quantity: qty }),
    });
    const d = await r.json();
    if (!r.ok) { alert(d.error || '매수 실패'); return; }
    closeBuyModal();
    await loadDashboard();
    alert(`✅ ${ticker} ${qty}주 매수 완료`);
  } catch (e) { alert('네트워크 오류: ' + (e.message || e)); }
}

// ── 매도 모달 ───────────────────────────────────────────────
let _sellMaxQty = 0;
function openSellModal(ticker, name, currency, price, maxQty) {
  document.getElementById('sellTicker').value   = ticker;
  document.getElementById('sellCurrency').value = currency;
  document.getElementById('sellCurrencyLabel').textContent = `(${currency})`;
  document.getElementById('sellModalDesc').textContent = `${name} (${ticker})`;
  document.getElementById('sellPrice').value = currency === 'KRW' ? Math.round(price) : (price ? price.toFixed(2) : '');
  document.getElementById('sellQty').value = '';
  document.getElementById('sellMaxQty').textContent = maxQty;
  _sellMaxQty = maxQty;
  updateSellSummary();
  document.getElementById('tradeSellModal').classList.remove('hidden');
  setTimeout(() => document.getElementById('sellQty').focus(), 50);
}

function closeSellModal(event) {
  if (!event || event.target === document.getElementById('tradeSellModal')) {
    document.getElementById('tradeSellModal').classList.add('hidden');
  }
}

function setSellMax() {
  document.getElementById('sellQty').value = Math.floor(_sellMaxQty);
  updateSellSummary();
}

function updateSellSummary() {
  const cur = document.getElementById('sellCurrency').value || 'USD';
  const price = parseFloat(document.getElementById('sellPrice').value) || 0;
  const qty   = parseFloat(document.getElementById('sellQty').value) || 0;
  const amount = price * qty;
  const fx = dashboardData ? dashboardData.exchange_rate : 1380;
  const amount_krw = cur === 'KRW' ? amount : amount * fx;
  const fee = Math.round(amount_krw * 0.001);
  const proceeds = amount_krw - fee;

  // 손익 추정 (현재 보유 종목 기준)
  const ticker = document.getElementById('sellTicker').value;
  let pnl_estimate = null;
  if (dashboardData && ticker) {
    const pos = (dashboardData.positions || []).find(p => p.ticker === ticker);
    if (pos) {
      const cost_native = pos.purchase_price * qty;
      const cost_krw = cur === 'KRW' ? cost_native : cost_native * fx;
      pnl_estimate = amount_krw - cost_krw;
    }
  }

  document.getElementById('sellSummary').innerHTML = `
    <div class="ts-row"><span>매도 금액 (${cur})</span><span>${cur === 'KRW' ? fmtKRW(amount) : '$' + amount.toFixed(2)}</span></div>
    ${cur === 'USD' ? `<div class="ts-row"><span>KRW 환산 (₩${fx})</span><span>${fmtKRW(amount_krw)}</span></div>` : ''}
    <div class="ts-row"><span>수수료 (0.1%)</span><span>${fmtKRW(fee)}</span></div>
    <div class="ts-row total"><span>받을 금액</span><span>${fmtKRW(proceeds)}</span></div>
    ${pnl_estimate != null ? `<div class="ts-row ${pnl_estimate >= 0 ? 'up' : 'warn'}"><span>예상 손익</span><span>${pnl_estimate >= 0 ? '+' : ''}${fmtKRW(pnl_estimate)}</span></div>` : ''}
  `;
}

async function submitSell() {
  const ticker = document.getElementById('sellTicker').value;
  const price  = parseFloat(document.getElementById('sellPrice').value);
  const qty    = parseFloat(document.getElementById('sellQty').value);
  if (!price || !qty || price <= 0 || qty <= 0) { alert('가격/수량을 입력해주세요'); return; }
  if (qty > _sellMaxQty) { alert(`보유 수량(${_sellMaxQty})을 초과할 수 없습니다`); return; }
  try {
    const r = await fetch('/api/trading/sell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, price, quantity: qty }),
    });
    const d = await r.json();
    if (!r.ok) { alert(d.error || '매도 실패'); return; }
    closeSellModal();
    await loadDashboard();
    const pnl = d.realized_pnl_krw || 0;
    alert(`✅ ${ticker} ${qty}주 매도 완료${pnl ? `\n실현 손익: ${pnl >= 0 ? '+' : ''}${fmtKRW(pnl)}` : ''}`);
  } catch (e) { alert('네트워크 오류: ' + (e.message || e)); }
}

// ── 초기화 ─────────────────────────────────────────────────
async function resetTrading() {
  if (!confirm('정말 초기화하시겠습니까?\n모든 보유 종목과 거래 내역이 삭제되고 현금이 1억원으로 돌아갑니다.')) return;
  try {
    const r = await fetch('/api/trading/reset', { method: 'POST' });
    if (!r.ok) { alert('초기화 실패'); return; }
    await loadDashboard();
    alert('✅ 초기화 완료 (현금 1억원)');
  } catch (e) { alert('네트워크 오류: ' + (e.message || e)); }
}

// ── 매매 모달 입력 검증 ────────────────────────────────────
function _stepTradeNum(modalType, inputId, direction) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const curEl = document.getElementById(modalType === 'buy' ? 'buyCurrency' : 'sellCurrency');
  const isKRW = (curEl.value || 'USD') === 'KRW';
  let step = 1;
  if (inputId.includes('Price')) step = isKRW ? 100 : 1;

  let cur = parseFloat(input.value); if (isNaN(cur)) cur = 0;
  let next = cur + direction * step;
  if (next < 0) next = 0;
  if (inputId.includes('Qty') || (inputId.includes('Price') && isKRW)) next = Math.round(next);
  else next = Math.round(next * 100) / 100;
  input.value = next;

  if (modalType === 'buy')  updateBuySummary();
  else                       updateSellSummary();
}

function setupTradeInputs() {
  ['buyPrice', 'buyQty'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', updateBuySummary);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp')   { e.preventDefault(); _stepTradeNum('buy', id, 1); }
      if (e.key === 'ArrowDown') { e.preventDefault(); _stepTradeNum('buy', id, -1); }
    });
  });
  ['sellPrice', 'sellQty'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', updateSellSummary);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp')   { e.preventDefault(); _stepTradeNum('sell', id, 1); }
      if (e.key === 'ArrowDown') { e.preventDefault(); _stepTradeNum('sell', id, -1); }
    });
  });
}

// ─── 배지 시스템 ─────────────────────────────────────
let _nicknamePromptShown = false;
let _badgesCache = null;

async function loadBadges() {
  try {
    const r = await fetch('/api/me/badges');
    if (!r.ok) return;
    const data = await r.json();
    _badgesCache = data;
    renderBadges(data);
  } catch (e) {
    console.warn('badges load failed', e);
  }
}

function renderBadges(data) {
  const wrap = document.getElementById('badgesList');
  const prog = document.getElementById('badgeProgress');
  if (!wrap) return;
  if (prog) prog.textContent = `${data.earned_count} / ${data.total_count}`;

  // 카테고리별 그룹
  const byCat = {};
  for (const b of data.badges) {
    if (!byCat[b.category]) byCat[b.category] = [];
    byCat[b.category].push(b);
  }
  const order = ['활동', '수익', '트레이딩', '포트폴리오', '글로벌', '성과', '꾸준함'];
  const sortedCats = Object.keys(byCat).sort((a, b) => {
    const ai = order.indexOf(a); const bi = order.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });

  wrap.innerHTML = sortedCats.map(cat => `
    <div class="badge-cat">
      <div class="badge-cat-title">${cat}</div>
      <div class="badge-cat-grid">
        ${byCat[cat].map(b => _badgeHTML(b)).join('')}
      </div>
    </div>
  `).join('');
}

function _badgeHTML(b) {
  const cls = b.earned ? `badge-card badge-earned tier-${b.tier}` : 'badge-card badge-locked';
  const tierLabel = { bronze:'브론즈', silver:'실버', gold:'골드', diamond:'다이아', legend:'전설' }[b.tier] || '';
  const icon = b.earned ? b.icon : '🔒';
  return `
    <div class="${cls}" title="${b.desc.replace(/"/g, '&quot;')}" onclick="_toggleBadgeDetail(this)">
      <div class="badge-icon">${icon}</div>
      <div class="badge-name">${b.name}</div>
      <div class="badge-tier">${tierLabel}</div>
      <div class="badge-desc">${b.desc}</div>
    </div>`;
}

function _toggleBadgeDetail(el) {
  el.classList.toggle('badge-show-detail');
}

function _showBadgeToasts(keys) {
  if (!_badgesCache || !_badgesCache.badges) {
    // 아직 캐시 없으면 잠깐 후 다시 시도
    setTimeout(() => _showBadgeToasts(keys), 1200);
    return;
  }
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
  t.classList.remove('hidden');
  t.classList.add('show');
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.classList.add('hidden'), 350);
  }, 3000);
}

// ─── 닉네임 모달 ─────────────────────────────────────
function openNicknameModal(isFirstTime) {
  const modal = document.getElementById('nicknameModal');
  if (!modal) return;
  document.getElementById('nicknameError').textContent = '';
  const input = document.getElementById('nicknameInput');
  if (dashboardData && dashboardData.nickname) input.value = dashboardData.nickname;
  else input.value = '';
  modal.classList.remove('hidden');
  setTimeout(() => input.focus(), 80);
}

function closeNicknameModal() {
  const modal = document.getElementById('nicknameModal');
  if (modal) modal.classList.add('hidden');
}

async function submitNickname() {
  const input = document.getElementById('nicknameInput');
  const err = document.getElementById('nicknameError');
  const nick = (input.value || '').trim();
  err.textContent = '';
  if (!nick) {
    err.textContent = '닉네임을 입력해주세요';
    return;
  }
  try {
    const r = await fetch('/api/me/nickname', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nickname: nick }),
    });
    const data = await r.json();
    if (!r.ok) {
      err.textContent = data.error || '저장 실패';
      return;
    }
    closeNicknameModal();
    if (dashboardData) dashboardData.nickname = data.nickname;
    // 헤더 표시 갱신
    const nameEl = document.querySelector('.profile-name');
    if (nameEl) nameEl.textContent = data.nickname;
  } catch (e) {
    err.textContent = '네트워크 오류';
  }
}
