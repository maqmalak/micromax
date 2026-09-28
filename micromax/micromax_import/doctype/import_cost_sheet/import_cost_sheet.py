import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today

# Pakistan import costing (Customs Act 1969, Sales Tax Act 1990, Income Tax Ordinance 2001, IAS 2):
#   Assessable value (AV) = (purchase value + freight + insurance) × (1 + 1% landing charges)
#   Customs duty, additional customs duty and regulatory duty are levied on AV and are NOT refundable →
#   they are part of the stock's cost, as are freight, insurance, clearing, port and other charges.
#   Sales tax at import (s.7 STA — adjustable input tax for a registered manufacturer) and income tax
#   withheld at import (s.148 ITO — advance tax, adjustable against the annual liability) are NOT
#   inventory cost: they are debited to their tax asset accounts, never to the landed cost.
LANDING_CHARGE_RATE = 0.01
# Charges that are capitalised into stock (Landed Cost Voucher).
# (fieldname, charge label, Buying Settings field holding its default Expense Account)
CHARGE_FIELDS = [
    ("freight", "Freight", "freight_expense_account"),
    ("insurance", "Insurance", "insurance_expense_account"),
    ("customs_duty", "Customs Duty", "customs_duty_expense_account"),
    ("additional_duty", "Additional Duty", "additional_duty_expense_account"),
    ("regulatory_duty", "Regulatory Duty", "regulatory_duty_expense_account"),
    ("clearing_charges", "Clearing Charges", "clearing_charges_expense_account"),
    ("port_charges", "Port Charges", "port_charges_expense_account"),
    ("other_charges", "Other Charges", "other_charges_expense_account"),
]
# Adjustable taxes paid at import: booked to tax asset accounts, excluded from landed cost.
# (fieldname, label, Buying Settings field holding the asset account)
ADJUSTABLE_TAXES = [
    ("sales_tax", "Sales Tax at Import (s.7 STA, adjustable)", "sales_tax_expense_account"),
    ("income_tax_148", "Income Tax at Import (s.148 ITO, adjustable)", "import_income_tax_account"),
]
DUTY_FIELDS = ("customs_duty", "additional_duty", "regulatory_duty")


class ImportCostSheet(Document):
    def validate(self):
        tot = {"purchase": 0.0, "landed": 0.0, "duties": 0.0, "sales_tax": 0.0, "income_tax": 0.0}
        for row in self.get("import_cost_sheet_items") or []:
            pv = flt(row.get("purchase_value"))
            if row.meta.has_field("assessable_value"):
                row.assessable_value = flt((pv + flt(row.get("freight")) + flt(row.get("insurance"))) * (1 + LANDING_CHARGE_RATE), 2)
            # Landed cost = purchase value + capitalised charges only (no sales tax / s.148 income tax).
            row.total_landed_cost = flt(pv + sum(flt(row.get(f)) for f, _l, _s in CHARGE_FIELDS), 2)
            row.landed_cost_per_unit = flt(row.total_landed_cost / flt(row.quantity), 4) if flt(row.quantity) else 0
            tot["purchase"] += pv
            tot["landed"] += flt(row.total_landed_cost)
            tot["duties"] += sum(flt(row.get(f)) for f in DUTY_FIELDS)
            tot["sales_tax"] += flt(row.get("sales_tax"))
            tot["income_tax"] += flt(row.get("income_tax_148"))
        self.total_purchase_value = tot["purchase"]
        self.total_landed_cost = tot["landed"]
        for field, key in (("total_duties", "duties"), ("total_input_sales_tax", "sales_tax"), ("total_income_tax_148", "income_tax")):
            if self.meta.has_field(field):
                self.set(field, tot[key])

    @frappe.whitelist()
    def apply_pakistan_duties(self, customs_duty_rate=0, additional_duty_rate=0, regulatory_duty_rate=0, sales_tax_rate=18, income_tax_rate=1):
        """Fill every row's duties and taxes from rates (%), the way Pakistan Customs assesses a Goods Declaration:
        AV = CIF × 1.01; CD/ACD/RD = AV × rate; sales tax = (AV + CD + ACD + RD) × rate;
        s.148 income tax = (AV + CD + ACD + RD + sales tax) × rate."""
        cd, acd, rd, st, it = (flt(x) / 100 for x in (customs_duty_rate, additional_duty_rate, regulatory_duty_rate, sales_tax_rate, income_tax_rate))
        for row in self.get("import_cost_sheet_items") or []:
            av = (flt(row.purchase_value) + flt(row.freight) + flt(row.insurance)) * (1 + LANDING_CHARGE_RATE)
            row.customs_duty, row.additional_duty, row.regulatory_duty = flt(av * cd, 2), flt(av * acd, 2), flt(av * rd, 2)
            duty_paid_value = av + row.customs_duty + row.additional_duty + row.regulatory_duty
            row.sales_tax = flt(duty_paid_value * st, 2)
            if row.meta.has_field("income_tax_148"):
                row.income_tax_148 = flt((duty_paid_value + row.sales_tax) * it, 2)
        self.validate()
        return self.as_dict()

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
        # Only capitalisable charges go on the voucher; sales tax and s.148 income tax are adjustable (see top).
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

    @frappe.whitelist()
    def make_customs_journal_entry(self):
        """Unsaved Journal Entry for paying the Goods Declaration and import charges:
        Dr each capitalised charge account (cleared again by the Landed Cost Voucher, which moves the cost into stock),
        Dr Input Sales Tax (adjustable), Dr Advance Income Tax u/s 148 (adjustable), Cr Customs & Clearing Payable."""
        bs = frappe.get_single("Buying Settings")
        payable = bs.get("import_charges_payable_account")
        if not payable:
            frappe.throw(_("Set 'Customs & Clearing Payable Account' under Buying Settings > Landed Cost first."))
        rows, missing = [], []
        items = self.get("import_cost_sheet_items") or []
        for fieldname, label, settings_field in CHARGE_FIELDS + ADJUSTABLE_TAXES:
            amount = flt(sum(flt(r.get(fieldname)) for r in items), 2)
            if not amount:
                continue
            account = (self.expense_account if (fieldname, label, settings_field) in CHARGE_FIELDS else None) or bs.get(settings_field) \
                or (bs.default_landed_cost_expense_account if (fieldname, label, settings_field) in CHARGE_FIELDS else None)
            if not account:
                missing.append(label)
                continue
            rows.append({"account": account, "debit_in_account_currency": amount, "user_remark": label})
        if missing:
            frappe.throw(_("No account set in Buying Settings for: {0}").format(", ".join(missing)))
        total = flt(sum(r["debit_in_account_currency"] for r in rows), 2)
        rows.append({"account": payable, "credit_in_account_currency": total})
        cost_center = frappe.get_cached_value("Company", self.company, "cost_center")
        for r in rows:
            r.setdefault("cost_center", cost_center)
        posting = (frappe.db.get_value("Purchase Receipt", self.purchase_receipt, "posting_date") if self.purchase_receipt else None) or self.cost_sheet_date or today()
        je = frappe.new_doc("Journal Entry")
        je.update({"voucher_type": "Journal Entry", "company": self.company, "posting_date": posting, "cheque_no": self.name, "cheque_date": posting,
                   "user_remark": _("Goods Declaration & import charges for {0} ({1})").format(self.purchase_receipt or self.purchase_order or "", self.name),
                   "accounts": rows})
        return je.as_dict()
