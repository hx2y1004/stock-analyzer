// 보유 종목 공통 정렬 (홈 '내 포트폴리오' · 모의투자 '보유 종목')
//   기준 5종: 평가금액 · 수익률 · 평가손익 · 매입금액 · 종목명  +  방향(↓ 내림 / ↑ 오름)
//   금액 기준은 모두 원화 환산값으로 비교한다 (달러 종목 × 현재 환율) → 국내·해외 종목을 공정하게 비교
//   선택은 화면별 localStorage 키에 저장 (저장소를 못 쓰는 환경에서도 기본값으로 동작)
(function () {
  const KEYS = [
    { k: 'value', label: '평가금액순', dir: 'desc' },
    { k: 'ret',   label: '수익률순',   dir: 'desc' },
    { k: 'pl',    label: '평가손익순', dir: 'desc' },
    { k: 'cost',  label: '매입금액순', dir: 'desc' },
    { k: 'name',  label: '종목명순',   dir: 'asc' },
  ];
  const DEF = { key: 'value', dir: 'desc' };
  const byKey = Object.fromEntries(KEYS.map((x) => [x.k, x]));

  function load(storeKey) {
    try {
      const v = JSON.parse(localStorage.getItem(storeKey) || 'null');
      if (v && byKey[v.key] && (v.dir === 'asc' || v.dir === 'desc')) return { key: v.key, dir: v.dir };
    } catch (e) { /* 무시 */ }
    return { ...DEF };
  }
  function save(storeKey, st) {
    try { localStorage.setItem(storeKey, JSON.stringify({ key: st.key, dir: st.dir })); } catch (e) { /* 무시 */ }
  }

  // acc: { value(h), ret(h), pl(h), cost(h), name(h) } — 금액은 원화 환산값. 값이 없으면 null (방향과 상관없이 맨 뒤)
  function sort(items, st, acc) {
    const get = acc[st.key];
    const sign = st.dir === 'asc' ? 1 : -1;
    const nameCmp = (a, b) => String(acc.name(a) || '').localeCompare(String(acc.name(b) || ''), 'ko');
    return [...items].sort((a, b) => {
      if (st.key === 'name') return sign * nameCmp(a, b);
      const x = get(a), y = get(b);
      const xn = x == null || isNaN(x), yn = y == null || isNaN(y);
      if (xn || yn) return xn && yn ? nameCmp(a, b) : xn ? 1 : -1;
      return x === y ? nameCmp(a, b) : sign * (x - y);
    });
  }

  const arrow = (dir) => (dir === 'asc' ? '↑' : '↓');
  const dirLabel = (dir) => (dir === 'asc' ? '오름차순' : '내림차순');

  // [평가금액순 ▾] [↓]
  function controlHTML(id, st, ariaName) {
    return `<div class="hsort" id="${id}" role="group" aria-label="${ariaName} 정렬">
      <select class="hsort-sel" aria-label="정렬 기준">${KEYS.map((x) => `<option value="${x.k}"${x.k === st.key ? ' selected' : ''}>${x.label}</option>`).join('')}</select>
      <button type="button" class="hsort-dir" aria-label="정렬 방향: ${dirLabel(st.dir)} (눌러서 바꾸기)" title="${dirLabel(st.dir)}">${arrow(st.dir)}</button>
    </div>`;
  }
  function paint(root, st) {
    if (!root) return;
    const sel = root.querySelector('.hsort-sel'), btn = root.querySelector('.hsort-dir');
    if (sel) sel.value = st.key;
    if (btn) {
      btn.textContent = arrow(st.dir);
      btn.title = dirLabel(st.dir);
      btn.setAttribute('aria-label', `정렬 방향: ${dirLabel(st.dir)} (눌러서 바꾸기)`);
      btn.dataset.dir = st.dir;
    }
  }
  // 기준을 바꾸면 그 기준의 기본 방향으로 (종목명 → 오름차순, 나머지 → 내림차순). 같은 기준을 다시 고르면 방향만 뒤집는다(헤더 클릭용)
  function setKey(st, key, toggleIfSame) {
    if (!byKey[key]) return st;
    if (toggleIfSame && st.key === key) st.dir = st.dir === 'asc' ? 'desc' : 'asc';
    else { st.key = key; st.dir = byKey[key].dir; }
    return st;
  }
  function bind(root, st, storeKey, onChange) {
    if (!root || root.dataset.bound) return;
    root.dataset.bound = '1';
    root.querySelector('.hsort-sel').addEventListener('change', (e) => { setKey(st, e.target.value, false); save(storeKey, st); paint(root, st); onChange(); });
    root.querySelector('.hsort-dir').addEventListener('click', () => { st.dir = st.dir === 'asc' ? 'desc' : 'asc'; save(storeKey, st); paint(root, st); onChange(); });
    paint(root, st);
  }

  window.SAHoldSort = { KEYS, DEF, load, save, sort, controlHTML, paint, setKey, bind, arrow, dirLabel };
})();
