import frappe
from frappe.model.document import Document


class ExportShipment(Document):
    def validate(self):
        self.shipment_no = self.name
        if self.lc_proforma:
            self.copy_from_lc_proforma()

    def copy_from_lc_proforma(self):
        """Pull LC details without loading the whole document (perf)."""
        defaults = frappe.db.get_value(
            "LC Proforma",
            self.lc_proforma,
            ["customer", "lc_no", "port_of_loading", "port_of_discharge", "final_destination"],
            as_dict=True,
        )
        if not defaults:
            return
        for field in [
            "customer",
            "lc_no",
            "port_of_loading",
            "port_of_discharge",
            "final_destination",
        ]:
            if not self.get(field) and defaults.get(field):
                self.set(field, defaults[field])