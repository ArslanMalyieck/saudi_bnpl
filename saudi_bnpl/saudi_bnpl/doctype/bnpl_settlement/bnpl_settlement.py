import frappe
from frappe import _
from frappe.model.document import Document

from saudi_bnpl.financial import money, stable_name, validate_amounts
from saudi_bnpl.saudi_bnpl.doctype.bnpl_provider_account.bnpl_provider_account import account_details


def get_provider(name):
    frappe.get_doc("BNPL Provider Account", name).check_permission("read")
    provider = frappe.get_doc("BNPL Provider Account", name, for_update=True)
    provider.check_permission("read")
    return provider


class BNPLSettlement(Document):
    def autoname(self):
        provider = get_provider(self.provider_account)
        try:
            self.name = stable_name("BNPL-S-", self.company, provider.provider_name, self.statement_reference)
        except ValueError as exc:
            frappe.throw(str(exc))

    def validate(self):
        provider = get_provider(self.provider_account)
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
        previous = self.get_doc_before_save()
        if previous:
            if previous.provider_account != self.provider_account:
                frappe.throw(_("The provider account cannot be changed."))
            if previous.journal_entry:
                changed = any(money(previous.get(field)) != money(self.get(field)) for field in ("gross", "fee", "vat", "net"))
                changed |= frappe.utils.getdate(previous.payout_date) != frappe.utils.getdate(self.payout_date)
                changed |= any((previous.get(field) or None) != (self.get(field) or None) for field in ("tax_invoice", "vat_account"))
                if changed:
                    frappe.throw(_("Amounts and accounting details cannot be changed after a Journal Entry is generated."))
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
        matches = frappe.get_all(
            "Purchase Invoice",
            filters={"company": invoice.company, "supplier": invoice.supplier, "bill_no": invoice.bill_no, "docstatus": 1},
            fields=["name", "company", "supplier", "bill_no"],
            limit_page_length=0,
        )
        if any(row["name"] != invoice.name and row["company"] == invoice.company
               and row["supplier"] == invoice.supplier and row["bill_no"] == invoice.bill_no for row in matches):
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


@frappe.whitelist(methods=["POST"])
def create_journal_entry(name):
    # Check permission before acquiring a lock, then reload under the transaction lock.
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
    gross, fee, vat, net = validate_amounts(settlement.gross, settlement.fee, settlement.vat, settlement.net)
    accounts = [{"account": provider.bank_account, "debit_in_account_currency": float(net)}]
    if fee:
        invoice = settlement.validate_invoice(provider, for_update=True)
        other = frappe.db.get_value(
            "BNPL Settlement", {"name": ["!=", settlement.name], "tax_invoice": invoice.name, "journal_entry": ["!=", ""]},
            "name", for_update=True,
        )
        if other:
            frappe.throw(_("This fee invoice is already linked to another generated BNPL Journal Entry. Review it manually."))
        accounts.append({
            "account": invoice.credit_to, "party_type": "Supplier", "party": provider.supplier,
            "reference_type": "Purchase Invoice", "reference_name": invoice.name,
            "debit_in_account_currency": float(fee + vat),
        })
    accounts.append({"account": provider.clearing_account, "credit_in_account_currency": float(gross)})
    journal = frappe.get_doc({
        "doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": settlement.company,
        "posting_date": settlement.payout_date, "user_remark": _("Draft prepared for BNPL statement {0}").format(settlement.name),
        "accounts": accounts,
    })
    journal.insert()
    settlement.journal_entry = journal.name
    settlement.flags.creating_journal = True
    settlement.save()
    return journal.name
