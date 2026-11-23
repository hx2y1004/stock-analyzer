/* /ai-bots — AI 봇 검증 대시보드 */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (v, d = 2) => v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(d)}%`;
  const dirCls = (v) => v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
  function krw(n) {
    if (n == null) return '—';
    const a = Math.abs(n), sign = n < 0 ? '-' : '';
    if (a >= 1e8) return sign + (a / 1e8).toFixed(a % 1e8 === 0 ? 0 : 2) + '억원';
    if (a >= 1e4) return sign + Math.round(a / 1e4).toLocaleString('ko-KR') + '만원';
    return sign + Math.round(a).toLocaleString('ko-KR') + '원';
  }
  const px = (v, cur) => v == null ? '—' : cur === 'KRW' ? Math.round(v).toLocaleString('ko-KR') + '원'
    : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const STATUS = {
    PENDING: ['체결 대기', 'wait'], ACTIVE: ['진행 중', 'live'], TARGET_HIT: ['익절', 'win'],
    STOP_LOSS: ['손절', 'loss'], TRAILING_STOP: ['트레일링', 'trail'], TIME_STOP: ['기간만료', 'time'],
    TREND_EXIT: ['추세 이탈', 'time'], AI_RISK_EXIT: ['AI 악재 청산', 'loss'], CANCELLED: ['취소', 'off'],
  };
  const badge = (st, pnl) => {
    const [label, cls] = STATUS[st] || [st, 'off'];
    const c = st === 'TRAILING_STOP' ? (pnl >= 0 ? 'win' : 'loss') : cls;
    return `<span class="ab-badge ab-b-${c}">${label}</span>`;
  };

  let OV = null;

  // ── 개요 ──────────────────────────────────────────────────────────────
  async function loadOverview() {
    const r = await fetch('/api/ai-bots/overview');
    OV = await r.json();
    renderSchedule();
    renderCards();
    renderCompare();
  }

  function fmtWhen(iso, tzLabel) {
    const d = new Date(iso);
    const s = d.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
    return `${s} <span class="ab-muted">(내 시각, ${tzLabel})</span>`;
  }

  function renderSchedule() {
    const nb = OV.next_batch || {};
    const runs = OV.recent_runs || [];
    const last = runs.length
      ? runs.map((x) => `${x.market} ${esc(x.as_of)}: ${x.skipped ? '휴장/장중 — 추적만' : `${x.tickers}종목 분석 · 신호 ${x.signals}건 · AI ${x.ai_calls ?? 0}회`}`).join(' / ')
      : '아직 실행된 배치가 없습니다.';
    $('#abSchedule').innerHTML = `
      <div class="ab-sched-item"><span class="ab-sched-k">다음 배치 🇰🇷</span><span>${nb.KR ? fmtWhen(nb.KR, '장 마감 15:45 KST') : '—'}</span></div>
      <div class="ab-sched-item"><span class="ab-sched-k">다음 배치 🇺🇸</span><span>${nb.US ? fmtWhen(nb.US, '장 마감 16:30 뉴욕') : '—'}</span></div>
      <div class="ab-sched-item"><span class="ab-sched-k">최근 실행</span><span>${last}</span></div>
      <div class="ab-sched-item"><span class="ab-sched-k">진행 방식</span><span>장 마감 후 신호 → 다음 장 시가 체결 → 장중 5분마다 손절·익절 확인 · AI 평가 하루 최대 ${OV.ai_budget_limit}회 · 한국 종목 실적일 필터는 데이터가 없어 생략</span></div>`;
  }

  function statLine(k, v) { return `<div class="ab-stat"><span>${k}</span><strong>${v}</strong></div>`; }

  function renderCards() {
    const el = $('#abCards');
    el.innerHTML = OV.bots.map((b) => {
      const a = b.account || {}, s = b.stats || {};
      const empty = !s.trades && !s.open;
      return `
      <button type="button" class="card ab-card" data-key="${esc(b.key)}" style="--bot:${esc(b.color)}" aria-label="${esc(b.name)} 상세 보기">
        <div class="ab-card-top">
          <span class="ab-swatch" aria-hidden="true"></span>
          <span class="ab-card-name">${esc(b.emoji)} ${esc(b.name)}</span>
          <span class="ab-tag">${b.use_ai ? 'AI 검증' : '정량만'}</span>
        </div>
        <div class="ab-card-ret ${dirCls(a.return_pct)}">${pct(a.return_pct)}</div>
        <div class="ab-card-eq">${krw(a.total_krw)} <span class="ab-muted">/ 시작 ${krw(a.initial_krw)}</span></div>
        <div class="ab-stats">
          ${statLine('승률', s.win_rate == null ? '—' : s.win_rate + '%')}
          ${statLine('손익비', s.payoff == null ? '—' : s.payoff.toFixed(2))}
          ${statLine('MDD', s.mdd_pct == null ? '—' : pct(s.mdd_pct))}
          ${statLine('거래', `${s.trades || 0}회 · 보유 ${s.open || 0}`)}
        </div>
        <p class="ab-card-rule">${empty ? '아직 거래가 없습니다. ' : ''}${esc(b.summary.rule)}</p>
      </button>`;
    }).join('');
    el.querySelectorAll('.ab-card').forEach((c) => c.addEventListener('click', () => openDetail(c.dataset.key, true)));
  }

  function renderCompare() {
    const c = OV.comparison;
    const el = $('#abCompare');
    if (!c) { el.classList.add('hidden'); return; }
    const rows = [
      ['수익률', (x) => pct(x.return_pct), (x) => x.return_pct],
      ['승률', (x) => x.win_rate == null ? '—' : x.win_rate + '%', (x) => x.win_rate],
      ['최대 낙폭(MDD)', (x) => x.mdd_pct == null ? '—' : pct(x.mdd_pct), (x) => x.mdd_pct],
      ['평균 손익비', (x) => x.payoff == null ? '—' : x.payoff.toFixed(2), (x) => x.payoff],
      ['청산 거래 수', (x) => (x.trades || 0) + '회', null],
    ];
    const better = (f, a, b) => {
      if (!f) return ['', ''];
      const va = f(a), vb = f(b);
      if (va == null || vb == null || va === vb) return ['', ''];
      return va > vb ? [' ab-better', ''] : ['', ' ab-better'];
    };
    const enough = (c.ai.trades || 0) + (c.baseline.trades || 0) >= 1;
    el.innerHTML = `
      <h2 class="card-title" id="abCmpTitle">AI 검증 효과 — 같은 규칙, AI 필터만 다름</h2>
      <div class="ab-cmp-grid" role="table" aria-label="AI 검증 효과 비교">
        <div class="ab-cmp-row ab-cmp-head" role="row">
          <span role="columnheader"></span>
          <span role="columnheader">${esc(c.ai.emoji)} ${esc(c.ai.name)} <em>AI 검증</em></span>
          <span role="columnheader">${esc(c.baseline.emoji)} ${esc(c.baseline.name)} <em>AI 미사용</em></span>
        </div>
        ${rows.map(([k, f, raw]) => { const [ba, bb] = better(raw, c.ai, c.baseline); return `
        <div class="ab-cmp-row" role="row">
          <span role="rowheader">${k}</span>
          <span role="cell" class="${ba.trim()}">${f(c.ai)}</span>
          <span role="cell" class="${bb.trim()}">${f(c.baseline)}</span>
        </div>`; }).join('')}
      </div>
      <p class="ab-note">${enough ? '더 좋은 쪽을 굵게 표시합니다. 거래가 적을 때는 차이가 우연일 수 있습니다.'
        : '아직 청산된 거래가 없어 비교할 수 없습니다. 두 봇은 같은 정량 조건으로 후보를 고르고, 균형형만 AI가 한 번 더 걸러냅니다. 거래가 쌓이면 AI 필터가 수익률·승률·낙폭을 실제로 개선하는지 여기서 확인할 수 있습니다.'}</p>`;
  }

  // ── 차트 ──────────────────────────────────────────────────────────────
  let chart = null, lines = [];
  async function loadChart(period) {
    document.querySelectorAll('.ab-period').forEach((b) => {
      const on = b.dataset.p === period;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    const d = await (await fetch('/api/ai-bots/chart?period=' + period)).json();
    const wrap = $('#abChart');
    if (!chart) {
      chart = LightweightCharts.createChart(wrap, {
        autoSize: true,
        layout: { background: { color: 'transparent' }, textColor: '#8b949e', fontFamily: 'inherit' },
        grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(240,246,252,0.06)' } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false },
        crosshair: { mode: 0 },
        localization: { priceFormatter: (v) => (v == null || !isFinite(v)) ? '' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%` },
      });
      chart.subscribeCrosshairMove((p) => paintLegend(p));
    }
    lines.forEach((l) => chart.removeSeries(l.s));
    lines = d.series.map((s) => {
      const ser = chart.addLineSeries({
        color: s.color, lineWidth: 2, lineStyle: s.kind === 'benchmark' ? 2 : 0,
        priceLineVisible: false, lastValueVisible: false,
        pointMarkersVisible: s.data.length <= 2,
      });
      ser.setData(s.data.map((p) => ({ time: p.date, value: p.return_pct })));
      return { s: ser, meta: s };
    });
    chart.timeScale().fitContent();
    paintLegend(null);
    const bench = d.series.filter((s) => s.kind === 'benchmark' && s.data.length).length;
    $('#abChartNote').innerHTML = d.points < 2
      ? `봇 계좌는 ${esc(d.start)}에 1억 원으로 시작했습니다. 장 마감 배치가 하루씩 쌓이면 봇 곡선이 이어집니다.${bench ? ' 점선은 같은 기간 KOSPI·S&P 500입니다.' : ''}`
      : `${esc(d.start)} 기준 0%. 실선은 봇, 점선은 지수입니다. 봇 자산은 원화 기준(미국 종목은 환율 반영)입니다.`;
    renderTable(d);
  }

  function paintLegend(param) {
    $('#abLegend').innerHTML = lines.map(({ s, meta }) => {
      let v = null;
      if (param && param.seriesData && param.seriesData.get(s)) v = param.seriesData.get(s).value;
      else if (meta.data.length) v = meta.data[meta.data.length - 1].return_pct;
      return `<span class="ab-leg"><i style="background:${esc(meta.color)}" class="${meta.kind === 'benchmark' ? 'dash' : ''}"></i>${esc(meta.name)} <b>${v == null ? '—' : pct(v)}</b></span>`;
    }).join('');
  }

  function renderTable(d) {
    let det = $('#abTable');
    if (!det) {
      det = document.createElement('details');
      det.id = 'abTable';
      det.className = 'ab-table-view';
      $('#abChartNote').after(det);
    }
    det.innerHTML = `<summary>표로 보기</summary><div class="ab-scroll"><table class="ab-table"><thead><tr><th>구분</th><th>시작일</th><th>마지막 날짜</th><th>수익률</th></tr></thead><tbody>
      ${d.series.map((s) => { const a = s.data[0], z = s.data[s.data.length - 1]; return `<tr><td>${esc(s.name)}</td><td>${a ? esc(a.date) : '—'}</td><td>${z ? esc(z.date) : '—'}</td><td class="${dirCls(z && z.return_pct)}">${z ? pct(z.return_pct) : '—'}</td></tr>`; }).join('')}
    </tbody></table></div>`;
  }

  // ── 봇 상세 ───────────────────────────────────────────────────────────
  async function openDetail(key, scroll) {
    const el = $('#abDetail');
    el.classList.remove('hidden');
    el.innerHTML = '<div class="ab-muted">불러오는 중...</div>';
    document.querySelectorAll('.ab-card').forEach((c) => c.classList.toggle('selected', c.dataset.key === key));
    if (history.replaceState) history.replaceState(null, '', '#' + key);
    const r = await fetch('/api/ai-bots/' + encodeURIComponent(key));
    if (!r.ok) { el.innerHTML = '<div class="ab-muted">봇을 찾지 못했습니다.</div>'; return; }
    const b = await r.json();
    const s = b.stats, a = b.account || {};
    el.style.setProperty('--bot', b.color);
    el.innerHTML = `
      <div class="ab-det-head">
        <h2 class="card-title"><span class="ab-swatch" aria-hidden="true"></span>${esc(b.emoji)} ${esc(b.name)} <span class="ab-tag">${b.use_ai ? 'AI 검증' : '정량만'}</span></h2>
        <button type="button" class="sub-cancel ab-det-close" aria-label="상세 닫기">닫기</button>
      </div>
      <div class="ab-rules">
        <div><span>진입</span>${esc(b.summary.rule)}</div>
        <div><span>청산</span>${esc(b.summary.exit)}</div>
        <div><span>한도</span>${esc(b.summary.limits)} · ${esc(b.summary.fee)}</div>
      </div>
      <div class="ab-kpis">
        ${statLine('총자산', krw(a.total_krw))}
        ${statLine('수익률', `<span class="${dirCls(a.return_pct)}">${pct(a.return_pct)}</span>`)}
        ${statLine('현금', krw(a.cash_krw))}
        ${statLine('승률', s.win_rate == null ? '—' : s.win_rate + '%')}
        ${statLine('평균 수익 / 손실', `${pct(s.avg_win_pct)} / ${pct(s.avg_loss_pct)}`)}
        ${statLine('손익비', s.payoff == null ? '—' : s.payoff.toFixed(2))}
        ${statLine('MDD', s.mdd_pct == null ? '—' : pct(s.mdd_pct))}
        ${statLine('청산 거래', `${s.trades}회${s.skipped ? ` · 계좌 사정으로 건너뜀 ${s.skipped}` : ''}`)}
      </div>
      <div class="ab-tabs" role="tablist" aria-label="봇 상세">
        <button type="button" role="tab" class="ab-tab" id="abTab-pos" data-tab="pos" aria-controls="abPanel-pos">보유 포지션 <span class="ab-count">${b.positions.length}</span></button>
        <button type="button" role="tab" class="ab-tab" id="abTab-trades" data-tab="trades" aria-controls="abPanel-trades">매매 이력 <span class="ab-count" id="abTradeCount">${b.trades_total ?? 0}</span></button>
        <button type="button" role="tab" class="ab-tab" id="abTab-sig" data-tab="sig" aria-controls="abPanel-sig">신호 사후 검증 <span class="ab-count">${b.signals_total ?? b.signals.length}</span></button>
      </div>
      <div role="tabpanel" class="ab-panel" id="abPanel-pos" aria-labelledby="abTab-pos">
      ${b.positions.length ? `<div class="ab-scroll"><table class="ab-table">
        <thead><tr><th>종목</th><th>진입가</th><th>현재가</th><th>수익률</th><th>목표까지</th><th>손절까지</th><th>보유</th><th><span class="sr-only">모의 매수</span></th></tr></thead>
        <tbody>${b.positions.map((p) => `<tr>
          <td><div class="lg-id">${(window.renderStockLogo ? renderStockLogo(p.ticker, p.name, 'sm') : '')}<div class="lg-id-text"><a href="/stock/${encodeURIComponent(p.ticker)}">${esc(p.name || p.ticker)}</a> <span class="ab-muted">${esc(p.ticker)} · ${p.quantity}주</span></div></div></td>
          <td>${px(p.entry_price, p.currency)}</td><td>${px(p.current_price, p.currency)}</td>
          <td class="${dirCls(p.return_pct)}">${pct(p.return_pct)}</td>
          <td>${pct(p.to_target_pct)} <span class="ab-muted">${px(p.target_price, p.currency)}</span></td>
          <td>${pct(p.to_stop_pct)} <span class="ab-muted">${px(p.stop_price, p.currency)}</span></td>
          <td>${p.holding_days ?? 0}일</td>
          <td>${followLink(p.ticker, p.target_price, p.stop_price, p.currency, b.name, p.name)}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="ab-empty">지금 보유 중인 종목이 없습니다. 신호가 나오면 다음 장 시가에 자동으로 매수합니다.</p>'}
      </div>
      <div role="tabpanel" class="ab-panel" id="abPanel-trades" aria-labelledby="abTab-trades">
        <div class="ab-filter" role="group" aria-label="매매 구분">
          <button type="button" class="ab-fbtn active" data-side="all" aria-pressed="true">전체</button>
          <button type="button" class="ab-fbtn" data-side="buy" aria-pressed="false">매수</button>
          <button type="button" class="ab-fbtn" data-side="sell" aria-pressed="false">매도</button>
        </div>
        <div id="abTrades"><div class="ab-muted">불러오는 중...</div></div>
      </div>
      <div role="tabpanel" class="ab-panel" id="abPanel-sig" aria-labelledby="abTab-sig">
      ${b.signals.length ? `<div class="ab-scroll"><table class="ab-table ab-log">
        <thead><tr><th>신호일</th><th>종목</th><th>결과</th><th>진입 → 청산</th><th>수익률</th><th>신뢰도</th><th>근거</th></tr></thead>
        <tbody>${b.signals.map((x) => `<tr>
          <td>${esc(x.signal_date)}</td>
          <td><div class="lg-id">${(window.renderStockLogo ? renderStockLogo(x.ticker, x.name, 'sm') : '')}<div class="lg-id-text"><a href="/stock/${encodeURIComponent(x.ticker)}">${esc(x.name || x.ticker)}</a>${x.account && x.account.status === 'SKIPPED' ? ` <span class="ab-muted" title="${esc(x.account.skip_reason)}">계좌 미체결</span>` : ''}</div></div></td>
          <td>${badge(x.status, x.pnl_pct)}${x.status_reason ? `<div class="ab-muted ab-reason">${esc(x.status_reason)}</div>` : ''}
            ${x.status === 'ACTIVE' && x.side !== 'SELL' ? followLink(x.ticker, x.target_price, x.stop_price, x.market === 'KR' ? 'KRW' : 'USD', b.name, x.name) : ''}</td>
          <td>${px(x.fill_price || x.entry_price, x.market === 'KR' ? 'KRW' : 'USD')} → ${x.exit_price ? px(x.exit_price, x.market === 'KR' ? 'KRW' : 'USD') : '—'}</td>
          <td class="${dirCls(x.pnl_pct)}">${pct(x.pnl_pct)}</td>
          <td>${x.confidence ?? '—'}</td>
          <td><button type="button" class="ab-why" data-id="${x.id}">${x.has_ai ? 'AI 근거' : '근거'}</button></td></tr>`).join('')}</tbody></table>
          ${(b.signals_total || 0) > b.signals.length ? `<p class="ab-muted" style="margin-top:6px">최근 ${b.signals.length}건만 표시합니다 (전체 ${b.signals_total}건).</p>` : ''}</div>`
        : `<p class="ab-empty">아직 이 봇의 신호가 없습니다. 다음 장 마감 배치에서 조건에 맞는 종목이 있으면 신호가 생깁니다.</p>`}
      </div>
      <p class="ab-disclaimer">${esc(b.disclaimer)}</p>`;
    $('.ab-det-close', el).onclick = () => { el.classList.add('hidden'); document.querySelectorAll('.ab-card').forEach((c) => c.classList.remove('selected')); history.replaceState && history.replaceState(null, '', location.pathname); };
    el.querySelectorAll('.ab-why').forEach((btn) => btn.addEventListener('click', () => openWhy(btn.dataset.id)));
    // 탭 (선택은 브라우저에 기억)
    let tab = 'pos';
    try { tab = localStorage.getItem('abTab') || 'pos'; } catch (e) {}
    const tabs = [...el.querySelectorAll('.ab-tab')];
    const selectTab = (t, focus) => {
      tabs.forEach((x) => {
        const on = x.dataset.tab === t;
        x.classList.toggle('active', on);
        x.setAttribute('aria-selected', on ? 'true' : 'false');
        x.tabIndex = on ? 0 : -1;
        $('#abPanel-' + x.dataset.tab, el).hidden = !on;
        if (on && focus) x.focus();
      });
      try { localStorage.setItem('abTab', t); } catch (e) {}
    };
    tabs.forEach((x, i) => {
      x.addEventListener('click', () => selectTab(x.dataset.tab));
      x.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        const n = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
        selectTab(n.dataset.tab, true);
      });
    });
    selectTab(tabs.some((x) => x.dataset.tab === tab) ? tab : 'pos');
    // 매매 이력
    const T = { key, side: 'all', page: 1, items: [] };
    el.querySelectorAll('.ab-fbtn').forEach((f) => f.addEventListener('click', () => {
      el.querySelectorAll('.ab-fbtn').forEach((g) => { const on = g === f; g.classList.toggle('active', on); g.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      Object.assign(T, { side: f.dataset.side, page: 1, items: [] });
      loadTrades(T);
    }));
    loadTrades(T);
    if (scroll) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ── 매매 이력 ─────────────────────────────────────────────────────────
  const EXIT = {
    TARGET_HIT: ['목표가 익절', 'win'], STOP_LOSS: ['손절', 'loss'], TRAILING_STOP: ['트레일링 스탑', 'trail'],
    TIME_STOP: ['시간 청산', 'time'], TREND_EXIT: ['추세 이탈', 'time'], AI_RISK_EXIT: ['AI 악재 매도', 'loss'],
  };
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString('ko-KR', { year: '2-digit', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';

  async function loadTrades(T) {
    const box = $('#abTrades');
    if (!box) return;
    const req = (T.req = (T.req || 0) + 1);          // 필터를 빠르게 바꿔도 마지막 요청 결과만 그린다
    if (T.page === 1) {
      if (box.querySelector('.ab-trades, .ab-empty:not(.ab-loading)')) {
        // 필터 전환: 기존 내용을 흐리게 남기고 높이를 고정 → 영역이 줄었다 늘어나지 않음
        box.style.minHeight = box.offsetHeight + 'px';
        box.classList.add('is-loading');
        box.setAttribute('aria-busy', 'true');
      } else {
        box.innerHTML = '<p class="ab-empty ab-loading">불러오는 중...</p>';
      }
    }
    let d;
    try {
      const r = await fetch(`/api/ai-bots/${encodeURIComponent(T.key)}/trades?side=${T.side}&page=${T.page}&per=20`);
      if (!r.ok) throw new Error();
      d = await r.json();
    } catch (e) {
      if (req !== T.req) return;
      box.classList.remove('is-loading'); box.style.minHeight = ''; box.removeAttribute('aria-busy');
      box.innerHTML = '<p class="ab-empty">매매 이력을 불러오지 못했습니다.</p>';
      return;
    }
    if (req !== T.req) return;
    box.classList.remove('is-loading'); box.style.minHeight = ''; box.removeAttribute('aria-busy');
    const cnt = $('#abTradeCount');
    if (cnt) cnt.textContent = d.counts.all;
    T.items = T.items.concat(d.items);
    const label = { all: '', buy: '매수 ', sell: '매도 ' }[T.side];
    if (!T.items.length) {
      box.innerHTML = d.counts.all
        ? `<p class="ab-empty">${label}체결 내역이 없습니다.</p>`
        : '<p class="ab-empty">아직 체결된 매매 이력이 없습니다. 다음 장 시작 시 승인된 신호에 따라 첫 매수가 자동 실행됩니다.</p>';
      return;
    }
    box.innerHTML = `
      <p class="ab-muted ab-trade-sum">전체 ${d.counts.all}건 · 매수 ${d.counts.buy} · 매도 ${d.counts.sell}</p>
      <div class="ab-scroll"><table class="ab-table ab-trades">
        <thead><tr><th>체결 일시</th><th>구분</th><th>종목</th><th class="num">수량</th><th class="num">체결 단가</th>
          <th class="num">거래 금액</th><th class="num">수수료</th><th class="num">실현 손익</th><th>사유 / 근거</th></tr></thead>
        <tbody>${T.items.map((t) => {
          const sell = t.side === 'sell';
          const ex = EXIT[t.exit_status];
          const reason = sell
            ? (ex ? `<span class="ab-badge ab-b-${t.exit_status === 'TRAILING_STOP' ? ((t.pnl_pct || 0) >= 0 ? 'win' : 'loss') : ex[1]}">${ex[0]}</span>` : '<span class="ab-muted">—</span>')
              + (t.holding_days != null ? ` <span class="ab-muted">보유 ${t.holding_days}일</span>` : '')
            : (t.signal_id ? `<button type="button" class="ab-why" data-id="${t.signal_id}">${t.has_ai ? 'AI 근거' : '진입 근거'}</button>` : '<span class="ab-muted">—</span>');
          return `<tr>
            <td data-label="체결 일시">${esc(fmtTime(t.time))}</td>
            <td data-label="구분"><span class="ab-side ${sell ? 'ab-side-sell' : 'ab-side-buy'}">${sell ? '매도' : '매수'}</span></td>
            <td data-label="종목"><div class="lg-id">${(window.renderStockLogo ? renderStockLogo(t.ticker, t.name, 'sm') : '')}<div class="lg-id-text"><a href="/stock/${encodeURIComponent(t.ticker)}">${esc(t.name || t.ticker)}</a> <span class="ab-muted">${esc(t.ticker)}</span></div></div></td>
            <td class="num" data-label="수량">${Number(t.quantity).toLocaleString('ko-KR')}주</td>
            <td class="num" data-label="체결 단가">${px(t.price, t.currency)}${t.currency === 'USD' && t.exchange_rate ? `<div class="ab-muted">환율 ${Math.round(t.exchange_rate).toLocaleString('ko-KR')}</div>` : ''}</td>
            <td class="num" data-label="거래 금액">${Math.round(t.amount_krw).toLocaleString('ko-KR')}원</td>
            <td class="num" data-label="수수료">${Math.round(t.fee_krw).toLocaleString('ko-KR')}원${t.fee_pct != null ? `<div class="ab-muted">${t.fee_pct.toFixed(2)}%</div>` : ''}</td>
            <td class="num" data-label="실현 손익">${sell ? `<span class="${dirCls(t.realized_pnl_krw)}">${t.realized_pnl_krw > 0 ? '+' : ''}${Math.round(t.realized_pnl_krw).toLocaleString('ko-KR')}원</span><div class="${dirCls(t.pnl_pct)}">${pct(t.pnl_pct)}</div>` : '<span class="ab-muted">—</span>'}</td>
            <td data-label="사유 / 근거">${reason}</td></tr>`;
        }).join('')}</tbody></table></div>
      ${d.has_more ? '<button type="button" class="ab-more" id="abMore">더보기</button>' : ''}
      <p class="ab-muted ab-trade-note">실현 손익은 매수·매도 수수료(각 0.15%)를 모두 뺀 원화 기준입니다. 미국 종목은 체결 당시 환율로 환산합니다.</p>`;
    box.querySelectorAll('.ab-why').forEach((btn) => btn.addEventListener('click', () => openWhy(btn.dataset.id)));
    const more = $('#abMore');
    if (more) more.onclick = () => { more.disabled = true; more.textContent = '불러오는 중...'; T.page += 1; loadTrades(T); };
  }

  // ── 근거 모달 ─────────────────────────────────────────────────────────
  const modal = $('#abModal');
  const closeModal = () => modal.classList.add('hidden');
  modal.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('[data-close]')) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  // '따라 사기' → /trading?buy=…&target=…&stop=…&memo=[봇 이름 신호] (로그인 상태면 모의 매수 창이 바로 열림)
function followLink(ticker, target, stop, currency, botName, name) {
  const krw = currency === 'KRW';
  const r = (v) => krw ? String(Math.round(v)) : Number(v).toFixed(2);
  const q = new URLSearchParams({ buy: ticker });
  if (target > 0) q.set('target', r(target));
  if (stop > 0) q.set('stop', r(stop));
  q.set('memo', `[${botName} 신호]`);
  return `<a class="ab-follow" href="/trading?${q.toString()}" aria-label="${esc(name || ticker)} 따라 사기 (모의 매수)">따라 사기</a>`;
}

// 진입 근거 모달: 내부 키 → 한국어 라벨. bot_engine.fundamentals_for() 의 missing 은 지금
// fund_score · dcf_fair · analyst_target 세 가지이고, 나머지는 앞으로 늘어날 수 있는 키를 미리 둔 것.
// 표에 없는 키는 원문 그대로(이스케이프) 보여 준다.
const MISSING_LABELS = {
  fund_score: '펀더멘탈 점수', dcf_fair: 'DCF 적정가', dcf_reliability: 'DCF 신뢰도', analyst_target: '애널리스트 목표가',
  earnings_date: '실적 발표일', events: '일정(실적·배당)', news: '뉴스', fundamentals: '재무 데이터', financials: '재무제표',
  per: 'PER', forward_per: '선행 PER', pbr: 'PBR', eps: 'EPS', roe: 'ROE', dividend_yield: '배당수익률',
  market_cap: '시가총액', revenue_growth: '매출 성장률', sector: '섹터', vix: 'VIX', volume: '거래량', rsi: 'RSI',
  price: '현재가', ai: 'AI 평가',
};
const missingLabel = (k) => MISSING_LABELS[k] || String(k);
const VERDICT_LABELS = { pass: '통과', caution: '주의', reject: '제외' };
const verdictLabel = (v) => VERDICT_LABELS[String(v || '').toLowerCase()] || (v || '—');

async function openWhy(id) {
    const body = $('#abModalBody');
    body.innerHTML = '<div class="ab-muted">불러오는 중...</div>';
    modal.classList.remove('hidden');
    const d = await (await fetch('/api/bots/signals/' + id)).json();
    const ev = d.evidence || {}, f = ev.features || {}, fd = ev.fundamentals || {}, lv = ev.levels || {};
    const cur = d.market === 'KR' ? 'KRW' : 'USD';
    const ai = d.ai;
    const path = { momentum: '모멘텀 돌파', breakout: '추세 돌파', rebound: '과매도 반등', value_rebound: '가치 반등' }[ev.path] || ev.path || '—';
    $('#abModalTitle').textContent = `${d.name || d.ticker} · ${d.signal_date} 진입 근거`;
    body.innerHTML = `
      <div class="ab-why-sum">${esc(d.reason || '')}</div>
      <div class="ab-kpis ab-kpis-sm">
        ${statLine('경로', esc(path))}
        ${statLine('신뢰도', `${d.confidence ?? '—'} <span class="ab-muted">(정량 ${d.quant_score ?? '—'})</span>`)}
        ${statLine('진입 / 목표 / 손절', `${px(lv.entry, cur)} / ${px(d.target_price, cur)} / ${px(d.stop_price, cur)}`)}
        ${statLine('계획 손익비', d.rr != null ? d.rr.toFixed(2) : '—')}
        ${statLine('RSI · 거래량', `${f.rsi != null ? Number(f.rsi).toFixed(0) : '—'} · 평균의 ${f.vol_ratio != null ? Number(f.vol_ratio).toFixed(1) : '—'}배`)}
        ${statLine('펀더멘탈', fd.fund_score != null ? fd.fund_score + '점' : '—')}
      </div>
      ${ai ? `<h3 class="ab-h3">AI 평가 <span class="ab-muted">${esc(ai.as_of)} · ${esc(ai.model || '')}</span></h3>
        <p>${esc(ai.summary || '')}</p>
        <div class="ab-why-cols">
          <div><strong>긍정</strong><ul>${(ai.positives || []).map((t) => `<li>${esc(t)}</li>`).join('') || '<li>—</li>'}</ul></div>
          <div><strong>위험</strong><ul>${(ai.risks || []).map((t) => `<li>${esc(t)}</li>`).join('') || '<li>—</li>'}</ul></div>
        </div>
        <p class="ab-muted">판정 ${esc(verdictLabel(ai.verdict))} · AI 확신도 ${ai.confidence ?? '—'} · 단기 호재 ${ai.catalyst ? '있음' : '없음'} · 치명적 악재 ${ai.fatal_risk ? '있음' : '없음'}</p>`
        : '<p class="ab-muted">이 신호는 AI 검증 없이 정량 규칙으로만 만들어졌습니다.</p>'}
      ${(fd.missing || []).length ? `<p class="ab-muted">당시 비어 있던 데이터: ${esc(fd.missing.map(missingLabel).join(', '))}</p>` : ''}`;
  }

  // ── 시작 ──────────────────────────────────────────────────────────────
  document.querySelectorAll('.ab-period').forEach((b) => b.addEventListener('click', () => loadChart(b.dataset.p)));
  loadOverview().then(() => {
    const k = decodeURIComponent(location.hash.slice(1));
    if (k && OV.bots.some((b) => b.key === k)) openDetail(k, true);
  }).catch(() => { $('#abCards').innerHTML = '<div class="card ab-skel">불러오지 못했습니다. 새로고침해 주세요.</div>'; });
  loadChart('ALL').catch(() => { $('#abChartNote').textContent = '차트를 불러오지 못했습니다.'; });
})();
