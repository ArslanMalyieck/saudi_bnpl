import json
from collections import OrderedDict

import frappe
from frappe import _
from frappe.model.document import Document

from saudi_bnpl.financial import (
    money,
    payment_claim_name,
    payment_row_ids,
    selected_amount,
    stable_name,
    validate_amounts,
)
from saudi_bnpl.setup import BNPL_JOURNAL_SERIES, journal_entry_remark
from saudi_bnpl.saudi_bnpl.doctype.bnpl_provider_account.bnpl_provider_account import account_details


SOURCE_FIELDS = (
    "source_doctype", "source_invoice", "posting_datetime", "is_return",
    "gl_voucher", "provider_amount", "payment_row_ids", "late_entry", "settlement_note",
)


def get_provider(name):
    frappe.get_doc("BNPL Provider Account", name).check_permission("read")
    provider = frappe.get_doc("BNPL Provider Account", name, for_update=True)
    provider.check_permission("read")
    return provider


def validate_period(company, pos_profile, from_date, to_date):
    if not pos_profile or not from_date or not to_date:
        frappe.throw(_("POS Profile, From Date and To Date are required."))
    start, end = frappe.utils.getdate(from_date), frappe.utils.getdate(to_date)
    if start > end:
        frappe.throw(_("From Date cannot be after To Date."))
    if frappe.db.get_value("POS Profile", pos_profile, "company") != company:
        frappe.throw(_("POS Profile must belong to the settlement company."))
    return start, end


def _query_payment_rows(company, pos_profile, provider, from_date, to_date):
    return _query_provider_payment_rows(company, provider, from_date, to_date, pos_profile)


def _query_provider_payment_rows(company, provider, from_date, to_date, pos_profile=None):
    profile_condition = " and {alias}.pos_profile = %s" if pos_profile else ""
    values = [company, provider.mode_of_payment, provider.clearing_account]
    if pos_profile:
        values.append(pos_profile)
    values.extend((from_date, to_date))
    sql = f"""
        select p.name payment_row, p.parent source_invoice, 'Sales Invoice' source_doctype,
            si.posting_date, si.posting_time, si.is_return, si.name gl_voucher, si.pos_profile,
            p.base_amount, si.base_change_amount, si.account_for_change_amount
        from `tabSales Invoice Payment` p
        inner join `tabSales Invoice` si
            on si.name = p.parent and p.parenttype = 'Sales Invoice' and p.parentfield = 'payments'
        where si.docstatus = 1 and si.company = %s
            and si.is_pos = 1 and ifnull(si.is_consolidated, 0) = 0
            and p.mode_of_payment = %s and p.account = %s
            {profile_condition.format(alias='si')}
            and si.posting_date between %s and %s
        union all
        select p.name payment_row, p.parent source_invoice, 'POS Invoice' source_doctype,
            pi.posting_date, pi.posting_time, pi.is_return, pi.consolidated_invoice gl_voucher,
            pi.pos_profile,
            p.base_amount, pi.base_change_amount, pi.account_for_change_amount
        from `tabSales Invoice Payment` p
        inner join `tabPOS Invoice` pi
            on pi.name = p.parent and p.parenttype = 'POS Invoice' and p.parentfield = 'payments'
        inner join `tabSales Invoice` consolidated
            on consolidated.name = pi.consolidated_invoice and consolidated.docstatus = 1
        where pi.docstatus = 1 and pi.company = %s
            and p.mode_of_payment = %s and p.account = %s
            {profile_condition.format(alias='pi')}
            and pi.posting_date between %s and %s
        order by posting_date, posting_time, source_doctype, source_invoice, payment_row
    """
    return frappe.db.sql(sql, tuple(values + values), as_dict=True)


def _posting_datetime(row):
    time = row.posting_time or "00:00:00"
    return frappe.utils.get_datetime(f"{row.posting_date} {time}")


def _group_payment_rows(rows, clearing_account):
    grouped = OrderedDict()
    for row in rows:
        if money(row.base_change_amount) and row.account_for_change_amount == clearing_account:
            frappe.throw(_("Invoice {0} has change posted to the provider clearing account and cannot be settled automatically.").format(row.source_invoice))
        key = (row.source_doctype, row.source_invoice)
        item = grouped.setdefault(key, {
            "source_doctype": row.source_doctype,
            "source_invoice": row.source_invoice,
            "posting_datetime": _posting_datetime(row),
            "is_return": int(row.is_return or 0),
            "gl_voucher": row.gl_voucher,
            "pos_profile": row.pos_profile,
            "provider_amount": money(0),
            "payment_rows": [],
        })
        item["provider_amount"] += money(row.base_amount)
        item["payment_rows"].append({"name": row.payment_row, "amount": money(row.base_amount)})
    for item in grouped.values():
        item["payment_row_ids"] = json.dumps(sorted(row["name"] for row in item["payment_rows"]), separators=(",", ":"))
    return list(grouped.values())


