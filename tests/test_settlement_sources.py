import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement import bnpl_settlement as module


class Rejected(Exception):
    pass


def reject(message, *args):
    raise Rejected(message)


class SourceTests(unittest.TestCase):
    def test_query_has_canonical_branches_and_inclusive_parameters(self):
        db = MagicMock()
        db.sql.return_value = []
        provider = SimpleNamespace(mode_of_payment="BNPL", clearing_account="Clearing")
        with patch.object(module, "frappe", SimpleNamespace(db=db)):
            module._query_payment_rows("Company", "Profile", provider, date(2026, 11, 1), date(2026, 11, 30))
        sql, params = db.sql.call_args.args[:2]
        self.assertIn("ifnull(si.is_consolidated, 0) = 0", sql)
        self.assertIn("consolidated.docstatus = 1", sql)
        self.assertEqual(sql.count("between %s and %s"), 2)
        self.assertEqual(params[:6], params[6:])

    def test_groups_multiple_rows_and_keeps_signed_return_amount(self):
        rows = [
            SimpleNamespace(payment_row="pay-2", source_invoice="SI-1", source_doctype="Sales Invoice", posting_date="2026-11-01", posting_time="10:00:00", is_return=0, gl_voucher="SI-1", base_amount=15, base_change_amount=0, account_for_change_amount=None),
            SimpleNamespace(payment_row="pay-1", source_invoice="SI-1", source_doctype="Sales Invoice", posting_date="2026-11-01", posting_time="10:00:00", is_return=0, gl_voucher="SI-1", base_amount=100, base_change_amount=0, account_for_change_amount=None),
            SimpleNamespace(payment_row="return-1", source_invoice="SI-RET", source_doctype="Sales Invoice", posting_date="2026-11-02", posting_time="10:00:00", is_return=1, gl_voucher="SI-RET", base_amount=-15, base_change_amount=0, account_for_change_amount=None),
        ]
        fake = SimpleNamespace(utils=SimpleNamespace(get_datetime=lambda value: datetime.fromisoformat(value)), throw=reject)
        with patch.object(module, "frappe", fake), patch.object(module, "_", lambda value: value):
            grouped = module._group_payment_rows(rows, "Clearing")
        self.assertEqual([float(row["provider_amount"]) for row in grouped], [115.0, -15.0])
        self.assertEqual(grouped[0]["payment_row_ids"], '["pay-1","pay-2"]')

    def test_rejects_change_posted_to_provider_account(self):
        row = SimpleNamespace(payment_row="pay", source_invoice="SI-1", source_doctype="Sales Invoice", posting_date="2026-11-01", posting_time="10:00:00", is_return=0, gl_voucher="SI-1", base_amount=100, base_change_amount=5, account_for_change_amount="Clearing")
        with patch.object(module, "frappe", SimpleNamespace(throw=reject)), patch.object(module, "_", lambda value: value):
            with self.assertRaisesRegex(Rejected, "change posted"):
                module._group_payment_rows([row], "Clearing")

    def test_late_detection_includes_overlapping_intervals(self):
        fake = SimpleNamespace(utils=SimpleNamespace(getdate=lambda value: date.fromisoformat(value) if isinstance(value, str) else value))
        intervals = [(date(2026, 11, 1), date(2026, 11, 10)), (date(2026, 11, 8), date(2026, 11, 20))]
        with patch.object(module, "frappe", fake):
            self.assertTrue(module.late_status("2026-11-08", intervals))
            self.assertFalse(module.late_status("2026-11-21", intervals))

    def test_existing_claim_prevents_duplicate_settlement(self):
        row = SimpleNamespace(selected=1, source_doctype="Sales Invoice", source_invoice="SI-1", gl_voucher="SI-1", provider_amount=100, payment_row_ids='["pay-1"]')
        settlement = SimpleNamespace(company="Company", pos_profile="Profile", from_date="2026-11-01", to_date="2026-11-30", invoices=[row], gross=100, name="SET-2", provider_account="Provider")
        provider = SimpleNamespace(clearing_account="Clearing")
        current = [{"source_doctype": "Sales Invoice", "source_invoice": "SI-1", "gl_voucher": "SI-1", "provider_amount": 100, "payment_row_ids": '["pay-1"]', "payment_rows": [{"name": "pay-1", "amount": 100}]}]
        db = MagicMock()
        db.get_value.return_value = "SET-1"
        fake = SimpleNamespace(db=db, throw=reject)
        with patch.object(module, "frappe", fake), patch.object(module, "_", lambda value: value), patch.object(module, "validate_period", return_value=(date(2026, 11, 1), date(2026, 11, 30))), patch.object(module, "_query_payment_rows", return_value=[]), patch.object(module, "_group_payment_rows", return_value=current):
            with self.assertRaisesRegex(Rejected, "already claimed"):
                module._revalidate_selected_rows(settlement, provider)


if __name__ == "__main__":
    unittest.main()
