/**
 * App-shell cache only. Served at /sw.js (root scope — see routes/mobile.py)
 * so it can control /mobile without a Service-Worker-Allowed header.
 *
 * Deliberately does NOT touch POST requests or /mobile/analyze — the fetch
 * handler bails out for any non-GET request before it ever reaches
 * caches.match, so a screenshot upload always goes straight to the network.
 */
const CACHE_NAME = 'equiedge-pwa-v1';
const APP_SHELL = [
  '/mobile',
  '/static/pwa/app.js',
  '/static/pwa/style.css',
  '/static/pwa/icons/icon-192.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
