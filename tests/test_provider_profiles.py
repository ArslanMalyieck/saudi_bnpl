import unittest
from types import SimpleNamespace
from unittest.mock import patch

from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement import bnpl_settlement as module


class Rejected(Exception):
    pass


def reject(message, *args):
    raise Rejected(message)


class ProviderProfileTests(unittest.TestCase):
    def test_profile_row_overrides_provider_defaults(self):
        provider = SimpleNamespace(
            mode_of_payment="Default MoP",
            clearing_account="Default Clearing",
            bank_account="Default Bank",
            profiles=[
                SimpleNamespace(
                    enabled=1,
                    pos_profile="Shop A",
                    mode_of_payment="MoP A",
                    clearing_account="Clearing A",
                    bank_account="Bank A",
                )
            ],
        )
        module.configure_provider_for_profile(provider, "Shop A")
        self.assertEqual(provider.mode_of_payment, "MoP A")
        self.assertEqual(provider.clearing_account, "Clearing A")
        self.assertEqual(provider.bank_account, "Bank A")

    def test_legacy_provider_without_profiles_uses_defaults(self):
        provider = SimpleNamespace(
            mode_of_payment="Default MoP",
            clearing_account="Default Clearing",
            bank_account="Default Bank",
            profiles=[],
        )
        module.configure_provider_for_profile(provider, "Shop A")
        self.assertEqual(provider.mode_of_payment, "Default MoP")

    def test_unconfigured_profile_is_rejected(self):
        provider = SimpleNamespace(
            mode_of_payment="Default MoP",
            clearing_account="Default Clearing",
            bank_account="Default Bank",
            profiles=[SimpleNamespace(enabled=1, pos_profile="Shop A")],
        )
        with patch.object(module, "frappe", SimpleNamespace(throw=reject)), \
             patch.object(module, "_", lambda value: value):
            with self.assertRaisesRegex(Rejected, "not configured"):
                module.configure_provider_for_profile(provider, "Shop B")


if __name__ == "__main__":
    unittest.main()
