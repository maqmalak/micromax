"""Demo data generator for Import Shipments and Import Cost Sheets.

Run with:
    docker exec erpnext-backend-1 bench --site frontend execute \
        micromax.micromax_import.demo.import_demo.create_demo_data
"""

import frappe
from frappe.utils import add_days, nowdate


DEMO_COMPANY = "Silver Star Group (Demo)"
CURRENCY = "USD"

SHIPMENTS = [
    {"supplier": "Zuckerman Security Ltd.", "vessel": "MSC Aurora",
     "pol": "Shanghai", "pod": "Karachi", "bol": "MSCU-DEMO-1001",
     "container": "MSCU1001001", "shipping_line": "MSC",
     "duty": 125000.0, "tax": 38000.0, "status": "In Transit"},
    {"supplier": "MA Inc.", "vessel": "CMA CGM Marco Polo",
     "pol": "Ningbo", "pod": "Karachi", "bol": "CMDU-DEMO-1002",
     "container": "CMDU1002002", "shipping_line": "CMA CGM",
     "duty": 98000.0, "tax": 29500.0, "status": "Arrived"},
    {"supplier": "Summit Traders Ltd.", "vessel": "Ever Given",
     "pol": "Ho Chi Minh", "pod": "Port Qasim", "bol": "EGLV-DEMO-1003",
     "container": "EGLV1003003", "shipping_line": "Evergreen",
     "duty": 76000.0, "tax": 22000.0, "status": "Delivered"},
]

# (item, qty, purchase_value, freight, insurance, customs_duty)
ITEM_ROWS = [
    ("Demo Item - Basic T-Shirt", 500, 2500.0, 120.0, 30.0, 375.0),
    ("Demo Item - Cargo Pants", 300, 4800.0, 160.0, 40.0, 600.0),
    ("Demo Item - Men's Bomber Jacket", 150, 7500.0, 220.0, 55.0, 825.0),
    ("Demo Item - Women's Chino Pants", 350, 4200.0, 140.0, 35.0, 525.0),
    ("Demo Item - Men's Denim Jeans", 400, 5600.0, 180.0, 45.0, 675.0),
]

EXTRA_CHARGES = {
    "additional_duty": 150.0,
    "sales_tax": 450.0,
    "regulatory_duty": 90.0,
    "clearing_charges": 200.0,
    "port_charges": 260.0,
    "other_charges": 85.0,
}


def _get_or_create_purchase_orders():
    """Create one demo Purchase Order per supplier if none exist yet."""
    pos = {}
    for sup in [s["supplier"] for s in SHIPMENTS]:
        po = frappe.db.get_value(
            "Purchase Order", {"supplier": sup, "company": DEMO_COMPANY},
            order_by="creation desc")
        if not po:
            item = ITEM_ROWS[0][0]
            po_doc = frappe.new_doc("Purchase Order")
            po_doc.company = DEMO_COMPANY
            po_doc.supplier = sup
            po_doc.schedule_date = nowdate()
            po_doc.append("items", {
                "item_code": item, "qty": 100, "rate": 10.0,
                "schedule_date": nowdate(),
            })
            po_doc.insert(ignore_permissions=True)
            po = po_doc.name
        pos[sup] = po
    return pos


def create_demo_data():
    """Create 3 demo Import Shipments and 3 matching Import Cost Sheets."""
    company_currency = frappe.db.get_value("Company", DEMO_COMPANY, "default_currency")
    currency = company_currency or CURRENCY
    pos = _get_or_create_purchase_orders()
    created_shipments, created_cost_sheets = [], []
    uom = frappe.db.get_value("Item", ITEM_ROWS[0][0], "stock_uom") or "Nos"

    for i, s in enumerate(SHIPMENTS):
        shipment = frappe.new_doc("Import Shipment")
        shipment.supplier = s["supplier"]
        shipment.purchase_order = pos.get(s["supplier"])
        shipment.bill_of_lading = s["bol"]
        shipment.container_no = s["container"]
        shipment.shipping_line = s["shipping_line"]
        shipment.vessel = s["vessel"]
        shipment.port_of_loading = s["pol"]
        shipment.port_of_discharge = s["pod"]
        shipment.etd = add_days(nowdate(), -20 + i * 3)
        shipment.eta = add_days(nowdate(), -5 + i * 3)
        shipment.duty_amount = s["duty"]
        shipment.tax_amount = s["tax"]
        shipment.shipment_status = s["status"]
        shipment.insert(ignore_permissions=True)
        created_shipments.append(shipment.name)

        cost_sheet = frappe.new_doc("Import Cost Sheet")
        cost_sheet.company = DEMO_COMPANY
        cost_sheet.supplier = s["supplier"]
        cost_sheet.purchase_order = pos.get(s["supplier"])
        cost_sheet.import_shipment = shipment.name
        cost_sheet.currency = currency
        cost_sheet.cost_sheet_date = nowdate()
        total_purchase = 0.0
        for row in ITEM_ROWS:
            item, qty, purchase_value, freight, insurance, customs_duty = row
            landed = (purchase_value + freight + insurance + customs_duty
                      + sum(EXTRA_CHARGES.values()))
            cost_sheet.append("import_cost_sheet_items", {
                "item": item, "quantity": qty, "uom": uom,
                "purchase_value": purchase_value, "freight": freight,
                "insurance": insurance, "customs_duty": customs_duty,
                **EXTRA_CHARGES,
                "total_landed_cost": landed,
                "landed_cost_per_unit": round(landed / qty, 2),
            })
            total_purchase += purchase_value
        cost_sheet.total_purchase_value = total_purchase
        cost_sheet.total_landed_cost = sum(
            r.total_landed_cost for r in cost_sheet.import_cost_sheet_items)
        cost_sheet.insert(ignore_permissions=True)
        created_cost_sheets.append(cost_sheet.name)

    frappe.db.commit()
    return {"shipments": created_shipments, "cost_sheets": created_cost_sheets}


def clear_demo_data():
    """Remove the demo Import Cost Sheets and Import Shipments."""
    for dt in ("Import Cost Sheet", "Import Shipment"):
        for name in frappe.get_all(dt, pluck="name"):
            frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
    frappe.db.commit()
    return "cleared"
