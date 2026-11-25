import unittest
from types import SimpleNamespace
from unittest.mock import patch

from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement import bnpl_settlement as module


class Rejected(Exception):
    pass


def reject(message, *args):
    raise Rejected(message)


class ProviderProfileTests(unittest.TestCase):
    def test_no_profiles_keeps_legacy_company_wide_behavior(self):
        provider = {"profiles": []}
        module.validate_provider_profile(provider, "Any Company Profile")

    def test_configured_profiles_allow_any_listed_profile(self):
        provider = {
            "profiles": [
                {"enabled": 1, "pos_profile": "Shop A"},
                {"enabled": 1, "pos_profile": "Shop B"},
            ]
        }
        module.validate_provider_profile(provider, "Shop A")
        module.validate_provider_profile(provider, "Shop B")

    def test_configured_profiles_reject_unlisted_or_disabled_profile(self):
        provider = {
            "profiles": [
                {"enabled": 1, "pos_profile": "Shop A"},
                {"enabled": 0, "pos_profile": "Shop B"},
            ]
        }
        with patch.object(module, "frappe", SimpleNamespace(throw=reject)), \
             patch.object(module, "_", lambda value: value):
            with self.assertRaisesRegex(Rejected, "not configured"):
                module.validate_provider_profile(provider, "Shop B")
            with self.assertRaisesRegex(Rejected, "not configured"):
                module.validate_provider_profile(provider, "Shop C")


if __name__ == "__main__":
    unittest.main()
