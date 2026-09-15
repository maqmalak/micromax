import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

# (fieldname, charge label, Buying Settings field holding its default Expense Account)
CHARGE_FIELDS = [
    ("freight", "Freight", "freight_expense_account"),
    ("insurance", "Insurance", "insurance_expense_account"),
    ("customs_duty", "Customs Duty", "customs_duty_expense_account"),
    ("additional_duty", "Additional Duty", "additional_duty_expense_account"),
    ("sales_tax", "Sales Tax", "sales_tax_expense_account"),
    ("regulatory_duty", "Regulatory Duty", "regulatory_duty_expense_account"),
    ("clearing_charges", "Clearing Charges", "clearing_charges_expense_account"),
    ("port_charges", "Port Charges", "port_charges_expense_account"),
    ("other_charges", "Other Charges", "other_charges_expense_account"),
]


class ImportCostSheet(Document):
    def validate(self):
        tot_purchase = 0
        tot_landed = 0
        # Child rows are validated by the framework; only aggregate here.
        for row in self.get("import_cost_sheet_items") or []:
            tot_purchase += flt(row.get("purchase_value"))
            tot_landed += flt(row.get("total_landed_cost"))
        self.total_purchase_value = tot_purchase
        self.total_landed_cost = tot_landed

    @frappe.whitelist()
    def make_landed_cost_voucher(self, receipt_doctype=None, receipt_document=None):
        """Build an unsaved Landed Cost Voucher whose charge breakdown
        (freight, insurance, customs duty, ...) is pulled from this cost
        sheet's items, summed across all rows.

        By default the voucher references this cost sheet's own linked
        Purchase Receipt. A caller may instead pass `receipt_doctype` /
        `receipt_document` to reference a different submitted document (e.g.
        a Purchase Invoice that shares this cost sheet's Purchase Order) —
        used when generating from that document's own page, so the charges
        still come from here but the voucher lines up with the document the
        user is actually looking at.
        """
        receipt_doctype = receipt_doctype or "Purchase Receipt"
        receipt_document = receipt_document or self.purchase_receipt

        if receipt_doctype not in ("Purchase Receipt", "Purchase Invoice"):
            frappe.throw(_("Unsupported receipt document type: {0}").format(receipt_doctype))

        if not receipt_document:
            frappe.throw(
                _(
                    "Link a submitted Purchase Receipt on this Import Cost Sheet "
                    "before generating a Landed Cost Voucher."
                )
            )

        fields = ["supplier", "company", "base_grand_total", "docstatus"]
        if receipt_doctype == "Purchase Invoice":
            fields.append("update_stock")
        details = frappe.db.get_value(receipt_doctype, receipt_document, fields, as_dict=True)
        if not details or details.docstatus != 1:
            frappe.throw(
                _("{0} {1} must be submitted before it can be used on a Landed Cost Voucher.").format(
                    receipt_doctype, receipt_document
                )
            )
        if receipt_doctype == "Purchase Invoice" and not details.update_stock:
            frappe.throw(
                _(
                    "Purchase Invoice {0} does not have 'Update Stock' enabled, so it has no "
                    "stock impact and cannot be used on a Landed Cost Voucher."
                ).format(receipt_document)
            )

        buying_settings = frappe.get_single("Buying Settings")

        def resolve_account(buying_settings_field):
            # This cost sheet's own Expense Account (if set) overrides every
            # charge uniformly; otherwise prefer the charge-specific Buying
            # Settings account, falling back to the general default.
            return (
                self.expense_account
                or buying_settings.get(buying_settings_field)
                or buying_settings.default_landed_cost_expense_account
            )

        lcv = frappe.new_doc("Landed Cost Voucher")
        lcv.company = details.company
        lcv.posting_date = self.cost_sheet_date or today()
        lcv.append(
            "purchase_receipts",
            {
                "receipt_document_type": receipt_doctype,
                "receipt_document": receipt_document,
                "supplier": details.supplier,
                "grand_total": details.base_grand_total,
            },
        )
        lcv.get_items_from_purchase_receipts()

        missing_accounts = []
        for fieldname, label, settings_field in CHARGE_FIELDS:
            amount = sum(flt(row.get(fieldname)) for row in self.get("import_cost_sheet_items") or [])
            if not amount:
                continue
            account = resolve_account(settings_field)
            if not account:
                missing_accounts.append(label)
                continue
            lcv.append("taxes", {"description": label, "amount": amount, "expense_account": account})

        if missing_accounts:
            frappe.throw(
                _(
                    "No Expense Account found for: {0}. Set one on this Import Cost Sheet, "
                    "or under Buying Settings > Landed Cost."
                ).format(", ".join(missing_accounts))
            )

        if not lcv.get("taxes"):
            frappe.throw(
                _(
                    "This Import Cost Sheet has no landed cost charges to allocate — every "
                    "Freight / Insurance / Customs Duty / ... column on its Item Allocation "
                    "table is zero. Enter charge amounts there before generating a Landed "
                    "Cost Voucher."
                )
            )

        return lcv.as_dict()