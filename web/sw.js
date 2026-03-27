/**
 * 보세전시장 챗봇 Service Worker
 * 오프라인 캐싱 및 PWA 지원
 */

const CACHE_NAME = 'bonded-chatbot-v1';
const STATIC_ASSETS = [
  '/',
  '/static/index.html',
  '/static/manifest.json',
  '/static/icon-192.svg',
  '/static/icon-512.svg',
];

// 설치: 정적 리소스 캐싱
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// 활성화: 이전 캐시 정리
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  self.clients.claim();
});

// 요청 가로채기: 네트워크 우선, 실패 시 캐시
self.addEventListener('fetch', (event) => {
  // API 요청은 항상 네트워크로
  if (event.request.url.includes('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return new Response(
          JSON.stringify({ error: '오프라인 상태입니다. 네트워크 연결을 확인해 주세요.' }),
          { headers: { 'Content-Type': 'application/json' } }
        );
      })
    );
    return;
  }

  // 정적 리소스: 네트워크 우선, 캐시 폴백
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const responseClone = response.clone();
        caches.open(CACHE_NAME).then((cache) => {
          cache.put(event.request, responseClone);
        });
        return response;
      })
      .catch(() => {
        return caches.match(event.request);
      })
  );
});
