import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from saudi_bnpl.financial import stable_name
from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement import bnpl_settlement as module


class Rejected(Exception):
    pass


def reject(message, *args):
    raise Rejected(message)


class FakeDoc(SimpleNamespace):
    def get(self, key):
        return getattr(self, key, None)

    def get_doc_before_save(self):
        return self.previous


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.provider = SimpleNamespace(provider_name="Provider", company="Company", check_permission=MagicMock())
        data = dict(name=stable_name("BNPL-S-", "Company", "Provider", "Ref"), company="Company",
                    provider_account="Provider config", statement_reference="Ref", status="Open",
                    gross=Decimal("115.00"), fee=Decimal("10.00"), vat=Decimal("1.50"), net=Decimal("103.50"),
                    tax_invoice="PI-1", vat_account="VAT", journal_entry="JE-1", payout_date=date(2026, 11, 1))
        self.previous = FakeDoc(**data)
        self.doc = FakeDoc(**data, previous=self.previous, flags={})
        self.doc.payout_date = "2026-11-01"
        self.doc.gross, self.doc.fee, self.doc.vat, self.doc.net = 115.0, 10.0, 1.5, 103.5
        fake_frappe = SimpleNamespace(get_doc=MagicMock(return_value=self.provider), throw=reject,
            utils=SimpleNamespace(getdate=lambda value: date.fromisoformat(value) if isinstance(value, str) else value))
        self.patches = [patch.object(module, "frappe", fake_frappe), patch.object(module, "_", lambda x: x)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_linked_statement_can_change_operational_status(self):
        self.doc.status = "Closed"
        module.BNPLSettlement.validate(self.doc)

    def test_link_cannot_be_changed_or_financial_details_edited(self):
        for field, value in [("journal_entry", None), ("payout_date", "2026-11-02"),
                             ("tax_invoice", "PI-2"), ("provider_account", "Other")]:
            with self.subTest(field=field):
                original = getattr(self.doc, field)
                setattr(self.doc, field, value)
                with self.assertRaises(Rejected):
                    module.BNPLSettlement.validate(self.doc)
                setattr(self.doc, field, original)

    def test_provider_reloads_under_lock_after_permission_check(self):
        stale = SimpleNamespace(check_permission=MagicMock(), enabled=1)
        current = SimpleNamespace(check_permission=MagicMock(), enabled=0)
        with patch.object(module.frappe, "get_doc", side_effect=[stale, current]) as get_doc:
            self.assertIs(module.get_provider("Provider config"), current)
        self.assertEqual(get_doc.call_args_list[0].kwargs, {})
        self.assertEqual(get_doc.call_args_list[1].kwargs, {"for_update": True})
        stale.check_permission.assert_called_once_with("read")
        current.check_permission.assert_called_once_with("read")