def _settled_intervals(provider_account, pos_profile, settlement_name=None):
    conditions = "provider_account=%s and pos_profile=%s and ifnull(journal_entry, '') != '' and from_date is not null and to_date is not null"
    values = [provider_account, pos_profile]
    if settlement_name:
        conditions += " and name != %s"
        values.append(settlement_name)
    rows = frappe.db.sql(
        f"select name, from_date, to_date from `tabBNPL Settlement` where {conditions}",
        tuple(values), as_dict=True,
    )
    return [(frappe.utils.getdate(row.from_date), frappe.utils.getdate(row.to_date)) for row in rows]


def late_status(posting_date, intervals):
    posting_date = frappe.utils.getdate(posting_date)
    return any(start <= posting_date <= end for start, end in intervals)


def _row_signature(rows):
    return [tuple(row.get(field) for field in SOURCE_FIELDS) + (int(row.get("selected") or 0),) for row in rows]


class BNPLSettlement(Document):
    def autoname(self):
        provider = get_provider(self.provider_account)
        self.company = provider.company
        try:
            self.name = stable_name("BNPL-S-", self.company, provider.provider_name, self.statement_reference)
        except ValueError as exc:
            frappe.throw(str(exc))

    def validate(self):
        provider = get_provider(self.provider_account)
        legacy_linked = bool(self.journal_entry and not self.get("invoices"))
        try:
            expected = stable_name("BNPL-S-", self.company, provider.provider_name, self.statement_reference)
            amounts = validate_amounts(self.gross, self.fee, self.vat, self.net)
            fee, vat = amounts[1:3]
        except ValueError as exc:
            frappe.throw(str(exc))
        if self.name != expected or provider.company != self.company:
            frappe.throw(_("Statement identity and company must match the permanent provider configuration."))
        if self.status not in ("Open", "Closed"):
            frappe.throw(_("Operational status must be Open or Closed."))
        if not legacy_linked:
            validate_period(self.company, self.pos_profile, self.from_date, self.to_date)
            self._validate_rows()
        previous = self.get_doc_before_save()
        if previous:
            if previous.provider_account != self.provider_account:
                frappe.throw(_("The provider account cannot be changed."))
            if previous.journal_entry:
                changed = any(money(previous.get(field)) != money(self.get(field)) for field in ("gross", "fee", "vat", "net"))
                changed |= frappe.utils.getdate(previous.payout_date) != frappe.utils.getdate(self.payout_date)
                changed |= any((previous.get(field) or None) != (self.get(field) or None) for field in ("tax_invoice", "vat_account", "pos_profile", "from_date", "to_date"))
                changed |= _row_signature(previous.get("invoices") or []) != _row_signature(self.get("invoices") or [])
                if changed:
                    frappe.throw(_("Amounts, period, invoice rows and accounting details cannot be changed after a Journal Entry is generated."))
        if ((previous.journal_entry if previous else None) or None) != (self.journal_entry or None) and not self.flags.get("creating_journal"):
            frappe.throw(_("The Journal Entry link can only be set by Create Draft Journal Entry and cannot be cleared."))
        if not fee and (self.tax_invoice or self.vat_account):
            frappe.throw(_("A zero-fee statement must not have a Purchase Invoice or VAT account."))
        if fee and not self.tax_invoice:
            frappe.throw(_("A submitted provider fee Purchase Invoice is required."))
        if vat and not self.vat_account:
            frappe.throw(_("Select the VAT account used by the provider fee invoice."))
        if not vat and self.vat_account:
            frappe.throw(_("A VAT account is not applicable when VAT is zero."))
        if self.tax_invoice and not self.journal_entry:
            self.validate_invoice(provider)

    def _validate_rows(self):
        if not self.get("invoices"):
            frappe.throw(_("Fetch and select at least one provider invoice."))
        seen = set()
        for row in self.invoices:
            if row.source_doctype not in ("Sales Invoice", "POS Invoice") or not row.source_invoice or not row.gl_voucher:
                frappe.throw(_("Settlement invoice rows must identify a supported source and GL Sales Invoice."))
            try:
                ids = payment_row_ids(row.payment_row_ids)
                amount = money(row.provider_amount)
            except ValueError as exc:
                frappe.throw(str(exc))
            if seen.intersection(ids):
                frappe.throw(_("A provider payment row cannot appear more than once in a settlement."))
            seen.update(ids)
            if not amount:
                frappe.throw(_("A settlement invoice row cannot have a zero provider amount."))
        if selected_amount(self.invoices) != money(self.gross):
            frappe.throw(_("Gross must equal the signed sum of selected provider invoice rows."))

    def validate_invoice(self, provider, for_update=False):
        try:
            return self._validate_invoice(provider, for_update)
        except ValueError as exc:
            frappe.throw(str(exc))

    def _validate_invoice(self, provider, for_update=False):
        invoice = frappe.get_doc("Purchase Invoice", self.tax_invoice, for_update=for_update)
        invoice.check_permission("read")
        fee, vat = money(self.fee), money(self.vat)
        if (invoice.docstatus != 1 or invoice.company != self.company or invoice.supplier != provider.supplier
            or invoice.currency != "SAR" or invoice.party_account_currency != "SAR"
            or money(invoice.conversion_rate) != 1 or invoice.is_return or invoice.is_paid or invoice.on_hold
            or invoice.update_stock or not invoice.bill_no or not invoice.bill_no.strip()):
            frappe.throw(_("Fee invoice must be submitted, open, non-return, non-stock, SAR, for this company and supplier, with a supplier invoice reference. Cross-currency and returns are not supported."))
        if not invoice.posting_date or not self.payout_date or frappe.utils.getdate(self.payout_date) < frappe.utils.getdate(invoice.posting_date):
            frappe.throw(_("Payout date cannot be earlier than the fee Purchase Invoice posting date."))
        matches = frappe.get_all("Purchase Invoice", filters={"company": invoice.company, "supplier": invoice.supplier, "bill_no": invoice.bill_no, "docstatus": 1}, fields=["name", "company", "supplier", "bill_no"], limit_page_length=0)
        if any(row["name"] != invoice.name and row["company"] == invoice.company and row["supplier"] == invoice.supplier and row["bill_no"] == invoice.bill_no for row in matches):
            frappe.throw(_("More than one submitted Purchase Invoice has this exact company, supplier and supplier invoice number. Finance must resolve the duplicate before proceeding."))
        if (money(invoice.net_total) != fee or money(invoice.base_net_total) != fee
            or money(invoice.grand_total) != fee + vat or money(invoice.base_grand_total) != fee + vat
            or money(invoice.outstanding_amount) != fee + vat or money(invoice.total_advance)
            or money(invoice.rounding_adjustment) or money(invoice.write_off_amount)
            or len(invoice.payment_schedule or []) > 1):
            frappe.throw(_("Fee invoice must be fully outstanding with exactly the statement fee and VAT, without rounding, advances, write-offs or multiple payment terms. Partial settlements are not supported."))
        payable = account_details(invoice.credit_to, self.company, _("Invoice payable account"))
        if payable.account_type != "Payable" or invoice.credit_to in (provider.clearing_account, provider.bank_account):
            frappe.throw(_("Fee invoice must use a distinct SAR supplier payable account."))
        if money(invoice.total_taxes_and_charges) != vat or money(invoice.base_total_taxes_and_charges) != vat:
            frappe.throw(_("Fee invoice tax total must equal the statement VAT."))
        total = money(0)
        for tax in invoice.taxes:
            amount = money(tax.tax_amount_after_discount_amount)
            if (tax.category != "Total" or tax.add_deduct_tax != "Add" or tax.is_tax_withholding_account
                or not tax.account_head or tax.account_head != self.vat_account
                or amount < 0 or money(tax.base_tax_amount_after_discount_amount) != amount):
                frappe.throw(_("Only identifiable additive fee VAT rows on the selected SAR VAT account are supported; foreign taxes and other charges are not supported."))
            total += amount
        if total != vat or (vat and not invoice.taxes):
            frappe.throw(_("Fee invoice VAT rows must add up exactly to the statement VAT."))
        if vat:
            account_details(self.vat_account, self.company, _("VAT account"))
            if self.vat_account in (provider.clearing_account, provider.bank_account, invoice.credit_to):
                frappe.throw(_("VAT account must be distinct from clearing, bank and payable accounts."))
        return invoice

    def on_trash(self):
        frappe.throw(_("Statements cannot be deleted; close them to reserve their reference."))


