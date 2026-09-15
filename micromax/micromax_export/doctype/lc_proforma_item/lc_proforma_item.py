# This is a child table doctype; the controller is minimal.
# Amount = Quantity * Rate is computed in the parent's validate / JS.

import frappe
from frappe.model.document import Document


class LCProformaItem(Document):
    def validate(self):
        self.amount = (self.quantity or 0) * (self.rate or 0)