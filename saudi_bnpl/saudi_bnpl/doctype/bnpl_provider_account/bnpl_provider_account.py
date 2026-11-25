import frappe
from frappe import _
from frappe.model.document import Document

from saudi_bnpl.financial import stable_name

PROVIDERS = ("Tabby", "Tamara", "Emkan", "NEO", "Madfu")
CONFIG_FIELDS = ("company", "provider_name", "mode_of_payment", "clearing_account", "bank_account", "supplier")


def account_details(name, company, label):
    account = frappe.db.get_value(
        "Account", name,
        ["company", "is_group", "disabled", "account_currency", "report_type", "account_type", "root_type"],
        as_dict=True,
    )
    if not account or account.company != company or account.is_group or account.disabled or account.account_currency != "SAR":
        frappe.throw(_("{0} must be an enabled, leaf SAR account belonging to the company.").format(label))
    return account


class BNPLProviderAccount(Document):
    def autoname(self):
        try:
            self.name = stable_name("BNPL-P-", self.company, self.provider_name)
        except ValueError as exc:
            frappe.throw(str(exc))

    def validate(self):
        if self.provider_name not in PROVIDERS:
            frappe.throw(_("Provider must be one of Tabby, Tamara, Emkan, NEO or Madfu."))
        previous = self.get_doc_before_save()
        if previous:
            changed = any(previous.get(field) != self.get(field) for field in CONFIG_FIELDS)
            if changed and frappe.db.get_value(
                "BNPL Settlement", {"provider_account": self.name}, "name", for_update=True
            ):
                frappe.throw(_("This provider configuration is referenced by a BNPL Settlement. Its company, provider, payment mode, clearing account, bank account and supplier cannot be changed. You may enable or disable it."))
            if not changed and previous.enabled and not self.enabled:
                return
        try:
            expected = stable_name("BNPL-P-", self.company, self.provider_name)
        except ValueError as exc:
            frappe.throw(str(exc))
        if self.name != expected:
            frappe.throw(_("Company and provider name are permanent; create a different provider configuration instead."))
        if frappe.db.get_value("Company", self.company, "default_currency") != "SAR":
            frappe.throw(_("Only companies with SAR as their default currency are supported."))
        if self.clearing_account == self.bank_account:
            frappe.throw(_("Clearing and payout bank accounts must be distinct."))
        clearing = account_details(self.clearing_account, self.company, _("Clearing account"))
        bank = account_details(self.bank_account, self.company, _("Bank account"))
        if clearing.report_type != "Balance Sheet" or clearing.root_type != "Asset" or clearing.account_type != "Cash":
            frappe.throw(_("Clearing must be an asset balance-sheet account classified as Cash for the native Mode of Payment selector. Bank and Receivable clearing accounts are not supported; finance must approve the Cash classification."))
        if bank.report_type != "Balance Sheet" or bank.account_type != "Bank":
            frappe.throw(_("Payout account must be a balance-sheet Bank account."))
        mode = frappe.get_doc("Mode of Payment", self.mode_of_payment)
        mode.check_permission("read")
        if not mode.enabled:
            frappe.throw(_("Mode of Payment must be enabled."))
        mappings = [row.default_account for row in mode.accounts if row.company == self.company]
        if mappings != [self.clearing_account]:
            frappe.throw(_("Mode of Payment must have exactly one company mapping, pointing to the clearing account."))
        if not frappe.db.exists("Supplier", self.supplier) or frappe.db.get_value("Supplier", self.supplier, "disabled"):
            frappe.throw(_("Provider supplier must exist and be enabled."))

    def on_trash(self):
        frappe.throw(_("Provider configurations cannot be deleted; disable them instead."))
