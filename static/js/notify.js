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

  // 회사 로고는 static/js/logo.js (window.SALogo · renderStockLogo) — 이 파일보다 먼저 불러온다

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

  const KIND_ICON = { signal_new: '🟢', signal_exit: '🏁', signal_cancel: '⚪', paper_exit: '🎯', disclosure: '📢' };

  async function openPanel() {
    if (window.SAProfile) window.SAProfile.close(false);
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
      <div class="sub-group sub-disc">
        <label class="sub-switch">
          <input type="checkbox" id="subDisc" ${sub.disclosure_alerts ? 'checked' : ''} />
          <span>보유·관심종목 핵심 공시·수주·내부자 매수 알림</span>
        </label>
        <div class="sub-hint">보유(실제·모의투자)·관심종목에 한국 DART 공급계약·자사주·유상증자, 미국 SEC Form 4 장내 매수·8-K 핵심 공시가 올라오면 바로 알려 드려요. 봇 신호 알림과 따로 켜고 끕니다.</div>
      </div>
      <div class="sub-group push-box" id="pushBox">
        <div class="sub-legend">모바일/브라우저 푸시 알림</div>
        <div class="push-row">
          <span class="push-state" id="pushState">확인 중...</span>
          <button type="button" class="push-toggle" id="pushToggle" hidden>이 기기에서 푸시 받기</button>
          <button type="button" class="push-test" id="pushTest" hidden>테스트 푸시 보내기</button>
        </div>
        <div class="sub-hint" id="pushHint">앱을 닫아 두어도 이 기기로 신호 알림이 옵니다. 위에서 고른 봇·범위·신뢰도 조건을 따릅니다.</div>
        <div class="sub-msg" id="pushMsg" role="status"></div>
      </div>
      <div class="sub-msg" id="subMsg" role="status"></div>
      <div class="sub-actions">
        <button type="button" class="sub-cancel" data-act="close2">닫기</button>
        <button type="button" class="sub-save" id="subSave">저장</button>
      </div>
      <p class="sub-disclaimer">장 마감 후 하루 한 번 만들어지는 가상 신호입니다. 알고리즘 검증용 모의 데이터이며 실제 투자 권유가 아닙니다.</p>`;
    box.querySelector('[data-act="close2"]').onclick = closeModal;
    initPush(box);
    const conf = box.querySelector('#subConf');
    conf.oninput = () => { box.querySelector('#subConfVal').textContent = conf.value; };
    box.querySelector('#subSave').onclick = async () => {
      const msg = box.querySelector('#subMsg');
      const bots = [...box.querySelectorAll('input[name="subBot"]:checked')].map((i) => i.value);
      const enabled = box.querySelector('#subEnabled').checked;
      if (enabled && !bots.length) { msg.textContent = '받을 봇을 하나 이상 골라 주세요.'; msg.className = 'sub-msg err'; return; }
      const scopeEl = box.querySelector('input[name="subScope"]:checked');
      const disc = box.querySelector('#subDisc').checked;
      try {
        const r = await api('/api/signal-subscription', { method: 'PUT', body: JSON.stringify({
          enabled, strategies: bots, scope: scopeEl ? scopeEl.value : 'all', min_confidence: Number(conf.value), disclosure_alerts: disc }) });
        const on = [r.subscription.enabled ? '봇 신호' : '', r.subscription.disclosure_alerts ? '보유·관심종목 공시' : ''].filter(Boolean);
        msg.textContent = on.length ? `저장했습니다. ${on.join(' · ')} 알림을 보내 드립니다.` : '알림을 모두 껐습니다.';
        msg.className = 'sub-msg ok';
        if (r.subscription.scope === 'watchlist') { try { await SAWatch.load(true); } catch (e) {} }
        if (r.subscription.scope === 'watchlist' && SAWatch.size() === 0) {
          msg.textContent += ' 관심종목이 비어 있어요. 종목 분석 화면의 ☆로 추가해 주세요.';
        }
      } catch (e) { msg.textContent = e.message || '저장 실패'; msg.className = 'sub-msg err'; }
    };
  }

  // ── 웹 푸시 (이 기기) ───────────────────────────────────────────────────
  const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  function b64ToBytes(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }
  async function swReg() {
    // 페이지가 이미 서비스워커를 등록해 두므로 준비될 때까지 기다린다 (최대 5초)
    return Promise.race([navigator.serviceWorker.ready, new Promise((_, rej) => setTimeout(() => rej(new Error('sw')), 5000))]);
  }

  async function initPush(box) {
    const st = box.querySelector('#pushState'), tg = box.querySelector('#pushToggle'),
      tb = box.querySelector('#pushTest'), msg = box.querySelector('#pushMsg'), hint = box.querySelector('#pushHint');
    const say = (t, cls) => { msg.textContent = t || ''; msg.className = 'sub-msg' + (cls ? ' ' + cls : ''); };
    const paint = (state, label) => { st.textContent = label; st.className = 'push-state ' + state; };
    if (!pushSupported()) {
      paint('off', '이 환경은 푸시 미지원');
      hint.textContent = isIOS()
        ? 'iPhone·iPad는 Safari에서 "홈 화면에 추가"한 앱으로 열었을 때만 푸시를 받을 수 있어요 (iOS 16.4 이상).'
        : '이 브라우저는 웹 푸시를 지원하지 않아요. 크롬·엣지·삼성 인터넷·파이어폭스 최신 버전에서 이용해 주세요.';
      return;
    }
    let cfg;
    try { cfg = await api('/api/push/vapid-public-key'); } catch (e) { paint('off', '상태 확인 실패'); return; }
    if (!cfg.enabled) { paint('off', '서버 미설정'); hint.textContent = cfg.reason || '서버에 푸시 알림이 아직 설정되지 않았습니다.'; return; }
    if (isIOS() && !isStandalone()) hint.textContent = 'iPhone은 Safari 공유 버튼 → "홈 화면에 추가" 후 그 앱에서 켜야 푸시가 와요.';

    let reg, sub;
    const refresh = async () => {
      say('');
      if (Notification.permission === 'denied') {
        paint('blocked', '브라우저 알림 차단됨');
        tg.hidden = true; tb.hidden = true;
        hint.textContent = '주소창 왼쪽 자물쇠(사이트 설정) → 알림을 "허용"으로 바꾼 뒤 다시 열어 주세요.';
        return;
      }
      try { reg = await swReg(); sub = await reg.pushManager.getSubscription(); }
      catch (e) { paint('off', '서비스워커 준비 안 됨'); tg.hidden = true; tb.hidden = true; return; }
      // 서버 공개키가 바뀌었으면 예전 구독은 쓸 수 없음 → 꺼진 것으로 표시
      const keyOk = sub && sub.options && sub.options.applicationServerKey
        ? btoa(String.fromCharCode(...new Uint8Array(sub.options.applicationServerKey))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') === cfg.public_key
        : !!sub;
      const on = !!sub && keyOk;
      paint(on ? 'on' : 'off', on ? '켜짐' : '꺼짐');
      tg.hidden = false; tg.textContent = on ? '이 기기 푸시 끄기' : '이 기기에서 푸시 받기';
      tg.setAttribute('aria-pressed', on ? 'true' : 'false');
      tb.hidden = !on;
    };
    tg.onclick = async () => {
      tg.disabled = true;
      try {
        if (sub && tg.getAttribute('aria-pressed') === 'true') {
          await api('/api/push/unsubscribe', { method: 'POST', body: JSON.stringify({ endpoint: sub.endpoint }) }).catch(() => {});
          await sub.unsubscribe();
          sub = null;
          await refresh(); say('이 기기의 푸시를 껐습니다.', 'ok');
        } else {
          const perm = await Notification.requestPermission();
          if (perm !== 'granted') { await refresh(); if (perm !== 'denied') say('알림 권한을 허용해야 푸시를 받을 수 있어요.', 'err'); return; }
          if (sub) { try { await sub.unsubscribe(); } catch (e) {} }        // 예전 키로 만든 구독 정리
          sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg.public_key) });
          const r = await api('/api/push/subscribe', { method: 'POST', body: JSON.stringify({ subscription: sub.toJSON() }) });
          await refresh(); say(`켜졌습니다 (${r.device || '이 기기'}). '테스트 푸시 보내기'로 확인해 보세요.`, 'ok');
        }
      } catch (e) {
        say(e.code === 401 ? '로그인이 필요합니다.' : `푸시를 켜지 못했습니다: ${e.message || e}`, 'err');
      } finally { tg.disabled = false; }
    };
    tb.onclick = async () => {
      tb.disabled = true; say('보내는 중...');
      try {
        const r = await api('/api/push/test', { method: 'POST' });
        say(r.sent ? `테스트 푸시를 보냈습니다 (성공 ${r.sent}${r.failed ? ` · 실패 ${r.failed}` : ''}${r.removed ? ` · 만료 정리 ${r.removed}` : ''}). 몇 초 안에 도착해요.`
          : `보내지 못했습니다 (실패 ${r.failed}, 만료 정리 ${r.removed}).`, r.sent ? 'ok' : 'err');
      } catch (e) { say(e.message || '테스트 푸시 실패', 'err'); }
      finally { tb.disabled = false; }
    };
    await refresh();
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
            ${window.renderStockLogo ? renderStockLogo(i.ticker, i.name, 'md') : ''}
            <span class="watch-id"><span class="watch-name">${esc(i.name)}</span><span class="watch-tk">${esc(i.ticker)}</span></span>
          </button>
          <span class="watch-quote" data-q="${esc(i.ticker)}"><span class="watch-px">…</span><span class="watch-chg"></span></span>
          ${window.SAAlerts ? `<button type="button" class="watch-bell" data-t="${esc(i.ticker)}" data-n="${esc(i.name)}" title="알림 설정" aria-label="${esc(i.name)} 알림 설정">🔔</button>` : ''}
          <button type="button" class="watch-del" data-t="${esc(i.ticker)}" aria-label="${esc(i.name)} 관심종목에서 빼기">${ICON_X}</button>
        </div>`).join('');
      this.fillQuotes(el);
      el.querySelectorAll('.watch-open').forEach((b) => { b.onclick = () => onOpen && onOpen(b.dataset.t); });
      el.querySelectorAll('.watch-bell').forEach((b) => { b.onclick = () => window.SAAlerts && SAAlerts.open(b.dataset.t, b.dataset.n); });
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

  // ── 다른 화면(내 종목 알림)에서 쓰는 이 기기 푸시 상태·켜기 (위 initPush 와 같은 절차) ──
  window.SAPush = {
    supported: pushSupported, isIOS, isStandalone,
    /** → {supported, server, permission, subscribed, iosNeedsInstall, reason} */
    async status() {
      const out = { supported: pushSupported(), server: false, permission: 'default', subscribed: false,
        iosNeedsInstall: isIOS() && !isStandalone(), reason: null };
      if (!out.supported) return out;
      out.permission = Notification.permission;
      try {
        const cfg = await api('/api/push/vapid-public-key');
        out.server = !!cfg.enabled; out.reason = cfg.reason || null;
        if (!out.server) return out;
        const reg = await swReg();
        const sub = await reg.pushManager.getSubscription();
        const key = sub && sub.options && sub.options.applicationServerKey
          ? btoa(String.fromCharCode(...new Uint8Array(sub.options.applicationServerKey))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') : null;
        out.subscribed = !!sub && (!key || key === cfg.public_key);
      } catch (e) { /* 서비스워커 준비 안 됨 등 → 꺼짐으로 */ }
      return out;
    },
    /** 권한 요청 → 구독 → 서버 저장. → {ok, error} */
    async enable() {
      if (!pushSupported()) return { ok: false, error: isIOS() ? 'iPhone은 Safari 공유 → "홈 화면에 추가" 후 그 앱에서 알림을 허용해 주세요.' : '이 브라우저는 웹 푸시를 지원하지 않아요.' };
      try {
        const cfg = await api('/api/push/vapid-public-key');
        if (!cfg.enabled) return { ok: false, error: cfg.reason || '서버에 푸시 알림이 아직 설정되지 않았습니다' };
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') return { ok: false, error: perm === 'denied' ? '브라우저에서 알림이 차단돼 있어요. 주소창 왼쪽 사이트 설정에서 알림을 허용해 주세요.' : '알림 권한을 허용해야 푸시를 받을 수 있어요.' };
        const reg = await swReg();
        let sub = await reg.pushManager.getSubscription();
        if (sub) { try { await sub.unsubscribe(); } catch (e) {} }
        sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg.public_key) });
        const r = await api('/api/push/subscribe', { method: 'POST', body: JSON.stringify({ subscription: sub.toJSON() }) });
        return { ok: true, device: r.device };
      } catch (e) { return { ok: false, error: e.code === 401 ? '로그인이 필요합니다.' : `푸시를 켜지 못했습니다: ${e.message || e}` }; }
    },
  };
  window.SANotify = { openSettings, refresh: refreshCount };

  // ── 프로필 메뉴 (헤더 우측 아바타 → 드롭다운: 이름·이메일 · 알림 설정 · 로그아웃) ──────────────
  // 서버(_auth_area.html)가 같은 마크업을 먼저 그리고, 각 페이지 JS 가 /api/me 뒤에 SAProfile.html(user) 로 다시 그린다.
  const CARET = '<svg class="profile-caret" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function profileHTML(u) {
    u = u || {};
    const name = u.name || '사용자';
    const img = u.profile_image
      ? `<img class="profile-img" src="${esc(u.profile_image)}" alt="" onerror="this.style.visibility='hidden'" />`
      : '<span class="profile-img profile-img-ph" aria-hidden="true">👤</span>';
    return `<div class="profile-menu">
      <button type="button" class="profile-area profile-trigger" aria-haspopup="menu" aria-expanded="false" aria-controls="profileDropdown" aria-label="${esc(name)} 계정 메뉴">
        ${img}<span class="profile-name">${esc(name)}</span>${CARET}
      </button>
      <div class="profile-dropdown" id="profileDropdown" role="menu" aria-label="계정" hidden>
        <div class="pd-head"><span class="pd-name">${esc(name)}</span>${u.email ? `<span class="pd-email">${esc(u.email)}</span>` : ''}</div>
        <button type="button" class="pd-item" role="menuitem" data-act="notify">알림 설정</button>
        <a class="pd-item pd-logout" role="menuitem" href="/auth/logout">로그아웃</a>
      </div>
    </div>`;
  }
  function pmParts() {
    const t = document.querySelector('.header-right .profile-trigger');
    return t ? { t, m: t.parentElement.querySelector('.profile-dropdown') } : {};
  }
  function closeProfile(focusTrigger) {
    const { t, m } = pmParts();
    if (!m || m.hidden) return;
    m.hidden = true; t.setAttribute('aria-expanded', 'false');
    if (focusTrigger) t.focus();
  }
  function openProfile() {
    const { t, m } = pmParts();
    if (!m) return;
    closePanel();                                   // 알림 패널과 동시에 열리지 않게
    m.hidden = false; t.setAttribute('aria-expanded', 'true');
    const first = m.querySelector('[role="menuitem"]');
    if (first) first.focus();
  }
  document.addEventListener('click', (ev) => {
    const trg = ev.target.closest('.profile-trigger');
    const { m } = pmParts();
    if (trg) { ev.preventDefault(); (m && m.hidden) ? openProfile() : closeProfile(false); return; }
    if (m && !m.hidden && !ev.target.closest('.profile-dropdown')) closeProfile(false);
    const act = ev.target.closest('.profile-dropdown [data-act="notify"]');
    if (act) { closeProfile(false); openSettings(); }
  });
  document.addEventListener('keydown', (ev) => {
    const { m } = pmParts();
    if (!m || m.hidden) return;
    if (ev.key === 'Escape') { ev.preventDefault(); closeProfile(true); return; }
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      const items = [...m.querySelectorAll('[role="menuitem"]')];
      const i = items.indexOf(document.activeElement);
      const n = ev.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
      ev.preventDefault(); items[n].focus();
    }
    if (ev.key === 'Tab') closeProfile(false);
  });
  window.SAProfile = { html: profileHTML, close: closeProfile };


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
