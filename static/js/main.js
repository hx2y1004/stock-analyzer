let chartData = null;
let chartType = 'candlestick';
let indicators = { ma: true, bb: true, ichimoku: true };
let currentInterval = '1d';
let currentUser = null;
let currentAnalysisTicker = null;
let currentPositionId = null;
let currentPositionData = null;

// ── 앱 초기화 ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  checkAuth();
});

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
  const img = currentUser.profile_image
    ? `<img class="profile-img" src="${currentUser.profile_image}" />`
    : `<div class="profile-img" style="background:var(--bg3);display:flex;align-items:center;justify-content:center;font-size:14px">👤</div>`;
  area.innerHTML = `
    <div class="profile-area">
      ${img}
      <span class="profile-name">${currentUser.name || '사용자'}</span>
    </div>
    <a href="/auth/logout" class="logout-btn">로그아웃</a>`;
}

// ── 포트폴리오 ───────────────────────────────────────────────────────────────
async function loadPortfolio() {
  document.getElementById('portfolioSection').classList.remove('hidden');
  const cards = document.getElementById('portfolioCards');
  cards.innerHTML = '<div class="pf-loading">불러오는 중...</div>';
  try {
    const res  = await fetch('/api/portfolio');
    const data = await res.json();
    renderPortfolioCards(data);
  } catch (e) {
    cards.innerHTML = '<div class="pf-loading">불러오기 실패</div>';
  }
}

function renderPortfolioCards(holdings) {
  const cards = document.getElementById('portfolioCards');
  if (!holdings.length) {
    cards.innerHTML = `<div class="pf-empty">보유 종목이 없습니다. 종목을 추가해보세요!</div>`;
    return;
  }
  cards.innerHTML = holdings.map(h => {
    const hasPrice  = h.current_price != null;
    const retPct    = h.return_pct;
    const retAmt    = h.return_amount;
    const retClass  = retPct == null ? 'neutral' : retPct > 0 ? 'up' : retPct < 0 ? 'down' : 'neutral';
    const retIcon   = retPct > 0 ? '▲' : retPct < 0 ? '▼' : '';
    const cur       = h.currency || 'USD';
    const fmtPrice  = (v) => v == null ? '—' : (cur === 'USD' ? '$' : '') + v.toLocaleString();

    return `
    <div class="pf-card" onclick="quickSearch('${h.ticker}')">
      <div class="pf-card-top">
        <div>
          <div class="pf-stock-name">${h.name}</div>
          <div class="pf-ticker">${h.ticker}</div>
        </div>
        <button class="pf-delete-btn" onclick="deleteHolding(event, ${h.id})" title="삭제">✕</button>
      </div>
      <div class="pf-prices">
        <div class="pf-price-item">
          <div>매입가</div>
          <div class="pf-price-val">${fmtPrice(h.purchase_price)}</div>
        </div>
        <div class="pf-price-item" style="text-align:right">
          <div>현재가</div>
          <div class="pf-price-val">${hasPrice ? fmtPrice(h.current_price) : '<span class="pf-loading-price">로딩중</span>'}</div>
        </div>
      </div>
      <div class="pf-return ${retClass}">
        <div>
          <div class="pf-return-pct">${retPct != null ? `${retIcon} ${Math.abs(retPct).toFixed(2)}%` : '—'}</div>
          <div class="pf-qty">${h.quantity}주</div>
        </div>
        <div class="pf-return-amt">${retAmt != null ? (retAmt >= 0 ? '+' : '') + fmtPrice(Math.round(retAmt)) : ''}</div>
      </div>
    </div>`;
  }).join('');
}

async function deleteHolding(event, hid) {
  event.stopPropagation();
  if (!confirm('이 종목을 포트폴리오에서 삭제할까요?')) return;
  await fetch(`/api/portfolio/${hid}`, { method: 'DELETE' });
  loadPortfolio();
  currentPositionId   = null;
  currentPositionData = null;
  renderTradeActions(null);
  renderPositionCard(null, null);
}

