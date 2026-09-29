import frappe
from frappe.model.document import Document


class ExportPackingDetails(Document):
    def validate(self):
        self.packing_no = self.name
        self.update_totals()

    def update_totals(self):
        cartons = pieces = net = gross = cbm = 0
        for row in self.get("export_packing_details") or []:
            n = carton_count(row.get("carton_no"))
            cartons += n
            pieces += n * int(row.get("pieces_per_carton") or 0) if row.get("pieces_per_carton") else float(row.get("quantity") or 0)
            net += float(row.get("net_weight") or 0)
            gross += float(row.get("gross_weight") or 0)
            cbm += float(row.get("volume_cbm") or 0)
        self.total_cartons = cartons
        self.total_pieces = pieces
        self.total_net_weight = net
        self.total_gross_weight = gross
        self.total_cbm = cbm

def carton_count(carton_no):
    """"12" → 1 carton, "1-40" / "1 – 40" → 40 cartons, blank → 0."""
    if not carton_no:
        return 0
    parts = [p.strip() for p in str(carton_no).replace("–", "-").split("-")]
    try:
        return int(parts[-1]) - int(parts[0]) + 1 if len(parts) == 2 else 1
    except ValueError:
        return 1
