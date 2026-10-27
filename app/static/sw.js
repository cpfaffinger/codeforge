/* codeforge service worker: makes the scanner installable ("add to home screen") and keeps the
   page shell plus the decoder libraries cached, so the camera page opens instantly. API calls are
   always fetched from the network; the version in the cache name changes with every release. */
const VERSION = "__VERSION__";
const CACHE = "codeforge-" + VERSION;
const SHELL = [
  "/scan", "/decode", "/", "/showcase",
  "/static/style.css?v=" + VERSION, "/static/app.js?v=" + VERSION, "/static/codes.js?v=" + VERSION,
  "/static/vendor/zxing-reader.iife.js?v=" + VERSION, "/static/vendor/zxing_reader.wasm",
  "/static/vendor/zxing-library.min.js", "/static/favicon.svg", "/static/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => null)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  // API, docs and health: network only
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/docs") || url.pathname.startsWith("/redoc") || url.pathname === "/openapi.json" || url.pathname === "/healthz" || url.pathname === "/stats" || url.pathname === "/qr" || url.search.includes("bcid=")) return;
  // static assets: cache first (their URLs carry the version)
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request).then((r) => { if (r.ok) caches.open(CACHE).then((c) => c.put(event.request, r.clone())); return r; })));
    return;
  }
  // pages: network first, cached shell when offline
  event.respondWith(fetch(event.request).then((r) => { if (r.ok) caches.open(CACHE).then((c) => c.put(event.request, r.clone())); return r; }).catch(() => caches.match(event.request)));
});
