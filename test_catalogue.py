"""Offline catalogue/last-good safety regressions. Never starts the scraper."""
import ast
import copy
import json
import pathlib
import re
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from catalogue import (assert_publishable_output, attach_identities,
                       identity_fields, strip_advertising_suffix, validated_gtin)
from validate_feed import validate


class IdentityTests(unittest.TestCase):
    def test_gtin_checksum_and_canonical_padding(self):
        for raw, expected in [("96385074", "00000096385074"),
                              ("036000291452", "00036000291452"),
                              ("4006381333931", "04006381333931")]:
            self.assertEqual(validated_gtin(raw), expected)
        for raw in ["4006381333932", "0000000000000", "4006 381333931",
                    "EAN:4006381333931", 4006381333931, True, "123", None]:
            self.assertIsNone(validated_gtin(raw), raw)

    def test_same_gtin_across_sources_but_not_conflicting_gtins(self):
        a = identity_fields({"barcode": "4006381333931", "name": "Pen", "store": "A"})
        b = identity_fields({"ean": "04006381333931", "name": "Друг надпис", "store": "B"})
        self.assertEqual(a, b)
        conflict = identity_fields({"barcode": "4006381333931", "ean": "96385074", "name": "Pen"})
        self.assertEqual(conflict["identityWarning"], "conflicting_gtin")
        self.assertTrue(conflict["productId"].startswith("name:"))

    def test_known_full_catalogue_fields_are_stable_not_fuzzy(self):
        row = {"name": "Coffee Rich Aroma 100g", "brand": "Davidoff", "pack": "100g", "store": "A"}
        first = identity_fields(row)
        self.assertEqual(first, identity_fields({**row, "name": " COFFEE  RICH AROMA 100g ", "store": "B"}))
        self.assertEqual(first["identityBasis"], "catalogue-name-v1")
        for changed in [{"name": "Coffee Fine Aroma 100g"}, {"pack": "200g"}, {"brand": "Other"}]:
            self.assertNotEqual(first["productId"], identity_fields({**row, **changed})["productId"])

    def test_unknown_metadata_is_scoped_and_not_fabricated(self):
        row = {"name": "Кафе различни видове", "store": "A"}
        identity = identity_fields(row)
        self.assertEqual(identity["identityBasis"], "source-name-v1")
        self.assertNotEqual(identity["productId"], identity_fields({**row, "store": "B"})["productId"])
        self.assertEqual(set(identity), {"productId", "identityBasis"})

    def test_attach_does_not_change_images_prices_or_names(self):
        row = {"name": "Coffee Rich Aroma 100g", "store": "A", "price": 5.99,
               "img": "https://example.test/exact.jpg", "productId": "obsolete"}
        output = {"offers": [copy.deepcopy(row)], "basics": [], "community": [], "ebag": []}
        attach_identities(output)
        saved = output["offers"][0]
        for key in ("name", "price", "img"):
            self.assertEqual(saved[key], row[key])
        self.assertNotEqual(saved["productId"], "obsolete")


