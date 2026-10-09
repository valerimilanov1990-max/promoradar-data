"""Conservative catalogue identifiers. No network, fuzzy matching or photo reuse.

A valid check digit is not proof that a barcode was issued to this product.
Name-based IDs are candidate keys, never proof that two variants are identical.
"""
import hashlib
import json
import math
import re
import unicodedata


def normalized_text(value):
    if not isinstance(value, str):
        return ""
    # Keep words, order, punctuation, accents and all variant/size information.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def validated_gtin(value):
    """Accept explicit GTIN-8/12/13/14 strings; never guess missing digits."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})", value):
        return None
    if not value.strip("0"):
        return None
    check = (-sum(int(d) * (3 if i % 2 == 0 else 1)
                  for i, d in enumerate(reversed(value[:-1])))) % 10
    return value.zfill(14) if check == int(value[-1]) else None


def identity_fields(row):
    """Return additive metadata without changing names, prices or images.

    Explicit source brand/pack fields can refine the full-name fingerprint.
    AI labels are intentionally not accepted here. Missing metadata keeps IDs
    scoped to the source store to avoid cross-chain merges of generic names.
    """
    codes = {code for key in ("gtin", "gtin14", "ean", "barcode")
             if (code := validated_gtin(row.get(key)))}
    if len(codes) == 1:
        return {"productId": "gtin:" + next(iter(codes)),
                "identityBasis": "gtin-checksum-v1"}
    name = normalized_text(row.get("name") or row.get("product"))
    if not name:
        return {}
    brand = normalized_text(row.get("brand"))
    pack = normalized_text(row.get("pack"))
    # qty/qtyUnit can come from a deterministic parser. Preserve explicit
    # quantity in the fingerprint but do not infer a brand or a variant.
    qty = row.get("qty")
    unit = normalized_text(row.get("qtyUnit"))
    if not pack and isinstance(qty, (float, int)) and not isinstance(qty, bool):
        if 0 < qty < float("inf") and unit:
            pack = format(qty, ".10g") + " " + unit
    scope = "" if brand and pack else normalized_text(row.get("store") or row.get("chain"))
    basis = "catalogue-name-v1" if brand and pack else "source-name-v1"
    payload = json.dumps([name, brand, pack, scope], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    result = {"productId": "name:" + digest, "identityBasis": basis}
    if len(codes) > 1:
        result["identityWarning"] = "conflicting_gtin"
    return result


def attach_identities(output):
    """Enrich each raw source row; consumers may ignore these optional keys."""
    counts = {}
    for section in ("offers", "basics", "community", "ebag"):
        for row in output.get(section, []):
            # Do not leave an obsolete supplied ID when metadata changes.
            for key in ("productId", "identityBasis", "identityWarning"):
                row.pop(key, None)
            row.update(identity_fields(row))
            basis = row.get("identityBasis", "missing-name")
            counts[basis] = counts.get(basis, 0) + 1
    output.setdefault("stats", {})["catalogue_identity"] = counts


def assert_publishable_output(output):
    """Fail before write_output touches any last-good files.

    The post-generation validate_feed.py gate still checks snapshot consistency,
    previous coverage and official-price provenance before remote publication.
    """
    failures = []
    if not isinstance(output.get("updated"), str) or not output["updated"].strip():
        failures.append("missing snapshot timestamp")
    if not isinstance(output.get("history"), dict) or not output["history"]:
        failures.append("missing preserved history")
    for section in ("offers", "basics", "community", "ebag"):
        rows = output.get(section)
        if not isinstance(rows, list):
            failures.append("invalid array: " + section)
            continue
        if section == "offers" and not rows:
            failures.append("empty offers catalogue")
        for row in rows:
            if not isinstance(row, dict):
                failures.append("invalid row: " + section)
                continue
            name = row.get("name") or row.get("product")
            price = row.get("price")
            if (not isinstance(name, str) or not name.strip()
                    or not isinstance(price, (int, float)) or isinstance(price, bool)
                    or not math.isfinite(price) or not 0 < price <= 5000):
                failures.append("invalid name/price: " + section)
            if section == "offers" and not (isinstance(row.get("store"), str) and row["store"].strip()):
                failures.append("missing store: offers")
    if failures:
        raise ValueError("Publication blocked before writing: " + "; ".join(failures[:10]))


def strip_advertising_suffix(name):
    """Remove only recognized trailing price/discount UI, not product variants.

    Prices are already stored separately. Run after extracting card/quantity
    metadata from the raw title. Names such as 'Нашата Трапеза', '100% Arabica',
    'Сос за 4 порции' and 'Кафе различни видове' must retain their meaning.
    """
    text = str(name or "")
    # A sequence of display prices (including dual currency) is not a size.
    text = re.sub(r"(?:(?:\s+|(?<=%))\d{1,4}[.,]\d{2}\s*(?:€|EUR|лв\.?|BGN))+\s*$", "", text, flags=re.I)
    for _ in range(3):
        previous = text
        text = re.sub(r"\s*[-–—]\s*\d{1,2}\s*%\s*$", "", text)
        text = re.sub(r"\s*[-–—]?\s*\d{1,2}\s*%\s*отстъпка(?:\s+с(?:\s+карта)?)?(?:\s*(?:кг|гр|мл|л|бр))?\s*$", "", text, flags=re.I)
        text = re.sub(r"\s+(?:отстъпка(?:\s+с)?|цена\s+с(?:\s+карта)?)\s*$", "", text, flags=re.I)
        text = text.strip()
        if text == previous:
            break
    return text if sum(c.isalpha() for c in text) >= 3 else name
