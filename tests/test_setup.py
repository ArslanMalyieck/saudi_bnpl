import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from saudi_bnpl import setup


class SetupTests(unittest.TestCase):
    def test_naming_series_is_appended_without_replacing_options(self):
        fake = SimpleNamespace(get_meta=MagicMock(return_value=SimpleNamespace(
            get_field=MagicMock(return_value=SimpleNamespace(options="ACC-JV-.YYYY.-\nOTHER-.#####"))
        )))
        with patch.object(setup, "frappe", fake), patch.object(setup, "make_property_setter") as setter:
            setup.add_journal_entry_naming_series()
        value = setter.call_args.args[3]
        self.assertEqual(value.splitlines(), ["ACC-JV-.YYYY.-", "OTHER-.#####", setup.BNPL_JOURNAL_SERIES])

    def test_naming_series_setup_is_idempotent(self):
        fake = SimpleNamespace(get_meta=MagicMock(return_value=SimpleNamespace(
            get_field=MagicMock(return_value=SimpleNamespace(options=f"ACC-JV-.YYYY.-\n{setup.BNPL_JOURNAL_SERIES}"))
        )))
        with patch.object(setup, "frappe", fake), patch.object(setup, "make_property_setter") as setter:
            setup.add_journal_entry_naming_series()
        setter.assert_not_called()

    def test_custom_fields_are_read_only_and_no_copy(self):
        with patch.object(setup, "create_custom_fields") as create:
            setup.create_journal_entry_fields()
        fields = create.call_args.args[0]["Journal Entry"]
        self.assertTrue(all(field["read_only"] and field["no_copy"] for field in fields))
        settlement = next(field for field in fields if field["fieldname"] == "custom_bnpl_settlement")
        self.assertTrue(settlement["unique"])


if __name__ == "__main__":
    unittest.main()
