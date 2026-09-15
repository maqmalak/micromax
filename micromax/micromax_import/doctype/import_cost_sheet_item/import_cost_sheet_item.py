from frappe.model.document import Document


class ImportCostSheetItem(Document):
    def validate(self):
        costs = [
            "purchase_value",
            "freight",
            "insurance",
            "customs_duty",
            "additional_duty",
            "sales_tax",
            "regulatory_duty",
            "clearing_charges",
            "port_charges",
            "other_charges",
        ]
        total = sum(float(self.get(c) or 0) for c in costs)
        self.total_landed_cost = total
        qty = float(self.get("quantity") or 0)
        self.landed_cost_per_unit = (total / qty) if qty else 0