class NameTests(unittest.TestCase):
    def test_live_ebag_price_lines_exclude_unit_prices(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        helpers = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "JS_HELPERS" for t in node.targets))
        samples = [
            "~ 7.53 кг / бр\nБългарски продукт\nТиква Сорт Мускат де Прованс\n7,45 €\n10,47 €\n0,99 € за кг\nДобави\n−28%",
            "700 г\nБългарски продукт\nСливи Грийн Ред от Агротайм\n1,04 €\n1,39 €\n1,49 € за кг\nДобави\n−25%\nОТ ФЕРМАТА",
        ]
        js = "const x=JSON.parse(require('fs').readFileSync(0,'utf8')); const f=new Function('el','text',x.helpers+';return pricesOf(el,text);'); console.log(JSON.stringify(x.samples.map(s=>f({querySelectorAll:()=>[]},s))));"
        result = subprocess.run(["node", "-e", js], input=json.dumps({"helpers": helpers, "samples": samples}),
                                encoding="utf-8", capture_output=True, check=True)
        prices = json.loads(result.stdout)
        self.assertEqual(prices, [[{"v": 7.45, "c": "eur"}, {"v": 10.47, "c": "eur"}],
                                  [{"v": 1.04, "c": "eur"}, {"v": 1.39, "c": "eur"}]])

    def test_ebag_target_uses_verified_offers_route(self):
        config = json.loads(pathlib.Path(__file__).with_name("config.json").read_text(encoding="utf-8-sig"))
        targets = next(value for value in config.values() if isinstance(value, list)
                       and any(isinstance(row, dict) and row.get("name") == "eBag" for row in value))
        target = next(row for row in targets if row.get("name") == "eBag")
        self.assertIn("https://ebag.bg/offers?mode=promo", target["urls"])
        self.assertNotIn("https://www.ebag.bg/promo", target["urls"])
        self.assertIn("offers", target["keywords"])

    def test_discount_heading_without_product_is_junk(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        node = next(node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "JUNK_NAME_RE" for t in node.targets))
        namespace = {"re": re}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        self.assertTrue(namespace["JUNK_NAME_RE"].search("отстъпкакг-20%3,06 € 3,83 €"))
        self.assertFalse(namespace["JUNK_NAME_RE"].search("Сирене с отстъпка"))

    def test_full_clean_name_handles_glued_tails_after_unit_cleanup(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        namespace = {"re": re, "strip_advertising_suffix": strip_advertising_suffix}
        constants = {"_GLUE_CASE", "_GLUE_LAT_CYR", "_GLUE_CYR_LAT", "_GLUE_ALPHA_NUM", "_GLUE_NUM_ALPHA",
                     "_TAIL_RE", "_GLUED_PREP", "_MARKETING", "_GLUED_SUFFIX", "_TRAILING_UNIT", "_PRICE_TAIL"}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets)
                    or isinstance(node, ast.FunctionDef) and node.name in {"_split_glued_word", "clean_name"}):
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        self.assertEqual(namespace["clean_name"]("Дорада филе Отстъпкакг-20%6,54 € 8,18 €"), "Дорада филе")
        self.assertEqual(namespace["clean_name"]("Нашата Трапеза Шпек ТВПС"), "Нашата Трапеза Шпек ТВПС")

    def test_real_discount_tails(self):
        examples = [
            ("Попче Размразено -20% отстъпка кг -20% 4,49 € 5,62 €", "Попче Размразено"),
            ("Балканска пъстърва чистена-20% отстъпкакг-20%8,17 € 10,22 €", "Балканска пъстърва чистена"),
            ("Davidoff Кафе 100 г 6,99 € 13,67 лв.", "Davidoff Кафе 100 г"),
            ("Сирене цена с карта", "Сирене"),
        ]
        for before, after in examples:
            self.assertEqual(strip_advertising_suffix(before), after)

    def test_product_details_and_brands_are_kept(self):
        for name in ["Нашата Трапеза Шпек", "100% Arabica", "Сос за 4 порции",
                     "Кафе различни видове", "Мляко 3% 1 л", "Кафе Fine Aroma 250 г"]:
            self.assertEqual(strip_advertising_suffix(name), name)


