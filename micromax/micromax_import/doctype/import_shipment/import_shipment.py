import frappe
from frappe.model.document import Document


class ImportShipment(Document):
    def validate(self):
        self.shipment_no = self.name

    def after_insert(self):
        self.update_cost_sheet_link()

    def update_cost_sheet_link(self):
        if self.import_cost_sheet:
            frappe.db.set_value("Import Cost Sheet", self.import_cost_sheet, "import_shipment", self.name)