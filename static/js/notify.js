/* 헤더 알림 종 + 신호 알림 설정 + 관심종목 (모든 페이지 공통)
 * - 종(#notifyBell)은 로그인 상태일 때 서버가 그려 둔다. 여기서 안 읽은 개수와 목록을 채운다.
 * - window.SAWatch: 관심종목 조회·추가·삭제 (분석 페이지의 ☆ 버튼, 메인의 '관심종목' 탭이 사용)
 */
(function () {
  const POLL_MS = 60000;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function api(url, opts) {
    const r = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
    if (r.status === 401) throw Object.assign(new Error('login'), { code: 401 });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || '요청 실패');
    return data;
  }

  // 일부 기기 글꼴에 없는 ✕ 문자가 ☒ 처럼 깨져 보여 SVG 로 그린다
  const ICON_X = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M18 6 6 18M6 6l12 12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" fill="none"/></svg>';

  function fmtPx(v, cur) {
    if (v == null) return '—';
    return cur === 'KRW' ? Math.round(v).toLocaleString('ko-KR') + '원'
      : '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function ago(iso) {
    if (!iso) return '';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return '방금';
    if (s < 3600) return Math.floor(s / 60) + '분 전';
    if (s < 86400) return Math.floor(s / 3600) + '시간 전';
    return Math.floor(s / 86400) + '일 전';
  }

  // ── 종 ──────────────────────────────────────────────────────────────────
  let bell, badge, panel, pollTimer;

  function setBadge(n) {
    if (!badge) return;
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.classList.toggle('hidden', !n);
    bell.setAttribute('aria-label', n ? `알림 ${n}개 안 읽음` : '알림');
  }

  async function refreshCount() {
    try {
      const d = await api('/api/notifications?limit=0');
      setBadge(d.unread || 0);
    } catch (e) {
      if (e.code === 401 && bell) bell.closest('.notify-wrap').classList.add('hidden');
    }
  }

  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(() => { if (!document.hidden) refreshCount(); }, POLL_MS);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshCount(); });
  }

  function placePanel() {
    // 종 오른쪽 끝에 맞추되, 화면 밖으로 나가지 않게 좌우 8px 안쪽으로 고정
    const r = bell.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const w = panel.offsetWidth || Math.min(360, vw - 16);
    const left = Math.min(Math.max(8, r.right - w), vw - w - 8);
    panel.style.top = (r.bottom + 8) + 'px';
    panel.style.left = Math.max(8, left) + 'px';
  }

  const KIND_ICON = { signal_new: '🟢', signal_exit: '🏁', signal_cancel: '⚪' };

  async function openPanel() {
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'notify-panel hidden';
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-label', '알림');
      document.body.appendChild(panel);
      document.addEventListener('click', (ev) => {
        if (!panel.classList.contains('hidden') && !panel.contains(ev.target) && !bell.contains(ev.target)) closePanel();
      });
      document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closePanel(); });
      window.addEventListener('resize', () => { if (!panel.classList.contains('hidden')) placePanel(); });
    }
    panel.innerHTML = `
      <div class="notify-head">
        <strong>알림</strong>
        <div class="notify-head-actions">
          <button type="button" class="notify-link" data-act="readall">모두 읽음</button>
          <button type="button" class="notify-link" data-act="settings">알림 설정</button>
        </div>
      </div>
      <div class="notify-list"><div class="notify-empty">불러오는 중...</div></div>`;
    panel.classList.remove('hidden');
    bell.setAttribute('aria-expanded', 'true');
    placePanel();
    panel.querySelector('[data-act="readall"]').onclick = async () => {
      try { await api('/api/notifications/read', { method: 'POST', body: JSON.stringify({ all: true }) }); } catch (e) {}
      panel.querySelectorAll('.notify-item.unread').forEach((el) => el.classList.remove('unread'));
      setBadge(0);
    };
    panel.querySelector('[data-act="settings"]').onclick = () => { closePanel(); openSettings(); };
    const list = panel.querySelector('.notify-list');
    try {
      const d = await api('/api/notifications?limit=20');
      setBadge(d.unread || 0);
      if (!d.items.length) {
        list.innerHTML = `<div class="notify-empty">아직 알림이 없습니다.<br>
          <button type="button" class="notify-cta">AI 봇 신호 알림 받기</button></div>`;
        list.querySelector('.notify-cta').onclick = () => { closePanel(); openSettings(); };
        return;
      }
      list.innerHTML = d.items.map((n) => `
        <a class="notify-item${n.read ? '' : ' unread'}" href="${esc(n.link || '#')}" data-id="${n.id}">
          <span class="notify-ic" aria-hidden="true">${KIND_ICON[n.kind] || '🔔'}</span>
          <span class="notify-txt">
            <span class="notify-title">${esc(n.title)}</span>
            <span class="notify-body">${esc(n.body || '')}</span>
            <span class="notify-time">${esc(ago(n.created_at))}</span>
          </span>
        </a>`).join('') +
        `<div class="notify-foot">알고리즘 검증용 가상 신호이며 실제 투자 권유가 아닙니다.</div>`;
      list.querySelectorAll('.notify-item').forEach((a) => {
        a.addEventListener('click', () => {
          if (!a.classList.contains('unread')) return;
          const body = JSON.stringify({ ids: [Number(a.dataset.id)] });
          try { navigator.sendBeacon('/api/notifications/read', new Blob([body], { type: 'application/json' })); }
          catch (e) { fetch('/api/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }); }
        });
      });
    } catch (e) {
      list.innerHTML = `<div class="notify-empty">${e.code === 401 ? '로그인이 필요합니다' : '불러오지 못했습니다'}</div>`;
    }
  }

  function closePanel() {
    if (panel) panel.classList.add('hidden');
    if (bell) bell.setAttribute('aria-expanded', 'false');
  }

  // ── 알림 설정 모달 ───────────────────────────────────────────────────────
  let modal;
  async function openSettings() {
    if (!modal) {
      modal = document.createElement('div');
      modal.className = 'modal-overlay hidden';
      modal.addEventListener('click', (ev) => { if (ev.target === modal) modal.classList.add('hidden'); });
      document.body.appendChild(modal);
    }
    modal.innerHTML = `<div class="modal-box sub-modal" role="dialog" aria-modal="true" aria-labelledby="subTitle">
      <button class="modal-close" aria-label="닫기" data-act="close">${ICON_X}</button>
      <h2 id="subTitle" class="modal-title">AI 봇 신호 알림</h2>
      <div class="sub-body"><div class="notify-empty">불러오는 중...</div></div></div>`;
    modal.classList.remove('hidden');
    const closeModal = () => modal.classList.add('hidden');
    modal.querySelector('[data-act="close"]').onclick = closeModal;
    if (!modal._escBound) {
      modal._escBound = true;
      document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && !modal.classList.contains('hidden')) closeModal(); });
    }
    const box = modal.querySelector('.sub-body');
    let d;
    try { d = await api('/api/signal-subscription'); }
    catch (e) { box.innerHTML = `<div class="notify-empty">${e.code === 401 ? '로그인이 필요합니다' : '불러오지 못했습니다'}</div>`; return; }
    const sub = d.subscription;
    box.innerHTML = `
      <label class="sub-switch">
        <input type="checkbox" id="subEnabled" ${sub.enabled ? 'checked' : ''} />
        <span>신호 알림 받기</span>
      </label>
      <fieldset class="sub-group">
        <legend>받을 봇 (여러 개 선택)</legend>
        ${d.strategies.map((s) => `
          <label class="sub-opt">
            <input type="checkbox" name="subBot" value="${esc(s.key)}" ${sub.strategies.includes(s.key) ? 'checked' : ''} />
            <span>${esc(s.emoji || '')} ${esc(s.name)}</span>
            <span class="sub-tag">${s.use_ai ? 'AI 검증' : '정량만'}</span>
          </label>`).join('')}
      </fieldset>
      <fieldset class="sub-group">
        <legend>종목 범위</legend>
        ${d.scopes.map((sc) => `
          <label class="sub-opt">
            <input type="radio" name="subScope" value="${esc(sc.key)}" ${sub.scope === sc.key ? 'checked' : ''} />
            <span>${esc(sc.label)}</span>
          </label>`).join('')}
      </fieldset>
      <div class="sub-group">
        <label for="subConf" class="sub-legend">최소 신뢰도 <strong id="subConfVal">${sub.min_confidence}</strong></label>
        <input type="range" id="subConf" min="50" max="95" step="5" value="${sub.min_confidence}" />
        <div class="sub-hint">AI 봇은 AI 확신도와 정량 점수를 합친 값, 기준선 봇은 정량 점수입니다.</div>
      </div>
      <div class="sub-msg" id="subMsg" role="status"></div>
      <div class="sub-actions">
        <button type="button" class="sub-cancel" data-act="close2">닫기</button>
        <button type="button" class="sub-save" id="subSave">저장</button>
      </div>
      <p class="sub-disclaimer">장 마감 후 하루 한 번 만들어지는 가상 신호입니다. 알고리즘 검증용 모의 데이터이며 실제 투자 권유가 아닙니다.</p>`;
    box.querySelector('[data-act="close2"]').onclick = closeModal;
    const conf = box.querySelector('#subConf');
    conf.oninput = () => { box.querySelector('#subConfVal').textContent = conf.value; };
    box.querySelector('#subSave').onclick = async () => {
      const msg = box.querySelector('#subMsg');
      const bots = [...box.querySelectorAll('input[name="subBot"]:checked')].map((i) => i.value);
      const enabled = box.querySelector('#subEnabled').checked;
      if (enabled && !bots.length) { msg.textContent = '받을 봇을 하나 이상 골라 주세요.'; msg.className = 'sub-msg err'; return; }
      const scopeEl = box.querySelector('input[name="subScope"]:checked');
      try {
        const r = await api('/api/signal-subscription', { method: 'PUT', body: JSON.stringify({
          enabled, strategies: bots, scope: scopeEl ? scopeEl.value : 'all', min_confidence: Number(conf.value) }) });
        msg.textContent = r.subscription.enabled ? '저장했습니다. 다음 장 마감 후 신호부터 알려 드립니다.' : '알림을 껐습니다.';
        msg.className = 'sub-msg ok';
        if (r.subscription.scope === 'watchlist') { try { await SAWatch.load(true); } catch (e) {} }
        if (r.subscription.scope === 'watchlist' && SAWatch.size() === 0) {
          msg.textContent += ' 관심종목이 비어 있어요. 종목 분석 화면의 ☆로 추가해 주세요.';
        }
      } catch (e) { msg.textContent = e.message || '저장 실패'; msg.className = 'sub-msg err'; }
    };
  }

  // ── 관심종목 ─────────────────────────────────────────────────────────────
  let watchSet = null;
  const SAWatch = {
    async load(force) {
      if (watchSet && !force) return watchSet;
      try {
        const d = await api('/api/watchlist');
        watchSet = new Map(d.items.map((i) => [i.ticker.toUpperCase(), i]));
      } catch (e) { watchSet = new Map(); if (e.code === 401) throw e; }
      return watchSet;
    },
    size() { return watchSet ? watchSet.size : 0; },
    has(t) { return !!(watchSet && watchSet.has(String(t).toUpperCase())); },
    async add(ticker, name) {
      const d = await api('/api/watchlist', { method: 'POST', body: JSON.stringify({ ticker, name }) });
      await this.load(true);
      return d;
    },
    async remove(ticker) {
      await api('/api/watchlist/' + encodeURIComponent(ticker), { method: 'DELETE' });
      await this.load(true);
    },
    /** 분석 화면 ☆ 버튼 연결. 로그인 안 했으면 숨김 */
    async bindStar(btn, ticker, name) {
      if (!btn) return;
      try { await this.load(); } catch (e) { btn.classList.add('hidden'); return; }
      const paint = () => {
        const on = this.has(ticker);
        btn.textContent = on ? '★' : '☆';
        btn.classList.toggle('on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        btn.title = on ? '관심종목에서 빼기' : '관심종목에 추가';
        btn.setAttribute('aria-label', btn.title);
      };
      paint();
      btn.classList.remove('hidden');
      btn.onclick = async () => {
        btn.disabled = true;
        try { this.has(ticker) ? await this.remove(ticker) : await this.add(ticker, name); }
        catch (e) { alert(e.message || '처리하지 못했습니다'); }
        btn.disabled = false;
        paint();
      };
    },
    /** 카드에 현재가·전일 대비 등락률 채우기 (상승 초록 / 하락 빨강, 사이트 색 규칙) */
    async fillQuotes(el) {
      let q = {};
      try { q = (await api('/api/watchlist/quotes')).quotes || {}; } catch (e) {}
      el.querySelectorAll('.watch-quote').forEach((box) => {
        const d = q[box.dataset.q] || {};
        box.querySelector('.watch-px').textContent = fmtPx(d.price, d.currency);
        const c = box.querySelector('.watch-chg');
        if (d.change_pct == null) { c.textContent = ''; return; }
        c.textContent = `${d.change_pct > 0 ? '+' : ''}${d.change_pct.toFixed(2)}%`;
        c.className = 'watch-chg ' + (d.change_pct > 0 ? 'up' : d.change_pct < 0 ? 'down' : 'flat');
      });
    },
    /** 메인 '관심종목' 탭 목록 */
    async renderList(el, onOpen) {
      if (!el) return;
      el.innerHTML = '<div class="pf-loading">불러오는 중...</div>';
      let m;
      try { m = await this.load(true); } catch (e) { el.innerHTML = '<div class="pf-loading">로그인이 필요합니다</div>'; return; }
      if (!m.size) {
        el.innerHTML = '<div class="watch-empty">관심종목이 없습니다. 종목 분석 화면에서 ☆를 눌러 추가하세요.<br>' +
          '<span>알림 범위를 \'관심종목\'으로 정하면 이 종목들의 봇 신호만 받습니다.</span></div>';
        return;
      }
      el.innerHTML = [...m.values()].map((i) => `
        <div class="watch-row">
          <button type="button" class="watch-open" data-t="${esc(i.ticker)}">
            <span class="watch-name">${esc(i.name)}</span><span class="watch-tk">${esc(i.ticker)}</span>
          </button>
          <span class="watch-quote" data-q="${esc(i.ticker)}"><span class="watch-px">…</span><span class="watch-chg"></span></span>
          <button type="button" class="watch-del" data-t="${esc(i.ticker)}" aria-label="${esc(i.name)} 관심종목에서 빼기">${ICON_X}</button>
        </div>`).join('');
      this.fillQuotes(el);
      el.querySelectorAll('.watch-open').forEach((b) => { b.onclick = () => onOpen && onOpen(b.dataset.t); });
      el.querySelectorAll('.watch-del').forEach((b) => {
        b.onclick = async () => {
          b.disabled = true;
          try { await this.remove(b.dataset.t); } catch (e) {}
          this.renderList(el, onOpen);
        };
      });
    },
  };
  window.SAWatch = SAWatch;
  window.SANotify = { openSettings, refresh: refreshCount };

  function init() {
    bell = document.getElementById('notifyBell');
    if (!bell) return;
    badge = document.getElementById('notifyBadge');
    bell.addEventListener('click', (ev) => {
      ev.stopPropagation();
      (panel && !panel.classList.contains('hidden')) ? closePanel() : openPanel();
    });
    refreshCount();
    startPolling();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
