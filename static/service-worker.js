/* StockAnalyzer Service Worker
 * - 정적 자산(쉘) 캐싱: cache-first
 * - API/HTML: network-first (오프라인 시 캐시 폴백)
 *
 * 캐시 버전을 올리면 사용자는 다음 방문 시 새 자산을 받습니다.
 */
const CACHE_VERSION = 'sa-v93';
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

const PRECACHE_URLS = [
  '/',
  '/trading',
  '/static/css/style.css',
  '/static/js/main.js',
  '/static/js/logo.js',
  '/static/js/hsort.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-180.png',
];

// ── 설치: 핵심 정적 자원 프리캐시 ──
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

// ── 활성화: 옛 버전 캐시 정리 ──
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => !k.startsWith(CACHE_VERSION))
            .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── fetch 핸들러 ──
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // 외부 도메인은 패스 (CDN 등)
  if (url.origin !== self.location.origin) return;

  // 로그인(OAuth)·로그아웃은 서비스 워커가 손대지 않는다: 리다이렉트·쿠키 응답을 캐시하거나 오프라인 폴백으로
  // 옛 화면을 돌려주면 iOS PWA(standalone)에서 로그인이 꼬일 수 있다
  if (url.pathname.startsWith('/auth/') || url.pathname.startsWith('/login') || url.pathname.startsWith('/logout')) return;

  // 핵심 정적 자산(JS/CSS): network-first (배포 시 즉시 반영되도록)
  // 오프라인이거나 네트워크 실패 시에만 캐시 사용
  // /static/js/* 전체와 style.css/manifest 포함
  if (url.pathname.startsWith('/static/js/')
      || url.pathname === '/static/css/style.css'
      || url.pathname === '/static/manifest.json') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(STATIC_CACHE).then((c) => c.put(req, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // 폴링용 status 엔드포인트: 절대 캐싱 안 함 (실시간 진행률)
  if (url.pathname === '/api/trends/status') {
    event.respondWith(fetch(req));
    return;
  }

  // API 경로: network-first (실시간성 중요)
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/analyze')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok && !res.redirected && res.type === 'basic') {      // 정상 응답만 (리다이렉트·오류는 저장 안 함)
            const copy = res.clone();
            caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // HTML 네비게이션: network-first → 오프라인이면 캐시된 '/' 반환
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok && !res.redirected && res.type === 'basic') {      // 정상 응답만 (리다이렉트·오류는 저장 안 함)
            const copy = res.clone();
            caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(req).then((m) => m || caches.match('/')))
    );
    return;
  }

  // 정적 자산: cache-first
  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      });
    })
  );
});

// ── 웹 푸시: 서버 JSON {title, body, url, tag} → 알림 표시 ──
self.addEventListener('push', (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data ? event.data.text() : '' }; }
  const title = d.title || 'StockAnalyzer';
  const options = {
    body: d.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/badge-96.png',   // 상단바용: 투명 배경 + 흰 모양 (안드로이드는 알파만 사용)
    data: { url: d.url || '/' },
    tag: d.tag || undefined,          // 같은 tag 는 새 알림이 이전 것을 대체 (중복 방지)
    renotify: !!d.tag,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// ── 알림 클릭: 열린 앱/탭이 있으면 그 창으로 이동, 없으면 새로 열기 ──
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) {
        try { await w.focus(); } catch (e) { /* 포커스 불가 환경 */ }
        if ('navigate' in w) { try { await w.navigate(target); return; } catch (e) { /* 아래에서 새 창 */ } }
        else return;
      }
    }
    if (clients.openWindow) await clients.openWindow(target);
  })());
});

