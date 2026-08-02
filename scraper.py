#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Промо Радар — дневен събирач на данни v2 (върви на GitHub Actions).

v2: БЕЗ OCR. Истинският браузър (Playwright) чете продуктовите плочки
директно от сайтовете — име, цена, стара цена, СНИМКА и линк.
Резултат: data.json. Всяка секция е независима.
"""
import json, re, io, zipfile, datetime, traceback
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0 Safari/537.36",
      "Accept-Language": "bg-BG,bg;q=0.9"}
OUT = {"updated": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
       "ebag": [], "offers": [], "basics": [], "brochures": [], "errors": []}

CFG = json.load(open("config.json", encoding="utf-8"))

def log_err(section, e):
    OUT["errors"].append(f"{section}: {type(e).__name__}: {e}")
    traceback.print_exc()

PRICE_RE = re.compile(r"(\d{1,3}[.,]\d{2})\s*лв")

# ---------- 1. eBag: точни продукти ----------
def scrape_ebag():
    for pid in CFG.get("ebag_ids", []):
        try:
            r = requests.get(f"https://www.ebag.bg/x/{pid}", headers=UA, timeout=30)
            if r.status_code != 200:
                OUT["errors"].append(f"ebag/{pid}: HTTP {r.status_code}")
                continue
            m_name = (re.search(r'og:title["\']?\s+content=["\']([^"\']+)', r.text)
                      or re.search(r'content=["\']([^"\']+)["\']\s+property=["\']og:title', r.text)
                      or re.search(r"<title>([^<]+)</title>", r.text))
            m_price = PRICE_RE.search(r.text)
            if m_name and m_price:
                name = re.sub(r"\s*-\s*eBag\.bg\s*$", "", m_name.group(1)).strip()
                OUT["ebag"].append({
                    "id": str(pid), "name": name,
                    "price": float(m_price.group(1).replace(",", ".")),
                })
            else:
                OUT["errors"].append(f"ebag/{pid}: цена/име не се разпознават")
        except Exception as e:
            log_err(f"ebag/{pid}", e)

# ---------- 2. Оферти с картинки: DOM екстракция (без OCR!) ----------
# Взимаме продуктовите плочки директно от рендирания сайт: всяка плочка
# е <a> с <img> и цена в текста. Име = alt на снимката, линкът е истински.
TILE_JS = """
() => {
  const out = [];
  document.querySelectorAll('a').forEach(a => {
    const img = a.querySelector('img');
    if (!img) return;
    const t = (a.innerText || '').trim();
    const ms = [...t.matchAll(/(\\d{1,3}[.,]\\d{2})/g)].map(x => parseFloat(x[1].replace(',', '.')));
    if (!ms.length) return;
    const name = ((img.alt || '').trim() || t.split('\\n')[0] || '').trim();
    out.push({name: name, prices: ms,
              img: img.currentSrc || img.src || '',
              url: a.href || ''});
  });
  return out;
}
"""

def scrape_dom_offers():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        log_err("dom/imports", e); return

    targets = [
        ("Кауфланд", ["https://www.kaufland.bg/aktualni-predlozheniya/ot-ponedelnik.html",
                      "https://www.kaufland.bg/aktualni-predlozheniya/oferti.html"]),
        ("Лидл", ["https://www.lidl.bg/"]),
        ("Метро", ["https://www.metro.bg/oferti/top-oferti",
                   "https://www.metro.bg/"]),
        ("Билла", ["https://www.billa.bg/promocii"]),
    ]

    def harvest(ctx, store, url):
        pg = ctx.new_page()
        found = []
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(5000)
            for _ in range(4):  # ленивите снимки се зареждат при скрол
                pg.mouse.wheel(0, 2200)
                pg.wait_for_timeout(900)
            items = pg.evaluate(TILE_JS)
            extra_links = []
            if store == "Лидл" and url.rstrip("/").endswith("lidl.bg"):
                links = pg.evaluate(
                    "() => [...document.querySelectorAll('a[href*=\\\"/c/\\\"]')].map(a=>a.href)")
                seen = set()
                for l in links:
                    if any(k in l for k in ("aktsiya", "oferti", "predlozheni", "top-")) \
                            and l not in seen:
                        seen.add(l); extra_links.append(l)
                extra_links = extra_links[:3]
            for it in items:
                prices = [x for x in it.get("prices", []) if 0.1 <= x <= 1000]
                if not prices:
                    continue
                name = re.sub(r"\s+", " ", it.get("name") or "").strip()
                if sum(c.isalpha() for c in name) < 5:
                    continue
                price = min(prices)
                old = max(prices) if len(prices) > 1 and max(prices) > price * 1.02 else None
                found.append({"store": store, "name": name[:90], "price": round(price, 2),
                              "old": round(old, 2) if old else None,
                              "img": (it.get("img") or "")[:300],
                              "url": (it.get("url") or "")[:300]})
            return found, extra_links
        except Exception as e:
            log_err(f"dom/{store}/{url[:40]}", e)
            return found, []
        finally:
            pg.close()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 2400},
                                  locale="bg-BG", user_agent=UA["User-Agent"])
        for store, urls in targets:
            per_store = []
            queue = list(urls)
            visited = set()
            while queue:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                items, extra = harvest(ctx, store, url)
                per_store += items
                queue += [l for l in extra if l not in visited]
            # дедупликация и лимит на магазин
            seen, ded = set(), []
            for o in per_store:
                k = (o["name"].lower(), o["price"])
                if k not in seen:
                    seen.add(k); ded.append(o)
            OUT["offers"] += ded[:60]
            if not ded:
                OUT["errors"].append(f"dom/{store}: 0 плочки — проверка на селекторите")
        browser.close()

    # съвместимост: „brochures“ = офертите на Лидл и Билла
    OUT["brochures"] = [o for o in OUT["offers"] if o["store"] in ("Лидл", "Билла")]

# ---------- 3. kolkostruva.bg: основните стоки ----------
CHAIN_MAP = [("КАУФЛАНД", "Кауфланд"), ("KAUFLAND", "Кауфланд"),
             ("БИЛЛА", "Билла"), ("BILLA", "Билла"),
             ("ЛИДЛ", "Лидл"), ("LIDL", "Лидл"),
             ("МЕТРО", "Метро"), ("METRO", "Метро"),
             ("ФАНТАСТИКО", "Фантастико"), ("Т МАРКЕТ", "T-Market"),
             ("T MARKET", "T-Market"), ("CBA", "CBA"), ("БУЛМАГ", "BulMag")]
SKIP_CHAIN = ("АПТЕКА", "ФАРМА", "ДРОГЕРИЯ", "PHARM")

def norm_chain(raw):
    up = raw.upper()
    if any(k in up for k in SKIP_CHAIN):
        return None
    for key, nice in CHAIN_MAP:
        if key in up:
            return nice
    clean = re.sub(r"_\d+$", "", raw).strip()
    clean = clean.split("(")[0].strip()  # маха „(ЕТ ...)“ юридическите опашки
    return clean[:24] if clean else None

def scrape_basics():
    import csv
    day = datetime.date.today()
    for back in range(8):
        d = (day - datetime.timedelta(days=back)).isoformat()
        try:
            r = requests.get(f"https://kolkostruva.bg/opendata_files/{d}.zip",
                             headers=UA, timeout=60)
            if r.status_code != 200 or len(r.content) < 1000:
                continue
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            rows = []
            for nm in zf.namelist():
                low = nm.lower()
                chain = nm.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                try:
                    if low.endswith(".csv"):
                        raw = zf.read(nm)
                        for enc in ("utf-8-sig", "windows-1251", "utf-8"):
                            try:
                                textdata = raw.decode(enc); break
                            except UnicodeDecodeError:
                                continue
                        sniff = textdata.splitlines()[0] if textdata else ""
                        delim = max([";", ",", "\t"], key=sniff.count)
                        rdr = csv.reader(io.StringIO(textdata), delimiter=delim)
                        hdr = [h.strip().lower() for h in next(rdr, [])]
                        def col(*names):
                            for i, h in enumerate(hdr):
                                if any(n in h for n in names): return i
                            return -1
                        pc = col("продукт", "стока", "наименование", "артикул", "product", "name")
                        cc = col("цена", "price")
                        if pc < 0 or cc < 0: continue
                        for row in rdr:
                            try:
                                price = float(re.sub(r"[^\d.]", "",
                                              row[cc].replace(",", ".")))
                                nmv = row[pc].strip()
                                ch = norm_chain(chain)
                                if ch and nmv and 0 < price < 1000:
                                    rows.append({"chain": ch, "product": nmv, "price": price})
                            except Exception:
                                pass
                    elif low.endswith((".xlsx", ".xls")):
                        from openpyxl import load_workbook
                        wb = load_workbook(io.BytesIO(zf.read(nm)), read_only=True)
                        ws = wb.active
                        it = ws.iter_rows(values_only=True)
                        hdr = [str(h or "").strip().lower() for h in next(it, [])]
                        def col2(*names):
                            for i, h in enumerate(hdr):
                                if any(n in h for n in names): return i
                            return -1
                        pc = col2("продукт", "стока", "наименование", "артикул")
                        cc = col2("цена")
                        if pc < 0 or cc < 0: continue
                        for row in it:
                            try:
                                price = float(str(row[cc]).replace(",", "."))
                                nmv = str(row[pc] or "").strip()
                                ch = norm_chain(chain)
                                if ch and nmv and 0 < price < 1000:
                                    rows.append({"chain": ch, "product": nmv, "price": price})
                            except Exception:
                                pass
                except Exception as e:
                    log_err(f"basics/{nm}", e)
            if rows:
                OUT["basics"] = rows[:8000]
                OUT["basics_date"] = d
                return
        except Exception as e:
            log_err(f"basics/{d}", e)

if __name__ == "__main__":
    scrape_ebag()
    scrape_dom_offers()
    scrape_basics()
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=1)
    print(f"ebag={len(OUT['ebag'])} offers={len(OUT['offers'])} "
          f"basics={len(OUT['basics'])} errors={len(OUT['errors'])}")
