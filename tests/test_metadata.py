import json
import unittest
from pathlib import Path


class MetadataTests(unittest.TestCase):
    def test_doctypes_and_nondeletable_settlement(self):
        root = Path(__file__).resolve().parents[1] / "saudi_bnpl" / "saudi_bnpl" / "doctype"
        for slug, name in (("bnpl_provider_account", "BNPL Provider Account"), ("bnpl_settlement", "BNPL Settlement")):
            with self.subTest(doctype=name):
                data = json.loads((root / slug / (slug + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(data["name"], name)
                self.assertFalse(data.get("is_submittable"))
                self.assertFalse(data.get("allow_rename"))
                self.assertTrue(all(p.get("delete") for p in data["permissions"]))
                self.assertEqual([p["role"] for p in data["permissions"]], ["Accounts Manager"])
                self.assertEqual(set(data["field_order"]), {f["fieldname"] for f in data["fields"]})
                if slug == "bnpl_provider_account":
                    field = next(f for f in data["fields"] if f["fieldname"] == "provider_name")
                    self.assertEqual(field["fieldtype"], "Select")
                    self.assertEqual(field["options"].splitlines(), ["", "Tabby", "Tamara", "Emkan", "NEO", "Madfu"])

    def test_internal_invoice_and_claim_doctypes(self):
        root = Path(__file__).resolve().parents[1] / "saudi_bnpl" / "saudi_bnpl" / "doctype"
        child = json.loads((root / "bnpl_settlement_invoice" / "bnpl_settlement_invoice.json").read_text(encoding="utf-8"))
        claim = json.loads((root / "bnpl_payment_claim" / "bnpl_payment_claim.json").read_text(encoding="utf-8"))
        self.assertTrue(child["istable"])
        self.assertEqual(child["permissions"], [])
        self.assertEqual(claim["permissions"], [])
        payment_row = next(field for field in claim["fields"] if field["fieldname"] == "payment_row")
        self.assertTrue(payment_row["unique"])
        settlement = json.loads((root / "bnpl_settlement" / "bnpl_settlement.json").read_text(encoding="utf-8"))
        table = next(field for field in settlement["fields"] if field["fieldname"] == "invoices")
        self.assertEqual(table["options"], "BNPL Settlement Invoice")

    def test_provider_ledger_is_standard_and_accounts_manager_only(self):
        path = (Path(__file__).resolve().parents[1] / "saudi_bnpl" / "saudi_bnpl" / "report"
                / "bnpl_provider_ledger" / "bnpl_provider_ledger.json")
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["report_type"], "Script Report")
        self.assertEqual(report["is_standard"], "Yes")
        self.assertTrue(report["apply_user_permissions"])
        self.assertEqual(report["roles"], [{"role": "Accounts Manager"}])

    def test_provider_profiles_and_api_credentials_are_supported(self):
        root = Path(__file__).resolve().parents[1] / "saudi_bnpl" / "saudi_bnpl" / "doctype"
        provider = json.loads((root / "bnpl_provider_account" / "bnpl_provider_account.json").read_text(encoding="utf-8"))
        profile = json.loads((root / "bnpl_provider_profile" / "bnpl_provider_profile.json").read_text(encoding="utf-8"))
        settlement = json.loads((root / "bnpl_settlement" / "bnpl_settlement.json").read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in provider["fields"]}
        self.assertEqual(fields["profiles"]["fieldtype"], "Table")
        self.assertEqual(fields["profiles"]["options"], "BNPL Provider Profile")
        self.assertTrue(profile["istable"])
        self.assertEqual(profile["permissions"], [])
        profile_fields = {field["fieldname"]: field for field in profile["fields"]}
        self.assertEqual(profile_fields["pos_profile"]["options"], "POS Profile")
        self.assertEqual(profile_fields["mode_of_payment"]["options"], "Mode of Payment")
        self.assertEqual(profile_fields["clearing_account"]["options"], "Account")
        self.assertEqual(profile_fields["bank_account"]["options"], "Account")
        settlement_fields = {field["fieldname"] for field in settlement["fields"]}
        self.assertTrue({"mode_of_payment", "clearing_account", "bank_account"} <= settlement_fields)
