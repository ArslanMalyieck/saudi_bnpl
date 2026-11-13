import unittest
from datetime import date, datetime
from unittest.mock import patch

from saudi_bnpl.saudi_bnpl.report.bnpl_provider_ledger import bnpl_provider_ledger as report


class ProviderLedgerTests(unittest.TestCase):
    def test_draft_settlement_is_visible_but_does_not_reduce_posted_balance(self):
        rows = [
            self.row(datetime(2026, 11, 1, 10), "Sale", 115, 115, 0),
            self.row(datetime(2026, 11, 2, 10), "Return", -15, 0, 15),
            self.row(datetime(2026, 11, 3, 23, 59), "Settlement", 0, 0, 100),
        ]
        with patch.object(report, "_", lambda value: value):
            data, totals = report.apply_running_balance(rows)
        self.assertEqual([row["posted_running_balance"] for row in data], [115.0, 100.0, 100.0])
        self.assertEqual(float(totals["settlement_credit"]), 0)
        self.assertEqual(float(totals["closing_balance"]), 100)

    def test_submitted_settlement_reduces_balance_and_opening_is_retained(self):
        rows = [
            self.row(datetime(2026, 10, 31, 10), "Sale", 115, 115, 0),
            self.row(datetime(2026, 11, 2, 23, 59), "Settlement", -115, 0, 115),
        ]
        with patch.object(report, "_", lambda value: value):
            data, totals = report.apply_running_balance(rows, date(2026, 11, 1))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["posted_running_balance"], 0.0)
        self.assertEqual(float(totals["settlement_credit"]), 115)
        self.assertEqual(float(totals["closing_balance"]), 0)

    @staticmethod
    def row(posting_datetime, entry_type, delta, debit, credit):
        return {
            "posting_datetime": posting_datetime,
            "posting_date": posting_datetime.date(),
            "entry_type": entry_type,
            "posted_delta": delta,
            "debit": debit,
            "credit": credit,
        }


if __name__ == "__main__":
    unittest.main()
