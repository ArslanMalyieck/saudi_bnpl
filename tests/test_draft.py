import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement import bnpl_settlement as module


class Rejected(Exception):
    pass


def reject(message, *args):
    raise Rejected(message)


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.provider = MagicMock(enabled=1, bank_account="Bank", clearing_account="Clearing", supplier="Supplier")
        self.settlement = MagicMock(name="statement", status="Open", journal_entry=None,
                                    provider_account="provider", company="Company", payout_date="2026-11-01",
                                    gross=115, fee=10, vat=1.5, net=103.5)
        self.settlement.name = "statement"
        self.settlement.flags = SimpleNamespace()
        self.invoice = SimpleNamespace(name="PI-1", credit_to="Payable")
        self.settlement.validate_invoice.return_value = self.invoice
        self.journal = MagicMock(name="journal")
        self.journal.name = "JE-1"
        self.payloads = []
        self.loads = []
        self.db = MagicMock()
        self.db.get_value.return_value = None
        self.frappe = SimpleNamespace(get_doc=self.get_doc, has_permission=MagicMock(return_value=True),
                                      db=self.db, throw=reject, PermissionError=PermissionError)
        self.patches = [patch.object(module, "frappe", self.frappe), patch.object(module, "_", lambda x: x)]
        self.patches.append(patch.object(module, "_revalidate_selected_rows", return_value=[]))
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def get_doc(self, *args, **kwargs):
        self.loads.append((args, kwargs))
        if isinstance(args[0], dict):
            self.payloads.append(args[0])
            return self.journal
        if args[0] == "BNPL Settlement":
            return self.settlement
        if args[0] == "BNPL Provider Account":
            return self.provider
        raise AssertionError(args)

    def test_creates_balanced_draft_and_links_it(self):
        self.assertEqual(module.create_journal_entry("statement"), "JE-1")
        self.assertEqual(self.payloads[0]["accounts"], [
            {"account": "Bank", "debit_in_account_currency": 103.5},
            {"account": "Payable", "party_type": "Supplier", "party": "Supplier", "reference_type": "Purchase Invoice", "reference_name": "PI-1", "debit_in_account_currency": 11.5},
            {"account": "Clearing", "credit_in_account_currency": 115.0},
        ])
        self.journal.insert.assert_called_once_with()
        self.journal.submit.assert_not_called()
        self.settlement.save.assert_called_once_with()
        self.assertEqual(self.settlement.journal_entry, "JE-1")
        self.settlement.validate_invoice.assert_called_once_with(self.provider, for_update=True)
        self.db.get_value.assert_called_once()
        self.assertIn((("BNPL Provider Account", "provider"), {"for_update": True}), self.loads)

    def test_insert_defaults_are_not_overwritten(self):
        self.journal.insert.side_effect = lambda: setattr(self.journal, "cost_center", "Core Default")
        module.create_journal_entry("statement")
        self.assertEqual(self.journal.cost_center, "Core Default")
        self.journal.save.assert_not_called()

    def test_zero_fee_has_two_lines_and_no_invoice(self):
        self.settlement.gross = self.settlement.net = 100
        self.settlement.fee = self.settlement.vat = 0
        module.create_journal_entry("statement")
        self.assertEqual(len(self.payloads[0]["accounts"]), 2)
        self.settlement.validate_invoice.assert_not_called()

    def test_guard_conditions(self):
        for field, value in [("status", "Closed"), ("journal_entry", "JE-OLD")]:
            with self.subTest(field=field):
                old = getattr(self.settlement, field)
                setattr(self.settlement, field, value)
                with self.assertRaises(Rejected):
                    module.create_journal_entry("statement")
                setattr(self.settlement, field, old)
        self.frappe.has_permission.return_value = False
        with self.assertRaises(Rejected):
            module.create_journal_entry("statement")
        self.journal.insert.assert_not_called()

    def test_duplicate_invoice_is_rejected(self):
        self.db.get_value.return_value = "other statement"
        with self.assertRaises(Rejected):
            module.create_journal_entry("statement")
        self.journal.insert.assert_not_called()

    def test_permission_and_insert_failure_do_not_link(self):
        self.settlement.check_permission.side_effect = PermissionError("write denied")
        with self.assertRaises(PermissionError):
            module.create_journal_entry("statement")
        self.journal.insert.assert_not_called()
        self.settlement.check_permission.side_effect = None
        self.journal.insert.side_effect = Rejected("invalid journal")
        with self.assertRaises(Rejected):
            module.create_journal_entry("statement")
        self.settlement.save.assert_not_called()
        self.assertIsNone(self.settlement.journal_entry)


