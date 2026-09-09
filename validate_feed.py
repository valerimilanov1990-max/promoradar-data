"""Fail closed before publication. Usage: python validate_feed.py ROOT [BASELINE]."""
import json
import math
import pathlib
import re
import sys

def validate(root, baseline=None):
    errors = []
    def read(relative):
        try:
            return json.loads((root / relative).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")
            return {}
    index = read("feed/index.json")
    search = read("feed/search.json")
    history = read("feed/history.json")
    rows = search.get("items", [])
    if not rows or not index.get("stores") or not history.get("h"):
        errors.append("Missing non-empty search, stores or preserved history")
    if search.get("updated") != index.get("updated"):
        errors.append("Search/index snapshot mismatch")
    for ref in index.get("stores", []):
        slug = ref.get("slug", "")
        if not re.fullmatch(r"[a-z0-9_-]+", slug):
            errors.append(f"Invalid store slug: {slug}")
            continue
        feed = read(f"feed/offers-{slug}.json")
        if feed.get("updated") != index.get("updated") or len(feed.get("offers", [])) != ref.get("count"):
            errors.append(f"Incomplete store snapshot: {slug}")
    for row in rows:
        price = row.get("p")
        if not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0:
            errors.append(f"Invalid price: {row.get('n')}")
            continue
        if row.get("currency", "BGN") != "BGN":
            errors.append(f"Unsupported storage currency: {row.get('n')}")
        old = row.get("o")
        if old is not None and (not isinstance(old, (float, int)) or not math.isfinite(old) or old <= price):
            errors.append(f"Invalid old price: {row.get('n')}")
        eur = [float(x.replace(',', '.')) for x in re.findall(r"(\d+[.,]\d{2})\s*(?:€|EUR)", row.get("n", ""), re.I)]
        if eur and any(abs(price - x) < 0.02 for x in eur) and not any(abs(price / 1.95583 - x) < 0.02 for x in eur):
            errors.append(f"Unnormalized EUR: {row.get('n')}")
    if baseline and (baseline / "feed/index.json").exists():
        previous = json.loads((baseline / "feed/index.json").read_text(encoding="utf-8-sig"))
        counts = {r["slug"]: r["count"] for r in index.get("stores", [])}
        for ref in previous.get("stores", []):
            if counts.get(ref["slug"], 0) < ref["count"] * 0.5:
                errors.append(f"Store coverage dropped by >50%: {ref['slug']}")
        old_history_path = baseline / "feed/history.json"
        if old_history_path.exists():
            old_history = json.loads(old_history_path.read_text(encoding="utf-8-sig")).get("h", {})
            retained = len(set(old_history) & set(history.get("h", {})))
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
