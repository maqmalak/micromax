import frappe
import frappe.utils
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc


class LCProforma(Document):
    def validate(self):
        self.proforma_no = self.name
        self.update_item_totals()
        self.update_lc_status_from_workflow()

    def on_submit(self):
        """Update LC status when document is submitted."""
        self.db_set("lc_status", "Submitted")

    def on_cancel(self):
        """Update LC status when document is cancelled."""
        self.db_set("lc_status", "Cancelled")

    def update_item_totals(self):
        total_qty = total_cartons = total_net = total_gross = total_value = 0
        for row in self.get("lc_proforma_items") or []:
            row.amount = (row.quantity or 0) * (row.rate or 0)
            total_qty += row.quantity or 0
            total_cartons += row.cartons or 0
            total_net += row.net_weight or 0
            total_gross += row.gross_weight or 0
            total_value += row.amount or 0
        self.total_quantity = total_qty
        self.total_cartons = total_cartons
        self.total_net_weight = total_net
        self.total_gross_weight = total_gross
        self.total_proforma_value = total_value

    def update_lc_status_from_workflow(self):
        if getattr(self, "workflow_state", None):
            self.lc_status = self.workflow_state
        elif not self.get("lc_status"):
            self.lc_status = "Draft"

    @frappe.whitelist()
    def make_sales_order(self):
        """Create an ERPNext Sales Order from the LC Proforma."""
        if not self.get("lc_proforma_items"):
            frappe.throw("No items to create a Sales Order from.")

        # Guard against creating duplicate Sales Orders for the same proforma.
        if self.get("export_order") and frappe.db.exists("Sales Order", self.export_order):
            frappe.msgprint(
                _("Sales Order {0} already exists for LC Proforma {1}.").format(
                    self.export_order, self.name
                )
            )
            return self.export_order

        target_doc = get_mapped_doc(
            "LC Proforma",
            self.name,
            {
                "LC Proforma": {
                    "doctype": "Sales Order",
                    "field_map": {
                        "customer": "customer",
                        "transaction_date": "transaction_date",
                        "currency": "currency",
                        "buyer_po_no": "po_no",
                    },
                },
                "LC Proforma Item": {
                    "doctype": "Sales Order Item",
                    "field_map": {
                        "item": "item_code",
                        "quantity": "qty",
                        "rate": "rate",
                        "uom": "uom",
                    },
                },
            },
            ignore_permissions=True,
        )

        target_doc.transaction_date = self.proforma_date or frappe.utils.today()
        target_doc.delivery_date = self.latest_shipment_date or self.proforma_date
        target_doc.company = self.company
        target_doc.currency = self.currency
        target_doc.po_no = self.buyer_po_no

        # Mapped item rows can inherit a cost center / warehouse belonging to
        # a different company (via Item defaults). Re-point them to the
        # target company's defaults so validation passes.
        company_cost_center = frappe.db.get_value(
            "Company", target_doc.company, "cost_center"
        )
        for row in target_doc.get("items") or []:
            row.cost_center = company_cost_center

        # Populate MicroMax export custom fields on the Sales Order
        target_doc.lc_proforma = self.name
        target_doc.lc_no = self.lc_no
        target_doc.lc_date = self.lc_date
        target_doc.lc_amount = self.lc_amount
        target_doc.lc_currency = self.lc_currency
        target_doc.lc_issuing_bank = self.lc_issuing_bank
        target_doc.lc_expiry_date = self.lc_expiry_date
        target_doc.latest_shipment_date = self.latest_shipment_date
        target_doc.port_of_loading = self.port_of_loading
        target_doc.port_of_discharge = self.port_of_discharge
        target_doc.final_destination = self.final_destination
        target_doc.incoterm = self.incoterm
        target_doc.shipment_mode = self.shipment_mode
        target_doc.country_of_destination = self.country_of_destination
        target_doc.export_status = "Planned"
        target_doc.export_order_flag = target_doc.name

        target_doc.insert(ignore_permissions=True, ignore_mandatory=True)

        # Link back: LC Proforma -> Sales Order
        self.db_set("export_order", target_doc.name)

        frappe.msgprint(
            f"Sales Order {target_doc.name} created from LC Proforma {self.name}"
        )
        return target_doc.name