def _existing_row_map(existing_rows, settlement=None):
    rows = list(settlement.get("invoices") or []) if settlement else []
    if existing_rows:
        rows.extend(frappe.parse_json(existing_rows) if isinstance(existing_rows, str) else existing_rows)
    result = {}
    for row in rows:
        try:
            key = (row.get("source_doctype"), row.get("source_invoice"), payment_row_ids(row.get("payment_row_ids")))
        except ValueError:
            continue
        result[key] = row
    return result


@frappe.whitelist(methods=["POST"])
def fetch_provider_invoices(provider_account, company, pos_profile, from_date, to_date, settlement_name=None, existing_rows=None):
    provider = get_provider(provider_account)
    if not provider.enabled:
        frappe.throw(_("Provider configuration is disabled."))
    provider.validate()
    if provider.company != company:
        frappe.throw(_("Company must match the provider configuration."))
    start, end = validate_period(company, pos_profile, from_date, to_date)
    settlement = None
    if settlement_name and frappe.db.exists("BNPL Settlement", settlement_name):
        settlement = frappe.get_doc("BNPL Settlement", settlement_name)
        settlement.check_permission("write")
        if settlement.journal_entry:
            frappe.throw(_("Provider invoices cannot be refreshed after a Journal Entry is generated."))
        if settlement.provider_account != provider_account or settlement.company != company:
            frappe.throw(_("The saved settlement does not match the selected provider and company."))
    raw = _query_payment_rows(company, pos_profile, provider, start, end)
    claimed = {}
    if raw:
        for claim in frappe.get_all("BNPL Payment Claim", filters={"payment_row": ["in", [row.payment_row for row in raw]]}, fields=["payment_row", "settlement"]):
            claimed[claim.payment_row] = claim.settlement
    raw = [row for row in raw if row.payment_row not in claimed or claimed[row.payment_row] == settlement_name]
    grouped = _group_payment_rows(raw, provider.clearing_account)
    intervals = _settled_intervals(provider_account, pos_profile, settlement_name)
    existing = _existing_row_map(existing_rows, settlement)
    result = []
    late_count = 0
    for row in grouped:
        ids = payment_row_ids(row["payment_row_ids"])
        old = existing.get((row["source_doctype"], row["source_invoice"], ids), {})
        late = late_status(row["posting_datetime"], intervals)
        late_count += int(late)
        result.append({
            "selected": int(old.get("selected", 1)),
            "source_doctype": row["source_doctype"],
            "source_invoice": row["source_invoice"],
            "posting_datetime": row["posting_datetime"],
            "is_return": row["is_return"],
            "gl_voucher": row["gl_voucher"],
            "provider_amount": float(row["provider_amount"]),
            "payment_row_ids": row["payment_row_ids"],
            "late_entry": int(late),
            "settlement_note": old.get("settlement_note") or "",
        })
    gross = selected_amount(result)
    overlap = any(interval_start <= end and start <= interval_end for interval_start, interval_end in intervals)
    if late_count:
        message = _("{0} unclaimed invoice row(s) fall in an earlier completed settlement interval. Review and include them in this catch-up settlement if appropriate.").format(late_count)
    elif not result and overlap:
        message = _("This period overlaps an earlier completed settlement and appears already settled; no new unclaimed provider invoices were found.")
    elif not result:
        message = _("No eligible unclaimed provider invoices were found for this profile and inclusive date range.")
    else:
        message = _("Fetched {0} unclaimed provider invoice row(s).").format(len(result))
    warning = message if late_count or (not result and overlap) else ""
    return {"rows": result, "gross": float(gross), "late_count": late_count, "message": message, "warning": warning}