// ── 종목 추가 모달 ─────────────────────────────────────────────────────────
function openAddModal(ticker, name, currency) {
  if (!currentUser) { openLoginModal(); return; }
  document.getElementById('addTicker').value  = ticker  || '';
  document.getElementById('addName').value    = name    || '';
  document.getElementById('addCurrencyLabel').textContent = currency ? `(${currency})` : '';
  document.getElementById('addModalDesc').textContent = ticker
    ? `${name || ticker} 을(를) 포트폴리오에 추가합니다`
    : '검색 후 분석창에서 종목을 추가할 수 있어요';
  document.getElementById('addPrice').value = '';
  document.getElementById('addQty').value   = '';
  document.getElementById('addModal').classList.remove('hidden');
}
function closeAddModal(event) {
  if (!event || event.target === document.getElementById('addModal'))
    document.getElementById('addModal').classList.add('hidden');
}
async function submitAddHolding() {
  const ticker = document.getElementById('addTicker').value;
  const name   = document.getElementById('addName').value;
  const price  = parseFloat(document.getElementById('addPrice').value);
  const qty    = parseFloat(document.getElementById('addQty').value);
  if (!ticker || !price || !qty) { alert('모든 항목을 입력해주세요'); return; }
  const res = await fetch('/api/portfolio', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker, name, quantity: qty, purchase_price: price }),
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
function renderPositionCard(position, stock) {
  const el = document.getElementById('positionContent');
  currentPositionData = position;
  currentPositionId   = position ? position.id : null;

  // 로그인 안 됨
  if (!currentUser) {
    el.innerHTML = `
      <div class="position-empty">
        <div>🔐 로그인하면 보유 주식 기준<br>매매 타이밍을 분석해 드려요</div>
        <button class="position-login-btn" onclick="openLoginModal()">로그인하기</button>
      </div>`;
    renderTradeActions(null);
    return;
  }

  // 보유 없음
  if (!position) {
    const ticker = currentAnalysisTicker;
    const name   = stock ? stock.name : ticker;
    const cur    = stock ? stock.currency : 'USD';
    el.innerHTML = `
      <div class="position-empty">
        <div>이 종목을 보유하고 계신가요?</div>
        <button class="position-add-btn" onclick="openAddModal('${ticker}','${name}','${cur}')">+ 포트폴리오에 추가</button>
      </div>`;
    renderTradeActions(null);
    return;
  }

  // 보유 중
  const cur = position.currency || 'USD';
  const fmtP = (v) => v == null ? '—' : (cur === 'KRW' ? '' : '$') + Number(v).toLocaleString() + (cur === 'KRW' ? '원' : '');
  const rec  = position.recommendation;
  const pct  = rec ? rec.return_pct : null;
  const pctStr   = pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—';
  const pctColor = pct > 0 ? 'var(--green)' : pct < 0 ? 'var(--red)' : 'var(--text)';

  el.innerHTML = `
    <div class="position-holding">
      <div class="position-row">
        <span class="position-label">보유 수량</span>
        <span class="position-value">${position.quantity}주</span>
      </div>
      <div class="position-row">
        <span class="position-label">매입 평균가</span>
        <span class="position-value">${fmtP(position.purchase_price)}</span>
      </div>
      <div class="position-row">
        <span class="position-label">현재가</span>
        <span class="position-value">${fmtP(position.current_price)}</span>
      </div>
      <div class="position-row">
        <span class="position-label">평가 손익</span>
        <span class="position-value" style="color:${pctColor}">${pctStr}</span>
      </div>
      <hr class="position-divider" />
      ${rec ? `
      <div class="position-rec ${rec.color}">
        <div class="position-rec-action">${rec.action}</div>
        <div class="position-rec-reason">${rec.reason}</div>
      </div>` : ''}
    </div>`;

  renderTradeActions(position, stock);
}

// ── 차트 아래 거래 버튼 표시 ────────────────────────────────────────────────
function renderTradeActions(position, stock) {
  const el = document.getElementById('tradeActions');
  if (!position) {
    el.classList.add('hidden');
    return;
  }
  // 버튼에 최신 ticker/name/currency 반영
  const cur  = position.currency || 'USD';
  const name = stock ? stock.name : position.name;
  el.querySelector('.buy').onclick  = () => openAddModal(position.ticker, name, cur);
  el.querySelector('.sell').onclick = () => openSellModal();
  el.querySelector('.del').onclick  = (e) => deleteHolding(e, position.id);
  el.classList.remove('hidden');
}