class InvoiceTests(unittest.TestCase):
    def setUp(self):
        self.tax = SimpleNamespace(category="Total", add_deduct_tax="Add", is_tax_withholding_account=0,
                                   account_head="VAT", tax_amount_after_discount_amount=1.5,
                                   base_tax_amount_after_discount_amount=1.5)
        self.invoice = SimpleNamespace(name="PI-1", posting_date="2026-11-01", docstatus=1, company="Company", supplier="Supplier", currency="SAR",
            party_account_currency="SAR", conversion_rate=1, is_return=0, is_paid=0, on_hold=0, update_stock=0,
            bill_no="INV-1", net_total=10, base_net_total=10, grand_total=11.5, base_grand_total=11.5,
            outstanding_amount=11.5, total_advance=0, rounding_adjustment=0, write_off_amount=0,
            payment_schedule=[], credit_to="Payable", total_taxes_and_charges=1.5,
            base_total_taxes_and_charges=1.5, taxes=[self.tax], check_permission=MagicMock())
        self.provider = SimpleNamespace(supplier="Supplier", clearing_account="Clearing", bank_account="Bank")
        self.settlement = SimpleNamespace(tax_invoice="PI-1", company="Company", fee=10, vat=1.5, vat_account="VAT", payout_date="2026-11-02")
        self.settlement._validate_invoice = lambda provider, for_update: module.BNPLSettlement._validate_invoice(
            self.settlement, provider, for_update
        )
        self.frappe = SimpleNamespace(get_doc=MagicMock(return_value=self.invoice), throw=reject,
            get_all=MagicMock(return_value=[]), utils=SimpleNamespace(getdate=lambda value: date.fromisoformat(value) if isinstance(value, str) else value))
        self.patches = [patch.object(module, "frappe", self.frappe), patch.object(module, "_", lambda x: x),
                        patch.object(module, "account_details", side_effect=lambda name, *_: SimpleNamespace(
                            account_type="Payable" if name == "Payable" else "Tax", report_type="Balance Sheet"))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_valid_invoice(self):
        self.assertIs(module.BNPLSettlement.validate_invoice(self.settlement, self.provider, True), self.invoice)
        self.frappe.get_doc.assert_called_once_with("Purchase Invoice", "PI-1", for_update=True)
        self.invoice.check_permission.assert_called_once_with("read")
        self.frappe.get_all.assert_called_once_with("Purchase Invoice", filters={"company": "Company", "supplier": "Supplier", "bill_no": "INV-1", "docstatus": 1}, fields=["name", "company", "supplier", "bill_no"], limit_page_length=0)

    def test_payout_date_must_not_precede_invoice(self):
        self.settlement.payout_date = "2026-10-31"
        with self.assertRaisesRegex(Rejected, "Payout date"):
            module.BNPLSettlement.validate_invoice(self.settlement, self.provider)
        self.settlement.payout_date = date(2026, 11, 1)
        self.assertIs(module.BNPLSettlement.validate_invoice(self.settlement, self.provider), self.invoice)

    def test_duplicate_exact_submitted_invoice_is_rejected(self):
        self.frappe.get_all.return_value = [
            {"name": "PI-1", "company": "Company", "supplier": "Supplier", "bill_no": "INV-1"},
            {"name": "PI-2", "company": "Company", "supplier": "Supplier", "bill_no": "INV-1"},
        ]
        with self.assertRaisesRegex(Rejected, "More than one submitted"):
            module.BNPLSettlement.validate_invoice(self.settlement, self.provider)
        self.frappe.get_all.return_value[1]["bill_no"] = "inv-1"
        self.assertIs(module.BNPLSettlement.validate_invoice(self.settlement, self.provider), self.invoice)

    def test_rejects_partial_return_foreign_and_wrong_tax(self):
        for obj, field, value in [(self.invoice, "outstanding_amount", 10), (self.invoice, "is_return", 1),
                                  (self.invoice, "currency", "USD"), (self.invoice, "docstatus", 0),
                                  (self.invoice, "bill_no", " "), (self.invoice, "net_total", 10.001),
                                  (self.tax, "account_head", "Other"),
                                  (self.tax, "add_deduct_tax", "Deduct")]:
            with self.subTest(field=field):
                old = getattr(obj, field)
                setattr(obj, field, value)
                with self.assertRaises(Rejected):
                    module.BNPLSettlement.validate_invoice(self.settlement, self.provider)
                setattr(obj, field, old)
