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
  } catch (e) {
    console.error('loadDashboard', e);
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
    const badgeCls = p.pnl_pct >= 30 ? 'score-high' : p.pnl_pct >= 5 ? 'score-mid' : p.pnl_pct >= -5 ? 'score-low' : 'score-neg';
    const tierClass = (idx < 3 && p.pnl_pct > 0) ? `trend-tier-${idx + 1}` : '';
    return `
      <div class="pf-card trend-card-v2 ${tierClass}">
        <div class="trend-card-row">
          ${rank}
          <div class="trend-name-block">
            <div class="pf-stock-name">${p.name}</div>
            <div class="pf-ticker">${p.ticker}</div>
          </div>
          <div class="trend-spark-wrap"></div>
          <div class="trend-price-block">
            <div class="trend-price-val">${fmtNative(p.current_price, p.currency)}</div>
            <div class="trend-chg pf-${cls}">${(p.pnl_krw >= 0 ? '+' : '') + fmtKRW(p.pnl_krw)}</div>
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
