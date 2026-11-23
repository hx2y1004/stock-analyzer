// 회사 로고 공통 컴포넌트 — 모든 페이지가 notify.js 보다 먼저 불러온다.
//   renderStockLogo(ticker, name, 'xs' | 'sm' | 'md' | 'lg')  →  HTML 문자열
//   xs 22px (자동완성·비교 칩) · sm 24px (AI 봇·거래 내역) · md 32px (포트폴리오·모의투자, 촘촘한 표·카드에선 CSS 로 28px) · lg 40px (종목 상세 헤더)
//
// 구조: 이니셜 배지(티커별 고유 색)가 바닥에 깔리고, 로고 <img> 는 다 받아진 뒤에만 보인다.
//   실패(onerror)·1x1 투명 이미지(naturalWidth < 8)면 <img> 를 지워 이니셜만 남는다 → 깨진 이미지가 보이지 않는다.
//   한 번 실패한 티커는 메모리(Set)에 기록해, 다른 목록·자동완성에서는 요청 없이 바로 이니셜로 그린다 (같은 탭 안에서는 페이지를 옮겨도 유지).
// 로고 주소는 서버 LOGO_URL_TEMPLATE ({symbol}) → window.SA_LOGO_TPL. 한국 종목·지수(^)·환율(=)은 키 없는 공개 로고가 없어 이니셜만.
(function () {
  const SIZES = { xs: 22, sm: 24, md: 32, lg: 40 };
  const failed = new Set();           // 로고가 실패한 티커 (대문자) — 메모리 Set 이 기준
  const loaded = new Set();           // 성공한 티커 (디버그·테스트용)
  // 같은 탭에서 다른 페이지로 옮겨도 다시 404 를 요청하지 않게 sessionStorage 에도 적어 둔다 (탭을 닫으면 사라짐).
  // 저장소를 못 쓰는 환경(사생활 보호 모드 등)에서도 메모리 Set 만으로 동작한다.
  const KEY = 'sa_logo_failed';
  try { (JSON.parse(sessionStorage.getItem(KEY) || '[]') || []).forEach((t) => failed.add(String(t))); } catch (e) { /* 무시 */ }
  const persist = () => { try { sessionStorage.setItem(KEY, JSON.stringify([...failed].slice(-500))); } catch (e) { /* 무시 */ } };

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = (t) => String(t || '').trim().toUpperCase();

  const SALogo = {
    SIZES,
    failed,
    loaded,
    initials(ticker, name) {
      const t = norm(ticker), n = String(name || '').trim();
      if (/\.(KS|KQ)$/.test(t) || /[가-힣]/.test(n.slice(0, 2))) return (n || t).replace(/\s+/g, '').slice(0, 2);
      return t.replace(/[^A-Z0-9]/g, '').slice(0, 2) || n.slice(0, 2).toUpperCase();
    },
    hue(ticker) {
      let h = 0;
      for (const c of norm(ticker)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
      return h % 360;
    },
    url(ticker) {
      const t = norm(ticker), tpl = window.SA_LOGO_TPL;
      if (!t || !tpl || failed.has(t) || /\.(KS|KQ)$/.test(t) || /[\^=]/.test(t)) return null;
      return tpl.replace('{symbol}', encodeURIComponent(t));
    },
    // <img> 이벤트 처리 (인라인 핸들러에서 호출)
    _ok(img) {
      if (img.naturalWidth < 8) return this._fail(img);    // 1x1 투명 "없음" 이미지
      loaded.add(img.dataset.t);
      img.classList.add('ok');
    },
    _fail(img) {
      const t = img.dataset.t;
      if (t && !failed.has(t)) { failed.add(t); persist(); }
      img.remove();
    },
    html(ticker, name, size = 'md') {
      const cls = typeof size === 'string' && SIZES[size] ? size : null;
      const px = cls ? SIZES[cls] : (Number(size) || SIZES.md);
      const u = this.url(ticker);
      const t = norm(ticker);
      // 크기 클래스가 있으면 CSS 가 크기를 정한다 (화면별로 md 를 28px 로 줄이는 등). 숫자로 주면 그 크기 그대로.
      const sizeStyle = cls ? '' : `--lg-size:${px}px;`;
      return `<span class="sa-logo${cls ? ' sa-logo--' + cls : ''}" style="${sizeStyle}--lg-hue:${this.hue(t)}" aria-hidden="true">` +
        `<span class="sa-logo-init">${esc(this.initials(ticker, name))}</span>` +
        (u ? `<img src="${esc(u)}" alt="" width="${px}" height="${px}" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-t="${esc(t)}"` +
             ` onload="SALogo._ok(this)" onerror="SALogo._fail(this)">` : '') +
        `</span>`;
    },
  };
  window.SALogo = SALogo;
  window.renderStockLogo = (ticker, name, sizeClass) => SALogo.html(ticker, name, sizeClass);
})();
