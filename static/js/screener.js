/* /screener — 원클릭 종목 스크리너 · 2~3종목 나란히 비교 */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ICON_X = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path d="M18 6 6 18M6 6l12 12" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>';
  const ICON_PLUS = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>';
  const ICON_CHECK = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
  const STAR = (on) => `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" fill="${on ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
  const pct = (v, d = 2) => v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(d)}%`;
  const dir = (v) => v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
  const num = (v, d = 1, suf = '') => v == null ? '—' : Number(v).toFixed(d) + suf;
  const isKR = (t) => /\.(KS|KQ)$/i.test(t);
  const px = (v, t) => v == null ? '—' : isKR(t) ? Math.round(v).toLocaleString('ko-KR') + '원'
    : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const cap = (v, t) => {
    if (!v) return '—';
    if (isKR(t)) return v >= 1e12 ? (v / 1e12).toFixed(v >= 1e14 ? 0 : 1) + '조' : Math.round(v / 1e8).toLocaleString('ko-KR') + '억';
    return v >= 1e12 ? '$' + (v / 1e12).toFixed(2) + 'T' : '$' + (v / 1e9).toFixed(1) + 'B';
  };
  const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };

  const S = { tab: 'screen', market: 'ALL', preset: '', meta: null, basket: store.get('scBasket', []), cmp: [], period: '6M', loggedIn: false };

  // ── URL ────────────────────────────────────────────────────────────────
  function readUrl() {
    const q = new URLSearchParams(location.search);
    S.tab = document.body.dataset.tab === 'compare' || q.get('tab') === 'compare' ? 'compare' : 'screen';
    if (['ALL', 'KR', 'US'].includes((q.get('market') || '').toUpperCase())) S.market = q.get('market').toUpperCase();
    if (q.get('preset')) S.preset = q.get('preset');
    const t = (q.get('tickers') || '').split(',').map((x) => x.trim().toUpperCase()).filter(Boolean);
    if (t.length) S.cmp = [...new Set(t)].slice(0, 3);
  }
  function writeUrl() {
    const q = new URLSearchParams();
    if (S.tab === 'compare') { q.set('tab', 'compare'); if (S.cmp.length) q.set('tickers', S.cmp.join(',')); }
    else { if (S.market !== 'ALL') q.set('market', S.market); if (S.preset) q.set('preset', S.preset); }
    const u = location.pathname + (q.toString() ? '?' + q.toString().replace(/%2C/g, ',') : '');
    history.replaceState(null, '', u);
  }

  // ── 탭 ─────────────────────────────────────────────────────────────────
  function selectTab(t, focus) {
    S.tab = t;
    $$('.sc-tab').forEach((b) => {
      const on = b.dataset.tab === t;
      b.classList.toggle('active', on); b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    $('#scPanel-screen').hidden = t !== 'screen';
    $('#scPanel-compare').hidden = t !== 'compare';
    renderBasket();
    writeUrl();
    if (t === 'compare') loadCompare();
  }
  $$('.sc-tab').forEach((b, i, all) => {
    b.addEventListener('click', () => selectTab(b.dataset.tab));
    b.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      selectTab(all[(i + (e.key === 'ArrowRight' ? 1 : all.length - 1)) % all.length].dataset.tab, true);
    });
  });

  // ── 스크리너 ───────────────────────────────────────────────────────────
  async function loadMeta() {
    S.meta = await (await fetch('/api/screener/meta')).json();
    $('#scPresets').innerHTML = S.meta.presets.map((p) =>
      `<button type="button" class="sc-preset" data-p="${esc(p.key)}" aria-pressed="false"><span aria-hidden="true">${esc(p.emoji)}</span> ${esc(p.name)}</button>`).join('');
    $$('.sc-preset').forEach((b) => b.addEventListener('click', () => {
      S.preset = S.preset === b.dataset.p ? '' : b.dataset.p;
      paintPresets();
      b.blur();                                   // 터치 기기에서 이전 버튼의 포커스·호버 잔상이 남지 않게
      $('#fSort').value = ''; runQuery();
    }));
    $('#fSort').innerHTML = '<option value="">프리셋 기본</option>' + S.meta.sorts.map((s) => `<option value="${s.key}">${esc(s.name)}</option>`).join('');
    fillSectors();
  }
  function fillSectors() {
    const sel = $('#fSector'); const cur = sel.value;
    const groups = S.market === 'ALL' ? ['KR', 'US'] : [S.market];
    sel.innerHTML = '<option value="">전체 섹터</option>' + groups.map((m) =>
      `<optgroup label="${m === 'KR' ? '한국' : '미국'}">${S.meta.sectors[m].map((s) => `<option value="${esc(s.key)}">${esc(s.name)}</option>`).join('')}</optgroup>`).join('');
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }
  $$('#scMarket button').forEach((b) => b.addEventListener('click', () => {
    S.market = b.dataset.m;
    $$('#scMarket button').forEach((x) => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-pressed', on); });
    fillSectors(); runQuery();
  }));
  $('#fApply').addEventListener('click', () => runQuery());
  $('#fReset').addEventListener('click', () => {
    ['fSector', 'fFund', 'fRsiMin', 'fRsiMax', 'fPer', 'fUpside', 'fSort'].forEach((id) => { $('#' + id).value = ''; });
    $('#fOrder').value = 'desc'; S.preset = ''; runQuery();
  });
  $$('.sc-filter-grid input').forEach((i) => i.addEventListener('keydown', (e) => { if (e.key === 'Enter') runQuery(); }));

  let qSeq = 0;
  function paintPresets() {
    // 항상 현재 프리셋 1개만 강조 (모든 버튼의 상태를 매번 다시 씀)
    $$('.sc-preset').forEach((b) => { const on = !!S.preset && b.dataset.p === S.preset; b.classList.toggle('active', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); });
  }

  async function runQuery() {
    paintPresets();
    const p = (S.meta.presets.find((x) => x.key === S.preset));
    $('#scPresetDesc').textContent = p ? `${p.emoji} ${p.desc}` : '프리셋을 누르면 바로 걸러 드려요. 아래에서 조건을 직접 정할 수도 있습니다.';
    writeUrl();
    const q = new URLSearchParams({ market: S.market, limit: '60' });
    if (S.preset) q.set('preset', S.preset);
    const map = { fSector: 'sector', fFund: 'min_fundamental', fRsiMin: 'min_rsi', fRsiMax: 'max_rsi', fPer: 'max_per', fUpside: 'min_upside', fSort: 'sort_by' };
    Object.entries(map).forEach(([id, k]) => { const v = $('#' + id).value; if (v !== '') q.set(k, v); });
    if ($('#fSort').value) q.set('order', $('#fOrder').value);
    const seq = ++qSeq;
    const list = $('#scList');
    list.classList.add('is-loading');
    let d;
    try { d = await (await fetch('/api/screener?' + q)).json(); } catch (e) { list.innerHTML = '<p class="ab-empty">불러오지 못했습니다.</p>'; return; }
    if (seq !== qSeq) return;
    list.classList.remove('is-loading');
    renderResults(d);
  }

  function coverageNote(c) {
    const parts = [];
    if (c.price_updated_at) parts.push(`시세 ${new Date(c.price_updated_at).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })} 기준`);
    if (c.with_fund < c.universe) parts.push(`펀더멘탈 집계 중 ${c.with_fund}/${c.universe}`);
    if (c.with_tech < c.universe) parts.push(`기술 지표 ${c.with_tech}/${c.universe}`);
    return parts.join(' · ');
  }

  function renderResults(d) {
    const c = d.meta || {};
    $('#scAsof').textContent = coverageNote(c);
    $('#scCount').textContent = `${d.total.toLocaleString('ko-KR')}종목`;
    $('#scNote').textContent = d.total > d.items.length ? ` · 상위 ${d.items.length}개 표시` : '';
    const list = $('#scList');
    if (!c.with_price) {
      list.innerHTML = '<p class="ab-empty">아직 첫 집계 전입니다. 서버가 시세를 모으는 중이에요 (몇 분 걸려요). 잠시 후 새로고침해 주세요.</p>';
      return;
    }
    if (!d.items.length) {
      const fundNote = c.with_fund < c.universe ? ` 펀더멘탈 점수는 아직 ${c.with_fund}/${c.universe}종목만 집계돼 결과가 적을 수 있어요.` : '';
      list.innerHTML = `<p class="ab-empty">조건에 맞는 종목이 없습니다.${fundNote}</p>`;
      return;
    }
    list.innerHTML = `<div class="ab-scroll"><table class="ab-table sc-table">
      <thead><tr><th>종목</th><th class="num">현재가</th><th class="num">등락률</th><th class="num">펀더멘탈</th><th class="num">RSI</th>
        <th class="num">PER (TTM)</th><th class="num">상승여력</th><th class="sc-act-h"><span class="sr-only">관심 · 비교</span></th></tr></thead>
      <tbody>${d.items.map((r) => `<tr>
        <td data-label="종목"><a href="/stock/${encodeURIComponent(r.ticker)}" class="sc-name">${esc(r.name)}</a>
          <span class="ab-muted">${esc(r.ticker)}${r.sector_name ? ' · ' + esc(r.sector_name) : ''}</span>
          ${r.ai_strategies ? `<span class="sc-ai">AI 신호 ${r.ai_strategies.length}</span>` : ''}</td>
        <td class="num" data-label="현재가">${px(r.price, r.ticker)}</td>
        <td class="num ${dir(r.change_pct)}" data-label="등락률">${pct(r.change_pct)}</td>
        <td class="num" data-label="펀더멘탈">${r.fundamental_score == null ? '<span class="ab-muted">집계 중</span>' : `<span class="sc-score s${Math.floor(r.fundamental_score / 20)}">${Math.round(r.fundamental_score)}</span>`}</td>
        <td class="num" data-label="RSI">${num(r.rsi14, 0)}</td>
        <td class="num" data-label="PER (TTM)">${r.per == null ? '—' : num(r.per, 1, 'x')}</td>
        <td class="num ${dir(r.upside_pct)}" data-label="상승여력">${pct(r.upside_pct, 1)}</td>
        <td class="sc-act" data-label="">
          <button type="button" class="sc-star" data-t="${esc(r.ticker)}" data-n="${esc(r.name)}" aria-pressed="false" aria-label="${esc(r.name)} 관심종목">${STAR(false)}</button>
          <button type="button" class="sc-add" data-t="${esc(r.ticker)}" data-n="${esc(r.name)}"></button>
        </td></tr>`).join('')}</tbody></table></div>`;
    paintRowButtons();
    $$('.sc-add', list).forEach((b) => b.addEventListener('click', () => toggleBasket(b.dataset.t, b.dataset.n)));
    $$('.sc-star', list).forEach((b) => b.addEventListener('click', () => toggleStar(b)));
  }

  function paintRowButtons() {
    $$('.sc-add').forEach((b) => {
      const on = S.basket.some((x) => x.t === b.dataset.t);
      b.innerHTML = on ? `${ICON_CHECK}<span>담김</span>` : `${ICON_PLUS}<span>비교 담기</span>`;
      b.classList.toggle('on', on); b.setAttribute('aria-pressed', on);
    });
    $$('.sc-star').forEach((b) => {
      const on = !!(window.SAWatch && S.loggedIn && SAWatch.has(b.dataset.t));
      b.innerHTML = STAR(on); b.classList.toggle('on', on); b.setAttribute('aria-pressed', on);
      b.title = on ? '관심종목에서 빼기' : '관심종목에 추가';
    });
  }

  async function toggleStar(b) {
    if (!S.loggedIn) { toast('관심종목은 로그인 후 쓸 수 있어요.'); return; }
    b.disabled = true;
    try { SAWatch.has(b.dataset.t) ? await SAWatch.remove(b.dataset.t) : await SAWatch.add(b.dataset.t, b.dataset.n); }
    catch (e) { toast(e.message || '처리하지 못했습니다'); }
    b.disabled = false; paintRowButtons();
  }

  // ── 비교 바구니 ────────────────────────────────────────────────────────
  function toggleBasket(t, n) {
    const i = S.basket.findIndex((x) => x.t === t);
    if (i >= 0) S.basket.splice(i, 1);
    else if (S.basket.length >= 3) { toast('비교는 최대 3종목까지 담을 수 있어요.'); return; }
    else S.basket.push({ t, n });
    store.set('scBasket', S.basket); paintRowButtons(); renderBasket();
  }
  function renderBasket() {
    const el = $('#scBasket');
    const show = S.tab === 'screen' && S.basket.length > 0;
    el.classList.toggle('hidden', !show);
    document.body.classList.toggle('has-basket', show);
    if (!show) return;
    $('#scBasketItems').innerHTML = S.basket.map((x) =>
      `<span class="sc-chip">${esc(x.n)}<button type="button" data-t="${esc(x.t)}" aria-label="${esc(x.n)} 빼기">${ICON_X}</button></span>`).join('');
    $$('#scBasketItems button').forEach((b) => b.addEventListener('click', () => toggleBasket(b.dataset.t)));
    const go = $('#scBasketGo');
    go.disabled = S.basket.length < 2;
    go.textContent = S.basket.length < 2 ? `${S.basket.length}개 선택됨 · 1개 더 담아 주세요` : `${S.basket.length}개 선택됨 · 나란히 비교하기 →`;
  }
  $('#scBasketGo').addEventListener('click', () => {
    S.cmp = S.basket.map((x) => x.t);
    S.basket.forEach((x) => { names[x.t] = x.n; });
    selectTab('compare');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  let toastT;
  function toast(msg) {
    let el = $('#scToast');
    if (!el) { el = document.createElement('div'); el.id = 'scToast'; el.className = 'sc-toast'; el.setAttribute('role', 'status'); document.body.appendChild(el); }
    el.textContent = msg; el.classList.add('show');
    clearTimeout(toastT); toastT = setTimeout(() => el.classList.remove('show'), 2400);
  }

  // ── 비교 ───────────────────────────────────────────────────────────────
  const names = {};
  const COLORS = ['#3987e5', '#d95926', '#199e70'];       // 다크 배경 검증 팔레트 (봇 대시보드와 동일 계열)

  function renderPicked() {
    $('#cmpPicked').innerHTML = S.cmp.length ? S.cmp.map((t, i) =>
      `<span class="sc-chip" style="--c:${COLORS[i]}"><i class="sc-dot" aria-hidden="true"></i>${names[t] ? `${esc(names[t])}<span class="ab-muted">${esc(t)}</span>` : esc(t)}
        <button type="button" data-t="${esc(t)}" aria-label="${esc(names[t] || t)} 빼기">${ICON_X}</button></span>`).join('')
      : '<span class="ab-muted">비교할 종목을 2~3개 골라 주세요.</span>';
    $$('#cmpPicked button').forEach((b) => b.addEventListener('click', () => { S.cmp = S.cmp.filter((x) => x !== b.dataset.t); loadCompare(); }));
    $('#cmpInput').disabled = S.cmp.length >= 3;
    $('#cmpInput').placeholder = S.cmp.length >= 3 ? '최대 3종목까지 비교할 수 있어요' : '종목명 또는 티커로 추가 (최대 3개)';
  }

  // 자동완성 (기존 /api/search 재사용)
  let acItems = [], acIdx = -1, acTimer;
  const inp = $('#cmpInput'), ac = $('#cmpAc');
  inp.addEventListener('input', () => {
    clearTimeout(acTimer);
    const q = inp.value.trim();
    if (!q) { ac.classList.add('hidden'); return; }
    acTimer = setTimeout(async () => {
      try { acItems = await (await fetch('/api/search?q=' + encodeURIComponent(q))).json(); } catch (e) { acItems = []; }
      acIdx = -1;
      if (!acItems.length) { ac.classList.add('hidden'); return; }
      ac.innerHTML = acItems.map((it, i) => `<div class="autocomplete-item" role="option" data-i="${i}">
        <span class="ac-symbol">${esc(it.symbol)}</span><span class="ac-name">${esc(it.name)}</span><span class="ac-exchange">${esc(it.exchange || '')}</span></div>`).join('');
      $$('.autocomplete-item', ac).forEach((el) => el.addEventListener('mousedown', (e) => { e.preventDefault(); pick(acItems[+el.dataset.i]); }));
      ac.classList.remove('hidden');
    }, 180);
  });
  inp.addEventListener('keydown', (e) => {
    if (ac.classList.contains('hidden')) { if (e.key === 'Enter' && inp.value.trim()) { e.preventDefault(); inp.dispatchEvent(new Event('input')); } return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); acIdx = Math.max(0, Math.min(acItems.length - 1, acIdx + (e.key === 'ArrowDown' ? 1 : -1)));
      $$('.autocomplete-item', ac).forEach((el, i) => el.classList.toggle('active', i === acIdx));
    } else if (e.key === 'Enter') { e.preventDefault(); pick(acItems[Math.max(0, acIdx)]); }
    else if (e.key === 'Escape') ac.classList.add('hidden');
  });
  inp.addEventListener('blur', () => setTimeout(() => ac.classList.add('hidden'), 120));
  function pick(it) {
    if (!it) return;
    ac.classList.add('hidden'); inp.value = '';
    const t = it.symbol.toUpperCase();
    if (S.cmp.includes(t)) { toast('이미 담긴 종목이에요.'); return; }
    if (S.cmp.length >= 3) { toast('최대 3종목까지 비교할 수 있어요.'); return; }
    names[t] = it.name; S.cmp.push(t); loadCompare();
  }
  $$('#cmpExamples button').forEach((b) => b.addEventListener('click', () => { S.cmp = b.dataset.t.split(','); loadCompare(); }));

  let cmpSeq = 0, cmpData = null;
  async function loadCompare() {
    writeUrl(); renderPicked();
    const body = $('#cmpBody');
    if (S.cmp.length < 2) {
      body.innerHTML = `<div class="card"><p class="ab-empty">${S.cmp.length ? '한 종목을 더 골라 주세요.' : '위에서 종목을 검색하거나 예시를 눌러 2~3종목을 비교해 보세요.'}</p></div>`;
      cmpData = null; return;
    }
    const seq = ++cmpSeq;
    body.innerHTML = `<div class="card"><p class="ab-muted">불러오는 중... (처음 보는 종목은 재무 데이터를 받느라 몇 초 걸릴 수 있어요)</p></div>`;
    let d;
    try {
      const r = await fetch('/api/compare?tickers=' + encodeURIComponent(S.cmp.join(',')));
      d = await r.json(); if (!r.ok) throw new Error(d.error || '실패');
    } catch (e) { if (seq === cmpSeq) body.innerHTML = `<div class="card"><p class="ab-empty">비교 데이터를 불러오지 못했습니다. ${esc(e.message || '')}</p></div>`; return; }
    if (seq !== cmpSeq) return;
    d.items.forEach((x) => { names[x.ticker] = x.name; });
    renderPicked();
    cmpData = d;
    renderCompare(d);
  }

  let chart = null, lines = [];
  function renderCompare(d) {
    const body = $('#cmpBody');
    body.innerHTML = `
      <section class="card sc-chart-card" aria-labelledby="cmpChartTitle">
        <div class="ab-chart-head">
          <h2 class="card-title" id="cmpChartTitle">상대 수익률 (시작일 0%)</h2>
          <div class="ab-periods" role="group" aria-label="기간">
            ${['3M', '6M', '1Y'].map((p) => `<button type="button" class="ab-period${S.period === p ? ' active' : ''}" data-p="${p}" aria-pressed="${S.period === p}">${p}</button>`).join('')}
          </div>
        </div>
        <div class="ab-legend" id="cmpLegend"></div>
        <div id="cmpChart" class="ab-chart"></div>
        <details class="ab-table-view"><summary>표로 보기</summary><div id="cmpRetTable"></div></details>
      </section>
      <section class="card" aria-labelledby="cmpTblTitle">
        <h2 class="card-title" id="cmpTblTitle">항목별 비교</h2>
        <p class="ab-muted sc-tbl-hint">각 줄에서 더 유리한 값에 <span class="sc-best-badge">우위</span> 표시를 붙였어요. 시세·기간이 다른 시장끼리는 통화가 다릅니다.</p>
        <div class="ab-scroll">${compareTable(d.items)}</div>
      </section>`;
    $$('.ab-period', body).forEach((b) => b.addEventListener('click', () => {
      S.period = b.dataset.p;
      $$('.ab-period', body).forEach((x) => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-pressed', on); });
      drawChart(d);
    }));
    chart = null;
    drawChart(d);
  }

  function drawChart(d) {
    const wrap = $('#cmpChart');
    if (!wrap || !window.LightweightCharts) return;
    if (!chart) {
      chart = LightweightCharts.createChart(wrap, {
        autoSize: true,
        layout: { background: { color: 'transparent' }, textColor: '#8b949e', fontFamily: 'inherit' },
        grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(240,246,252,0.06)' } },
        rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false }, crosshair: { mode: 0 },
        localization: { priceFormatter: (v) => (v == null || !isFinite(v)) ? '' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%` },
      });
      chart.subscribeCrosshairMove((p) => legend(p));
      lines = [];
    }
    lines.forEach((l) => chart.removeSeries(l.s));
    lines = d.items.map((it, i) => {
      const s = chart.addLineSeries({ color: COLORS[i], lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
      const data = ((it.series || {})[S.period] || []).map((p) => ({ time: p.date, value: p.value }));
      s.setData(data);
      return { s, it, data, color: COLORS[i] };
    });
    const n = Math.max(0, ...lines.map((l) => l.data.length));
    if (n > 1) {
      // 첫 봉이 왼쪽 경계에 붙으면 첫 날짜 라벨이 잘린다 → 앞뒤로 데이터 폭의 약 4%(최소 2칸) 여백
      const pad = Math.max(2, Math.round(n * 0.04));
      chart.timeScale().setVisibleLogicalRange({ from: -pad, to: n - 1 + Math.max(1, Math.round(pad / 2)) });
    } else chart.timeScale().fitContent();
    legend(null);
    $('#cmpRetTable').innerHTML = `<table class="ab-table"><thead><tr><th>종목</th><th class="num">3M</th><th class="num">6M</th><th class="num">1Y</th></tr></thead><tbody>${
      d.items.map((it) => `<tr><td>${esc(it.name)}</td>${['3M', '6M', '1Y'].map((k) => `<td class="num ${dir((it.returns || {})[k])}">${pct((it.returns || {})[k], 1)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }
  function legend(param) {
    const el = $('#cmpLegend'); if (!el) return;
    el.innerHTML = lines.map(({ s, it, data, color }) => {
      let v = null;
      if (param && param.seriesData && param.seriesData.get(s)) v = param.seriesData.get(s).value;
      else if (data.length) v = data[data.length - 1].value;
      return `<span class="ab-leg"><i style="background:${color}"></i>${esc(it.name)} <b>${v == null ? '—' : pct(v, 1)}</b></span>`;
    }).join('');
  }

  // 비교 표: [라벨, 값 꺼내기, 표시, 더 좋은 쪽('high'|'low'|null), 섹션]
  function compareTable(items) {
    const yes = (b) => b == null ? '—' : b ? '정배열' : '아님';
    const rows = [
      ['기본 시세'],
      ['현재가', (x) => x.price, (x) => px(x.price, x.ticker), null],
      ['오늘 등락률', (x) => x.change_pct, (x) => `<span class="${dir(x.change_pct)}">${pct(x.change_pct)}</span>`, 'high', 2],
      ['시가총액', (x) => x.market_cap, (x) => cap(x.market_cap, x.ticker), null],
      ['52주 범위 위치', (x) => x.pos52, (x) => x.pos52 == null ? '—' : `<span class="sc-bar52" aria-hidden="true"><i style="left:${x.pos52}%"></i></span> ${x.pos52}%`, null],
      ['펀더멘탈'],
      ['종합 점수', (x) => x.fundamental_score, (x) => num(x.fundamental_score, 0), 'high', 0],
      ['퀄리티', (x) => x.quality_score, (x) => num(x.quality_score, 0), 'high', 0],
      ['가치', (x) => x.value_score, (x) => num(x.value_score, 0), 'high', 0],
      ['성장', (x) => x.growth_score, (x) => num(x.growth_score, 0), 'high', 0],
      ['모멘텀', (x) => x.momentum_score, (x) => num(x.momentum_score, 0), 'high', 0],
      ['밸류에이션'],
      ['PER (TTM)', (x) => (x.per > 0 ? x.per : null), (x) => x.per == null ? '—' : num(x.per, 1, 'x'), 'low'],
      ['선행 PER', (x) => (x.forward_per > 0 ? x.forward_per : null), (x) => x.forward_per == null ? '—' : num(x.forward_per, 1, 'x'), 'low'],
      ['PBR', (x) => (x.pbr > 0 ? x.pbr : null), (x) => x.pbr == null ? '—' : num(x.pbr, 2, 'x'), 'low', 2],
      ['배당수익률', (x) => x.dividend_yield, (x) => x.dividend_yield == null ? '—' : num(x.dividend_yield, 2, '%'), 'high', 2],
      ['목표가 상승여력', (x) => x.upside_pct, (x) => `<span class="${dir(x.upside_pct)}">${pct(x.upside_pct, 1)}</span>`, 'high'],
      ['기술 지표'],
      ['추세 점수', (x) => x.trend_score, (x) => num(x.trend_score, 0), 'high', 0],
      ['이동평균', (x) => (x.ma_aligned == null ? null : x.ma_aligned ? 1 : 0), (x) => yes(x.ma_aligned), 'high', 0],
      ['RSI(14)', (x) => x.rsi14, (x) => num(x.rsi14, 0), null],
      ['볼린저 위치', (x) => x.bb_pos, (x) => num(x.bb_pos, 0, '%'), null],
      ['거래량 (20일 평균 대비)', (x) => x.volume_ratio, (x) => x.volume_ratio == null ? '—' : num(x.volume_ratio, 2, '배'), null],
      ['수익률'],
      ['3개월', (x) => (x.returns || {})['3M'], (x) => `<span class="${dir((x.returns || {})['3M'])}">${pct((x.returns || {})['3M'], 1)}</span>`, 'high'],
      ['6개월', (x) => (x.returns || {})['6M'], (x) => `<span class="${dir((x.returns || {})['6M'])}">${pct((x.returns || {})['6M'], 1)}</span>`, 'high'],
      ['1년', (x) => (x.returns || {})['1Y'], (x) => `<span class="${dir((x.returns || {})['1Y'])}">${pct((x.returns || {})['1Y'], 1)}</span>`, 'high'],
    ];
    const head = `<thead><tr><th scope="col">항목</th>${items.map((x, i) =>
      `<th scope="col"><span class="sc-dot" style="--c:${COLORS[i]}" aria-hidden="true"></span><a href="/stock/${encodeURIComponent(x.ticker)}">${esc(x.name)}</a><div class="ab-muted">${esc(x.ticker)}</div></th>`).join('')}</tr></thead>`;
    const body = rows.map((r) => {
      if (r.length === 1) return `<tr class="sc-sec"><th colspan="${items.length + 1}" scope="rowgroup">${r[0]}</th></tr>`;
      const [label, get, show, better, dec = 1] = r;
      // 화면에 보이는 자릿수로 비교 (3.07 과 3.12 가 둘 다 '3.1x' 로 보이면 동점 → 배지 없음)
      const vals = items.map((x) => { const v = get(x); return v == null || !isFinite(v) ? null : Number(Number(v).toFixed(dec)); });
      let best = -1;
      const valid = vals.map((v, i) => [v, i]).filter(([v]) => v != null && isFinite(v));
      if (better && valid.length >= 2) {
        const tgt = better === 'high' ? Math.max(...valid.map((a) => a[0])) : Math.min(...valid.map((a) => a[0]));
        const winners = valid.filter(([v]) => v === tgt);
        if (winners.length === 1) best = winners[0][1];       // 동점이면 표시 안 함
      }
      return `<tr><th scope="row">${label}</th>${items.map((x, i) =>
        `<td class="num${i === best ? ' sc-best' : ''}">${show(x)}${i === best ? ' <span class="sc-best-badge">우위</span>' : ''}</td>`).join('')}</tr>`;
    }).join('');
    return `<table class="ab-table sc-cmp">${head}<tbody>${body}</tbody></table>`;
  }

  // ── 시작 ───────────────────────────────────────────────────────────────
  (async function init() {
    readUrl();
    $$('#scMarket button').forEach((x) => { const on = x.dataset.m === S.market; x.classList.toggle('active', on); x.setAttribute('aria-pressed', on); });
    if (window.SAWatch && document.getElementById('notifyBell')) {
      try { await SAWatch.load(); S.loggedIn = true; } catch (e) { S.loggedIn = false; }
    }
    try { await loadMeta(); } catch (e) { $('#scList').innerHTML = '<p class="ab-empty">불러오지 못했습니다. 새로고침해 주세요.</p>'; }
    selectTab(S.tab);
    if (S.meta) runQuery();
    renderBasket();
  })();
})();
