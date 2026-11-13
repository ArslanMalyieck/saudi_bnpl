import unittest
from decimal import Decimal

from saudi_bnpl.financial import payment_claim_name, payment_row_ids, selected_amount, selection_hash, money, stable_name, validate_amounts


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

    def test_payment_row_normalization_hash_and_signed_sum(self):
        rows = [
            {"selected": 1, "payment_row_ids": '["row-b","row-a"]', "provider_amount": "115.00"},
            {"selected": 1, "payment_row_ids": '["return-row"]', "provider_amount": "-15.00"},
            {"selected": 0, "payment_row_ids": '["ignored"]', "provider_amount": "99.00"},
        ]
        self.assertEqual(payment_row_ids(rows[0]["payment_row_ids"]), ("row-a", "row-b"))
        self.assertEqual(selected_amount(rows), Decimal("100.00"))
        self.assertEqual(selection_hash(rows), selection_hash(list(reversed(rows))))
        self.assertEqual(payment_claim_name("row-a"), payment_claim_name("row-a"))
        self.assertNotEqual(payment_claim_name("row-a"), payment_claim_name("row-b"))
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
