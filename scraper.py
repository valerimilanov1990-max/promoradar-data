#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Промо Радар — дневен събирач на данни v3 (върви на GitHub Actions).

Какво е ново спрямо v2:
  * eBag минава през истински браузър (Playwright), а не през requests —
    така се хващат и цени, които се дорисуват с JavaScript.
  * Всеки продукт получава КОЛИЧЕСТВО и ЦЕНА ЗА ЕДИНИЦА (лв/кг, лв/л,
    лв/бр). Това е числото, по което цените реално се сравняват.
  * Диагностика: „stats“ казва колко неща е върнал всеки източник,
    а „errors“ — какво точно се е счупило. Никога празен изход без обяснение.
  * Всички ключове съществуват винаги (ebag, offers, basics, brochures,
    errors, stats) — приложението никога не получава непълен JSON.

Изход: data.json
"""
import csv
import datetime
import io
import json
import re
import traceback
import zipfile

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "bg-BG,bg;q=0.9"}

OUT = {
    "updated": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
    "version": 3,
    "ebag": [],
    "offers": [],
    "basics": [],
    "brochures": [],
    "errors": [],
    "stats": {},
}

CFG = json.load(open("config.json", encoding="utf-8"))


def log_err(section, e):
    OUT["errors"].append(f"{section}: {type(e).__name__}: {e}")
    traceback.print_exc()


def note(section, msg):
    OUT["errors"].append(f"{section}: {msg}")


PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2})\s*(?:лв|lv)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Количество и цена за единица
# ---------------------------------------------------------------------------
# Редът има значение: по-дългите мерки (кг, мг, мл) СТОЯТ ПРЕДИ късите (г, л),
# иначе „200мг“ ще се разчете като „200 г“.
_UNITS = r"кг|kg|мг|mg|мл|ml|гр|г|g|литра|литър|л|l|броя|бройки|бр\.?|броя|pcs|db"

_MULTI_RE = re.compile(
    r"(\d{1,3})\s*[xх×]\s*(\d+(?:[.,]\d+)?)\s*(" + _UNITS + r")(?![а-яa-z])",
    re.IGNORECASE)
_SINGLE_RE = re.compile(
    r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(" + _UNITS + r")(?![а-яa-z])",
    re.IGNORECASE)


def _to_base(amount, unit):
    """Връща (количество_в_базова_мярка, етикет) или None, ако мярката се пропуска."""
    u = unit.lower().rstrip(".")
    if u in ("кг", "kg"):
        return amount, "кг"
    if u in ("г", "гр", "g"):
        return amount / 1000.0, "кг"
    if u in ("мг", "mg"):
        return None                      # милиграми: не са хранителна мярка
    if u in ("л", "l", "литра", "литър"):
        return amount, "л"
    if u in ("мл", "ml"):
        return amount / 1000.0, "л"
    if u in ("бр", "бр.", "броя", "бройки", "pcs", "db"):
        return amount, "бр"
    return None


def parse_qty(name):
    """Изважда количеството от името на продукта.

    „Мляко прясно 3% 1л“        -> (1.0, 'л')
    „Кисело мляко 400 г“        -> (0.4, 'кг')
    „Вода Девин 6 х 1.5 л“      -> (9.0, 'л')
    „Яйца M 10 бр.“             -> (10.0, 'бр')
    Връща None, когато няма разпознаваемо количество.
    """
    if not name:
        return None
    txt = name.replace(" ", " ")

    m = _MULTI_RE.search(txt)
    if m:
        try:
            packs = float(m.group(1).replace(",", "."))
            each = float(m.group(2).replace(",", "."))
            base = _to_base(each, m.group(3))
            if base and 0 < packs <= 100:
                return round(base[0] * packs, 4), base[1]
        except ValueError:
            pass

    best = None
    for m in _SINGLE_RE.finditer(txt):
        try:
            val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        base = _to_base(val, m.group(2))
        if not base:
            continue
        amount, label = base
        if not (0 < amount <= 100):
            continue
        # при няколко съвпадения взимаме последното (обикновено то е грамажът)
        best = (round(amount, 4), label)
    return best


def unit_price(price, name):
    """Връща (цена_за_единица, етикет) напр. (4.98, 'лв/кг') или None."""
    q = parse_qty(name)
    if not q or not price:
        return None
    amount, label = q
    if amount <= 0:
        return None
    up = price / amount
    if not (0 < up < 10000):
        return None
    # +1e-9 компенсира двоичната неточност: 1.99/0.4 е 4.9749999… във float,
    # което иначе се закръгля надолу до 4.97 вместо до 4.98.
    return round(up + 1e-9, 2), f"лв/{label}"


EUR_RATE = 1.95583   # фиксираният курс лев/евро


def collapse_currency(prices, curs=None, pct=None):
    """Маха дублиращите се цени, изписани в другата валута.

    След приемането на еврото магазините показват ЕДНА цена два пъти:
    „29.99 €“ и „58.66 лв“. Ако ги подадем нататък като две числа, кодът
    ги взима за цена и стара цена и обявява фалшиво намаление от 49%.
    Точно това се случи с офертите на Кауфланд.

    Разпознаваме двойките по съотношението 1.9558 и пазим левовата.
    """
    if not prices:
        return []
    curs = list(curs or [""] * len(prices))
    if len(curs) < len(prices):
        curs += [""] * (len(prices) - len(curs))

    # Ако на плочката пише намаление около 49%, то може и да е истинско —
    # тогава не пипаме нищо, за да не изтрием реална стара цена.
    if pct and 46 <= pct <= 52:
        return list(prices)

    pairs = list(zip(prices, curs))
    kept = []
    for v, c in pairs:
        if v <= 0:
            continue
        twin = any(o > v and abs(o / v - EUR_RATE) < 0.02 * EUR_RATE
                   for o, _ in pairs)
        if twin and c != "bgn":
            continue          # v е същата цена, но в евро — махаме я
        kept.append((v, c))

    if not kept:
        return list(prices)
    # Ако накрая всичко е в евро, превръщаме в лева
    if all(c == "eur" for _, c in kept):
        return [round(v * EUR_RATE, 2) for v, _ in kept]
    return [v for v, _ in kept]


def pick_price_pair(prices, pct=None):
    """Избира (текуща цена, стара цена) измежду намерените на плочката числа.

    Плочките често носят повече от две числа — цена за килограм, цена на
    вноска, тегло. Ако вземем сляпо най-малкото и най-голямото, старата
    цена излиза грешна.

    Затова: когато на плочката пише процент намаление, търсим двойката,
    която му отговаря най-точно. Иначе взимаме най-ниската цена и
    най-близката над нея.
    """
    vals = sorted({round(p, 2) for p in prices if 0.05 <= p <= 5000})
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], None

    if pct and 1 <= pct <= 95:
        target = pct / 100.0
        best, best_err = None, 1e9
        for i, p in enumerate(vals):
            for o in vals[i + 1:]:
                if o <= p:
                    continue
                err = abs((1 - p / o) - target)
                if err < best_err:
                    best_err, best = err, (p, o)
        # до 6 процентни пункта разлика приемаме за същата двойка
        if best and best_err <= 0.06:
            return best

    price = vals[0]
    # Старата цена е най-близката над текущата, но НЕ повече от 5 пъти:
    # съотношение 10 или 20 значи, че сме хванали цена за 100 г или за
    # кашон, а не истинско намаление от 95%.
    old = next((v for v in vals[1:] if price * 1.02 < v <= price * 5), None)
    return price, old


def enrich(item):
    """Добавя qty/unit/unitPrice към запис с полета name+price."""
    up = unit_price(item.get("price"), item.get("name") or item.get("product", ""))
    if up:
        item["unitPrice"], item["unitLabel"] = up
    q = parse_qty(item.get("name") or item.get("product", ""))
    if q:
        item["qty"], item["qtyUnit"] = q
    return item


# ---------------------------------------------------------------------------
# 1. eBag — през истински браузър (с резервен вариант requests)
# ---------------------------------------------------------------------------
def _ebag_from_html(pid, html):
    m_name = (re.search(r'og:title["\']?\s+content=["\']([^"\']+)', html)
              or re.search(r'content=["\']([^"\']+)["\']\s+property=["\']og:title', html)
              or re.search(r"<title>([^<]+)</title>", html))
    m_price = PRICE_RE.search(html)
    if not (m_name and m_price):
        return None
    name = re.sub(r"\s*[-|]\s*eBag\.bg\s*$", "", m_name.group(1)).strip()
    return {"id": str(pid), "name": name,
            "price": float(m_price.group(1).replace(",", "."))}


def scrape_ebag(ctx):
    ids = CFG.get("ebag_ids", [])
    base = CFG.get("ebag_base", "https://www.ebag.bg/x/")
    ok = 0
    for pid in ids:
        url = f"{base}{pid}"
        rec = None

        # 1) истински браузър — вижда и това, което JavaScript дорисува
        if ctx is not None:
            pg = None
            try:
                pg = ctx.new_page()
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(2500)
                rec = _ebag_from_html(pid, pg.content())
                if not rec:
                    # цената понякога е само във видимия текст, не в мета
                    txt = pg.inner_text("body")
                    m = PRICE_RE.search(txt)
                    title = (pg.title() or f"Продукт {pid}")
                    if m:
                        rec = {"id": str(pid),
                               "name": re.sub(r"\s*[-|]\s*eBag\.bg\s*$", "", title).strip(),
                               "price": float(m.group(1).replace(",", "."))}
            except Exception as e:
                log_err(f"ebag/{pid}/browser", e)
            finally:
                if pg:
                    try:
                        pg.close()
                    except Exception:
                        pass

        # 2) резервно: обикновена заявка
        if not rec:
            try:
                r = requests.get(url, headers=UA, timeout=30)
                if r.status_code == 200:
                    rec = _ebag_from_html(pid, r.text)
                else:
                    note(f"ebag/{pid}", f"HTTP {r.status_code}")
            except Exception as e:
                log_err(f"ebag/{pid}/requests", e)

        if rec:
            OUT["ebag"].append(enrich(rec))
            ok += 1
        else:
            note(f"ebag/{pid}", "цената не се разпознава (нито в браузър, нито директно)")

    OUT["stats"]["ebag"] = f"{ok}/{len(ids)}"


# ---------------------------------------------------------------------------
# 2. Оферти от рендирания сайт (без OCR)
# ---------------------------------------------------------------------------
# --- ОБЩИ ПОМОЩНИЦИ ЗА ДВАТА МЕТОДА -----------------------------------
# Кауфланд мина по резервния метод, който нямаше поправките за имената и
# цените — затова логиката вече е ЕДНА и се вгражда и в двата.
JS_HELPERS = r"""
  const PRICE_G = /(\d{1,4}[.,]\d{2})/g;
  const PRICE_T = /\d{1,4}[.,]\d{2}/;

  // Редове като „цена за кг: 9.95“ са единични цени, не цената на продукта.
  const isUnitLine = (l) => /цена\s*за|\/\s*(кг|kg|л|l|бр|100\s*(г|мл))|за\s+\d+\s*(г|мл|kg|кг)\b/i.test(l);

  // След числото стои мярка -> това е ОБЕМ/ТЕГЛО, не цена („Мартини 0,75 л“).
  const UNIT_AFTER = /^\s*(л\b|l\b|кг|kg|гр|г\b|g\b|мл|ml|бр|броя|%|["“”]|x|х|см|cm|мм|mm|м\b|m\b|вт|w\b|kw|квт)/i;
  const CUR = /(лв|лева|bgn|€|eur)/i;
  const EURO = /€|eur/i;
  const LEVA = /лв|лева|bgn/i;

  // Връща [{v: число, c: 'eur'|'bgn'|''}] — валутата е нужна, защото
  // магазините изписват ЕДНАТА цена два пъти: в евро и в лева.
  const readPrices = (text, strict) => {
    const res = [];
    let m;
    PRICE_G.lastIndex = 0;
    while ((m = PRICE_G.exec(text)) !== null) {
      const end = m.index + m[0].length;
      const after = text.slice(end, end + 10);
      const before = text.slice(Math.max(0, m.index - 14), m.index);
      if (UNIT_AFTER.test(after)) continue;
      if (/\d$/.test(text.charAt(m.index - 1))) continue;
      const hasCur = CUR.test(after) || CUR.test(before);
      if (strict && !hasCur) continue;
      // Валутата се чете от ТЯСНО прозорче. Иначе символът от предишната
      // цена („16.61 €“) попада в контекста на следващата („32.49 лв“) и
      // двете излизат в евро — тогава кодът ги умножава по курса наново.
      const near = after.slice(0, 6);
      const pre  = before.slice(-3);
      let c = '';
      if (LEVA.test(near)) c = 'bgn';
      else if (EURO.test(near)) c = 'eur';
      else if (EURO.test(pre)) c = 'eur';       // „€40.39“
      else if (LEVA.test(pre)) c = 'bgn';
      res.push({v: parseFloat(m[1].replace(',', '.')), c: c});
    }
    return res;
  };

  const pricesOf = (el, wholeText) => {
    // ВАЖНО: четем от ЦЕЛИЯ текст на плочката, не от изолирания елемент с
    // клас „price“. Само там мярката стои непосредствено до числото и
    // „0,75 л“ се разпознава като обем, а не като цена от 75 стотинки.
    const lines = wholeText.split('\n').filter(l => !isUnitLine(l)).join('\n');
    let ms = readPrices(lines, true);      // първо с валутен знак наблизо
    if (!ms.length) ms = readPrices(lines, false);
    if (!ms.length) {
      // краен случай: плочката няма никакъв текст с цена наоколо
      const priceEls = el.querySelectorAll(
        '[class*="price" i],[class*="cena" i],[class*="Preis" i],[itemprop="price"]');
      let ptxt = '';
      priceEls.forEach(p => { ptxt += '\n' + (p.textContent || ''); });
      ms = readPrices(ptxt.split('\n').filter(l => !isUnitLine(l)).join('\n'), false);
    }
    return ms;
  };

  // alt текстове, които описват СНИМКАТА, а не продукта
  const ALT_PREFIX = /^\s*(изображение на|снимка на|картинка на|image of|photo of|picture of)\s*/i;
  const looksLikeDescription = (s) => {
    const words = s.split(/\s+/).filter(Boolean).length;
    if (words > 10) return true;
    if (/[.!?]$/.test(s.trim())) return true;
    return false;
  };

  // „rating.starFilled“, „product_title“ — вътрешни ключове на сайта,
  // изтекли в текста. Човешко име винаги има интервал или кирилица.
  const looksLikeCode = (s) => {
    if (/[А-Яа-я]/.test(s)) return false;
    if (/\s/.test(s)) return false;
    return /[._]/.test(s) || /^[a-z]+[A-Z]/.test(s);
  };

  const nameOf = (el) => {
    // Събираме ВСИЧКИ кандидати и взимаме най-информативния.
    // Ако вземем първия заглавен елемент, за Кауфланд излиза само
    // марката („Martini“) вместо цялото име на продукта.
    const cands = [];
    el.querySelectorAll('[class*="title" i],[class*="name" i],[class*="titel" i],h2,h3,h4,h5')
      .forEach(t => cands.push((t.textContent || '').replace(/\s+/g, ' ').trim()));
    const img = el.querySelector('img');
    if (img && img.alt) {
      cands.push(img.alt.replace(ALT_PREFIX, '').replace(/\s+/g, ' ').trim());
    }
    (el.innerText || el.textContent || '').split('\n')
      .forEach(s => cands.push(s.replace(/\s+/g, ' ').trim()));

    let best = '';
    for (const s of cands) {
      if (!s || s.length < 3 || s.length > 140) continue;
      if (/^[\d.,\s%€лвkg\-–—]+$/i.test(s)) continue;   // само числа
      if (isUnitLine(s)) continue;
      if (looksLikeDescription(s)) continue;
      if (looksLikeCode(s)) continue;
      if (s.length > best.length) best = s;
    }
    return best;
  };

  const imgOf = (el) => {
    const img = el.querySelector('img');
    if (img) {
      const d = img.currentSrc || img.src || img.getAttribute('data-src')
             || img.getAttribute('data-original') || '';
      if (d && d.indexOf('data:') !== 0) return {src: d, alt: (img.alt || '').trim()};
      const ss = img.getAttribute('data-srcset') || img.getAttribute('srcset') || '';
      if (ss) return {src: ss.split(',')[0].trim().split(' ')[0], alt: (img.alt || '').trim()};
      if (d) return {src: d, alt: (img.alt || '').trim()};
    }
    const s = el.querySelector('source');
    if (s) {
      const ss = s.getAttribute('srcset') || '';
      if (ss) return {src: ss.split(',')[0].trim().split(' ')[0], alt: ''};
    }
    return null;
  };

  const linkOf = (el) => {
    const a = el.tagName === 'A' ? el : (el.querySelector('a') || el.closest('a'));
    return a ? (a.href || '') : '';
  };

  const pctOf = (txt) => {
    const pm = txt.match(/-\s?(\d{1,2})\s?%/);
    return pm ? parseInt(pm[1], 10) : null;
  };

  const buildItem = (el) => {
    const t = (el.innerText || el.textContent || '').trim();
    const ms = pricesOf(el, t);
    if (!ms.length) return null;
    const name = (nameOf(el) || '').replace(/\s+/g, ' ').trim();
    if (!name || name.length < 3) return null;
    const im = imgOf(el);
    return {name: name,
            prices: ms.map(x => x.v),
            cur: ms.map(x => x.c),
            pct: pctOf(t),
            img: im ? im.src : '',
            url: linkOf(el)};
  };
"""

# --- 1) Самонастройващо се откриване на продуктовата решетка ------------
GRID_JS = "() => {" + JS_HELPERS + r"""
  // Продуктите в един списък са ЕДНАКВИ елементи, повторени много пъти.
  // Търсим class-подписа с най-много повторения, чиито елементи съдържат
  // цена — това е решетката, каквато и да е разметката.
  const sig = (el) => {
    const cls = (typeof el.className === 'string' ? el.className : '').trim();
    const parts = cls ? cls.split(/\s+/).filter(c => c.length > 1 &&
                        !/\d{3,}/.test(c)).slice(0, 3).sort().join('.') : '';
    return el.tagName + (parts ? '.' + parts : '');
  };

  const cands = [];
  const all = document.querySelectorAll('div,li,article,section,a');
  for (let i = 0; i < all.length; i++) {
    const el = all[i];
    if (el.children.length > 12) continue;
    const t = el.textContent || '';
    if (t.length < 4 || t.length > 400) continue;
    if (!PRICE_T.test(t)) continue;
    cands.push(el);
  }

  const groups = new Map();
  for (const el of cands) {
    let cur = el;
    for (let d = 0; d < 5 && cur && cur !== document.body; d++) {
      const s = sig(cur);
      if (s.length > 3) {
        if (!groups.has(s)) groups.set(s, new Set());
        groups.get(s).add(cur);
      }
      cur = cur.parentElement;
    }
  }

  let best = null, bestScore = 0, bestSig = '';
  for (const [s, set] of groups) {
    const els = Array.from(set);
    if (els.length < 6) continue;
    let sum = 0, imgs = 0;
    for (const e of els) {
      sum += (e.textContent || '').length;
      if (e.querySelector('img,source')) imgs++;
    }
    const avg = sum / els.length;
    if (avg > 400) continue;
    const score = els.length * (avg < 220 ? 2 : 1) * (imgs > els.length / 2 ? 2 : 1);
    if (score > bestScore) { bestScore = score; best = els; bestSig = s; }
  }
  if (!best) return {items: [], sig: '', n: 0};

  const out = [], seen = new Set();
  for (const el of best) {
    const it = buildItem(el);
    if (!it) continue;
    const key = it.name.toLowerCase().slice(0, 60) + '|' + Math.min.apply(null, it.prices);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(it);
  }
  return {items: out, sig: bestSig, n: best.length};
}
"""

# --- 2) Резервен метод: по плочки --------------------------------------
TILE_JS = "() => {" + JS_HELPERS + r"""
  const hasImage = (el) => !!(el.querySelector('img') || el.querySelector('source'));
  const cands = new Set();
  document.querySelectorAll('a').forEach(a => { if (hasImage(a)) cands.add(a); });
  document.querySelectorAll(
    '[class*="product"],[class*="Product"],[class*="tile"],[class*="Tile"],' +
    '[class*="offer"],[class*="Offer"],[class*="article"],[data-testid*="product"],' +
    'article,li'
  ).forEach(el => {
    const t = el.innerText || '';
    if (hasImage(el) && t.length > 0 && t.length < 400) cands.add(el);
  });

  const out = [], seen = new Set();
  cands.forEach(el => {
    const it = buildItem(el);
    if (!it) return;
    if (it.prices.length > 5) return;      // групов контейнер, не плочка
    const key = it.name.toLowerCase().slice(0, 60) + '|' + Math.min.apply(null, it.prices);
    if (seen.has(key)) return;
    seen.add(key);
    out.push(it);
  });
  return out;
}
"""

# За всяка верига: начални страници + думи, по които се разпознават
# връзките към още страници с оферти/категории. Обхождат се до MAX_PAGES.
#
# Списъкът се чете от config.json (ключ "stores"), за да се добавят вериги
# БЕЗ промяна в кода. Долното е само резервният вариант, ако го няма там.
DEFAULT_TARGETS = [
    ("Кауфланд",
     ["https://www.kaufland.bg/aktualni-predlozheniya/ot-ponedelnik.html",
      "https://www.kaufland.bg/aktualni-predlozheniya/oferti.html"],
     ("aktualni-predlozheniya", "oferti", "ot-ponedelnik", "ot-chetvartak")),
    ("Лидл",
     ["https://www.lidl.bg/"],
     ("aktsiya", "oferti", "predlozheni", "top-", "/c/")),
    ("Метро",
     ["https://www.metro.bg/oferti/top-oferti", "https://www.metro.bg/"],
     ("oferti", "promo", "katalog")),
    ("Билла",
     ["https://www.billa.bg/promocii"],
     ("promocii", "promo", "oferti", "katalog")),
]


def _load_targets():
    """Чете веригите от config.json; при липса ползва вградените."""
    raw = CFG.get("stores")
    if not raw:
        return DEFAULT_TARGETS
    out = []
    for s in raw:
        try:
            if not s.get("enabled", True):
                continue
            name = str(s["name"]).strip()
            urls = [u for u in s.get("urls", []) if str(u).startswith("http")]
            kws = tuple(str(k).lower() for k in s.get("keywords", []))
            if name and urls:
                out.append((name, urls, kws))
        except Exception as e:
            log_err("config/stores", e)
    return out or DEFAULT_TARGETS


TARGETS = _load_targets()

# Лимитите се четат от config.json, за да се настройват без промяна на кода.
import time as _time
_START = _time.time()
# Общ времеви бюджет за обхождането. GitHub Actions спира задачата по
# някое време; ако се ударим в лимита, оставаме БЕЗ никакъв изход. Затова
# спираме сами навреме и записваме каквото сме събрали дотук.
TIME_BUDGET_S = int(CFG.get("time_budget_min", 45)) * 60


def out_of_time():
    return (_time.time() - _START) > TIME_BUDGET_S


def elapsed_min():
    return (_time.time() - _START) / 60.0


MAX_PAGES = int(CFG.get("max_pages", 60))          # страници на верига
MAX_PER_STORE = int(CFG.get("max_per_store", 1500))  # оферти на верига в изхода
LOAD_MORE_ROUNDS = int(CFG.get("load_more_rounds", 25))  # натискания на „покажи още“
POLITE_MS = int(CFG.get("polite_ms", 400))         # пауза между страниците

# Бутони за дозареждане — по текст, защото класовете се сменят постоянно.
LOAD_MORE_JS = """
() => {
  const words = ['покажи още','зареди още','още продукти','виж още','load more',
                 'показать','повече','следваща страница'];
  const els = [...document.querySelectorAll('button,a,div[role=button],span[role=button]')];
  for (const el of els) {
    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
    if (!t || t.length > 40) continue;
    if (words.some(w => t.includes(w))) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { el.scrollIntoView({block:'center'}); el.click(); return true; }
    }
  }
  return false;
}
"""

# Колко продуктови елемента има в момента — за да знаем дали расте.
COUNT_JS = """
() => {
  const P = /\\d{1,4}[.,]\\d{2}/;
  let n = 0;
  const all = document.querySelectorAll('div,li,article,a');
  for (const el of all) {
    if (el.children.length > 12) continue;
    const t = el.textContent || '';
    if (t.length > 3 && t.length < 400 && P.test(t)) n++;
  }
  return n;
}
"""

# Връзки към следващи страници (pagination)
NEXT_JS = """
() => {
  const out = new Set();
  const rel = document.querySelector('link[rel=next], a[rel=next]');
  if (rel && rel.href) out.add(rel.href);
  document.querySelectorAll('a[href]').forEach(a => {
    const h = a.href || '';
    const t = (a.innerText || '').trim().toLowerCase();
    if (/[?&](page|p|strana|offset)=\\d+/i.test(h)) out.add(h.split('#')[0]);
    else if (t === 'следваща' || t === 'напред' || t === 'next' || t === '>') {
      if (h) out.add(h.split('#')[0]);
    }
  });
  return [...out];
}
"""


def _harvest(ctx, store, url, keywords=()):
    pg = ctx.new_page()
    found, extra_links = [], []
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3000)

        # Защитен екран („Един момент…“, „Checking your browser“) — сайтът
        # още не е зареден. Изчакваме проверката да мине, вместо да четем
        # междинната страница и да отчетем 0 продукта.
        # 5 опита по 2.5 сек стигат за нормална проверка; ако сайтът не
        # пусне дотогава, той просто блокира автоматизацията и чакането
        # само изяжда бюджета (миналото пускане отиде 28 мин заради това).
        for _ in range(5):
            try:
                txt = (pg.inner_text("body") or "")
                title = (pg.title() or "")
            except Exception:
                break
            waiting = (len(txt) < 600 or
                       re.search(r"един момент|checking your browser|just a moment|"
                                 r"проверка на браузъра|attention required",
                                 txt + " " + title, re.IGNORECASE))
            if not waiting:
                break
            pg.wait_for_timeout(2500)

        pg.wait_for_timeout(1500)
        # --- Дозареждане: скролваме и натискаме „покажи още“, докато расте ---
        last, stale = -1, 0
        for _ in range(LOAD_MORE_ROUNDS):
            for _ in range(3):
                pg.mouse.wheel(0, 3000)
                pg.wait_for_timeout(500)
            clicked = False
            try:
                clicked = bool(pg.evaluate(LOAD_MORE_JS))
            except Exception:
                pass
            pg.wait_for_timeout(1200 if clicked else 600)
            try:
                now = int(pg.evaluate(COUNT_JS))
            except Exception:
                break
            if now <= last:
                stale += 1
                if stale >= 2 and not clicked:
                    break          # два пъти подред нищо ново — стигнали сме дъното
            else:
                stale = 0
            last = now

        # 1) самонастройващо се откриване на продуктовата решетка
        items, used = [], ""
        try:
            grid = pg.evaluate(GRID_JS) or {}
            items = grid.get("items") or []
            if items:
                used = f"решетка {grid.get('sig','?')} ({grid.get('n',0)} елемента)"
        except Exception as e:
            log_err(f"grid/{store}", e)

        # 2) резервно: старият метод по плочки
        if len(items) < 5:
            try:
                alt = pg.evaluate(TILE_JS) or []
                if len(alt) > len(items):
                    items, used = alt, "плочки (резервен метод)"
            except Exception as e:
                log_err(f"tiles/{store}", e)

        if used:
            OUT["stats"][f"метод/{store}"] = used

        # Връзки към още страници — категории + пагинация. Така стигаме до
        # целия каталог, а не само до първата страница.
        host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
        seen = set()
        try:                                    # следваща страница (pagination)
            for l in (pg.evaluate(NEXT_JS) or []):
                if host in l and l not in seen:
                    seen.add(l)
                    extra_links.append(l)
        except Exception:
            pass
        if keywords:                            # категории и раздели с оферти
            try:
                links = pg.evaluate(
                    "() => [...document.querySelectorAll('a[href]')].map(a => a.href)")
                for l in links:
                    if host not in l or l in seen:
                        continue
                    if any(k in l.lower() for k in keywords):
                        seen.add(l)
                        extra_links.append(l.split("#")[0])
            except Exception:
                pass

        raw = len(items)
        if raw == 0:
            # Диагностика на място: без нея „0 оферти“ не казва нищо.
            try:
                diag = pg.evaluate(
                    "() => ({title: document.title,"
                    " links: document.querySelectorAll('a').length,"
                    " imgs: document.querySelectorAll('img').length,"
                    " text: (document.body.innerText||'').length})")
                note(f"dom/{store}", f"0 плочки на {url[:60]} — "
                                     f"страница „{str(diag.get('title'))[:40]}“, "
                                     f"{diag.get('links')} линка, {diag.get('imgs')} картинки, "
                                     f"{diag.get('text')} знака текст")
            except Exception:
                note(f"dom/{store}", f"0 плочки на {url[:60]} (и диагностиката не мина)")

        for it in items:
            # първо махаме дублите в другата валута, после избираме двойката
            prices = collapse_currency(it.get("prices", []),
                                       it.get("cur"), it.get("pct"))
            prices = [x for x in prices if 0.1 <= x <= 5000]
            if not prices:
                continue
            name = re.sub(r"\s+", " ", it.get("name") or "").strip()
            if sum(c.isalpha() for c in name) < 5:
                continue
            price, old = pick_price_pair(prices, it.get("pct"))
            if price is None:
                continue
            rec = {
                "store": store, "name": name[:90], "price": round(price, 2),
                "old": round(old, 2) if old else None,
                "img": (it.get("img") or "")[:300],
                "url": (it.get("url") or "")[:300],
            }
            # Процентът, изписан на самата плочка, е по-верен от сметнатия:
            # понякога на плочката има и цена за килограм, която обърква сметката.
            if it.get("pct"):
                rec["pct"] = int(it["pct"])
            found.append(enrich(rec))
        if raw and not found:
            note(f"dom/{store}", f"{raw} плочки, но 0 с валидна цена/име")
        return found, extra_links
    except Exception as e:
        log_err(f"dom/{store}/{url[:45]}", e)
        return found, []
    finally:
        try:
            pg.close()
        except Exception:
            pass


def scrape_dom_offers(ctx):
    if ctx is None:
        note("dom", "браузърът не е наличен — офертите се пропускат")
        return
    for store, urls, keywords in TARGETS:
        if out_of_time():
            note("време", f"бюджетът свърши — {store} и следващите са пропуснати")
            break
        # Времето се дели поравно между веригите, за да не изяде първата всичко
        store_deadline = _time.time() + max(60, (TIME_BUDGET_S - (_time.time() - _START))
                                            / max(1, len(TARGETS) - TARGETS.index((store, urls, keywords))))
        per_store, queue, visited = [], list(urls), set()
        while queue and len(visited) < MAX_PAGES:
            if out_of_time() or _time.time() > store_deadline:
                OUT["stats"][f"време/{store}"] = f"спрян на {len(visited)} страници"
                break
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            items, extra = _harvest(ctx, store, url, keywords)
            per_store += items
            for l in extra:
                if l not in visited and l not in queue and len(queue) < MAX_PAGES * 3:
                    queue.append(l)
            # Малка пауза — не искаме да натоварваме чужди сайтове
            if POLITE_MS:
                try:
                    ctx.pages[0].wait_for_timeout(POLITE_MS)
                except Exception:
                    pass
            # Достатъчно събрахме за тази верига
            if len(per_store) >= MAX_PER_STORE * 2:
                break

        seen, ded = set(), []
        for o in per_store:
            k = (o["name"].lower(), o["price"])
            if k not in seen:
                seen.add(k)
                ded.append(o)
        OUT["offers"] += ded[:MAX_PER_STORE]
        OUT["stats"][f"offers/{store}"] = f"{len(ded[:MAX_PER_STORE])} от {len(visited)} страници"
        if not ded:
            note(f"dom/{store}", "0 оферти — вероятно смениха сайта, провери селекторите")

    OUT["brochures"] = [o for o in OUT["offers"] if o["store"] in ("Лидл", "Билла")]


# ---------------------------------------------------------------------------
# 3. kolkostruva.bg — официалните дневни цени
# ---------------------------------------------------------------------------
CHAIN_MAP = [
    ("КАУФЛАНД", "Кауфланд"), ("KAUFLAND", "Кауфланд"),
    ("БИЛЛА", "Билла"), ("BILLA", "Билла"),
    ("ЛИДЛ", "Лидл"), ("LIDL", "Лидл"),
    ("МЕТРО", "Метро"), ("METRO", "Метро"),
    ("ФАНТАСТИКО", "Фантастико"), ("FANTASTIKO", "Фантастико"),
    ("Т МАРКЕТ", "T-Market"), ("T MARKET", "T-Market"), ("ТМАРКЕТ", "T-Market"),
    ("БУЛМАГ", "BulMag"), ("BULMAG", "BulMag"),
    ("БЕРЕЗКА", "Березка"), ("BEREZKA", "Березка"),
    ("АВАНТИ", "Аванти"), ("AVANTI", "Аванти"),
    ("ЛЕКСИ", "Лекси"), ("LEKSI", "Лекси"),
    ("КЛАСИКО", "Класико"), ("KLASIKO", "Класико"),
    ("ПРОМАРКЕТ", "Promarket"), ("ТРИУМФ", "Триумф"),
    ("ЖАНЕТ", "Жанет"), ("ПИКАДИЛИ", "Пикадили"), ("PICCADILLY", "Пикадили"),
    ("ЛИЛИ", "Lilly"), ("LILLY", "Lilly"),
    ("ЕБАГ", "eBag"), ("EBAG", "eBag"),
    # Кратките ключове се проверяват с граници на думата (виж _key_hit),
    # за да не хванат „ДАР“ вътре в „БОЖУРДАР“.
    ("CBA", "CBA"), ("СБА", "CBA"), ("ДАР", "Дар"), ("DAR", "Дар"),
    ("DM", "dm"), ("ДМ", "dm"),
]

# Аптеките отпадат — там се продават лекарства, не хранителни стоки.
# Дрогериите (Lilly, dm) ОСТАВАТ: там има перилни, козметика и битова химия,
# които влизат в кошницата. Конкуренцията ги показва и с право.
# „АПТЕК“ без окончание — иначе „АПТЕКИ ГАЛЕН“ се промъква покрай „АПТЕКА“.
SKIP_CHAIN = ("АПТЕК", "APTEK", "ФАРМА", "PHARM", "ЛЕКАРСТВ", "ЗДРАВНА КАСА",
              "ОПТИК", "ЗООМАГ",
              # бензиностанции: продават храна, но цените им не са
              # представителни за пазаруване и само шумят в класацията
              "БЕНЗИНОСТ", "ПЕТРОЛ", "PETROL", "ЛУКОЙЛ", "LUKOIL",
              "ШЕЛ", "SHELL", "ОМВ", "OMV", "ЕКО БЪЛГАРИЯ", "ROMPETROL")


def _key_hit(up, key):
    """Дълъг ключ = обикновено съвпадение; кратък = само като цяла дума."""
    if len(key) >= 5:
        return key in up
    return re.search(r"(?<![А-ЯЁA-Z])" + re.escape(key) + r"(?![А-ЯЁA-Z])", up) is not None


def norm_chain(raw):
    up = raw.upper()
    if any(k in up for k in SKIP_CHAIN):
        return None
    for key, nice in CHAIN_MAP:
        if _key_hit(up, key):
            return nice
    # Непозната верига: чистим я, но я ПАЗИМ — държавните данни покриват
    # и вериги, които никой не е сложил в списък, и точно там е предимството.
    clean = re.sub(r"_\d+$", "", raw).strip()
    clean = clean.split("(")[0].strip()          # маха юридическите опашки
    clean = re.sub(r"\s+(ЕООД|ООД|АД|ЕАД|ЕТ|КД|СД)\b.*$", "", clean,
                   flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean[:24] if len(clean) >= 2 else None


def _read_csv(raw, chain, rows):
    for enc in ("utf-8-sig", "windows-1251", "utf-8"):
        try:
            textdata = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return
    first = textdata.splitlines()[0] if textdata else ""
    delim = max([";", ",", "\t"], key=first.count)
    rdr = csv.reader(io.StringIO(textdata), delimiter=delim)
    hdr = [h.strip().lower() for h in next(rdr, [])]

    def col(*names):
        for i, h in enumerate(hdr):
            if any(n in h for n in names):
                return i
        return -1

    pc = col("продукт", "стока", "наименование", "артикул", "product", "name")
    cc = col("цена", "price")
    if pc < 0 or cc < 0:
        note("basics", f"{chain[:30]}: колони продукт/цена не се разпознават ({hdr[:4]})")
        return
    for row in rdr:
        try:
            price = float(re.sub(r"[^\d.]", "", row[cc].replace(",", ".")))
            nmv = row[pc].strip()
            ch = norm_chain(chain)
            if ch and nmv and 0 < price < 1000:
                rows.append(enrich({"chain": ch, "product": nmv,
                                    "name": nmv, "price": price}))
        except Exception:
            pass


def _read_xlsx(blob, chain, rows):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob), read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [str(h or "").strip().lower() for h in next(it, [])]

    def col(*names):
        for i, h in enumerate(hdr):
            if any(n in h for n in names):
                return i
        return -1

    pc = col("продукт", "стока", "наименование", "артикул")
    cc = col("цена")
    if pc < 0 or cc < 0:
        return
    for row in it:
        try:
            price = float(str(row[cc]).replace(",", "."))
            nmv = str(row[pc] or "").strip()
            ch = norm_chain(chain)
            if ch and nmv and 0 < price < 1000:
                rows.append(enrich({"chain": ch, "product": nmv,
                                    "name": nmv, "price": price}))
        except Exception:
            pass


# Веригите, в които хората реално пазаруват. Те получават дял ПЪРВИ —
# иначе се пълни с местни магазинчета и в приложението липсват Кауфланд
# и Билла, което прави класацията на кошницата безполезна.
PRIORITY_CHAINS = [
    "Кауфланд", "Билла", "Лидл", "Метро", "Фантастико", "T-Market",
    "BulMag", "dm", "Lilly", "CBA", "Аванти", "КООП", "Триумф",
    "Промаркет", "Пикадили", "Жанет", "Березка", "Дар", "Лекси", "Класико",
]


def _select_basics(rows):
    """Избира кои официални цени да влязат във фийда.

    Архивът на КЗП е огромен — над милион реда от 131 вериги, защото
    съдържа по един запис за всеки магазин на веригата. Два проблема:
    файлът става непосилен, а сляпото рязане изхвърля точно големите вериги.

    Затова: първо сливаме повторенията (една цена на продукт във верига,
    най-ниската), после даваме дял първо на веригите, в които се пазарува.
    """
    per_chain = int(CFG.get("max_basics_per_chain", 1500))
    total_cap = int(CFG.get("max_basics_total", 20000))

    # 1) сливане: най-ниската цена за продукт във всяка верига
    merged = {}
    for r in rows:
        key = (r["chain"], re.sub(r"\s+", " ", r["product"]).strip().lower()[:70])
        cur = merged.get(key)
        if cur is None or r["price"] < cur["price"]:
            merged[key] = r

    buckets = {}
    for r in merged.values():
        buckets.setdefault(r["chain"], []).append(r)

    # 2) подредба: първо приоритетните, после останалите по големина
    def rank(chain):
        return (PRIORITY_CHAINS.index(chain) if chain in PRIORITY_CHAINS
                else len(PRIORITY_CHAINS) + 1)

    order = sorted(buckets.keys(), key=lambda c: (rank(c), -len(buckets[c])))

    picked = []
    for ch in order:
        if len(picked) >= total_cap:
            break
        picked.extend(buckets[ch][:per_chain])

    picked = picked[:total_cap]
    got = sorted({r["chain"] for r in picked})
    OUT["stats"]["basics_вериги_в_архива"] = len(buckets)
    OUT["stats"]["basics_редове_в_архива"] = len(rows)
    OUT["stats"]["basics_след_сливане"] = len(merged)
    OUT["stats"]["basics"] = f"{len(picked)} реда, {len(got)} вериги"
    OUT["stats"]["basics_chains"] = got
    missing = [c for c in PRIORITY_CHAINS[:6] if c not in got]
    if missing:
        note("basics", f"липсват едри вериги: {', '.join(missing)} — "
                       f"или не подават данни, или името им не се разпознава")
    return picked


def scrape_basics():
    base = CFG.get("food_base", "https://kolkostruva.bg/opendata_files/")
    day = datetime.date.today()
    for back in range(8):
        d = (day - datetime.timedelta(days=back)).isoformat()
        try:
            r = requests.get(f"{base}{d}.zip", headers=UA, timeout=60)
            if r.status_code != 200 or len(r.content) < 1000:
                continue
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            rows, skipped = [], 0
            for nm in zf.namelist():
                low = nm.lower()
                chain = nm.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if norm_chain(chain) is None:
                    skipped += 1
                    continue                      # аптеки и пр. — не ги четем изобщо
                try:
                    if low.endswith(".csv"):
                        _read_csv(zf.read(nm), chain, rows)
                    elif low.endswith((".xlsx", ".xls")):
                        _read_xlsx(zf.read(nm), chain, rows)
                except Exception as e:
                    log_err(f"basics/{nm[:40]}", e)
            if rows:
                OUT["basics"] = _select_basics(rows)
                OUT["basics_date"] = d
                OUT["stats"]["basics_пропуснати_файла"] = skipped
                return
        except Exception as e:
            log_err(f"basics/{d}", e)
    note("basics", "нито един архив от последните 8 дни не се прочете")


# ---------------------------------------------------------------------------
# Разделяне на изхода: телефонът да тегли само каквото гледа
# ---------------------------------------------------------------------------
SLUG_MAP = {
    "Кауфланд": "kaufland", "Лидл": "lidl", "Билла": "billa", "Метро": "metro",
    "Фантастико": "fantastiko", "T-Market": "tmarket", "BulMag": "bulmag",
    "Березка": "berezka", "Аванти": "avanti", "Дар": "dar", "Лекси": "leksi",
    "Класико": "klasiko", "Lilly": "lilly", "dm": "dm", "CBA": "cba",
    "eBag": "ebag", "Промаркет": "promarket", "Триумф": "triumf",
    "Жанет": "janet", "Пикадили": "piccadilly",
}


def slug(name):
    if name in SLUG_MAP:
        return SLUG_MAP[name]
    s = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", "-", name).strip("-").lower()
    tr = str.maketrans("абвгдежзийклмнопрстуфхцчшщъьюя",
                       "abvgdejziyklmnoprstufhc4w6a-ya")
    return (s.translate(tr) or "store")[:20]


def _dump(path, obj):
    import os
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path)


def write_output():
    """Пише разделен фийд + пълния файл за съвместимост.

    feed/index.json      малък: кои вериги има, колко продукта, топ офертите
    feed/offers-<x>.json офертите на една верига — тегли се при нужда
    feed/search.json     компактен индекс за търсене
    feed/basics.json     официалните цени — тегли се само при „къде е най-евтино“
    data.json            пълният файл (както досега), за съвместимост
    """
    import os
    sizes = {}

    # --- по вериги ---
    stores = []
    by_store = {}
    for o in OUT["offers"]:
        by_store.setdefault(o["store"], []).append(o)
    for name, lst in sorted(by_store.items(), key=lambda kv: -len(kv[1])):
        sl = slug(name)
        sizes[f"offers-{sl}"] = _dump(f"feed/offers-{sl}.json",
                                      {"store": name, "updated": OUT["updated"],
                                       "offers": lst})
        stores.append({"name": name, "slug": sl, "count": len(lst)})

    # --- официалните цени (тежки, но нужни само за кошницата) ---
    chains = sorted({b["chain"] for b in OUT["basics"]})
    sizes["basics"] = _dump("feed/basics.json",
                            {"updated": OUT["updated"],
                             "date": OUT.get("basics_date", ""),
                             "chains": chains,
                             "basics": OUT["basics"]})

    # --- компактен индекс за търсене (кратки ключове = малък файл) ---
    idx = []
    for o in OUT["offers"]:
        idx.append({"s": o["store"], "n": o["name"], "p": o["price"],
                    "o": o.get("old"), "u": o.get("unitPrice"),
                    "l": o.get("unitLabel", ""), "f": 0})
    for b in OUT["basics"]:
        idx.append({"s": b["chain"], "n": b["product"], "p": b["price"],
                    "o": None, "u": b.get("unitPrice"),
                    "l": b.get("unitLabel", ""), "f": 1})
    sizes["search"] = _dump("feed/search.json",
                            {"updated": OUT["updated"], "items": idx})

    # --- индексът: това е единственото, което се тегли при всяко отваряне ---
    top = sorted(
        OUT["offers"],
        key=lambda o: -(o.get("pct") or (
            int((1 - o["price"] / o["old"]) * 100) if o.get("old") else 0))
    )[:200]
    index = {
        "updated": OUT["updated"], "version": 3,
        "stores": stores, "chains": chains,
        "counts": {"offers": len(OUT["offers"]), "basics": len(OUT["basics"]),
                   "ebag": len(OUT["ebag"])},
        "ebag": OUT["ebag"],
        "top": top,
        "errors": OUT["errors"][:40],
        "stats": OUT["stats"],
    }
    sizes["index"] = _dump("feed/index.json", index)

    # --- пълният файл (както досега) ---
    sizes["data"] = _dump("data.json", OUT)

    OUT["stats"]["размери"] = {k: f"{v/1024:.0f} KB" for k, v in sizes.items()}
    OUT["stats"]["тегли се при отваряне"] = f"{sizes['index']/1024:.0f} KB"
    if sizes["data"] > 6_000_000:
        note("размер", f"пълният data.json е {sizes['data']/1024/1024:.1f} MB — "
                       f"новото приложение тегли само index.json "
                       f"({sizes['index']/1024:.0f} KB), но ако ползваш стара "
                       f"версия, намали max_per_store")
    # презапис със сметките за размерите вътре
    _dump("feed/index.json", {**index, "stats": OUT["stats"]})
    _dump("data.json", OUT)


# ---------------------------------------------------------------------------
def main():
    ctx = browser = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 2400},
                                  locale="bg-BG", user_agent=UA["User-Agent"])
    except Exception as e:
        log_err("playwright/start", e)

    try:
        scrape_ebag(ctx)
        scrape_dom_offers(ctx)
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    scrape_basics()

    OUT["stats"]["време общо"] = f"{elapsed_min():.1f} мин"
    OUT["stats"]["offers_total"] = len(OUT["offers"])
    OUT["stats"]["with_unit_price"] = sum(
        1 for o in OUT["offers"] + OUT["basics"] if "unitPrice" in o)

    write_output()

    print(f"ebag={len(OUT['ebag'])} offers={len(OUT['offers'])} "
          f"basics={len(OUT['basics'])} errors={len(OUT['errors'])}")
    for k, v in OUT["stats"].items():
        print(f"  {k}: {v}")
    for e in OUT["errors"][:20]:
        print(f"  ! {e}")


if __name__ == "__main__":
    main()
