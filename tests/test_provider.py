import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from saudi_bnpl.financial import stable_name
from saudi_bnpl.saudi_bnpl.doctype.bnpl_provider_account import bnpl_provider_account as module


class Rejected(Exception):
    pass


def reject(message, *args):
    raise Rejected(message)


class FakeDoc(SimpleNamespace):
    def get(self, field):
        return getattr(self, field, None)

    def get_doc_before_save(self):
        return self.previous


class ProviderTests(unittest.TestCase):
    def setUp(self):
        data = dict(company="Company", provider_name="Tabby", mode_of_payment="BNPL Mode",
                    clearing_account="Clearing", bank_account="Bank", supplier="Supplier", enabled=1)
        self.previous = FakeDoc(**data)
        self.doc = FakeDoc(**data, name=stable_name("BNPL-P-", "Company", "Tabby"), previous=self.previous)
        self.clearing = SimpleNamespace(company="Company", is_group=0, disabled=0, account_currency="SAR",
                                        report_type="Balance Sheet", root_type="Asset", account_type="Cash")
        self.bank = SimpleNamespace(company="Company", is_group=0, disabled=0, account_currency="SAR",
                                    report_type="Balance Sheet", root_type="Asset", account_type="Bank")
        self.mode = SimpleNamespace(enabled=1, accounts=[SimpleNamespace(company="Company", default_account="Clearing")],
                                    check_permission=MagicMock())
        self.db = SimpleNamespace(get_value=MagicMock(side_effect=self.get_value), exists=MagicMock(return_value=True))
        self.referenced = False
        fake_frappe = SimpleNamespace(db=self.db, get_doc=MagicMock(return_value=self.mode), throw=reject)
        self.patches = [patch.object(module, "frappe", fake_frappe), patch.object(module, "_", lambda value: value)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def get_value(self, doctype, name, fields, **kwargs):
        if doctype == "BNPL Settlement":
            self.assertTrue(kwargs.get("for_update"))
            return "statement" if self.referenced else None
        if doctype == "Company":
            return "SAR"
        if doctype == "Supplier":
            return 0
        if doctype == "Account":
            self.assertIn("root_type", fields)
            return self.clearing if name == "Clearing" else self.bank
        raise AssertionError(doctype)

    def test_five_provider_names_only(self):
        for provider in module.PROVIDERS:
            self.doc.provider_name = provider
            self.doc.name = stable_name("BNPL-P-", "Company", provider)
            self.doc.previous = None
            module.BNPLProviderAccount.validate(self.doc)
        self.doc.provider_name = "Other"
        with self.assertRaisesRegex(Rejected, "Provider must be one of"):
            module.BNPLProviderAccount.validate(self.doc)

    def test_each_accounting_field_is_frozen_once_referenced(self):
        self.referenced = True
        replacements = {"company": "Other Company", "provider_name": "Tamara", "mode_of_payment": "Other Mode",
                        "clearing_account": "Other Clearing", "bank_account": "Other Bank", "supplier": "Other Supplier"}
        for field, value in replacements.items():
            with self.subTest(field=field):
                original = getattr(self.doc, field)
                setattr(self.doc, field, value)
                with self.assertRaisesRegex(Rejected, "referenced by a BNPL Settlement"):
                    module.BNPLProviderAccount.validate(self.doc)
                setattr(self.doc, field, original)

    def test_unreferenced_accounting_changes_allowed(self):
        self.doc.supplier = "Other Supplier"
        module.BNPLProviderAccount.validate(self.doc)
        self.db.get_value.assert_any_call("BNPL Settlement", {"provider_account": self.doc.name}, "name", for_update=True)

    def test_disable_allowed_even_when_external_mode_is_disabled(self):
        self.referenced = True
        self.doc.enabled = 0
        self.mode.enabled = 0
        module.BNPLProviderAccount.validate(self.doc)
        self.doc.previous.enabled = 0
        self.doc.enabled = 1
        with self.assertRaisesRegex(Rejected, "Mode of Payment must be enabled"):
            module.BNPLProviderAccount.validate(self.doc)
        self.mode.enabled = 1
        module.BNPLProviderAccount.validate(self.doc)

    def test_only_asset_cash_clearing_is_allowed(self):
        for field, value in [("account_type", "Bank"), ("account_type", "Receivable"),
                             ("account_type", "Current Asset"), ("root_type", "Liability"),
                             ("report_type", "Profit and Loss")]:
            with self.subTest(field=field, value=value):
                original = getattr(self.clearing, field)
                setattr(self.clearing, field, value)
                with self.assertRaisesRegex(Rejected, "asset balance-sheet"):
                    module.BNPLProviderAccount.validate(self.doc)
                setattr(self.clearing, field, original)
        module.BNPLProviderAccount.validate(self.doc)

    def test_account_must_be_company_leaf_enabled_sar(self):
        for field, value in [("company", "Other"), ("is_group", 1), ("disabled", 1), ("account_currency", "USD")]:
            with self.subTest(field=field):
                original = getattr(self.clearing, field)
                setattr(self.clearing, field, value)
                with self.assertRaisesRegex(Rejected, "enabled, leaf SAR"):
                    module.BNPLProviderAccount.validate(self.doc)
                setattr(self.clearing, field, original)

    def test_mode_must_map_to_clearing(self):
        self.mode.accounts[0].default_account = "Bank"
        with self.assertRaisesRegex(Rejected, "exactly one company mapping"):
            module.BNPLProviderAccount.validate(self.doc)
