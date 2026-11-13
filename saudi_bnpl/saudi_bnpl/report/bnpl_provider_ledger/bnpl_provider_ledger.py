from datetime import date

import frappe
from frappe import _

from saudi_bnpl.financial import money, payment_row_ids
from saudi_bnpl.saudi_bnpl.doctype.bnpl_settlement.bnpl_settlement import (
	_group_payment_rows,
	_query_provider_payment_rows,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	provider, start, end = validate_filters(filters)
	currency = frappe.db.get_value("Company", filters.company, "default_currency")
	rows = get_rows(filters, provider, start, end)
	data, totals = apply_running_balance(rows, start)
	return get_columns(currency), data, None, None, get_report_summary(totals, currency)


def validate_filters(filters):
	if not filters.company or not filters.provider_account:
		frappe.throw(_("Company and Provider Account are required."))
	start = frappe.utils.getdate(filters.from_date) if filters.from_date else None
	end = frappe.utils.getdate(filters.to_date) if filters.to_date else None
	if start and end and start > end:
		frappe.throw(_("From Date cannot be after To Date."))
	provider = frappe.get_doc("BNPL Provider Account", filters.provider_account)
	provider.check_permission("read")
	if provider.company != filters.company:
		frappe.throw(_("Provider Account must belong to the selected company."))
	if filters.pos_profile and frappe.db.get_value("POS Profile", filters.pos_profile, "company") != filters.company:
		frappe.throw(_("POS Profile must belong to the selected company."))
	return provider, start, end


def get_rows(filters, provider, start, end):
	query_start = date(1900, 1, 1)
	query_end = end or date(9999, 12, 31)
	raw = _query_provider_payment_rows(
		filters.company,
		provider,
		query_start,
		query_end,
		filters.pos_profile,
	)
	sources = _group_payment_rows(raw, provider.clearing_account)
	claims = get_claims(filters.provider_account)
	rows = [source_row(source, filters.provider_account, claims) for source in sources]
	rows.extend(get_settlement_rows(filters, query_end))
	return sorted(rows, key=lambda row: (row["posting_datetime"], row["entry_order"], row["source_reference"]))


def get_claims(provider_account):
	rows = frappe.db.sql(
		"""
		select claim.payment_row, claim.settlement, settlement.statement_reference,
			settlement.journal_entry, journal.docstatus journal_docstatus
		from `tabBNPL Payment Claim` claim
		inner join `tabBNPL Settlement` settlement on settlement.name = claim.settlement
		left join `tabJournal Entry` journal on journal.name = settlement.journal_entry
		where claim.provider_account = %s
		""",
		(provider_account,),
		as_dict=True,
	)
	return {row.payment_row: row for row in rows}


def source_row(source, provider_account, claims):
	ids = payment_row_ids(source["payment_row_ids"])
	claimed = [claims[payment_row] for payment_row in ids if payment_row in claims]
	settlements = {row.settlement for row in claimed}
	claim = claimed[0] if len(settlements) == 1 else None
	if not claimed:
		claim_status = _("Unclaimed")
	elif len(claimed) != len(ids):
		claim_status = _("Partially Claimed")
	elif len(settlements) > 1:
		claim_status = _("Multiple Settlements")
	else:
		claim_status = _("Claimed")
	amount = money(source["provider_amount"])
	return {
		"posting_datetime": source["posting_datetime"],
		"posting_date": frappe.utils.getdate(source["posting_datetime"]),
		"entry_order": 0,
		"entry_type": _("Return") if amount < 0 or source["is_return"] else _("Sale"),
		"provider_account": provider_account,
		"pos_profile": source["pos_profile"],
		"source_doctype": source["source_doctype"],
		"source_reference": source["source_invoice"],
		"gl_voucher": source["gl_voucher"],
		"debit": float(amount) if amount > 0 else 0,
		"credit": float(-amount) if amount < 0 else 0,
		"posted_delta": amount,
		"settlement": claim.settlement if claim else None,
		"statement_reference": claim.statement_reference if claim else None,
		"journal_entry": claim.journal_entry if claim else None,
		"je_status": journal_status(claim.journal_docstatus) if claim else None,
		"claim_status": claim_status,
	}


def get_settlement_rows(filters, end):
	conditions = [
		"settlement.company = %s",
		"settlement.provider_account = %s",
		"settlement.payout_date <= %s",
		"ifnull(settlement.journal_entry, '') != ''",
	]
	values = [filters.company, filters.provider_account, end]
	if filters.pos_profile:
		conditions.append("settlement.pos_profile = %s")
		values.append(filters.pos_profile)
	rows = frappe.db.sql(
		f"""
		select settlement.name, settlement.payout_date, settlement.pos_profile,
			settlement.gross, settlement.statement_reference, settlement.journal_entry,
			journal.docstatus journal_docstatus
		from `tabBNPL Settlement` settlement
		left join `tabJournal Entry` journal on journal.name = settlement.journal_entry
		where {' and '.join(conditions)}
		""",
		tuple(values),
		as_dict=True,
	)
	result = []
	for row in rows:
		gross = money(row.gross)
		submitted = row.journal_docstatus == 1
		result.append({
			"posting_datetime": frappe.utils.get_datetime(f"{row.payout_date} 23:59:59"),
			"posting_date": frappe.utils.getdate(row.payout_date),
			"entry_order": 1,
			"entry_type": _("Settlement"),
			"provider_account": filters.provider_account,
			"pos_profile": row.pos_profile,
			"source_doctype": "BNPL Settlement",
			"source_reference": row.name,
			"gl_voucher": None,
			"debit": 0,
			"credit": float(gross),
			"posted_delta": -gross if submitted else money(0),
			"settlement": row.name,
			"statement_reference": row.statement_reference,
			"journal_entry": row.journal_entry,
			"je_status": journal_status(row.journal_docstatus),
			"claim_status": _("Claimed"),
		})
	return result


def journal_status(docstatus):
	return {0: _("Draft (Pending)"), 1: _("Submitted"), 2: _("Cancelled")}.get(docstatus, _("Not Generated"))


def apply_running_balance(rows, start=None):
	balance = money(0)
	totals = {"source_debit": money(0), "return_credit": money(0), "settlement_credit": money(0)}
	data = []
	for row in rows:
		balance += money(row["posted_delta"])
		if start and row["posting_date"] < start:
			continue
		row = row.copy()
		row["posted_running_balance"] = float(balance)
		data.append(row)
		if row["entry_type"] == _("Sale"):
			totals["source_debit"] += money(row["debit"])
		elif row["entry_type"] == _("Return"):
			totals["return_credit"] += money(row["credit"])
		elif row["entry_type"] == _("Settlement") and row["posted_delta"]:
			totals["settlement_credit"] += money(row["credit"])
	totals["closing_balance"] = balance
	return data, totals


def get_columns(currency):
	return [
		{"label": _("Posting Date / Time"), "fieldname": "posting_datetime", "fieldtype": "Datetime", "width": 155},
		{"label": _("Entry Type"), "fieldname": "entry_type", "fieldtype": "Data", "width": 90},
		{"label": _("Provider"), "fieldname": "provider_account", "fieldtype": "Link", "options": "BNPL Provider Account", "width": 150},
		{"label": _("POS Profile"), "fieldname": "pos_profile", "fieldtype": "Link", "options": "POS Profile", "width": 130},
		{"label": _("Source Type"), "fieldname": "source_doctype", "fieldtype": "Link", "options": "DocType", "hidden": 1},
		{"label": _("Source / Reference"), "fieldname": "source_reference", "fieldtype": "Dynamic Link", "options": "source_doctype", "width": 170},
		{"label": _("GL Voucher"), "fieldname": "gl_voucher", "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
		{"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "options": currency, "width": 110},
		{"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "options": currency, "width": 110},
		{"label": _("Posted Running Balance"), "fieldname": "posted_running_balance", "fieldtype": "Currency", "options": currency, "width": 155},
		{"label": _("Settlement"), "fieldname": "settlement", "fieldtype": "Link", "options": "BNPL Settlement", "width": 170},
		{"label": _("Statement Reference"), "fieldname": "statement_reference", "fieldtype": "Data", "width": 150},
		{"label": _("Journal Entry"), "fieldname": "journal_entry", "fieldtype": "Link", "options": "Journal Entry", "width": 150},
		{"label": _("JE Status"), "fieldname": "je_status", "fieldtype": "Data", "width": 115},
		{"label": _("Claim Status"), "fieldname": "claim_status", "fieldtype": "Data", "width": 125},
	]


def get_report_summary(totals, currency):
	return [
		{"label": _("Posted Source Debit"), "value": float(totals["source_debit"]), "datatype": "Currency", "currency": currency},
		{"label": _("Posted Return Credit"), "value": float(totals["return_credit"]), "datatype": "Currency", "currency": currency},
		{"label": _("Submitted Settlement Credit"), "value": float(totals["settlement_credit"]), "datatype": "Currency", "currency": currency},
		{"label": _("Closing Posted Balance"), "value": float(totals["closing_balance"]), "datatype": "Currency", "currency": currency},
	]
