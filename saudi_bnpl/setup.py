import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


BNPL_JOURNAL_SERIES = "BNPL-JV-.YYYY.-"


def after_migrate():
	create_journal_entry_fields()
	add_journal_entry_naming_series()
	backfill_journal_entry_references()


def create_journal_entry_fields():
	fields = [
		{
			"fieldname": "custom_bnpl_traceability_section",
			"fieldtype": "Section Break",
			"label": "BNPL Traceability",
			"insert_after": "user_remark",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_bnpl_settlement",
			"fieldtype": "Link",
			"label": "BNPL Settlement",
			"options": "BNPL Settlement",
			"insert_after": "custom_bnpl_traceability_section",
			"read_only": 1,
			"no_copy": 1,
			"unique": 1,
		},
		{
			"fieldname": "custom_bnpl_provider_account",
			"fieldtype": "Link",
			"label": "BNPL Provider Account",
			"options": "BNPL Provider Account",
			"insert_after": "custom_bnpl_settlement",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_bnpl_pos_profile",
			"fieldtype": "Link",
			"label": "BNPL POS Profile",
			"options": "POS Profile",
			"insert_after": "custom_bnpl_provider_account",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_bnpl_from_date",
			"fieldtype": "Date",
			"label": "BNPL From Date",
			"insert_after": "custom_bnpl_pos_profile",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_bnpl_to_date",
			"fieldtype": "Date",
			"label": "BNPL To Date",
			"insert_after": "custom_bnpl_from_date",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_bnpl_statement_reference",
			"fieldtype": "Data",
			"label": "BNPL Statement Reference",
			"insert_after": "custom_bnpl_to_date",
			"read_only": 1,
			"no_copy": 1,
		},
	]
	create_custom_fields({"Journal Entry": fields}, update=True)


def add_journal_entry_naming_series():
	field = frappe.get_meta("Journal Entry").get_field("naming_series")
	options = (field.options or "").splitlines()
	if BNPL_JOURNAL_SERIES in options:
		return

	options.append(BNPL_JOURNAL_SERIES)
	make_property_setter(
		"Journal Entry",
		"naming_series",
		"options",
		"\n".join(options),
		"Text",
	)


def journal_entry_remark(provider_name, statement_reference, pos_profile, from_date, to_date):
	return (
		f"BNPL {provider_name} statement {statement_reference}; POS Profile {pos_profile}; "
		f"inclusive period {from_date} to {to_date}."
	)


def backfill_journal_entry_references():
	rows = frappe.db.sql(
		"""
		select settlement.name, settlement.journal_entry, settlement.provider_account,
			provider.provider_name, settlement.pos_profile, settlement.from_date,
			settlement.to_date, settlement.statement_reference, journal.user_remark,
			journal.custom_bnpl_settlement, journal.custom_bnpl_provider_account,
			journal.custom_bnpl_pos_profile, journal.custom_bnpl_from_date,
			journal.custom_bnpl_to_date, journal.custom_bnpl_statement_reference
		from `tabBNPL Settlement` settlement
		inner join `tabJournal Entry` journal on journal.name = settlement.journal_entry
		inner join `tabBNPL Provider Account` provider on provider.name = settlement.provider_account
		where ifnull(settlement.journal_entry, '') != ''
		""",
		as_dict=True,
	)
	for row in rows:
		values = {
			"custom_bnpl_settlement": row.name,
			"custom_bnpl_provider_account": row.provider_account,
			"custom_bnpl_pos_profile": row.pos_profile,
			"custom_bnpl_from_date": row.from_date,
			"custom_bnpl_to_date": row.to_date,
			"custom_bnpl_statement_reference": row.statement_reference,
		}
		updates = {field: value for field, value in values.items() if not row.get(field) and value}
		if not row.user_remark:
			updates["user_remark"] = journal_entry_remark(
				row.provider_name,
				row.statement_reference,
				row.pos_profile,
				row.from_date,
				row.to_date,
			)
		if updates:
			frappe.db.set_value("Journal Entry", row.journal_entry, updates, update_modified=False)