class PreflightTests(unittest.TestCase):
    def good_output(self):
        return {"updated": "09.10.2026 12:00", "history": {"known": {}},
                "offers": [{"store": "A", "name": "Мляко", "price": 2}],
                "basics": [], "community": [], "ebag": []}

    def test_valid_snapshot(self):
        assert_publishable_output(self.good_output())

    def test_empty_and_invalid_catalogues_are_blocked(self):
        for bad in [{"offers": []}, {"offers": None}, {"history": {}}, {"updated": ""}]:
            with self.assertRaisesRegex(ValueError, "before writing"):
                assert_publishable_output({**self.good_output(), **bad})
        for price in [0, -1, True, float("nan"), float("inf"), "2"]:
            output = self.good_output()
            output["offers"][0]["price"] = price
            with self.assertRaises(ValueError):
                assert_publishable_output(output)

    def test_invalid_output_does_not_call_any_writer(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        write_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "write_output")
        writer = Mock()
        namespace = {"OUT": {**self.good_output(), "offers": []}, "_dump": writer,
                     "assert_publishable_output": assert_publishable_output,
                     "attach_identities": attach_identities}
        exec(compile(ast.Module(body=[write_node], type_ignores=[]), str(source), "exec"), namespace)
        with self.assertRaises(ValueError):
            namespace["write_output"]()
        writer.assert_not_called()

    def test_quarantine_uses_original_currency_evidence(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "publication_exclusion")
        namespace = {"re": re, "EUR_RATE": 1.95583}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        reason = namespace["publication_exclusion"]({"name": "Маслини", "rawName": "Маслини 7,79 €", "price": 7.79})
        self.assertEqual(reason, "ambiguous_eur_storage")

    def test_write_output_copies_identities_to_search_and_preserves_raw_fields(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        output = self.good_output()
        output.update(stats={}, errors=[])
        output["offers"][0].update(barcode="4006381333931", img="exact.jpg")
        files = {}
        def write(path, value):
            files[path] = copy.deepcopy(value)
            return len(json.dumps(value))
        namespace = {"OUT": output, "_dump": write, "slug": lambda x: "a",
                     "assert_publishable_output": assert_publishable_output,
                     "attach_identities": attach_identities, "identity_fields": identity_fields}
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "write_output")
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        namespace["write_output"]()
        offer = files["feed/offers-a.json"]["offers"][0]
        hit = files["feed/search.json"]["items"][0]
        self.assertEqual(hit["productId"], offer["productId"])
        self.assertEqual(hit["identityBasis"], "gtin-checksum-v1")
        self.assertEqual(offer["img"], "exact.jpg")
        self.assertEqual(hit["n"], "Мляко")
        self.assertEqual(hit["p"], 2)
        self.assertEqual(files["feed/index.json"]["updated"], files["feed/search.json"]["updated"])

    def test_clean_offer_preserves_source_name_and_card_restriction(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "clean_offer")
        namespace = {"re": re, "JUNK_IMG_RE": re.compile("never-match"), "clean_name": strip_advertising_suffix}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        row = namespace["clean_offer"]({"name": "Мляко цена с карта 1,99 €", "price": 3.89})
        self.assertEqual(row["name"], "Мляко")
        self.assertEqual(row["rawName"], "Мляко цена с карта 1,99 €")
        self.assertTrue(row["requiresCard"])

    def test_harvest_preserves_evidence_before_first_name_clean(self):
        source = pathlib.Path(__file__).with_name("scraper.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        harvest = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_harvest")
        loop = next(node for node in ast.walk(harvest) if isinstance(node, ast.For)
                    and isinstance(node.target, ast.Name) and node.target.id == "it")
        namespace = {"re": re, "store": "A", "found": [],
                     "items": [{"name": "Мляко цена с карта 1,99 €", "prices": [3.89]}],
                     "clean_name": strip_advertising_suffix,
                     "collapse_currency": lambda prices, *_: prices,
                     "pick_price_pair": lambda prices, *_: (prices[0], None),
                     "enrich": lambda row: row}
        exec(compile(ast.Module(body=[loop], type_ignores=[]), str(source), "exec"), namespace)
        row = namespace["found"][0]
        self.assertEqual(row["name"], "Мляко")
        self.assertEqual(row["rawName"], "Мляко цена с карта 1,99 €")
        self.assertTrue(row["requiresCard"])


class SnapshotValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.write("feed/index.json", {"updated": "2026-10-09", "stores": [{"slug": "a", "count": 1}], "counts": {"offers": 1, "basics": 0}})
        self.write("feed/search.json", {"updated": "2026-10-09", "items": [{"n": "Мляко", "p": 2, "f": 0}]})
        self.write("feed/offers-a.json", {"updated": "2026-10-09", "offers": [{"name": "Мляко", "price": 2}]})
        self.write("feed/history.json", {"h": {"a": {}}})

    def write(self, path, value):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value), encoding="utf-8")

    def test_current_snapshot_passes(self):
        self.assertEqual(validate(self.root), [])

    def test_empty_or_wrong_shape_search_fails_closed(self):
        for content in [[], None, {"updated": "2026-10-09", "items": []},
                        {"updated": "2026-10-09", "items": [None]},
                        {"updated": "2026-10-09", "items": {"n": "Мляко"}}]:
            self.write("feed/search.json", content)
            self.assertTrue(validate(self.root), content)

    def test_positive_index_does_not_hide_zero_offers(self):
        self.write("feed/offers-a.json", {"updated": "2026-10-09", "offers": []})
        self.assertTrue(any("Incomplete" in error for error in validate(self.root)))

    def test_booleans_and_invalid_store_prices_fail(self):
        for price in [True, 0, float("nan"), float("inf")]:
            self.write("feed/offers-a.json", {"updated": "2026-10-09", "offers": [{"name": "Мляко", "price": price}]})
            self.assertTrue(any("Invalid store offer" in error for error in validate(self.root)))

    def test_invalid_gtin_claim_is_not_accepted(self):
        self.write("feed/search.json", {"updated": "2026-10-09", "items": [{"n": "Мляко", "p": 2,
                   "productId": "gtin:4006381333932", "identityBasis": "gtin-checksum-v1"}]})
        self.assertTrue(any("Invalid catalogue identity" in error for error in validate(self.root)))

    def test_basics_collapse_against_last_good_is_blocked(self):
        self.write("baseline/feed/index.json", {"stores": [{"slug": "a", "count": 1}], "counts": {"offers": 1, "basics": 100}})
        self.assertTrue(any("Catalogue coverage" in error for error in validate(self.root, self.root / "baseline")))


if __name__ == "__main__":
    unittest.main()