// ── 모의 매도 모달 ───────────────────────────────────────────────────────────
function openSellModal() {
  if (!currentPositionData) return;
  const p = currentPositionData;
  const cur = p.currency || 'USD';
  document.getElementById('sellModalDesc').textContent =
    `${p.name || p.ticker} 보유 ${p.quantity}주 · 평균매입가 ${Number(p.purchase_price).toLocaleString()}`;
  document.getElementById('sellCurrencyLabel').textContent = `(${cur})`;
  document.getElementById('sellPrice').value = '';
  document.getElementById('sellQty').value   = '';
  document.getElementById('sellModal').classList.remove('hidden');
}
function closeSellModal(event) {
  if (!event || event.target === document.getElementById('sellModal'))
    document.getElementById('sellModal').classList.add('hidden');
}
async function submitSellHolding() {
  const price = parseFloat(document.getElementById('sellPrice').value);
  const qty   = parseFloat(document.getElementById('sellQty').value);
  if (!price || !qty || qty <= 0) { alert('매도가와 수량을 입력해주세요'); return; }
  if (!currentPositionId) return;

  const p = currentPositionData;
  const newQty = p.quantity - qty;

  if (newQty <= 0) {
    // 전량 매도 → 포지션 삭제
    await fetch(`/api/portfolio/${currentPositionId}`, { method: 'DELETE' });
  } else {
    // 일부 매도 → 수량 업데이트
    await fetch(`/api/portfolio/${currentPositionId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quantity: newQty }),
    });
  }
  closeSellModal();
  loadPortfolio();
  if (currentAnalysisTicker === p.ticker) analyze();
}

function quickSearch(ticker) {
  document.getElementById('tickerInput').value = ticker;
  analyze();
}

async function setInterval(interval) {
  currentInterval = interval;
  document.querySelectorAll('.interval-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.interval === interval);
  });

  const ticker = document.getElementById('tickerInput').value.trim();
  if (!ticker || document.getElementById('result').classList.contains('hidden')) return;

  // 차트 영역만 로딩 표시
  const chartEl = document.getElementById('mainChart');
  chartEl.style.opacity = '0.4';

  try {
    const res = await fetch(`/api/chart?ticker=${encodeURIComponent(ticker)}&interval=${interval}`);
    const data = await res.json();
    if (!res.ok) { showError(data.error || '차트 로딩 실패'); return; }

    chartData = data.chart;
    document.getElementById('intervalBadge').textContent = data.interval_label;
    renderCharts(data.chart);
  } catch (e) {
    showError('차트 로딩 오류: ' + e.message);
  } finally {
    chartEl.style.opacity = '1';
  }
}

async function analyze() {
  const ticker = document.getElementById('tickerInput').value.trim();
  const period = 'max';
  if (!ticker) return;

  document.getElementById('loading').classList.remove('hidden');
  document.getElementById('result').classList.add('hidden');
  document.getElementById('errorBox').classList.add('hidden');

  try {
    const res = await fetch(`/api/analyze?ticker=${encodeURIComponent(ticker)}&period=${period}&interval=${currentInterval}`);
    const data = await res.json();

    if (!res.ok) {
      showError(data.error || '알 수 없는 오류');
      return;
    }

    currentAnalysisTicker = data.stock.ticker;
    chartData = data.chart;
    renderStockInfo(data.stock);
    renderVerdict(data.analysis, data.stock);
    renderDetail(data.analysis.details || []);
    renderFundamental(data.analysis.fundamental_details || []);
    renderMetrics(data.stock, data.analysis);
    renderPositionCard(data.position || null, data.stock);
    renderAnalysts(data.analysts, data.stock.currency);
    renderNews(data.news);

    // 봉 뱃지 업데이트
    document.getElementById('intervalBadge').textContent = data.interval_label || '일봉';

    // result를 먼저 보여준 뒤 레이아웃 계산 후 차트 렌더링
    document.getElementById('result').classList.remove('hidden');
    requestAnimationFrame(() => setTimeout(() => renderCharts(data.chart), 50));
  } catch (e) {
    showError('서버 연결 오류: ' + e.message);
  } finally {
    document.getElementById('loading').classList.add('hidden');
  }
}

function showError(msg) {
  const box = document.getElementById('errorBox');
  box.textContent = '⚠️ ' + msg;
  box.classList.remove('hidden');
  document.getElementById('loading').classList.add('hidden');
}

function fmt(num, currency = 'USD') {
  if (num == null) return '—';
  if (currency === 'KRW') return num.toLocaleString('ko-KR') + '원';
  return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function renderStockInfo(stock) {
  document.getElementById('stockName').textContent = stock.name;
  document.getElementById('stockTicker').textContent = stock.ticker;
  document.getElementById('stockSector').textContent = stock.sector || '';
  document.getElementById('stockExchange').textContent = stock.exchange ? `· ${stock.exchange}` : '';

  const cur = stock.currency;
  document.getElementById('currentPrice').textContent = fmt(stock.current_price, cur);

  const changeEl = document.getElementById('priceChange');
  if (stock.price_change != null) {
    const sign = stock.price_change >= 0 ? '+' : '';
    changeEl.textContent = `${sign}${fmt(stock.price_change, cur)} (${sign}${stock.price_change_pct}%)`;
    changeEl.className = 'price-change ' + (stock.price_change >= 0 ? 'up' : 'down');
  }
}

function renderVerdict(analysis, stock) {
  const badge = document.getElementById('verdictBadge');
  badge.textContent = analysis.verdict;
  badge.className = `verdict-badge ${analysis.verdict_color}`;

  // 점수 마커 위치 (score: -100~+100 → 0~100%)
  const pct = (analysis.score + 100) / 2;
  document.getElementById('scoreMarker').style.left = `${pct}%`;

  const cur = stock.currency;
  document.getElementById('entryPrice').textContent = fmt(analysis.entry_price, cur);
  document.getElementById('targetPrice').textContent = fmt(analysis.target_price, cur);
  document.getElementById('stopLoss').textContent = fmt(analysis.stop_loss, cur);

  // 시그널 요약은 상세 분석 근거로 이동

}

function renderMetrics(stock, analysis) {
  const cur = stock.currency;
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

  document.getElementById('pbRatio').textContent =
    stock.pb_ratio != null ? stock.pb_ratio.toFixed(2) + 'x' : '—';

  document.getElementById('epsValue').textContent =
    stock.eps != null ? fmt(stock.eps, cur) : '—';

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

// ── 분석 탭 전환 ────────────────────────────────────────
function switchDetailTab(tab) {
  document.querySelectorAll('.analysis-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.getElementById('detailList').classList.toggle('hidden', tab !== 'technical');
  document.getElementById('fundamentalList').classList.toggle('hidden', tab !== 'fundamental');
}

function _buildCards(details) {
  if (!details || !details.length) return '<p class="no-data">분석 데이터가 없습니다.</p>';
  return details.map(d => `
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
      </div>
    </div>
  `).join('');
}

// ── 기술적 분석 카드 ──────────────────────────────────────
function renderDetail(details) {
  document.getElementById('detailList').innerHTML = _buildCards(details);
}

// ── 투자 판단 카드 ────────────────────────────────────────
function renderFundamental(details) {
  document.getElementById('fundamentalList').innerHTML = _buildCards(details);
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

// ── 애널리스트 ────────────────────────────────────────
function renderAnalysts(analysts, currency) {
  if (!analysts) {
    document.getElementById('analystConsensus').innerHTML = '<p class="no-data">애널리스트 데이터가 없습니다.</p>';
    document.getElementById('analystTargets').innerHTML = '';
    return;
  }
  const { targets = [], summary = {} } = analysts;

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
function renderNews(news) {
  const el = document.getElementById('newsList');
  if (!news || !news.length) {
    el.innerHTML = '<p class="no-data">최근 뉴스를 찾을 수 없습니다.</p>';
    return;
  }
  el.innerHTML = news.map(n => `
    <a class="news-item" href="${n.url || '#'}" target="_blank" rel="noopener">
      <div class="news-body">
        <div class="news-title">${n.title}</div>
        ${n.desc ? `<div class="news-desc">${n.desc}</div>` : ''}
        <div class="news-meta">
          ${n.provider ? `<span class="news-provider">${n.provider}</span>` : ''}
          ${n.pub ? `<span>${n.pub}</span>` : ''}
        </div>
      </div>
    </a>
  `).join('');
}

// ── 차트 ──────────────────────────────────────────────
function setChartType(type) {
  chartType = type;
  document.querySelectorAll('.chart-btn').forEach((b, i) => {
    b.classList.toggle('active', (i === 0 && type === 'candlestick') || (i === 1 && type === 'line'));
  });
  if (chartData) renderCharts(chartData);
}

function toggleIndicator(name) {
  indicators[name] = !indicators[name];
  if (chartData) renderCharts(chartData);
}

function renderCharts(data) {
  renderMainChart(data);
  renderRSIChart(data);
  renderMACDChart(data);
}

function makeChart(el, height) {
  const width = el.clientWidth || el.parentElement.clientWidth || 900;
  const chart = LightweightCharts.createChart(el, {
    width,
    height,
    layout: { background: { color: '#161b22' }, textColor: '#8b949e' },
    grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
    crosshair: { mode: 1 },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true },
    handleScroll: true,
    handleScale: true,
  });
  const ro = new ResizeObserver(() => {
    const w = el.clientWidth;
    if (w > 0) chart.applyOptions({ width: w });
  });
  ro.observe(el);
  return chart;
}

function toSeries(dates, values) {
  return dates.map((d, i) => ({ time: d, value: values[i] })).filter(p => p.value != null);
}

function getChartHeights() {
  const w = window.innerWidth;
  if (w <= 480) return { main: 260, sub: 90 };
  if (w <= 768) return { main: 340, sub: 110 };
  return { main: 420, sub: 140 };
}

function renderMainChart(data) {
  const el = document.getElementById('mainChart');
  el.innerHTML = '';
  const chart = makeChart(el, getChartHeights().main);

  if (chartType === 'candlestick') {
    const candle = chart.addCandlestickSeries({
      upColor: '#3fb950', downColor: '#f85149',
      borderUpColor: '#3fb950', borderDownColor: '#f85149',
      wickUpColor: '#3fb950', wickDownColor: '#f85149',
    });
    candle.setData(data.dates.map((d, i) => ({
      time: d, open: data.open[i], high: data.high[i],
      low: data.low[i], close: data.close[i],
    })).filter(d => d.close != null));
  } else {
    const line = chart.addLineSeries({ color: '#58a6ff', lineWidth: 2 });
    line.setData(toSeries(data.dates, data.close));
  }

  if (indicators.bb) {
    chart.addLineSeries({ color: 'rgba(188,140,255,0.7)', lineWidth: 1, lineStyle: 2 })
      .setData(toSeries(data.dates, data.bb_upper));
    chart.addLineSeries({ color: 'rgba(188,140,255,0.4)', lineWidth: 1, lineStyle: 2 })
      .setData(toSeries(data.dates, data.bb_mid));
    chart.addLineSeries({ color: 'rgba(188,140,255,0.7)', lineWidth: 1, lineStyle: 2 })
      .setData(toSeries(data.dates, data.bb_lower));
  }

  if (indicators.ma) {
    [['ma5','#f0a500'],['ma20','#58a6ff'],['ma60','#3fb950'],['ma120','#f85149']].forEach(([key, color]) => {
      chart.addLineSeries({ color, lineWidth: 1 }).setData(toSeries(data.dates, data[key]));
    });
  }

  if (indicators.ichimoku) {
    chart.addLineSeries({ color: '#f85149', lineWidth: 1 }).setData(toSeries(data.dates, data.tenkan));
    chart.addLineSeries({ color: '#58a6ff', lineWidth: 1 }).setData(toSeries(data.dates, data.kijun));
  }
}

function renderRSIChart(data) {
  const el = document.getElementById('rsiChart');
  el.innerHTML = '';
  const chart = makeChart(el, getChartHeights().sub);

  chart.addLineSeries({ color: '#bc8cff', lineWidth: 2 })
    .setData(toSeries(data.dates, data.rsi));

  const validDates = data.dates.filter((_, i) => data.rsi[i] != null);
  if (validDates.length >= 2) {
    const first = validDates[0], last = validDates[validDates.length - 1];
    chart.addLineSeries({ color: 'rgba(248,81,73,0.5)', lineWidth: 1, lineStyle: 2 })
      .setData([{ time: first, value: 70 }, { time: last, value: 70 }]);
    chart.addLineSeries({ color: 'rgba(63,185,80,0.5)', lineWidth: 1, lineStyle: 2 })
      .setData([{ time: first, value: 30 }, { time: last, value: 30 }]);
  }
}

function renderMACDChart(data) {
  const el = document.getElementById('macdChart');
  el.innerHTML = '';
  const chart = makeChart(el, getChartHeights().sub);

  chart.addLineSeries({ color: '#58a6ff', lineWidth: 2 })
    .setData(toSeries(data.dates, data.macd));
  chart.addLineSeries({ color: '#f0a500', lineWidth: 2 })
    .setData(toSeries(data.dates, data.macd_signal));
  chart.addHistogramSeries({ priceFormat: { type: 'price' } })
    .setData(data.dates.map((d, i) => ({
      time: d, value: data.macd_hist[i],
      color: data.macd_hist[i] >= 0 ? '#3fb950' : '#f85149',
    })).filter(p => p.value != null));
}

// ── 자동완성 ─────────────────────────────────────────
let acItems = [];
let acIndex = -1;
let acTimer = null;

const input = document.getElementById('tickerInput');
const acList = document.getElementById('autocompleteList');

input.addEventListener('input', () => {
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
