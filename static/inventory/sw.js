// Offline support for the POS.
//
// Only static assets are cached. Pages and API responses are never stored,
// because tills are shared: a cached receipt, report or product catalogue
// would stay readable by whoever uses the browser next, even after logout.
// The POS keeps the data it needs offline in per-account local storage.

const CACHE_NAME = 'shop-pos-static-v2';
const ASSETS_TO_CACHE = [
  '/static/inventory/styles.css',
  '/static/inventory/app_hardening.js',
  '/static/inventory/offline.html',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS_TO_CACHE).catch((err) => {
      console.warn('Pre-cache error:', err);
    }))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
    ))
  );
  self.clients.claim();
});

function isCacheableAsset(url) {
  return url.origin === self.location.origin && url.pathname.startsWith('/static/');
}

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  if (isCacheableAsset(url)) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200 && response.type === 'basic') {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Pages and API calls always go to the network. If the network is gone, a
  // navigation gets the static offline notice instead of stale private data.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/static/inventory/offline.html'))
    );
  }
});