def _revalidate_selected_rows(settlement, provider):
    start, end = validate_period(settlement.company, settlement.pos_profile, settlement.from_date, settlement.to_date)
    selected = [row for row in settlement.invoices if row.selected]
    if not selected:
        frappe.throw(_("Select at least one provider invoice before creating the Journal Entry."))
    current = _group_payment_rows(_query_payment_rows(settlement.company, settlement.pos_profile, provider, start, end), provider.clearing_account)
    by_key = {(row["source_doctype"], row["source_invoice"], payment_row_ids(row["payment_row_ids"])): row for row in current}
    claims = []
    total = money(0)
    for row in selected:
        ids = payment_row_ids(row.payment_row_ids)
        current_row = by_key.get((row.source_doctype, row.source_invoice, ids))
        if not current_row or current_row["gl_voucher"] != row.gl_voucher or money(current_row["provider_amount"]) != money(row.provider_amount):
            frappe.throw(_("Provider invoice {0} changed or is no longer eligible. Fetch invoices again.").format(row.source_invoice))
        total += money(row.provider_amount)
        for payment in current_row["payment_rows"]:
            claim_name = payment_claim_name(payment["name"])
            owner = frappe.db.get_value("BNPL Payment Claim", claim_name, "settlement", for_update=True)
            if owner:
                frappe.throw(_("Payment row {0} is already claimed by settlement {1}.").format(payment["name"], owner))
            claims.append({
                "doctype": "BNPL Payment Claim", "settlement": settlement.name,
                "provider_account": settlement.provider_account, "source_doctype": row.source_doctype,
                "source_invoice": row.source_invoice, "payment_row": payment["name"],
                "provider_amount": float(payment["amount"]),
            })
    if total != money(settlement.gross):
        frappe.throw(_("Gross must equal the signed sum of selected provider invoice rows."))
    return claims


