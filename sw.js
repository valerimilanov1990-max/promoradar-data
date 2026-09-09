/* Промо Радар — офлайн работникът.
   Черупката се сервира от кеша мигновено (и се обновява отзад), а данните
   от фийда минават през мрежата и падат към последното изтеглено, когато
   обхват няма — точно в магазина, където приложението трябва най-много.

   Три неща тук са научени от грешки:

   1. VERSION се вдига при всяка промяна на index.html. При кеш-първо за
      черупката новата версия иначе се вижда чак при ВТОРОТО отваряне, без
      никакъв сигнал — а човек няма как да разбере, че гледа стар код.
   2. Черупката се кешира още при install. Досега тя влизаше в кеша едва
      при второто посещение: отвориш сайта веднъж, излезеш офлайн и няма
      нищо.
   3. Кешът с данни се подрязва. Индексът за търсене и етикетите са ~13 MB
      суров текст; на iOS таванът за origin е около 50 MB и мълчаливото
      изхвърляне на целия кеш е по-лошо от подрязването.
*/
"use strict";

const VERSION = "v3.12.1-currency";
const SHELL = "promoradar-shell-" + VERSION;
const DATA = "promoradar-data-" + VERSION;
const SHELL_FILES = ["./", "./index.html"];
const DATA_MAX = 40;   // записа; фийдът е ~10 файла, останалото е стар боклук

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    // Провалът тук не бива да блокира инсталацията — офлайн режимът е
    // удобство, а не условие сайтът да работи.
    try { await (await caches.open(SHELL)).addAll(SHELL_FILES); } catch (err) {}
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    const keep = new Set([SHELL, DATA]);
    const keys = await caches.keys();
    const upgrading = keys.some(k => k.startsWith("promoradar-shell-") && k !== SHELL);
    for (const k of keys) if (k.startsWith("promoradar-") && !keep.has(k)) await caches.delete(k);
    await self.clients.claim();
    // Страницата вече върви със стария код — казваме ѝ, че има нов.
    if (upgrading) for (const c of await self.clients.matchAll()) c.postMessage({ pr: "updated" });
  })());
});

async function trim(name, max) {
  const c = await caches.open(name);
  const keys = await c.keys();
  for (let i = 0; i < keys.length - max; i++) await c.delete(keys[i]);
}

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
          e.waitUntil(caches.open(DATA)
            .then(c => c.put(req, copy))
            .then(() => trim(DATA, DATA_MAX))
            .catch(() => {}));
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
            e.waitUntil(caches.open(SHELL).then(c => c.put(req, copy)).catch(() => {}));
          }
          return r;
        }).catch(() => hit);
        return hit || net;
      })
    );
  }
});
