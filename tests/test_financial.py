import unittest
from decimal import Decimal

from saudi_bnpl.financial import money, stable_name, validate_amounts


class FinancialTests(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(validate_amounts("115", "10", "1.50", "103.50"),
                         (Decimal("115"), Decimal("10"), Decimal("1.50"), Decimal("103.50")))
        self.assertEqual(validate_amounts(100, 0, 0, 100)[1], 0)

    def test_rejects_bad_amounts(self):
        for values in [(100, 10, 1, 90), (0, 0, 0, 0), (-1, 0, 0, -1),
                       (100, -1, 0, 101), (100, 0, 1, 99), (100, 1, 0, 99.001)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_amounts(*values)
        for value in ("NaN", "Infinity", "1.001", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)

    def test_names_are_normalized_and_unambiguous(self):
        name = stable_name("BNPL-S-", " Company ", "Provider", "  Ref  1 ")
        self.assertEqual(name, stable_name("BNPL-S-", "company", "PROVIDER", "ref 1"))
        self.assertNotEqual(name, stable_name("BNPL-S-", "company", "provider", "ref 2"))
        self.assertNotEqual(stable_name("", "ab", "c"), stable_name("", "a", "bc"))
        self.assertEqual(len(name), 71)
        with self.assertRaises(ValueError):
            stable_name("", "Company", "  ")


if __name__ == "__main__":
    unittest.main()
