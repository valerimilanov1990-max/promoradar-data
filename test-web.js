/* Тест на уеб версията върху ИСТИНСКИЯ фийд.
 *
 * Защо изобщо съществува: index.html е един файл без строеж и без внасяне
 * на модули — тоест няма как да се тества с обичайните средства. Тук
 * скриптът се изрязва от HTML-а, слага се в пясъчник с подправени
 * `document`, `localStorage` и `fetch` (които четат от диска), и логиката
 * се вика направо. Така всяко твърдение по-долу е проверено върху
 * 28 000-те реални имена, не върху измислени примери.
 *
 * Пускане:
 *   node test-web.js [папка-с-фийда]
 * Ако папката липсва, фийдът се тегли сам (или се вади от локалния
 * клон в _repo, ако интернет няма).
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { execFileSync } = require("child_process");

const DIR = process.argv[2] || path.join(__dirname, ".feed-cache");
const FILES = ["index", "search", "labels", "history", "digest"]
  .map(n => `feed/${n}.json`)
  .concat(["kaufland", "lidl", "dm", "ebag"].map(s => `feed/offers-${s}.json`));
const RAW = "https://raw.githubusercontent.com/valerimilanov1990-max/promoradar-data";

async function ensureFeed() {
  fs.mkdirSync(path.join(DIR, "feed"), { recursive: true });
  const missing = FILES.filter(f => !fs.existsSync(path.join(DIR, f)));
  if (!missing.length) return;
  console.log(`Тегля ${missing.length} файла от фийда…`);
  for (const f of missing) {
    const dest = path.join(DIR, f);
    try {
      const r = await fetch(`${RAW}/data/${f}`);
      if (!r.ok) throw new Error(String(r.status));
      fs.writeFileSync(dest, Buffer.from(await r.arrayBuffer()));
    } catch (e) {
      // Резерва: локалният клон в _repo. Полезна и когато няма интернет.
      try {
        const repo = path.join(__dirname, "_repo");
        fs.writeFileSync(dest, execFileSync("git",
          ["-C", repo, "show", `origin/data:${f}`],
          { maxBuffer: 1 << 28, encoding: "buffer" }));
      } catch (e2) {
        console.error(`Не можах да взема ${f}: ${e.message} / ${e2.message}`);
        process.exit(2);
      }
    }
  }
}
const HTML = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const JS = HTML.match(/<script>\n([\s\S]*)\n<\/script>/)[1];

/* ---------- пясъчник ---------- */
const noop = () => {};
const fakeEl = () => ({
  innerHTML: "", value: "", textContent: "", scrollTop: 0,
  dataset: {}, classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
  focus: noop, setSelectionRange: noop, getAttribute: () => null, click: noop,
});
const store = {};
const els = {};          // един и същ обект за едно id — за да се чете назад
const byId = id => (els[id] || (els[id] = fakeEl()));
const ctx = {
  console,
  setTimeout, clearTimeout, Math, JSON, Date, Set, Map, Object, Array, String,
  Number, RegExp, Promise, Intl, URLSearchParams, Error,
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
  location: { protocol: "file:", reload: noop },
  history: { pushState: noop, back: noop },
  navigator: {},
  document: {
    documentElement: { dataset: {} },
    getElementById: byId,
    querySelectorAll: () => [],
    addEventListener: noop,
  },
  window: { addEventListener: noop, devicePixelRatio: 1 },
  // fetch чете от диска — същите файлове, които сайтът тегли от GitHub
  fetch: async url => {
    const rel = url.split("/promoradar-data/")[1] || "";
    const p = path.join(DIR, rel.replace(/^(data|main)\//, ""));
    if (!fs.existsSync(p)) return { ok: false };
    return { ok: true, json: async () => JSON.parse(fs.readFileSync(p, "utf8")) };
  },
};
ctx.self = ctx.globalThis = ctx.window;
vm.createContext(ctx);
/* Скриптът е `const` от край до край, а `const` на върхово ниво в vm НЕ
   става свойство на контекста — затова се изнася изрично с `var`. И
   boot() не бива да тръгва сам: данните се зареждат управляемо в теста. */
const EXPORTS = ["S", "nameClean", "kindOf", "isAlcohol", "kCompatible", "catOf",
  "searchIn", "compareQuery", "rank", "applyLabels", "cleanRow", "isJunk",
  "labelFor", "typeOf", "esc", "safeUrl", "unitLbl", "matchScore",
  "render", "openDetail", "viewToday", "viewList", "viewMore",
  "iconOf", "GLYPH", "ICON_FOR", "thumb", "comparableRows",
  "suggestFor", "buildSuggest", "basketQuote", "currencyMismatch", "uncertainCurrency", "officialCurrencySafe"];
vm.runInContext(
  JS.replace(/^boot\(\);$/m, "") + `\nvar __X = {${EXPORTS.join(",")}};`,
  ctx, { filename: "index.html" });
Object.assign(ctx, ctx.__X);

const S = ctx.S;
const rd = f => JSON.parse(fs.readFileSync(path.join(DIR, "feed", f), "utf8"));

/* ---------- отчет ---------- */
let pass = 0, fail = 0;
const ok = (name, cond, detail = "") => {
  if (cond) { pass++; console.log(`  ✅ ${name}`); }
  else { fail++; console.log(`  ❌ ${name}${detail ? "\n       " + detail : ""}`); }
};
const head = t => console.log(`\n${t}\n${"─".repeat(t.length)}`);

/* ---------- зареждане ---------- */
function contractTests() {
head("10. Общ договор за количества и суми");
for (const row of JSON.parse(fs.readFileSync(path.join(__dirname, "basket-fixtures.json"), "utf8"))) {
  const quote = ctx.basketQuote({ s:"A", n:"продукт", p:row.price, u:row.unitPrice, l:"лв/"+row.unit },
    { quantity:row.quantity, unit:row.unit });
  ok(row.name, quote && quote.packs === row.packs && Math.abs(quote.total / 1.95583 - row.totalEur) < 0.001);
}
ok("Не смесва килограми и литри", ctx.basketQuote({n:"мляко",p:1,u:1,l:"лв/л"}, {quantity:1,unit:"кг"}) === null);
ok("Не включва условна цена с карта", ctx.basketQuote({n:"мляко",p:1,u:1,l:"лв/л",requiresCard:true}, {quantity:1,unit:"л"}) === null);
ok("Разпознава сгрешена валута от стар фийд", ctx.currencyMismatch("Стълба 17,49 € 35,79 €", 17.49));

ok("Скрива неясна валута в стария Кауфланд", ctx.uncertainCurrency("Кауфланд","Козметика",4.39,9.20,52,""));
ok("Приема изрично нормализирана валута", !ctx.uncertainCurrency("Кауфланд","Козметика",8.59,17.99,52,"BGN"));
ok("Не приема нечислово количество", ctx.basketQuote({n:"мляко",p:1,u:1,l:"лв/л"}, {quantity:NaN,unit:"л"}) === null);
const milk = {s:"ТАРИТА", n:"ПРЯСНО МЛЯКО БОР-ЧВОР 3% 1л", f:1,
  p:1.90, u:1.90, l:"лв/л", qty:1, qtyUnit:"л", currency:"BGN",
  sourcePrice:.97, sourceCurrency:"EUR", sourceDate:"2026-09-08", normalization:"kzp-currency-v1"};
const milkQuote = ctx.basketQuote(milk, {quantity:1,unit:"л"});
ok("Мляко 0.97 EUR от КЗП струва 0.97 EUR, не 0.50", milkQuote && Math.abs(milkQuote.total / 1.95583 - .97) < .001);
ok("Два литра са две опаковки за 1.94 EUR", Math.abs(ctx.basketQuote(milk,{quantity:2,unit:"л"}).total / 1.95583 - 1.94) < .001);
ok("Отхвърля стар официален кеш без маркер", !ctx.officialCurrencySafe({...milk,normalization:undefined,p:.97}));
ok("Отхвърля двойно превалутиране и с наличен маркер", !ctx.officialCurrencySafe({...milk,p:.97}));
ok("Корекцията не променя други източници", ctx.officialCurrencySafe({f:0,p:.97}));
}
async function main() {
if (process.argv.includes("--contract-only")) { contractTests(); console.log(pass+" passed; "+fail+" failed"); process.exit(fail ? 1 : 0); }
await ensureFeed();
const t0 = Date.now();
S.idx = rd("index.json");
ctx.applyLabels(rd("labels.json"));
S.search = rd("search.json").items.filter(i => i.n && ctx.officialCurrencySafe(i) && !ctx.uncertainCurrency(i.s, i.n, i.p, i.o, null, i.currency)).map(ctx.cleanRow);
S.history = rd("history.json");
const rawOffers = [];
for (const s of S.idx.stores) {
  const f = rd(`offers-${s.slug}.json`);
  for (const o of f.offers) if (!ctx.isJunk(o)) {
    rawOffers.push({ ...o });
    o.rawName = o.name; o.name = ctx.nameClean(o.name);
    S.all.push(o);
  }
}
console.log(`Заредено за ${Date.now() - t0} ms · ${S.all.length} оферти · ` +
  `${S.search.length} реда в индекса · ${Object.keys(S.labels).length} етикета ` +
  `(${S.labelAliases} свързани по почистено име)`);

/* =================================================================== */
head("1. NameClean — счупените имена");

const CASES = [
  ["Омарот нашата витринакг-20% отстъпка с", "Омар"],
  ["Мерлуза филеот нашата витринакг", "Мерлуза филе"],
  ["TuborgБира0,5 л кен", "Tuborg Бира 0,5 л кен"],
  // Хомоглифи: латинско E вътре в кирилска дума. Наивното правило ги
  // накъсва на срички — заради това границата иска по 2 знака.
  ["Raid Eлектрически комплект", "Raid Eлектрически комплект"],
  // „Нашата Трапеза“ е МАРКА колбаси, не рекламна фраза.
  ["Нашата Трапеза Шпек ТВПС", "Нашата Трапеза Шпек ТВПС"],
];
for (const [inp, want] of CASES)
  ok(`„${inp.slice(0, 34)}…“`, ctx.nameClean(inp) === want,
     `получих: „${ctx.nameClean(inp)}“, чаках: „${want}“`);

const glued = /([a-zа-яё])([A-ZА-ЯЁ][a-zа-яё])|([A-Za-z]{2})([А-Яа-яЁё]{2})/;
const before = rawOffers.filter(o => glued.test(o.name)).length;
const after = S.all.filter(o => glued.test(o.name)).length;
ok(`Слепени имена в офертите: ${before} → ${after}`, after <= before);

const tails = S.all.filter(o => /отстъпка\s*с?\s*$|цена с карта$/i.test(o.name));
ok(`Рекламни опашки в края: ${tails.length}`, tails.length === 0,
   tails.slice(0, 3).map(o => o.name).join(" | "));

const emptied = S.all.filter(o => o.name.trim().length < 3);
ok(`Изядени до празно имена: ${emptied.length}`, emptied.length === 0);

/* =================================================================== */
head("2. Kinds — алкохолът");

ok("„Салата Тракия“ не е ракия", !ctx.isAlcohol("Салата Тракия"));
ok("„Кашкавал Верея“ не е алкохол", !ctx.isAlcohol("Кашкавал Верея 400 г"));
ok("„Ромашка чай“ не е ром", !ctx.isAlcohol("Ромашка билков чай"));
ok("„Зехтин върджин“ не е джин", !ctx.isAlcohol("Зехтин Extra Virgin 1 л"));
ok("„Шоколад с кола“ не е безалкохолно", ctx.kindOf("Шоколад Милка") !== "bezalk");
ok("„Ракия Пещерска“ Е алкохол", ctx.isAlcohol("Ракия Пещерска гроздова 700 мл"));
ok("„Безалкохолна бира“ НЕ е алкохол", !ctx.isAlcohol("Безалкохолна бира Загорка 0,0%"));
ok("Сок и ракия са несъвместими",
   !ctx.kCompatible("Aspasia Негазирана напитка", "СП.НАПИТКА РАКИЯ ГРОЗДЕН"));

// Колко от индекса изобщо носи признак „алкохол“ от AI-я
let v2 = 0, alc = 0;
for (const k in S.labels) { const l = S.labels[k]; if ((l.v || 1) >= 2) { v2++; if (l.a === 1) alc++; } }
console.log(`     етикети с промпт v2: ${v2} · от тях алкохол: ${alc}`);

/* =================================================================== */
head("3. „Цените навсякъде“ — сравнението");

function cmp(query, strict) {
  const hit = ctx.searchIn(S.search, query, 5, false)[0];
  if (!hit) return null;
  const [ev, kw] = ctx.compareQuery(hit.n, strict);
  return { hit, ev, kw };
}
for (const q of ["негазирана напитка", "прясно мляко", "тоалетна хартия"]) {
  const r = cmp(q, false);
  if (!r) { ok(`„${q}“ — има какво да се сравни`, false); continue; }
  const bad = r.ev.filter(h => ctx.isAlcohol(h.n) !== ctx.isAlcohol(r.hit.n));
  ok(`„${q}“ → ${r.ev.length} вериги по „${r.kw}“, 0 несъвместими`,
     r.ev.length >= 1 && bad.length === 0,
     bad.length ? "промъкнало се: " + bad.map(h => h.n).join(" | ") : "нищо не се намери");
}

// Целият индекс: може ли изобщо да излезе алкохол до безалкохолно
let checked = 0, leaks = 0;
for (let i = 0; i < S.search.length && checked < 250; i += 97) {
  const it = S.search[i];
  if (ctx.catOf(it.n) !== "napit") continue;
  checked++;
  const [ev] = ctx.compareQuery(it.n, false);
  for (const h of ev) if (ctx.isAlcohol(h.n) !== ctx.isAlcohol(it.n)) { leaks++; break; }
}
ok(`${checked} напитки проверени, ${leaks} с промъкнал се алкохол`, leaks === 0);

/* =================================================================== */
head("3б. Съкращенията — „протеин“ не бива да вади паста за зъби");

/* Магазините съкращават всичко, затова търсенето приема и обратното
   съвпадение: дума в името, която е НАЧАЛО на заявката („КАШК.“ за
   кашкавал). Точно това правило правеше „PROT&CLEAN“ и
   „КОМПЛ.ПРОТ.УАЙТЪНИНГ“ попадения за „протеин“. */
const found = (q, sub) => ctx.searchIn(S.search, q, 400, false)
  .filter(h => h.n.toUpperCase().includes(sub.toUpperCase()));

for (const [q, bad] of [["протеин", "PROT&CLEAN"], ["протеин", "ПРОТ.УАЙТЪНИНГ"],
                        ["паста", "паст. мляко"], ["шампоан", "ЦИГАРИ"]]) {
  const hits = found(q, bad);
  ok(`„${q}“ вече не намира „${bad}“`, hits.length === 0,
     hits.slice(0, 2).map(h => h.n).join(" | "));
}
// А истинските съкращения трябва да оцелеят — иначе поправката е по-лоша
// от бъга. Всяко от тях е реален ред от фийда.
for (const [q, good] of [["кашкавал", "КАШК."], ["шампоан", "ШАМП."],
                         ["лютеница", "ЛЮТЕН."], ["бисквити", "БИСКВ."],
                         ["картофи", "КАРТОФ ПРОИЗХОД"]]) {
  ok(`„${q}“ още намира „${good}“`, found(q, good).length > 0);
}
// Основните стоки не бива да губят НИТО ЕДНО попадение
for (const q of ["мляко", "хляб", "яйца", "олио", "кафе", "сирене", "захар", "ориз"]) {
  const n = ctx.searchIn(S.search, q, 4000, false).length;
  ok(`„${q}“ — ${n} попадения, покритието не пострада`, n >= 10);
}
const prot = ctx.searchIn(S.search, "протеин", 400, false);
console.log(`     „протеин“ → ${prot.length} резултата, първите: ` +
  prot.slice(0, 3).map(h => h.n.slice(0, 34)).join(" | "));

/* =================================================================== */
head("3в. Марка срещу стока — „орехи“ не бива да вади шпек");

/* „Орехите“ е марка колбаси, „Медикс“ започва с „мед“, „Лукчета“ с „лук“,
   „Кафеавтомат“ с „кафе“. Лексикално всички са коректни попадения —
   „орехите“ Е членувано множествено на „орех“. Разграничава ги само
   смисълът, тоест типът и марката от AI етикета. */
const findIn = (q, sub) => ctx.searchIn(S.search, q, 400, false)
  .filter(h => h.n.toUpperCase().includes(sub.toUpperCase()));

for (const [q, bad, why] of [
  ["мед", "Медикс за Съдове", "марка препарати"],
  ["лук", "ЛУКЧЕТА", "таблетки за смучене"],
  ["лук", "Сапун Лукс", "сапун"],
  ["кафе", "Кафеавтомат", "уред, не кафе"],
  ["олио", "ОЛИОСЕТА", "шампоан"],
  ["хляб", "БРАШНО ДОБР ХЛЯБ", "брашно с марка „Добр хляб“"],
  ["праскови", "Шардоне", "вино с вкус на праскова"],
]) {
  const hits = findIn(q, bad);
  ok(`„${q}“ вече не намира „${bad}“ (${why})`, hits.length === 0,
     hits.slice(0, 2).map(h => h.n).join(" | "));
}

// А търсенето ПО МАРКА трябва да оцелее — иначе поправката е по-лоша от
// бъга. „Милка“ и „Верея“ са марки и почти всичките им попадения
// съвпадат само по марка.
for (const [q, min] of [["верея", 50], ["милка", 100], ["олинеза", 30]]) {
  const n = ctx.searchIn(S.search, q, 400, false).length;
  ok(`Търсенето по марка „${q}“ работи (${n} резултата)`, n >= min);
}
// Основните стоки не бива да губят покритие.
for (const q of ["мляко", "хляб", "сирене", "яйца", "захар", "боб", "вода", "бира"]) {
  const n = ctx.searchIn(S.search, q, 400, false).length;
  ok(`„${q}“ — ${n} попадения, покритието е цяло`, n >= 100);
}
// Редовете без етикет не се пипат: при липса на данни се пуска, не се гадае.
{
  const fake = [{ s: "Тест", n: "Нещо съвсем непознато орехи", p: 1 },
                { s: "Тест2", n: "Орехи белени 200 г", p: 2 }];
  fake.forEach(ctx.cleanRow);
  ok("Ред без етикет остава в резултата",
     ctx.searchIn(fake, "орехи", 40, false).length === 2);
}
/* Измерване на остатъка, не твърдение че е нула.
   „Орехи“ остава дефектна заявка и това е ЧЕСТНО да се знае: в целия
   каталог има 2 истински ореха срещу ~50 шпека на марка „Орехите“, а
   AI-ят е сложил марката вътре в типа („шпек орехите“). Никаква логика
   над етикета не поправя грешен етикет — поправено е в промпта на
   скрейпъра, значи важи от следващото преетикетиране. */
const nuts = ctx.searchIn(S.search, "орехи", 400, false);
const sausage = nuts.filter(h => /шпек|салам|колбас/i.test(h.n)).length;
console.log(`     „орехи“ → ${nuts.length} резултата, от тях ${sausage} колбаси ` +
  `(в каталога има 2 истински ореха; етикетът им казва „шпек орехите“)`);
ok(`„орехи“ — колбасите паднаха под 40 (${sausage})`, sausage < 40,
   "останалите носят марката вътре в типа — поправено е в промпта");
{
  // Редовете, чийто тип е само „шпек“ (без думата в него), вече отпадат.
  const clean = nuts.filter(h => {
    const l = ctx.labelFor(h.n);
    return l && l.t && !/орех/i.test(l.t) && /шпек|салам/i.test(l.t);
  });
  ok(`„орехи“ — 0 колбаси с ЧИСТ етикет (${clean.length})`, clean.length === 0,
     clean.slice(0, 2).map(h => h.n).join(" | "));
}

/* =================================================================== */
head("3г. Подсказки при писане — да се вижда какво избираш");

/* Търсенето по дума не може да познае намерението: „орехи“ съвпада и с
   ядките, и с колбасите „Орехите“. Подсказките показват двете като
   отделни избора вместо приложението да гадае. */
{
  const sug = ctx.suggestFor("орех", 6);
  console.log("     „орех“ → " + sug.map(e =>
    `${e.t} (${e.kind === "b" ? "марка/" : ""}${e.cat}, ${e.n})`).join(" | "));
  ok("„орех“ предлага и ядката, и марката колбаси",
     sug.some(e => /^орехи/.test(e.t) && e.cat !== "meso") &&
     sug.some(e => e.cat === "meso"),
     "точно това прави разликата видима вместо да я гадаем");

  const mlyako = ctx.suggestFor("мляко", 6);
  console.log("     „мляко“ → " + mlyako.map(e => `${e.t} (${e.n})`).join(" | "));
  ok("„мляко“ разделя киселото от прясното",
     mlyako.some(e => e.t.includes("кисело")) && mlyako.some(e => e.t.includes("прясно")));
  ok("Разфасовките са слети — няма „кисело мляко 2%“ и „3.6%“ поотделно",
     !mlyako.some(e => /\d/.test(e.t)));

  for (const [q, want] of [["кашк", "кашкавал"], ["шам", "шампоан"],
                           ["сол", "сол"], ["лук", "лук"], ["милка", "милка"]]) {
    const r = ctx.suggestFor(q, 6);
    ok(`„${q}“ предлага „${want}“`, r.some(e => e.t === want),
       r.map(e => e.t).join(" | "));
  }
  // Най-много две от категория — иначе списъкът се пълни с разновидности
  // на едно и също и различните неща не се виждат.
  for (const q of ["мляко", "кашк", "хл", "дом", "кафе"]) {
    const r = ctx.suggestFor(q, 6);
    const per = {};
    r.forEach(e => { const k = e.kind + e.cat; per[k] = (per[k] || 0) + 1; });
    ok(`„${q}“ — до 2 от категория (${r.length} предложения)`,
       Object.values(per).every(v => v <= 2));
  }
  ok("Под 2 знака няма подсказки", ctx.suggestFor("м", 6).length === 0);
  // Речникът се строи лениво при първата подсказка. Проверява се по
  // резултата, не по вътрешната променлива — тя се изнася по стойност и
  // към момента на изнасяне още е празна.
  const broad = ["мляко", "хляб", "кашк", "шам", "бир", "сол", "кафе", "чай"]
    .map(q => ctx.suggestFor(q, 6).length);
  ok(`Речникът работи за всички широки думи (${broad.join(",")})`,
     broad.every(n => n > 0));
}

/* =================================================================== */
head("4. Кошницата — честна ли е сметката");

S.list = ["прясно мляко", "хляб", "яйца", "олио", "кашкавал", "омар"];
const tRank = Date.now();
await ctx.rank();
console.log(`     сметнато за ${Date.now() - tRank} ms`);

ok("Класацията не е празна", S.ranking.length > 0);
ok("Подредена е по ПОКРИТИЕ, после по сума",
   S.ranking.every((r, i) => i === 0 || S.ranking[i - 1][2] > r[2] ||
     (S.ranking[i - 1][2] === r[2] && S.ranking[i - 1][1] <= r[1])));
ok("Никоя верига не покрива повече артикули, отколкото са в списъка",
   S.ranking.every(r => r[2] <= S.list.length));

const top = S.ranking[0];
console.log(`     печели: ${top[0]} — ${(top[1] / 1.95583).toFixed(2)} € за ${top[2]} от ${S.list.length}`);
const missing = S.list.filter(it => !(S.perItem[it] || []).some(h => h.s === S.chosen));
ok(`Липсващото си личи: ${missing.length ? missing.join(", ") : "нищо не липсва"}`,
   missing.length === S.list.length - top[2]);
ok("„омар“ не се намира навсякъде (рядка стока — очаквано)",
   (S.perItem["омар"] || []).length < (S.perItem["хляб"] || []).length);

// Изборът във всяка верига е по цена за ЕДИНИЦА, не по номинална.
//
// Не се сравнява с целия индекс: филтърът по тип нарочно изхвърля част от
// попаденията („ИЗВАРА КАШКАВАЛЕНА“ при заявка „кашкавал“ е извара, не
// кашкавал), значи по-евтин ред в същата верига може да е законно
// отпаднал. Проверяват се двете неща, които наистина се твърдят:
// по един ред на верига и подредба по цена за единица.
let oneEach = true, sorted = true, ex = "";
for (const it of S.list) {
  const rows = S.perItem[it] || [];
  const stores = new Set(rows.map(h => h.s));
  if (stores.size !== rows.length) { oneEach = false; ex = `${it}: ${rows.length} реда, ${stores.size} вериги`; }
  for (let i = 1; i < rows.length; i++)
    if (rows[i - 1].total > rows[i].total + 1e-9) {
      sorted = false; ex = `${it}: ${rows[i - 1].n} преди ${rows[i].n}`;
    }
}
ok("По един ред на верига, подредени по СУМА ЗА НУЖНИТЕ ОПАКОВКИ", oneEach && sorted, ex);

// Същото твърдение, но върху нагласени данни, където отговорът е известен:
// бутилка 200 мл за 0,79 не бива да „бие“ литър за 1,99.
const savedSearch = S.search, savedList = S.list;
S.search = [
  { s: "Тест", n: "Вода изворна 0,2 л бутилка", p: 0.79, u: 3.95, l: "лв/л" },
  { s: "Тест", n: "Вода изворна 1 л бутилка", p: 1.99, u: 1.99, l: "лв/л" },
];
S.list = ["вода"];
await ctx.rank();
const pick = (S.perItem["вода"] || [])[0];
ok("Литър за 1,99 бие 200 мл за 0,79", pick && pick.p === 1.99,
   pick ? `избра: ${pick.n}` : "нищо не избра");
S.search = savedSearch; S.list = savedList;

// Филтърът по тип: „мляко“ не бива да се срине до сирене
S.list = ["мляко"];
await ctx.rank();
const mlek = S.perItem["мляко"] || [];
const cheese = mlek.filter(h => ctx.kindOf(h.n) === "sirene").length;
ok(`„мляко“ → ${mlek.length} вериги, от тях ${cheese} сирене`,
   mlek.length >= 10 && cheese * 4 <= mlek.length,
   mlek.slice(0, 3).map(h => h.n).join(" | "));

/* =================================================================== */
head("4б. Кошницата — съпоставимост, не лотария");

/* Случаят от екрана: „спестяваш 13,08 €“ при кошница за 2,45 €.
   Причината беше, че се сравняваше ред БЕЗ обявено количество
   („БУЛГАРЧЕ Кашкавал 6,99 €“ — неизвестно колко) с ред от 200 грама.
   Това не е сравнение, а лотария. */
S.list = ["кашкавал", "хляб"];
await ctx.rank();
{
  const full = S.ranking.filter(r => r[2] === S.list.length);
  const sums = full.map(r => r[1]).sort((a, b) => a - b);
  const median = sums[Math.floor(sums.length / 2)];
  const save = median - full[0][1];
  console.log(`     ${full.length} вериги с пълна кошница · печели ${full[0][0]} ` +
    `${(full[0][1] / 1.95583).toFixed(2)} € · медиана ${(median / 1.95583).toFixed(2)} €`);

  for (const it of S.list) {
    const m = S.mode[it];
    ok(`„${it}“ се сравнява по цена за ${m.label || "опаковка"}`, m.unit === true,
       "без мярка сравнението е между различни разфасовки");
    // Ред без обявено количество няма как да е съпоставим с ред, който го има.
    const noUnit = (S.perItem[it] || []).filter(h => h.u == null);
    ok(`„${it}“ — 0 реда без мярка в сметката (${(S.perItem[it] || []).length} вериги)`,
       noUnit.length === 0, noUnit.slice(0, 2).map(h => h.s + ": " + h.n).join(" | "));
    // Сгрешена мярка („44,75 лв/кг кашкавал“) не е изгодна оферта.
    const us = (S.perItem[it] || []).map(h => h.u).sort((a, b) => a - b);
    const med2 = us[Math.floor(us.length / 2)];
    ok(`„${it}“ — няма стойност извън 3× от медианата`,
       us[0] >= med2 / 3 && us[us.length - 1] <= med2 * 3,
       `най-ниска ${us[0]}, медиана ${med2}, най-висока ${us[us.length - 1]}`);
  }

  // Спестяването е спрямо ОБИЧАЙНОТО, не спрямо най-скъпия екземпляр —
  // иначе излизаше по-голямо от самата кошница.
  ok(`Спестяването (${(save / 1.95583).toFixed(2)} €) е под сумата на кошницата`,
     save < full[0][1], "число, по-голямо от кошницата, само подяжда доверието");
  ok("Спестяването не е отрицателно", save >= 0);
}

// Изгледът трябва да КАЗВА какво е сравнено, не само да сипе числа.
S.tab = "list"; ctx.render();
{
  const h = els.view.innerHTML;
  ok("Пише какво точно се сравнява", h.includes("според зададените количества"),
     "иначе човек гледа шест непознати магазина и не разбира числата");
  ok("Победителят е отделен и натискаем", h.includes('class="win"') && h.includes("data-act=\"chain:"));
  ok("Останалите показват РАЗЛИКАТА, не само сума", /\+\d+\.\d\d €/.test(h));
  ok("Показва брой опаковки и цена на всяка",
     h.includes("опак. ×"));
  ok("Под артикула се вижда какво е в ИЗБРАНАТА верига", h.includes("sub here"));
  const dirty2 = (h.match(/\bundefined\b|\bNaN\b/g) || []);
  ok("Без „undefined“ и „NaN“ по числата", dirty2.length === 0, dirty2.join(" "));
}
S.tab = "today"; S.list = []; S.perItem = null; S.ranking = null; S.chosen = null;

/* =================================================================== */
head("5. Етикетите и категориите");

let withLab = 0;
for (let i = 0; i < S.search.length; i += 7)
  if (ctx.labelFor(S.search[i].n)) withLab++;
const pct = Math.round(withLab * 700 / S.search.length);
ok(`Покритие на етикетите след почистване на имената: ~${pct}%`, pct >= 90,
   "мостът към етикетите не работи — сравнението пада наполовина");

let withType = 0;
for (let i = 0; i < S.search.length; i += 7)
  if (ctx.typeOf(S.search[i].n)) withType++;
console.log(`     от тях с попълнен тип (поле t): ~${Math.round(withType * 700 / S.search.length)}%`);

const tCat = Date.now();
for (const o of S.all) ctx.catOf(o.name);
const cold = Date.now() - tCat;
const tCat2 = Date.now();
for (const o of S.all) ctx.catOf(o.name);
ok(`catOf по ${S.all.length} оферти: ${cold} ms студено, ${Date.now() - tCat2} ms от кеша`,
   Date.now() - tCat2 <= 5);

/* =================================================================== */
head("6. Сигурност — екранирането");

ok("esc() пази чупенето на атрибут", ctx.esc('a"><img src=x onerror=1>') ===
   "a&quot;&gt;&lt;img src=x onerror=1&gt;");
ok("javascript: URL се отхвърля", ctx.safeUrl("javascript:alert(1)") === "");
ok("https: URL минава", ctx.safeUrl("https://kaufland.bg/x") === "https://kaufland.bg/x");
ok("Мерната единица се екранира", ctx.unitLbl('лв/кг"><b>').includes("&quot;"));
const badUrls = S.all.filter(o => o.url && !ctx.safeUrl(o.url)).length;
ok(`Линкове във фийда без https: ${badUrls}`, badUrls === 0);

/* =================================================================== */
head("7. Рисуване — тримата екрана и детайлът");

/* Гърми ли изобщо, и излиза ли „undefined“ на екрана. Второто е по-
   коварното: една липсваща стойност в шаблонен низ не хвърля грешка, а
   просто изписва „undefined“ насред картата. */
const dirty = h => (h.match(/\bundefined\b|\bNaN\b|\[object Object\]/g) || []).slice(0, 3);
const balanced = h => {
  const o = (h.match(/<(?!\/)[a-z][^>]*?(?<!\/)>/g) || [])
    .filter(t => !/^<(img|input|br|hr|meta|link)\b/i.test(t)).length;
  const closed = (h.match(/<\/[a-z][a-z0-9]*>/gi) || []).length;
  return o === closed;
};
for (const [tab, extra] of [["today", () => {}], ["list", () => {}], ["more", () => {}]]) {
  S.tab = tab; extra();
  let err = null;
  try { ctx.render(); } catch (e) { err = e; }
  const h = els.view.innerHTML;
  ok(`Екран „${tab}“ се рисува (${h.length} знака)`, !err && h.length > 100,
     err ? String(err) : "празно");
  ok(`Екран „${tab}“ без „undefined“ по картите`, dirty(h).length === 0, dirty(h).join(" | "));
  ok(`Екран „${tab}“ със затворени тагове`, balanced(h));
}

S.tab = "today"; S.query = "мляко"; ctx.render();
ok("Търсенето рисува резултати", (els.view.innerHTML.match(/class="card"/g) || []).length > 5);
{
  // Броят се само плочките ВЪТРЕ в картите — подсказките отгоре също
  // имат образ и иначе изкривяват сметката.
  const cards = els.view.innerHTML.split('class="card"').slice(1);
  const withThumb = cards.filter(c => c.slice(0, 400).includes('class="th')).length;
  ok(`Всеки ред в търсенето има образ (${withThumb} от ${cards.length})`,
     cards.length > 0 && withThumb === cards.length);
}
S.query = "";

// Детайл: върху продукт, за който ЗНАЕМ, че има история — иначе тестът
// минава, без да е пипнал графиката.
const withHist = S.all.find(o =>
  S.history.h[o.store.trim().toLowerCase() + "|" +
    (o.rawName || o.name).replace(/\s+/g, " ").trim().toLowerCase().slice(0, 60)]);
ok("Има оферта със запазена история (ключът се строи от СУРОВОТО име)", !!withHist,
   "ако това падне, графиката е вечно празна след NameClean");
let derr = null;
try { ctx.openDetail({ t: "offer", s: (withHist || S.all[0]).store, n: (withHist || S.all[0]).name }); }
catch (e) { derr = e; }
const sh = els.sheet.innerHTML;
ok(`Детайлът се рисува (${sh.length} знака)`, !derr && sh.length > 200, derr ? String(derr) : "");
ok("Детайлът показва „Цените навсякъде“", sh.includes("Цените навсякъде"));
ok("Детайлът без „undefined“", dirty(sh).length === 0, dirty(sh).join(" | "));

// Натискаемите редове има смисъл да се проверят само върху продукт, за
// който сравнението НАИСТИНА е намерило други вериги — иначе тестът
// минава върху празния случай и не доказва нищо.
const cmpAble = S.all.find(o => ctx.compareQuery(o.name, false)[0].length >= 2);
ok("Има продукт с реално сравнение", !!cmpAble);
if (cmpAble) {
  ctx.openDetail({ t: "offer", s: cmpAble.store, n: cmpAble.name });
  const sh2 = els.sheet.innerHTML;
  ok(`Редовете в сравнението са НАТИСКАЕМИ („${cmpAble.name.slice(0, 30)}“)`,
     sh2.includes('role="button"') && sh2.includes("data-open="));
  ok("Има бутон за добавяне в списъка", sh2.includes("data-addlist"));
  ok("Няма линк с непроверена схема", !/href="(?!https?:)/.test(sh2));
}
if (withHist) ok("Графиката се появява при намерена история",
  sh.includes("История на цената") || sh.includes("следим тази цена"));

/* =================================================================== */
head("7б. Иконите — има ли всеки продукт образ");

/* Снимка има за 1 120 продукта; останалите 27 000 идват от ценоразписите
   на КЗП, където снимки няма. Затова обликът се рисува по типа от AI.
   Тук се мери КОЛКО от тях получават своя иконка, а не общата на
   категорията — това е разликата между „разпознавам стоката“ и
   „поредната кутия“. */
const CAT_FALLBACK = new Set(["fruit", "meat", "milk", "bread", "jar", "juice", "choco",
  "icecream", "cream", "clean", "pan", "shirt", "paw", "baby", "cigs", "box"]);
let specific = 0, generic = 0, boxed = 0;
const noIcon = {};
for (const it of S.search) {
  const t = ctx.typeOf(it.n).toLowerCase().split(" ")[0];
  const ic = ctx.iconOf(it.n);
  const byType = t && ctx.ICON_FOR[t];
  if (byType) specific++;
  else if (ic.g === ctx.GLYPH.box) { boxed++; if (t) noIcon[t] = (noIcon[t] || 0) + 1; }
  else generic++;
}
const pctSpec = Math.round(specific * 100 / S.search.length);
const pctBox = Math.round(boxed * 100 / S.search.length);
console.log(`     своя иконка: ${specific} (${pctSpec}%) · по категория: ${generic} · ` +
  `безлична кутия: ${boxed} (${pctBox}%)`);
ok(`Поне 80% от каталога получава СВОЯ иконка (${pctSpec}%)`, pctSpec >= 80);
ok(`Под 3% остават безлична кутия (${pctBox}%)`, pctBox < 3);
ok("Всеки продукт получава нещо за рисуване",
   S.search.every(it => { const g = ctx.iconOf(it.n).g; return typeof g === "string" && g.length > 20; }));
ok("Всеки глиф е валиден SVG (затворени тагове, само path/circle)",
   Object.values(ctx.GLYPH).every(g =>
     /^(<(path|circle)\s[^>]*\/>)+$/.test(g.replace(/\s+/g, " ").trim())));
ok("Няма вид, сочещ към несъществуващ глиф",
   Object.values(ctx.ICON_FOR).every(g => ctx.GLYPH[g]));
const topMiss = Object.entries(noIcon).sort((a, b) => b[1] - a[1]).slice(0, 8);
if (topMiss.length) console.log(`     без иконка, най-чести: ${topMiss.map(([w, n]) => w + "(" + n + ")").join(", ")}`);
// Конкретни примери — иконата трябва да е ТОЧНАТА, не приблизителната
for (const [n, want] of [["Ракия Пещерска гроздова 700 мл", "bottle"],
                         ["Прясно мляко Верея 3.6% 1 л", "milk"],
                         ["75 МЛ ПЗ ORAL-B PROT&CLEAN", "tube"],
                         ["Четка за зъби Oral-B", "brush"],
                         ["Краве масло Президент 250 г", "butter"],
                         ["Гел за съдове Fairy 900 мл", "clean"],
                         ["Хляб Добруджа пълнозърнест", "bread"],
                         ["Кашкавал от краве мляко 400 г", "cheese"]]) {
  ok(`„${n.slice(0, 30)}“ → ${want}`, ctx.iconOf(n).g === ctx.GLYPH[want]);
}

// Двусмислените думи — всеки от тези беше сбъркан при първото пускане и
// се хвана само защото иконите бяха разгледани върху истинския фийд.
for (const [n, want] of [["Червено грозде без семки", "fruit"],
                         ["Каберне Совиньон Мезек 750 мл", "bottle"],
                         ["Попче прясно", "fish"],
                         ["Хризантема Ø14 см", "veg"],
                         ["Пълнозърнеста юфка домашна", "pasta"],
                         ["MY SUSHI Суши комбо макси", "fish"],
                         ["MILKA Млечна напитка различни вкусове", "milk"],
                         ["Garnier Olia Боя за коса", "pump"]]) {
  ok(`„${n.slice(0, 30)}“ → ${want}`, ctx.iconOf(n).g === ctx.GLYPH[want],
     "получих: " + Object.keys(ctx.GLYPH).find(k => ctx.GLYPH[k] === ctx.iconOf(n).g));
}
// Всяка форма трябва да се ползва от нещо. Неизползвана значи или
// сбъркана дума в таблицата, или излишна рисунка.
const used = new Set(S.search.map(it => ctx.iconOf(it.n).g));
const idle = Object.keys(ctx.GLYPH).filter(k => !used.has(ctx.GLYPH[k]));
ok(`Всичките ${Object.keys(ctx.GLYPH).length} форми се ползват`, idle.length === 0,
   "стоят без работа: " + idle.join(", "));

/* =================================================================== */
head("7в. Иконите в Android — генерираният файл съвпада ли");

/* ProductIcons.kt се ГЕНЕРИРА от този HTML. Ако някой пипне таблицата и
   забрави да пусне генератора, приложението и уебът тръгват да рисуват
   различни неща — а това е точно видът разминаване, което никой не
   забелязва месеци наред. */
{
  const KT = path.join(__dirname, "..", "app", "src", "main", "java",
                       "bg", "promoradar", "ProductIcons.kt");
  if (!fs.existsSync(KT)) {
    ok("ProductIcons.kt съществува", false, "пусни: node gen-icons-kt.js");
  } else {
    const kt = fs.readFileSync(KT, "utf8");
    const glyphs = Object.keys(ctx.GLYPH);
    const missing = glyphs.filter(g => !kt.includes(`"${g}" to listOf(`));
    ok(`Всичките ${glyphs.length} форми са в Kotlin файла`, missing.length === 0,
       "липсват: " + missing.join(", "));
    const words = Object.keys(ctx.ICON_FOR);
    const notInKt = words.filter(w => !kt.includes(`"${w}" to `));
    ok(`Всичките ${words.length} думи са в Kotlin файла`, notInKt.length === 0,
       "липсват: " + notInKt.slice(0, 8).join(", ") +
       "\n       пусни: node gen-icons-kt.js");
    // Формите се пренасят като SVG пътища — Compose ги чете с PathParser.
    // <circle> не се разбира от него и се превръща в дъги.
    ok("Няма SVG тагове в Kotlin файла (само path data)",
       !/<(path|circle|svg)/.test(kt));
    ok("Няма float артефакти от превръщането на кръговете",
       !/\d\.\d{6,}/.test(kt));
    // Ключ с интервал никога не се улучва: търси се ПО ЕДНА ДУМА.
    const spaced = words.filter(w => w.includes(" "));
    ok("Няма ключове с интервал (те не се улучват никога)", spaced.length === 0,
       spaced.join(", "));
  }
}

/* =================================================================== */
head("8. Скорост — колко чака човекът");

const sample = [];
for (let i = 0; i < S.search.length && sample.length < 40; i += 601) sample.push(S.search[i].n);

/* Абсолютните милисекунди мерят МАШИНАТА, не кода: същият файл дава ту
   190 ms, ту 436 ms в зависимост от натоварването. Затова се мери
   СЪОТНОШЕНИЕ спрямо едно търсене през целия индекс — то поскъпва и
   поевтинява заедно с всичко останало, значи делението изчиства шума.
   Едно сравнение прави няколко обхождания, тъй че 10-15× е нормално;
   над 30× значи, че някой е добавил още един обход. */
const bench = fn => {
  const t = [];
  for (const n of sample) { const a = Date.now(); fn(n); t.push(Date.now() - a); }
  t.sort((x, y) => x - y);
  return { med: t[Math.floor(t.length / 2)], max: t[t.length - 1] };
};
const base = bench(n => ctx.searchIn(S.search, n.split(" ")[0] || n, 80, false));
const cmpT = bench(n => ctx.compareQuery(n, false));
const ratio = base.med > 0 ? cmpT.med / base.med : 0;
console.log(`     едно търсене: ${base.med} ms · едно сравнение: ${cmpT.med} ms ` +
  `(${ratio.toFixed(1)}×) · най-бавното сравнение: ${cmpT.max} ms`);
ok(`Сравнението е под 8 търсения (${ratio.toFixed(1)}×)`, ratio > 0 && ratio < 8,
   "толкова повече обхождания значи нов цикъл през целия индекс");

// Това е независимо от машината: кешираният отговор не смята нищо.
const tWarm = Date.now();
for (const n of sample) ctx.compareQuery(n, false);
const warm = Date.now() - tWarm;
ok(`Второто отваряне е мигновено от кеша (${warm} ms за ${sample.length})`, warm < 20);

/* =================================================================== */
head("9. Устойчивост — нещата, които чупеха тихо");

// Повреден запис в localStorage. Досега това хвърляше при създаването на
// S — тоест преди да е закачен който и да е обработчик, и страницата
// оставаше завинаги празна, без съобщение.
for (const bad of ["{счупено", "5", '{"a":1}', "null", ""]) {
  for (const k of ["pr_list", "pr_checked", "pr_watch", "pr_seen", "pr_hidden"]) store[k] = bad;
  let err = null;
  try {
    const c2 = { ...ctx };
    vm.createContext(c2);
    vm.runInContext(JS.replace(/^boot\(\);$/m, "") + `\nvar __ok = S.list.length + S.hidden.size;`,
      c2, { filename: "index.html" });
  } catch (e) { err = e; }
  ok(`Повреден localStorage („${bad || "празно"}“) не убива скрипта`, !err, String(err));
}
// И когато самото localStorage хвърля — Safari в частен режим прави точно това
{
  const c3 = { ...ctx, localStorage: {
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => { throw new Error("SecurityError"); } } };
  vm.createContext(c3);
  let err = null;
  try { vm.runInContext(JS.replace(/^boot\(\);$/m, ""), c3, { filename: "index.html" }); }
  catch (e) { err = e; }
  ok("Забранено localStorage (Safari частен режим) не убива скрипта", !err, String(err));
}
for (const k of ["pr_list", "pr_checked", "pr_watch", "pr_seen", "pr_hidden"]) delete store[k];

// Заключеното знаме: изключение вътре в смятането не бива да убива бутона
S.list = ["мляко"]; S.busy = false;
const realSearch = S.search;
S.search = { get length() { throw new Error("нарочна грешка"); } };
try { await ctx.rank(); } catch (e) { /* очаквано */ }
S.search = realSearch;
ok("Изключение в кошницата НЕ заключва бутона завинаги", S.busy === false);

// Ненамерен продукт не бива да чупи навигацията назад
S.detailStack = []; S.lastRef = null;
ctx.openDetail({ t: "hit", s: "Няма такава верига", n: "Няма такъв продукт" });
ok("Ненамерен продукт не пипа стека за навигация",
   S.detailStack.length === 0 && S.lastRef === null);

contractTests();
/* =================================================================== */
console.log(`\n${"═".repeat(50)}\n${pass} минаха · ${fail} се провалиха\n`);
process.exit(fail ? 1 : 0);
}
main().catch(e => { console.error(e); process.exit(1); });
