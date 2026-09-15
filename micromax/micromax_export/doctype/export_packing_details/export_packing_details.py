import frappe
from frappe.model.document import Document


class ExportPackingDetails(Document):
    def validate(self):
        self.packing_no = self.name
        self.update_totals()

    def update_totals(self):
        cartons = pieces = net = gross = cbm = 0
        for row in self.get("export_packing_details") or []:
            if row.get("carton_no"):
                cartons += 1
            pieces += float(row.get("quantity") or 0)
            net += float(row.get("net_weight") or 0)
            gross += float(row.get("gross_weight") or 0)
            cbm += float(row.get("volume_cbm") or 0)
        self.total_cartons = cartons
        self.total_pieces = pieces
        self.total_net_weight = net
        self.total_gross_weight = gross
        self.total_cbm = cbm