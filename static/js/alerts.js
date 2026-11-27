/* 내 종목 맞춤 알림 (목표가·손절가·RSI·공시/내부자 매수/실적 D-1) — 서버: stock_alerts.py · /api/alerts/*
 * window.SAAlerts.open(ticker, name, price?, currency?)  알림 설정 모달
 * window.SAAlerts.bindBell(btn, stock)                     종목 상세 '🔔 알림 설정' 버튼
 * window.SAAlerts.renderHome(el)                           홈 '내 알림' 탭 (규칙 목록 · 최근 발송 이력 · 테스트 푸시)
 * 푸시 권한·구독은 notify.js 의 window.SAPush 를 쓴다.
 */
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isKR = (t) => /\.(KS|KQ)$/i.test(t || '');
  async function api(url, opts) {
    const r = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
    if (r.status === 401) throw Object.assign(new Error('로그인이 필요합니다'), { code: 401 });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || '요청 실패');
    return d;
  }
  const fmtPx = (v, kr) => (v == null ? '—' : kr ? `${Math.round(v).toLocaleString('ko-KR')}원` : `$${(+v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  const roundPx = (v, kr) => (kr ? Math.round(v) : Math.round(v * 100) / 100);
  const TYPE = { target: '목표가', stop: '손절가', rsi_low: 'RSI 과매도', rsi_high: 'RSI 과매수', disclosure: '공시', insider_buy: '내부자 매수', earnings: '실적 D-1' };
  const ago = (iso) => {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return '방금'; if (s < 3600) return `${Math.floor(s / 60)}분 전`;
    if (s < 86400) return `${Math.floor(s / 3600)}시간 전`; return `${Math.floor(s / 86400)}일 전`;
  };
  const ruleCache = new Map();        // ticker → rule | null

  // ── 푸시 상태 블록 (모달 · 홈 공용) ─────────────────────────────────────────
  async function pushBox(box, onChange) {
    if (!box) return;
    const P = window.SAPush;
    if (!P) { box.innerHTML = ''; return; }
    const st = await P.status();
    let html;
    if (!st.supported) {
      html = `<p class="al-push-msg warn">${st.iosNeedsInstall || P.isIOS()
        ? 'iPhone·iPad는 Safari 공유 버튼 → <b>홈 화면에 추가</b>한 앱에서 열어야 알림을 허용할 수 있어요 (iOS 16.4 이상).'
        : '이 브라우저는 푸시 알림을 지원하지 않아요. 크롬·엣지·삼성 인터넷 최신 버전을 써 주세요.'}</p>`;
    } else if (!st.server) {
      html = `<p class="al-push-msg warn">${esc(st.reason || '서버에 푸시 알림이 아직 설정되지 않았어요')} · 헤더 종 아이콘(인앱 알림)으로는 받을 수 있어요.</p>`;
    } else if (st.subscribed && st.permission === 'granted') {
      html = '<p class="al-push-msg ok">✓ 이 기기로 푸시 알림을 받고 있어요.</p>';
    } else if (st.permission === 'denied') {
      html = '<p class="al-push-msg warn">이 브라우저에서 알림이 차단돼 있어요. 주소창 왼쪽 사이트 설정 → 알림을 <b>허용</b>으로 바꿔 주세요.</p>';
    } else {
      html = `<div class="al-push-row"><p class="al-push-msg">${st.iosNeedsInstall ? '홈 화면에 추가한 앱에서 알림을 허용하면 잠금화면으로 받아요.' : '이 기기로 푸시를 받으려면 알림을 허용해 주세요.'}</p>
        <button type="button" class="al-btn al-btn-pri al-push-on">🔔 이 기기에서 알림 받기</button></div>`;
    }
    box.innerHTML = html;
    const b = box.querySelector('.al-push-on');
    if (b) b.onclick = async () => {
      b.disabled = true; b.textContent = '허용 요청 중...';
      const r = await P.enable();
      if (!r.ok) { box.innerHTML = `<p class="al-push-msg warn">${esc(r.error)}</p>`; return; }
      await pushBox(box, onChange);
      onChange && onChange();
    };
    return st;
  }

  // ── 알림 설정 모달 ───────────────────────────────────────────────────────────
  let modal = null;
  function ensureModal() {
    if (modal) return modal;
    modal = document.createElement('div');
    modal.className = 'al-overlay hidden';
    modal.innerHTML = `
      <div class="al-modal" role="dialog" aria-modal="true" aria-labelledby="alTitle">
        <div class="al-head"><h3 id="alTitle">🔔 알림 설정</h3><button type="button" class="al-x" aria-label="닫기">✕</button></div>
        <div class="al-body"></div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    modal.querySelector('.al-x').onclick = close;
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.classList.contains('hidden')) close(); });
    return modal;
  }
  let lastFocus = null;
  function close() {
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.classList.remove('al-lock');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  async function open(ticker, name, price, currency) {
    ticker = (ticker || '').toUpperCase();
    const kr = currency ? currency === 'KRW' : isKR(ticker);
    const m = ensureModal();
    lastFocus = document.activeElement;
    const body = m.querySelector('.al-body');
    m.querySelector('#alTitle').innerHTML = `🔔 알림 설정 <span class="al-sub">${esc(name || ticker)}</span>`;
    body.innerHTML = '<p class="al-note">불러오는 중...</p>';
    m.classList.remove('hidden');
    document.body.classList.add('al-lock');
    let rule = null;
    try { rule = (await api(`/api/alerts/rules/${encodeURIComponent(ticker)}`)).rule; }
    catch (e) { body.innerHTML = `<p class="al-note err">${esc(e.message)}</p>`; return; }
    if (price == null) {
      try { price = (await api(`/api/realtime-price?ticker=${encodeURIComponent(ticker)}`)).price; } catch (e) { price = null; }
    }
    const step = kr ? '1' : '0.01';
    const tog = (id, on, label, sub) => `
      <label class="al-tog"><span class="al-tog-t">${label}<small>${sub}</small></span>
        <input type="checkbox" id="${id}" ${on ? 'checked' : ''}><i aria-hidden="true"></i></label>`;
    body.innerHTML = `
      <p class="al-cur">현재가 <b>${fmtPx(price, kr)}</b> <span class="al-tk">${esc(ticker)}</span></p>
      <div class="al-field">
        <label for="alTarget">목표가 <small>이상 도달 시 · 1회 알림 후 자동 해제</small></label>
        <div class="al-inrow"><input id="alTarget" type="number" inputmode="decimal" min="0" step="${step}" placeholder="${price ? fmtPx(price * 1.1, kr) : '목표가'}" value="${rule && rule.target_price != null ? rule.target_price : ''}">
          <span class="al-pct" id="alTargetPct"></span></div>
        <div class="al-chips"><button type="button" data-f="alTarget" data-p="5">+5%</button><button type="button" data-f="alTarget" data-p="10">+10%</button><button type="button" data-f="alTarget" data-p="20">+20%</button><button type="button" class="al-clear" data-f="alTarget">지우기</button></div>
      </div>
      <div class="al-field">
        <label for="alStop">손절가 <small>이하 도달 시 · 1회 알림 후 자동 해제</small></label>
        <div class="al-inrow"><input id="alStop" type="number" inputmode="decimal" min="0" step="${step}" placeholder="${price ? fmtPx(price * 0.9, kr) : '손절가'}" value="${rule && rule.stop_price != null ? rule.stop_price : ''}">
          <span class="al-pct" id="alStopPct"></span></div>
        <div class="al-chips"><button type="button" data-f="alStop" data-p="-5">-5%</button><button type="button" data-f="alStop" data-p="-10">-10%</button><button type="button" data-f="alStop" data-p="-20">-20%</button><button type="button" class="al-clear" data-f="alStop">지우기</button></div>
      </div>
      <div class="al-togs">
        ${tog('alRsiLow', rule && rule.rsi_oversold, 'RSI 과매도', 'RSI(14) 30 이하 · 24시간에 1번')}
        ${tog('alRsiHigh', rule && rule.rsi_overbought, 'RSI 과매수', 'RSI(14) 70 이상 · 24시간에 1번')}
        ${tog('alDisc', rule && rule.disclosure_enabled, '공시 · 내부자 매수 · 실적', `${kr ? 'DART 주요 공시·임원 장내 매수' : 'SEC 8-K·Form 4 내부자 매수'} · 실적 발표 전날`)}
      </div>
      <p class="al-note">가격·RSI는 정규장 중 5분마다(${kr ? '한국 09:00~15:30' : '미국 정규장'}), 공시·실적은 30분마다 확인해요. 시세는 최대 몇 분 늦을 수 있어요.</p>
      <div class="al-pushbox"></div>
      <p class="al-msg" role="status"></p>
      <div class="al-foot">
        ${rule ? '<button type="button" class="al-btn al-del">알림 삭제</button>' : ''}
        <button type="button" class="al-btn al-btn-pri al-save">저장</button>
      </div>`;
    const $ = (s) => body.querySelector(s);
    const pct = (inp, out) => {
      const v = parseFloat(inp.value);
      if (!price || !(v > 0)) { out.textContent = ''; out.className = 'al-pct'; return; }
      const p = (v / price - 1) * 100;
      out.textContent = `현재가 대비 ${p > 0 ? '+' : ''}${p.toFixed(1)}%`;
      out.className = `al-pct ${p > 0 ? 'up' : p < 0 ? 'down' : ''}`;
    };
    const tI = $('#alTarget'), sI = $('#alStop');
    tI.oninput = () => pct(tI, $('#alTargetPct')); sI.oninput = () => pct(sI, $('#alStopPct'));
    pct(tI, $('#alTargetPct')); pct(sI, $('#alStopPct'));
    body.querySelectorAll('.al-chips button').forEach((b) => {
      b.onclick = () => {
        const inp = $('#' + b.dataset.f);
        if (b.classList.contains('al-clear')) inp.value = '';
        else if (price) inp.value = roundPx(price * (1 + (+b.dataset.p) / 100), kr);
        inp.dispatchEvent(new Event('input'));
      };
      if (!price && !b.classList.contains('al-clear')) b.disabled = true;
    });
    pushBox($('.al-pushbox'));
    const msg = (t, cls) => { const e = $('.al-msg'); e.textContent = t || ''; e.className = 'al-msg' + (cls ? ' ' + cls : ''); };
    $('.al-save').onclick = async (ev) => {
      const t = parseFloat(tI.value), s = parseFloat(sI.value);
      if (price && t > 0 && t <= price) return msg('목표가는 현재가보다 높게 입력해 주세요 (이미 넘은 값이면 바로 울려요).', 'err');
      if (price && s > 0 && s >= price) return msg('손절가는 현재가보다 낮게 입력해 주세요.', 'err');
      ev.target.disabled = true; msg('저장하는 중...');
      try {
        const r = await api('/api/alerts/rules', { method: 'POST', body: JSON.stringify({
          ticker, stock_name: name || ticker, target_price: t > 0 ? t : null, stop_price: s > 0 ? s : null,
          rsi_oversold: $('#alRsiLow').checked, rsi_overbought: $('#alRsiHigh').checked, disclosure_enabled: $('#alDisc').checked, enabled: true }) });
        ruleCache.set(ticker, r.rule);
        msg('저장했어요. 조건이 맞으면 알려 드릴게요.', 'ok');
        paintBells(ticker);
        refreshHome();
        setTimeout(close, 700);
      } catch (e) { msg(e.message, 'err'); }
      finally { ev.target.disabled = false; }
    };
    const del = $('.al-del');
    if (del) del.onclick = async () => {
      if (!confirm(`${name || ticker} 알림을 모두 삭제할까요?`)) return;
      try { await api(`/api/alerts/rules/${encodeURIComponent(ticker)}`, { method: 'DELETE' }); ruleCache.set(ticker, null); paintBells(ticker); refreshHome(); close(); }
      catch (e) { msg(e.message, 'err'); }
    };
    setTimeout(() => tI.focus({ preventScroll: true }), 50);
  }

  // ── 종목 상세 '🔔 알림 설정' 버튼 ─────────────────────────────────────────────
  const bells = new Set();
  function paintBells(ticker) {
    bells.forEach((b) => {
      if (!b.isConnected) { bells.delete(b); return; }
      if (ticker && b.dataset.t !== ticker) return;
      const r = ruleCache.get(b.dataset.t);
      const on = !!(r && r.enabled && r.active);
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.title = on ? '알림 설정됨 · 눌러서 수정' : '목표가·손절가·RSI·공시 알림 설정';
      const lb = b.querySelector('.al-bell-t');
      if (lb) lb.textContent = on ? '알림 켜짐' : '알림 설정';
    });
  }
  async function bindBell(btn, stock) {
    if (!btn || !stock) return;
    const t = stock.ticker.toUpperCase();
    btn.dataset.t = t;
    bells.add(btn);
    btn.classList.remove('hidden');
    btn.onclick = () => open(t, stock.name, stock.current_price, stock.currency);
    if (!ruleCache.has(t)) {
      try { ruleCache.set(t, (await api(`/api/alerts/rules/${encodeURIComponent(t)}`)).rule); } catch (e) { if (e.code === 401) { btn.classList.add('hidden'); return; } }
    }
    paintBells(t);
  }

  // ── 홈 '내 알림' 탭 ─────────────────────────────────────────────────────────
  let homeEl = null;
  function refreshHome() { if (homeEl && homeEl.isConnected && !homeEl.closest('.hidden')) renderHome(homeEl); }
  function badges(r) {
    const kr = r.currency === 'KRW', out = [];
    if (r.target_price != null) out.push(`<span class="al-b tgt">목표 ${fmtPx(r.target_price, kr)}</span>`);
    if (r.stop_price != null) out.push(`<span class="al-b stp">손절 ${fmtPx(r.stop_price, kr)}</span>`);
    if (r.rsi_oversold) out.push('<span class="al-b">RSI ≤30</span>');
    if (r.rsi_overbought) out.push('<span class="al-b">RSI ≥70</span>');
    if (r.disclosure_enabled) out.push('<span class="al-b">공시·실적</span>');
    return out.join('') || '<span class="al-b off">조건 없음 (목표·손절 알림이 끝남)</span>';
  }
  async function renderHome(el) {
    if (!el) return;
    homeEl = el;
    if (!el.innerHTML.trim()) el.innerHTML = '<div class="pf-loading">불러오는 중...</div>';
    let d, lg;
    try { [d, lg] = await Promise.all([api('/api/alerts/rules'), api('/api/alerts/logs')]); }
    catch (e) { el.innerHTML = `<div class="watch-empty">${e.code === 401 ? '로그인하면 보유·관심종목의 목표가·손절가·RSI·공시 알림을 받을 수 있어요.' : esc(e.message)}</div>`; return; }
    d.rules.forEach((r) => ruleCache.set(r.ticker, r));
    const rules = d.rules.map((r) => `
      <li class="al-row ${r.enabled ? '' : 'off'}">
        <a class="al-row-id" href="/stock/${encodeURIComponent(r.ticker)}">
          ${window.renderStockLogo ? renderStockLogo(r.ticker, r.stock_name, 'sm') : ''}
          <span class="al-row-nm"><b>${esc(r.stock_name || r.ticker)}</b><small>${esc(r.ticker)}</small></span></a>
        <span class="al-row-bs">${badges(r)}</span>
        <span class="al-row-acts">
          <label class="al-sw" title="${r.enabled ? '알림 끄기' : '알림 켜기'}"><input type="checkbox" data-t="${esc(r.ticker)}" class="al-en" ${r.enabled ? 'checked' : ''} aria-label="${esc(r.stock_name || r.ticker)} 알림 켜기/끄기"><i aria-hidden="true"></i></label>
          <button type="button" class="al-ic al-edit" data-t="${esc(r.ticker)}" data-n="${esc(r.stock_name || r.ticker)}" data-c="${r.currency}" aria-label="${esc(r.stock_name || r.ticker)} 알림 수정" title="수정">✎</button>
          <button type="button" class="al-ic al-rm" data-t="${esc(r.ticker)}" data-n="${esc(r.stock_name || r.ticker)}" aria-label="${esc(r.stock_name || r.ticker)} 알림 삭제" title="삭제">✕</button>
        </span>
      </li>`).join('');
    const logs = (lg.logs || []).map((g) => `
      <li class="al-log"><a href="/stock/${encodeURIComponent(g.ticker)}">
        <span class="al-log-h"><span class="al-b t-${esc(g.rule_type)}">${esc(TYPE[g.rule_type] || g.type_label)}</span><em>${ago(g.fired_at)}</em></span>
        <b>${esc(g.title)}</b><small>${esc(g.body || '')}</small></a></li>`).join('');
    el.innerHTML = `
      <div class="al-home">
        <section class="al-card">
          <div class="al-card-h"><h4>푸시 알림</h4><button type="button" class="al-btn al-test">테스트 푸시 보내기</button></div>
          <div class="al-pushbox"></div>
          <p class="al-msg" role="status"></p>
        </section>
        <section class="al-card">
          <div class="al-card-h"><h4>내 알림 <span class="al-n">${d.rules.length}</span></h4></div>
          ${d.rules.length ? `<ul class="al-list">${rules}</ul>`
            : '<p class="al-empty">아직 설정한 알림이 없어요. 보유 종목·관심종목의 🔔 또는 종목 화면의 <b>🔔 알림 설정</b>에서 목표가·손절가·RSI·공시 알림을 만들 수 있어요.</p>'}
        </section>
        <section class="al-card">
          <div class="al-card-h"><h4>최근 발송 이력</h4><span class="al-sub">최근 20건</span></div>
          ${logs ? `<ul class="al-logs">${logs}</ul>` : '<p class="al-empty">아직 보낸 알림이 없어요.</p>'}
        </section>
      </div>`;
    const msg = (t, cls) => { const e = el.querySelector('.al-msg'); e.textContent = t || ''; e.className = 'al-msg' + (cls ? ' ' + cls : ''); };
    pushBox(el.querySelector('.al-pushbox'), () => msg('이 기기에서 알림을 켰어요. 테스트 푸시로 확인해 보세요.', 'ok'));
    el.querySelector('.al-test').onclick = async (ev) => {
      ev.target.disabled = true; msg('보내는 중...');
      try {
        const r = await api('/api/alerts/test', { method: 'POST' });
        msg(r.sent ? `테스트 푸시를 보냈어요 (${r.sent}대${r.removed ? ` · 만료 기기 ${r.removed}대 정리` : ''}). 몇 초 안에 도착해요.` : `보내지 못했어요 (실패 ${r.failed} · 만료 정리 ${r.removed}).`, r.sent ? 'ok' : 'err');
      } catch (e) { msg(e.message, 'err'); }
      finally { ev.target.disabled = false; }
    };
    el.querySelectorAll('.al-en').forEach((c) => {
      c.onchange = async () => {
        c.disabled = true;
        try { await api(`/api/alerts/rules/${encodeURIComponent(c.dataset.t)}`, { method: 'POST', body: JSON.stringify({ enabled: c.checked }) }); c.closest('.al-row').classList.toggle('off', !c.checked); }
        catch (e) { c.checked = !c.checked; msg(e.message, 'err'); }
        finally { c.disabled = false; }
      };
    });
    el.querySelectorAll('.al-edit').forEach((b) => { b.onclick = () => open(b.dataset.t, b.dataset.n, null, b.dataset.c); });
    el.querySelectorAll('.al-rm').forEach((b) => {
      b.onclick = async () => {
        if (!confirm(`${b.dataset.n} 알림을 삭제할까요?`)) return;
        try { await api(`/api/alerts/rules/${encodeURIComponent(b.dataset.t)}`, { method: 'DELETE' }); ruleCache.set(b.dataset.t, null); renderHome(el); }
        catch (e) { msg(e.message, 'err'); }
      };
    });
  }

  window.SAAlerts = { open, bindBell, renderHome, close };
})();
