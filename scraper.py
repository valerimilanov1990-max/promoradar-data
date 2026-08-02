#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Промо Радар — дневен събирач на данни (върви на GitHub Actions).
Резултат: data.json с всички цени и оферти, готов за приложението.
Всяка секция е независима: ако една верига се счупи, другите продължават.
"""
import json, re, io, zipfile, datetime, traceback
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0 Safari/537.36",
      "Accept-Language": "bg-BG,bg;q=0.9"}
OUT = {"updated": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
       "ebag": [], "offers": [], "basics": [], "brochures": [], "errors": []}

# Продуктите и думите се четат от config.json (редактираш го в GitHub)
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
            # името: og:title в произволен ред на атрибутите, после <title>
            m_name = (re.search(r'og:title["\']?\s+content=["\']([^"\']+)', r.text)
                      or re.search(r'content=["\']([^"\']+)["\']\s+property=["\']og:title', r.text)
                      or re.search(r"<title>([^<]+)</title>", r.text))
            m_price = PRICE_RE.search(r.text)
            if m_name and m_price:
                name = re.sub(r"\s*-\s*eBag\.bg\s*$", "", m_name.group(1)).strip()
                OUT["ebag"].append({
                    "id": str(pid),
                    "name": name,
                    "price": float(m_price.group(1).replace(",", ".")),
                })
            else:
                OUT["errors"].append(f"ebag/{pid}: страницата се чете, но цена/име не се разпознават")
        except Exception as e:
            log_err(f"ebag/{pid}", e)

# ---------- 2. Кауфланд + Метро: текстови оферти ----------
def scrape_text_offers():
    pages = [
        ("Кауфланд", "https://www.kaufland.bg/aktualni-predlozheniya/ot-ponedelnik.html"),
        ("Кауфланд", "https://www.kaufland.bg/aktualni-predlozheniya/oferti.html"),
        ("Метро", "https://www.metro.bg/"),
    ]
    for store, url in pages:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code != 200:
                OUT["errors"].append(f"offers/{store}: HTTP {r.status_code}")
                continue
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text)
            for m in PRICE_RE.finditer(text):
                price = float(m.group(1).replace(",", "."))
                if not (0.1 <= price <= 500):
                    continue
                start = max(0, m.start() - 70)
                name = text[start:m.start()]
                name = re.sub(r"[\d.,%€*|<>_=+~^]", " ", name)
                name = re.sub(r"\s+", " ", name).strip()
                name = " ".join(name.split(" ")[-7:])
                if sum(c.isalpha() for c in name) < 6:
                    continue
                OUT["offers"].append({"store": store, "name": name, "price": price})
        except Exception as e:
            log_err(f"offers/{store}", e)
    # дедупликация
    seen, ded = set(), []
    for o in OUT["offers"]:
        k = (o["store"], o["name"].lower(), o["price"])
        if k not in seen:
            seen.add(k); ded.append(o)
    OUT["offers"] = ded[:300]

# Нормализация на имената на веригите + филтър (архивът съдържа и аптеки)
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
        return None  # не е хранителна верига
    for key, nice in CHAIN_MAP:
        if key in up:
            return nice
    # непозната верига: чистим служебните суфикси и съкращаваме
    import re as _re
    clean = _re.sub(r"_\d+$", "", raw).strip()
    return clean[:30] if clean else None

# ---------- 3. kolkostruva.bg: основните стоки ----------
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

# ---------- 4. Лидл + Билла: брошурите (Playwright + OCR) ----------
def scrape_brochures():
    try:
        from playwright.sync_api import sync_playwright
        import pytesseract
        from PIL import Image
    except ImportError as e:
        log_err("brochures/imports", e); return
    targets = [("Лидл", "https://www.lidl.bg/c/broshura/s10020060"),
               ("Билла", "https://www.billa.bg/promocii")]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for store, url in targets:
            try:
                pg = browser.new_page(viewport={"width": 1280, "height": 2000})
                pg.goto(url, wait_until="networkidle", timeout=60000)
                pg.wait_for_timeout(4000)
                for screen in range(6):
                    shot = pg.screenshot(full_page=False)
                    txt = pytesseract.image_to_string(
                        Image.open(io.BytesIO(shot)), lang="bul+eng")
                    clean = re.sub(r"\s+", " ", txt)
                    for m in re.finditer(r"(\d{1,3}[.,]\d{2})", clean):
                        price = float(m.group(1).replace(",", "."))
                        if not (0.1 <= price <= 500): continue
                        start = max(0, m.start() - 55)
                        name = clean[start:m.start()]
                        name = re.sub(r"[\d.,%€*|<>_=+~^\"']", " ", name)
                        name = re.sub(r"\s+", " ", name).strip()
                        name = " ".join(name.split(" ")[-6:])
                        if sum(c.isalpha() for c in name) < 5: continue
                        OUT["brochures"].append(
                            {"store": store, "name": name, "price": price})
                    pg.mouse.wheel(0, 1800)
                    pg.wait_for_timeout(1500)
                pg.close()
            except Exception as e:
                log_err(f"brochures/{store}", e)
        browser.close()
    seen, ded = set(), []
    for o in OUT["brochures"]:
        k = (o["store"], o["name"].lower(), o["price"])
        if k not in seen:
            seen.add(k); ded.append(o)
    OUT["brochures"] = ded[:200]

if __name__ == "__main__":
    scrape_ebag()
    scrape_text_offers()
    scrape_basics()
    scrape_brochures()
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=1)
    print(f"ebag={len(OUT['ebag'])} offers={len(OUT['offers'])} "
          f"basics={len(OUT['basics'])} brochures={len(OUT['brochures'])} "
          f"errors={len(OUT['errors'])}")
