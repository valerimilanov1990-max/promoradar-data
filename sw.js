/* Промо Радар — офлайн работникът.
   Черупката се сервира от кеша мигновено (и се обновява отзад), а данните
   от фийда минават през мрежата и падат към последното изтеглено, когато
   обхват няма — точно в магазина, където приложението трябва най-много. */
"use strict";

const SHELL = "promoradar-shell-v1";
const DATA = "promoradar-data-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    const keep = new Set([SHELL, DATA]);
    for (const k of await caches.keys()) if (!keep.has(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = req.url;

  if (url.includes("raw.githubusercontent.com")) {
    // данните: мрежата първо (свежи цени), кешът при липса на обхват
    e.respondWith(
      fetch(req).then(r => {
        if (r.ok) {
          const copy = r.clone();
          caches.open(DATA).then(c => c.put(req, copy));
        }
        return r;
      }).catch(() => caches.match(req).then(hit => hit || Response.error()))
    );
  } else if (req.mode === "navigate" || url.endsWith("/index.html")) {
    // черупката: кешът първо (мигновено отваряне), обновяване на заден план
    e.respondWith(
      caches.match(req).then(hit => {
        const net = fetch(req).then(r => {
          if (r.ok) {
            const copy = r.clone();
            caches.open(SHELL).then(c => c.put(req, copy));
          }
          return r;
        }).catch(() => hit);
        return hit || net;
      })
    );
  }
});
