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
    const fmtPrice  = (v) => v == null ? '—' : cur === 'KRW'
      ? Number(v).toLocaleString('ko-KR') + '원'
      : '$' + Number(v).toLocaleString();

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
function _currencyFromTicker(ticker) {
  // 한국 주식: .KS(코스피) 또는 .KQ(코스닥)
  return /\.(KS|KQ)$/i.test(ticker) ? 'KRW' : 'USD';
}

function _updateAddCurrencyLabel(cur) {
  document.getElementById('addCurrencyLabel').textContent = `(${cur})`;
}

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
  const price    = parseFloat(document.getElementById('addPrice').value);
  const qty      = parseFloat(document.getElementById('addQty').value);
  if (!ticker) { alert('종목을 검색해서 선택해주세요'); return; }
  if (!price || !qty) { alert('매입가와 수량을 입력해주세요'); return; }
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
    renderZones(data.analysis, data.stock);
    renderAnalysts(data.analysts, data.stock.currency);
    renderPriceMoveBanner(data.stock, data.move_reason);
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

// ── 지표별 초보자 팁 ──────────────────────────────────────
const INDICATOR_TIPS = {
  '이동평균선 (MA)': {
    bullish: '📈 단기→중기→장기 순서로 정렬된 "정배열" 상태예요. 주가가 꾸준히 오르는 좋은 흐름이에요.',
    bearish: '📉 장기→중기→단기로 역전된 "역배열" 상태예요. 하락 추세가 이어질 수 있어 신중하게 접근하세요.',
    neutral: '↔️ 이동평균선들이 뒤섞여 있어요. 방향이 불분명해 추세가 확실해질 때까지 기다리는 것도 방법이에요.'
  },
  '볼린저밴드 (BB)': {
    bullish: '📈 주가가 밴드 아래쪽에 가까워요. 평소보다 많이 떨어진 상태로 반등 가능성을 의미해요.',
    bearish: '⚠️ 주가가 밴드 위쪽에 가까워요. 단기 과열 상태로 조정(하락)이 올 수 있어요.',
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
    bullish: '📊 거래량이 평소보다 많아요. 많은 투자자들이 적극적으로 매수하고 있다는 신호예요.',
    bearish: '📊 거래량이 하락 시 많아요. 매도 압력이 강하다는 신호일 수 있어요.',
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
  document.getElementById('detailList').classList.toggle('hidden', tab !== 'technical');
  document.getElementById('fundamentalList').classList.toggle('hidden', tab !== 'fundamental');
  document.getElementById('zonesList').classList.toggle('hidden', tab !== 'zones');
}

function _buildCards(details) {
  if (!details || !details.length) return '<p class="no-data">분석 데이터가 없습니다.</p>';
  return details.map(d => {
    const tipObj = INDICATOR_TIPS[d.indicator];
    const tip = tipObj ? (tipObj[d.color] || tipObj['neutral'] || '') : '';
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

// ── 투자 판단 카드 ────────────────────────────────────────
function renderFundamental(details) {
  document.getElementById('fundamentalList').innerHTML = _buildCards(details);
}

// ── 매수/매도 구간 탭 ─────────────────────────────────────
function _getZoneAdvice(zone) {
  const map = {
    '손절 구간':              '1개월 최저가 아래로 이탈했어요. 지지선 붕괴 신호로 추가 하락 리스크가 있으니 포지션 정리를 고려하세요.',
    '눌림목 매수 구간':       '상승 추세 속 일시적 조정 구간이에요. 추세가 유효하다면 분할 매수 기회가 될 수 있어요.',
    '상승 추세 (보유)':       '상승 추세가 살아있어요. 매수·매도보다 현 포지션 보유를 유지하는 게 유리해요.',
    '고점 부근 (일부 익절)':  '상승 추세 고점 부근이에요. 전량 매도보다 보유량 일부(20~30%)를 익절하며 리스크를 줄여보세요.',
    '매수 추천 구간':         '1개월 저점 부근이에요. 횡보 흐름에서 지지선 역할을 하는 구간으로 분할 매수를 고려해보세요.',
    '매도 추천 구간':         '1개월 고점 부근이에요. 횡보 흐름에서 저항선 역할을 하는 구간으로 분할 매도를 고려해보세요.',
    '하락 추세 저점 (관망)':  '하락 추세 저점 부근이에요. 반등이 나올 수 있지만 추세가 꺾이지 않았으니 섣불리 매수하기보다 관망을 추천해요.',
    '하락 추세 반등 (매도)':  '하락 추세 속 반등 구간이에요. 추세가 살아있다면 반등 시 일부 매도로 리스크를 줄이는 전략이 유효해요.',
    '관망 구간':              '가격이 매수·매도 구간 사이에 있어요. 뚜렷한 방향성이 나올 때까지 현 포지션을 유지하며 관망하세요.',
  };
  return map[zone] || '현재 가격 위치를 분석 중이에요.';
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
      ? Number(Math.round(v)).toLocaleString() + '원'
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

  el.innerHTML = `
  <div class="zones-wrap">
    <div class="zones-header">
      <span class="zones-current-price">현재가 <strong>${fmt(price)}</strong></span>
      <span class="trend-badge ${trendLabel.cls}">${trendLabel.text}</span>
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
        <div class="zone-icon-col">🟢</div>
        <div class="zone-content">
          <div class="zone-title-row">
            <span class="zone-name">매수 추천 구간</span>
            <span class="zone-price-range">${fmt(entryLow)} ~ ${fmt(entryHigh)}</span>
          </div>
          <div class="zone-explanation">최근 한 달 저점 부근이에요. 이 구간으로 주가가 내려오면 분할 매수를 고려해보세요. 한 번에 전액 투자하기보다 2~3번에 나눠 사면 리스크를 줄일 수 있어요.</div>
        </div>
      </div>

      <div class="zone-row zone-current ${priceColor}">
        <div class="zone-icon-col">📍</div>
        <div class="zone-content">
          <div class="zone-title-row">
            <span class="zone-name">현재 위치 <span class="zone-badge ${priceColor}">${priceZone}</span></span>
            <span class="zone-price-range">${fmt(price)}</span>
          </div>
          <div class="zone-explanation">${_getZoneAdvice(priceZone)}</div>
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

    <div class="zones-guide">
      <h4 class="zones-guide-title">📚 초보자를 위한 매매 가이드</h4>
      <div class="zones-guide-grid">
        <div class="guide-card buy-guide">
          <div class="guide-card-title">✅ 언제 살까요?</div>
          <div class="guide-card-body">현재가가 <strong>${fmt(entryLow)} ~ ${fmt(entryHigh)}</strong> 구간(최근 1개월 저점 부근)에 오거나 RSI가 40 이하로 내려오면 분할 매수를 고려하세요. 급등하는 종목을 쫓아가며 사는 건 위험해요.</div>
        </div>
        <div class="guide-card sell-guide">
          <div class="guide-card-title">💰 언제 팔까요?</div>
          <div class="guide-card-body">현재가가 <strong>${fmt(targetLow)} ~ ${fmt(targetHigh)}</strong> 구간(최근 1개월 고점 부근)에 오면 절반씩 분할 매도를 추천해요. RSI가 70을 넘으면 추가 상승보다 조정 가능성을 고려하세요.</div>
        </div>
        <div class="guide-card stop-guide">
          <div class="guide-card-title">🛑 손절 기준은?</div>
          <div class="guide-card-body"><strong>${fmt(stop)}</strong>(최근 1개월 최저가 아래) 로 떨어지면 지지선이 무너진 신호예요. 추가 하락 가능성이 높으니 손실이 커지기 전에 매도하는 게 현명해요.</div>
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
    <div class="move-reason-text">${moveReason.replace(/\n/g, '<br>')}</div>
  `;
  el.classList.remove('hidden');
}

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

function syncChartTimeScales(charts) {
  let syncing = false;
  charts.forEach(src => {
    src.timeScale().subscribeVisibleTimeRangeChange(range => {
      if (syncing || !range) return;
      syncing = true;
      charts.forEach(tgt => { if (tgt !== src) tgt.timeScale().setVisibleRange(range); });
      syncing = false;
    });
  });
}

function renderCharts(data) {
  const c1 = renderMainChart(data);
  const c2 = renderRSIChart(data);
  const c3 = renderMACDChart(data);

  // 시간(timestamp) 기준으로 통일 — 데이터 시작점이 달라도 날짜 기준으로 정렬됨
  const alignAndSync = () => {
    const range = c1.timeScale().getVisibleRange();
    if (range) {
      c2.timeScale().setVisibleRange(range);
      c3.timeScale().setVisibleRange(range);
    }
    syncChartTimeScales([c1, c2, c3]);
  };
  requestAnimationFrame(() => setTimeout(alignAndSync, 80));
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
  return chart;
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
  return chart;
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
  return chart;
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
