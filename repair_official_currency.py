"""Rebuild official prices from their original dated archive. No Git/network writes.

Usage: python repair_official_currency.py BASELINE OUTPUT
BASELINE is an untouched checkout of the published data branch; OUTPUT must not exist.
Original snapshot and history remain in BASELINE. Publish OUTPUT only after validation.
"""
import copy
import datetime
import io
import json
import os
import pathlib
import shutil
import sys
import zipfile

import requests

ROOT = pathlib.Path(__file__).resolve().parent
os.chdir(ROOT)
import scraper as s
from validate_feed import validate


def repair(baseline, output):
    if output.exists():
        raise RuntimeError("Output already exists; refusing to overwrite")
    data = json.loads((baseline / "data.json").read_text(encoding="utf-8-sig"))
    source_date = data["basics_date"]
    datetime.date.fromisoformat(source_date)
    original = data["basics"]
    if any(b.get("normalization") == "kzp-currency-v1" for b in original):
        raise RuntimeError("Snapshot already contains corrected rows; do not reconvert")
    wanted = {(b["chain"], b["product"]) for b in original}
    chains = {b["chain"] for b in original}
    archive_url = s.CFG.get("food_base", "https://kolkostruva.bg/opendata_files/") + source_date + ".zip"
    response = requests.get(archive_url, timeout=90)
    response.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    found = {}
    for name in archive.namelist():
        chain = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if s.norm_chain(chain) not in chains:
            continue
        rows = []
        if name.lower().endswith(".csv"):
            s._read_csv(archive.read(name), chain, rows, source_date)
        elif name.lower().endswith((".xlsx", ".xls")):
            s._read_xlsx(archive.read(name), chain, rows, source_date)
        for row in rows:
            key = (row["chain"], row["product"])
            if key in wanted and (key not in found or row["price"] < found[key]["price"]):
                found[key] = row
    missing = wanted - found.keys()
    if missing:
        raise RuntimeError(f"Original source missing {len(missing)} products: {list(missing)[:5]}")
    repaired = []
    for before in original:
        after = copy.deepcopy(found[(before["chain"], before["product"])])
        after.pop("name", None)
        if "k" in before:
            after["k"] = before["k"]
        repaired.append(after)
    data["basics"] = repaired
    # Reuse existing labels locally; no paid AI invocation. Unit prices are
    # recomputed from corrected prices, not rescaled rounded unit prices.
    s.OUT = data
    s.apply_ai_quantities()
    quarantine = s.quarantine_unsafe_rows(data)
    data["updated"] = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    history = json.loads((baseline / "feed/history.json").read_text(encoding="utf-8-sig"))["h"]
    today = datetime.date.today().isoformat()
    reset = 0
    for b in data["basics"]:
        key = s._hist_key(b["chain"], b["product"])
        if key in history:
            reset += 1
        history[key] = {"d": [today], "p": [b["price"]], "l": today,
                        "normalization": "kzp-currency-v1",
                        "resetReason": "prior_official_currency_unknown"}
    data["history"] = history
    data["stats"]["official_currency_repair"] = {
        "version": "kzp-currency-v1", "sourceDate": source_date,
        "corrected": len(data["basics"]), "historyReset": reset,
        "note": "Currency repair, not a new observation of offer prices"}
    shutil.copytree(baseline, output, ignore=shutil.ignore_patterns(".git"))
    (output.parent / "source-archive.zip").write_bytes(response.content)
    os.chdir(output)
    s.write_output()
    failures = validate(output, baseline)
    report = {"source": archive_url, "before": len(original),
              "after": len(data["basics"]), "quarantined": len(quarantine),
              "historyReset": reset, "failures": failures,
              "milk": [b for b in data["basics"] if b["chain"] == "ТАРИТА"
                       and "ПРЯСНО МЛЯКО БОР-ЧВОР" in b["product"]]}
    (output.parent / "repair-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output.parent / "quarantine.json").write_text(json.dumps(quarantine, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise RuntimeError("Publication blocked by validation")


if __name__ == "__main__":
    repair(pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve())
