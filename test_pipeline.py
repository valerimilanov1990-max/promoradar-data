"""Pure regression tests. No imports of the network-running scraper."""
import ast
import pathlib
import unittest
import json
import tempfile
import re
from unittest.mock import Mock
from validate_feed import validate

SOURCE = pathlib.Path(__file__).with_name("scraper.py")
tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
namespace = {"EUR_RATE": 1.95583, "CFG": {}, "UA": {}, "json": json, "re": re}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in {"collapse_currency", "pick_price_pair", "build_history", "publication_exclusion", "quarantine_unsafe_rows"}:
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)

class CurrencyTests(unittest.TestCase):
    def test_all_declared_discounts_normalize_eur(self):
        for pct in range(1, 96):
            with self.subTest(pct=pct):
                self.assertEqual(namespace["collapse_currency"]([17.49], ["eur"], pct), [34.21])

    def test_actual_51_percent_regression(self):
        values = namespace["collapse_currency"]([17.49, 35.79], ["eur", "eur"], 51)
        self.assertEqual(namespace["pick_price_pair"](values, 51), (34.21, 70.0))

    def test_actual_52_percent_regression(self):
        values = namespace["collapse_currency"]([4.39, 9.20], ["eur", "eur"], 52)
        self.assertEqual(namespace["pick_price_pair"](values, 52), (8.59, 17.99))

    def test_dual_display_is_one_price(self):
        for pct in [None, 49, 51]:
            self.assertEqual(len(namespace["collapse_currency"]([10, 19.56], ["eur", "bgn"], pct)), 1)

    def test_same_currency_half_discount_survives(self):
        self.assertEqual(namespace["collapse_currency"]([10, 19.56], ["bgn", "bgn"]), [10, 19.56])

    def test_empty_and_invalid(self):
        self.assertEqual(namespace["collapse_currency"]([], []), [])
        self.assertEqual(namespace["collapse_currency"]([0, -1], []), [])

class QuarantineTests(unittest.TestCase):
    def test_live_currency_regression_is_quarantined_not_repriced(self):
        row = {"product": "Маслини Каламата € 7,79 €", "price": 7.79}
        output = {"basics": [row]}
        rejected = namespace["quarantine_unsafe_rows"](output)
        self.assertEqual(output["basics"], [])
        self.assertEqual(rejected[0]["reason"], "ambiguous_eur_storage")
        self.assertEqual(row["price"], 7.79)

    def test_correctly_normalized_prices_and_coffee_survive(self):
        for name, price in [("Маслини 7,79 €", 15.24), ("Davidoff кафе", 10), ("Кентъки сос", 3)]:
            self.assertIsNone(namespace["publication_exclusion"]({"name": name, "price": price}))

    def test_live_tobacco_variants_are_excluded(self):
        for name in ["ЦИГАРИ МЕРИЛИН СЛИМС", "Merilyn Pink Slims FP", "THE KING CORE RED 100 LS", "CORSET LILAS HOLLOW FILTER", "ROTHMANS BLUE CLASSIC", "Вейп", "IQOS TEREA"]:
            self.assertEqual(namespace["publication_exclusion"]({"name": name, "price": 3}), "restricted_tobacco_nicotine", name)

    def test_every_price_surface_filtered_and_audited(self):
        output = {section: [{"name": "мляко", "price": 2}, {"name": "цигари", "price": 7}]
                  for section in ["offers", "basics", "community", "ebag"]}
        self.assertEqual(len(namespace["quarantine_unsafe_rows"](output)), 4)
        self.assertEqual(len(output["stats"]["quarantined_rows"]), 4)
        self.assertEqual(len(output["offers"]), 1)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.write("feed/index.json", {"updated": "2026-09-09", "stores": [{"slug": "a", "count": 1}]})
        self.write("feed/search.json", {"updated": "2026-09-09", "items": [{"n": "Мляко", "p": 2, "currency": "BGN"}]})
        self.write("feed/offers-a.json", {"updated": "2026-09-09", "offers": [{"name": "Мляко", "price": 2}]})
        self.write("feed/history.json", {"h": {"a": {"d": ["2026-09-09"], "p": [2]}}})

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_coherent_snapshot_passes(self):
        self.assertEqual(validate(self.root), [])

    def test_missing_store_fails(self):
        (self.root / "feed/offers-a.json").unlink()
        self.assertTrue(validate(self.root))

    def test_mixed_snapshots_fail(self):
        self.write("feed/search.json", {"updated": "2026-09-08", "items": [{"p": 2}]})
        self.assertIn("Search/index snapshot mismatch", validate(self.root))

    def test_currency_regression_fails(self):
        self.write("feed/search.json", {"updated": "2026-09-09", "items": [{"n": "Стълба 17,49 €", "p": 17.49}]})
        self.assertTrue(any("Unnormalized EUR" in e for e in validate(self.root)))

    def test_coverage_collapse_fails(self):
        self.write("baseline/feed/index.json", {"stores": [{"slug": "a", "count": 3}]})
        self.assertTrue(any("coverage" in e for e in validate(self.root, self.root / "baseline")))

    def test_history_loss_fails(self):
        self.write("baseline/feed/index.json", {"stores": [{"slug": "a", "count": 1}]})
        self.write("baseline/feed/history.json", {"h": {"a": {}, "b": {}, "c": {}}})
        self.assertTrue(any("history keys" in e for e in validate(self.root, self.root / "baseline")))

    def test_unavailable_or_empty_previous_history_aborts(self):
        for status, content, text in [(503, b"", ""), (200, b"{}", "{}"), (200, b"{}", '{"h":{}}')]:
            with self.subTest(status=status, body=text):
                namespace["requests"] = Mock(get=Mock(return_value=Mock(status_code=status, content=content, text=text)))
                with self.assertRaisesRegex(RuntimeError, "Previous price history unavailable"):
                    namespace["build_history"]()

if __name__ == "__main__":
    unittest.main()
