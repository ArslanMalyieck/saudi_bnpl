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
                self.assertTrue(all(not p.get("delete") for p in data["permissions"]))
                self.assertEqual([p["role"] for p in data["permissions"]], ["Accounts Manager"])
                self.assertEqual(set(data["field_order"]), {f["fieldname"] for f in data["fields"]})
                if slug == "bnpl_provider_account":
                    field = next(f for f in data["fields"] if f["fieldname"] == "provider_name")
                    self.assertEqual(field["fieldtype"], "Select")
                    self.assertEqual(field["options"].splitlines(), ["", "Tabby", "Tamara", "Emkan", "NEO", "Madfu"])
