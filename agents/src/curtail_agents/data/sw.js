/* Curtail Field service worker.
 *
 * Deliberately small. It precaches the shell so the field route opens with no signal,
 * and it NEVER caches an API response: a cached measurement list would show a
 * watermaster a ledger state that is not the ledger state, and this whole surface
 * exists to keep evidence honest.
 *
 * Network-first for navigation so a deployed change reaches a device that is online,
 * cache-only as the fallback when it is not.
 */
const VERSION = "curtail-field-v1";
const SHELL = [
  "/field",
  "/manifest.webmanifest",
  "/fonts/public-sans.woff2",
  "/fonts/jetbrains-mono.woff2",
  "/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  /* API responses are never cached. Stale evidence is worse than no evidence. */
  if (url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(VERSION).then((c) => c.put(event.request, copy)).catch(() => {});
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("/field")))
  );
});
