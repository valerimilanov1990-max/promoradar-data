"""Fail closed before publication. Usage: python validate_feed.py ROOT [BASELINE]."""
import json
import math
import pathlib
import re
import sys

from catalogue import validated_gtin

def validate(root, baseline=None):
    errors = []
    def read(relative):
        try:
            value = json.loads((root / relative).read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("Expected JSON object")
            return value
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")
            return {}
    index = read("feed/index.json")
    search = read("feed/search.json")
    history = read("feed/history.json")
    def object_rows(value, label):
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            errors.append(f"Invalid array of objects: {label}")
            return []
        return value
    rows = object_rows(search.get("items", []), "search.items")
    stores = object_rows(index.get("stores", []), "index.stores")
    history_keys = history.get("h", {})
    if not isinstance(history_keys, dict):
        errors.append("Invalid history map")
        history_keys = {}
    if not rows or not stores or not history_keys:
        errors.append("Missing non-empty search, stores or preserved history")
    if not isinstance(index.get("updated"), str) or not index["updated"].strip():
        errors.append("Missing snapshot timestamp")
    if search.get("updated") != index.get("updated"):
        errors.append("Search/index snapshot mismatch")
    store_slugs = set()
    declared_offers = 0
    for ref in stores:
        slug = ref.get("slug", "")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9_-]+", slug):
            errors.append(f"Invalid store slug: {slug}")
            continue
        if slug in store_slugs:
            errors.append(f"Duplicate store slug: {slug}")
        store_slugs.add(slug)
        count = ref.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            errors.append(f"Invalid non-empty store count: {slug}")
        else:
            declared_offers += count
        feed = read(f"feed/offers-{slug}.json")
        offers = object_rows(feed.get("offers", []), f"offers-{slug}.offers")
        if feed.get("updated") != index.get("updated") or len(offers) != count:
            errors.append(f"Incomplete store snapshot: {slug}")
        for offer in offers:
            price = offer.get("price")
            if (not isinstance(offer.get("name"), str) or not offer["name"].strip()
                    or not isinstance(price, (float, int)) or isinstance(price, bool)
                    or not math.isfinite(price) or price <= 0):
                errors.append(f"Invalid store offer: {slug}")
    actual_counts = {"offers": sum(row.get("f", 0) == 0 for row in rows),
                     "basics": sum(row.get("f") == 1 for row in rows)}
    if actual_counts["offers"] != declared_offers:
        errors.append("Offer/search coverage mismatch")
    counts = index.get("counts", {})
    if not isinstance(counts, dict):
        errors.append("Invalid index counts")
        counts = {}
    for section in ("offers", "basics"):
        if section in counts and counts[section] != actual_counts[section]:
            errors.append(f"Index/search count mismatch: {section}")
    for row in rows:
        price = row.get("p")
        name = row.get("n")
        if not isinstance(name, str) or not name.strip():
            errors.append("Missing search product name")
            name = ""
        if not isinstance(price, (float, int)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0:
            errors.append(f"Invalid price: {row.get('n')}")
            continue
        if row.get("currency", "BGN") != "BGN":
            errors.append(f"Unsupported storage currency: {row.get('n')}")
        old = row.get("o")
        if old is not None and (not isinstance(old, (float, int)) or isinstance(old, bool) or not math.isfinite(old) or old <= price):
            errors.append(f"Invalid old price: {row.get('n')}")
        eur = [float(x.replace(',', '.')) for x in re.findall(r"(\d+[.,]\d{2})\s*(?:€|EUR)", name, re.I)]
        if eur and any(abs(price - x) < 0.02 for x in eur) and not any(abs(price / 1.95583 - x) < 0.02 for x in eur):
            errors.append(f"Unnormalized EUR: {row.get('n')}")
        product_id = row.get("productId")
        basis = row.get("identityBasis")
        if product_id is not None:
            if not isinstance(product_id, str) or not (
                product_id.startswith("gtin:") and validated_gtin(product_id[5:]) == product_id[5:]
                and basis == "gtin-checksum-v1"
                or re.fullmatch(r"name:[0-9a-f]{32}", product_id)
                and basis in ("catalogue-name-v1", "source-name-v1")
            ):
                errors.append(f"Invalid catalogue identity: {name}")
        if row.get("f") == 1:
            source = row.get("sourcePrice")
            date = row.get("sourceDate", "")
            if not isinstance(date, str):
                date = ""
            currency = "EUR" if date >= "2026-01-01" else "BGN"
            if (row.get("normalization") != "kzp-currency-v1"
                    or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
                    or row.get("sourceCurrency") != currency
                    or not isinstance(source, (int, float)) or isinstance(source, bool) or not math.isfinite(source)
                    or source <= 0):
                errors.append(f"Missing official price provenance: {row.get('n')}")
            elif abs(price - round(source * (1.95583 if currency == "EUR" else 1), 2)) > 0.001:
                errors.append(f"Official currency conversion mismatch: {row.get('n')}")
    if any(row.get("f") == 1 for row in rows):
        basics = read("feed/basics.json")
        if basics.get("updated") != index.get("updated"):
            errors.append("Basics/index snapshot mismatch")
        basic_rows = object_rows(basics.get("basics", []), "basics.basics")
        lookup = {(b.get("chain"), b.get("product")): b for b in basic_rows}
        for row in rows:
            if row.get("f") == 1:
                b = lookup.get((row.get("s"), row.get("n")))
                if not b or b.get("price") != row.get("p") or b.get("unitPrice") != row.get("u"):
                    errors.append(f"Basics/search price mismatch: {row.get('n')}")
    if baseline and (baseline / "feed/index.json").exists():
        previous = json.loads((baseline / "feed/index.json").read_text(encoding="utf-8-sig"))
        counts = {r.get("slug"): r.get("count", 0) for r in stores if isinstance(r.get("count"), int)}
        for ref in previous.get("stores", []):
            if counts.get(ref["slug"], 0) < ref["count"] * 0.5:
                errors.append(f"Store coverage dropped by >50%: {ref['slug']}")
        previous_counts = previous.get("counts", {})
        for section in ("offers", "basics"):
            old_count = previous_counts.get(section, 0)
            if isinstance(old_count, (int, float)) and actual_counts[section] < old_count * 0.5:
                errors.append(f"Catalogue coverage dropped by >50%: {section}")
        old_history_path = baseline / "feed/history.json"
        if old_history_path.exists():
            old_history = json.loads(old_history_path.read_text(encoding="utf-8-sig")).get("h", {})
            retained = len(set(old_history) & set(history_keys))
            if retained < len(old_history) * 0.5:
                errors.append("More than 50% of history keys disappeared; review retention before publishing")
    return errors

if __name__ == "__main__":
    failures = validate(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "."),
        pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    print("\n".join(failures[:30]) if failures else "Feed quality gates passed")
    if failures:
        print(f"{len(failures)} failures — publication blocked")
    sys.exit(bool(failures))