@frappe.whitelist(methods=["POST"])
def create_journal_entry(name):
    frappe.get_doc("BNPL Settlement", name).check_permission("write")
    settlement = frappe.get_doc("BNPL Settlement", name, for_update=True)
    settlement.check_permission("write")
    if settlement.status != "Open":
        frappe.throw(_("Only an open statement can generate a draft Journal Entry."))
    if settlement.journal_entry:
        frappe.throw(_("A Journal Entry has already been generated for this statement; it will not be replaced."))
    if not frappe.has_permission("Journal Entry", "create"):
        frappe.throw(_("You need permission to create Journal Entries."), frappe.PermissionError)
    provider = get_provider(settlement.provider_account)
    if not provider.enabled:
        frappe.throw(_("Provider configuration is disabled."))
    provider.validate()
    settlement.validate()
    claims = _revalidate_selected_rows(settlement, provider)
    gross, fee, vat, net = validate_amounts(settlement.gross, settlement.fee, settlement.vat, settlement.net)
    accounts = [{"account": provider.bank_account, "debit_in_account_currency": float(net)}]
    if fee:
        invoice = settlement.validate_invoice(provider, for_update=True)
        other = frappe.db.get_value("BNPL Settlement", {"name": ["!=", settlement.name], "tax_invoice": invoice.name, "journal_entry": ["!=", ""]}, "name", for_update=True)
        if other:
            frappe.throw(_("This fee invoice is already linked to another generated BNPL Journal Entry. Review it manually."))
        accounts.append({
            "account": invoice.credit_to, "party_type": "Supplier", "party": provider.supplier,
            "reference_type": "Purchase Invoice", "reference_name": invoice.name,
            "debit_in_account_currency": float(fee + vat),
        })
    accounts.append({"account": provider.clearing_account, "credit_in_account_currency": float(gross)})
    for claim in claims:
        try:
            frappe.get_doc(claim).insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            owner = frappe.db.get_value("BNPL Payment Claim", payment_claim_name(claim["payment_row"]), "settlement")
            frappe.throw(_("Payment row {0} was concurrently claimed by settlement {1}.").format(claim["payment_row"], owner or _("another settlement")))
    journal = frappe.get_doc({
        "doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": settlement.company,
        "naming_series": BNPL_JOURNAL_SERIES,
        "posting_date": settlement.payout_date,
        "user_remark": journal_entry_remark(
            provider.provider_name, settlement.statement_reference, settlement.pos_profile,
            settlement.from_date, settlement.to_date,
        ),
        "custom_bnpl_settlement": settlement.name,
        "custom_bnpl_provider_account": settlement.provider_account,
        "custom_bnpl_pos_profile": settlement.pos_profile,
        "custom_bnpl_from_date": settlement.from_date,
        "custom_bnpl_to_date": settlement.to_date,
        "custom_bnpl_statement_reference": settlement.statement_reference,
        "accounts": accounts,
    })
    journal.insert()
    settlement.journal_entry = journal.name
    settlement.flags.creating_journal = True
    settlement.save()
    return journal.name
