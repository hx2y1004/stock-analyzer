/* StockAnalyzer Service Worker
 * - 정적 자산(쉘) 캐싱: cache-first
 * - API/HTML: network-first (오프라인 시 캐시 폴백)
 *
 * 캐시 버전을 올리면 사용자는 다음 방문 시 새 자산을 받습니다.
 */
const CACHE_VERSION = 'sa-v5';
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

const PRECACHE_URLS = [
  '/',
  '/trading',
  '/static/css/style.css',
  '/static/js/main.js',
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

  // API 경로: network-first (실시간성 중요)
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/analyze')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => {});
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
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => {});
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
