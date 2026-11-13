from frappe.model.document import Document

from saudi_bnpl.financial import payment_claim_name


class BNPLPaymentClaim(Document):
    def autoname(self):
        self.name = payment_claim_name(self.payment_row)
