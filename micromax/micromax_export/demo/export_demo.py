"""Demo data generator for Export Packing Details and Export Shipments.

Run with:
    docker cp .../export_demo.py <backend>:/home/frappe/frappe-bench/apps/micromax/micromax/micromax_export/demo/
    docker exec <backend> env/bin/python /tmp/run_export_demo.py
"""

import frappe
from frappe.utils import add_days, nowdate


SHIPMENT_SPECS = [
    {"so": "SAL-ORD-2026-00006", "vessel": "MSC Aurora", "bol": "MSCU-EXP-2001",
     "container": "MSCU2001001", "shipping_line": "MSC",
     "pol": "Karachi Port", "pod": "Barcelona", "status": "Shipped"},
    {"so": "SAL-ORD-2026-00007", "vessel": "CMA CGM Marco Polo",
     "bol": "CMDU-EXP-2002", "container": "CMDU2002002",
     "shipping_line": "CMA CGM", "pol": "Port Qasim", "pod": "Alicante",
     "status": "In Transit"},
]


def _split_into_cartons(total_qty, pieces_per_carton=50):
    """Yield per-carton quantities totalling total_qty."""
    remaining = int(total_qty)
    while remaining > 0:
        yield min(pieces_per_carton, remaining)
        remaining -= pieces_per_carton


def create_demo_data():
    """Create Export Packing Details (per Sales Order) and Export Shipments."""
    created_packings, created_shipments = [], []
    carton_no = 1

    for i, spec in enumerate(SHIPMENT_SPECS):
        so = frappe.get_doc("Sales Order", spec["so"])
        packing = frappe.new_doc("Export Packing Details")
        packing.sales_order = so.name
        packing.customer = so.customer
        packing.packing_date = add_days(nowdate(), -10 + i * 2)

        pieces = 0
        net = gross = cbm = 0.0
        for so_item in so.get("items") or []:
            qty = so_item.qty or 0
            for _qty_in_carton in _split_into_cartons(qty):
                n_wt = round(_qty_in_carton * 0.35, 2)
                g_wt = round(n_wt + 1.8, 2)
                vol = 0.065
                packing.append("export_packing_details", {
                    "carton_no": f"CTN-{carton_no:04d}",
                    "item": so_item.item_code,
                    "style": (so_item.item_name or so_item.item_code)[:40],
                    "color": "Assorted",
                    "size": "Mixed",
                    "quantity": _qty_in_carton,
                    "pieces_per_carton": 50,
                    "net_weight": n_wt,
                    "gross_weight": g_wt,
                    "dimensions": "60x40x40 cm",
                    "volume_cbm": vol,
                })
                carton_no += 1
                pieces += _qty_in_carton
                net += n_wt
                gross += g_wt
                cbm += vol

        packing.total_cartons = len(packing.export_packing_details)
        packing.total_pieces = pieces
        packing.total_net_weight = round(net, 2)
        packing.total_gross_weight = round(gross, 2)
        packing.total_cbm = round(cbm, 3)
        packing.insert(ignore_permissions=True)
        created_packings.append(packing.name)

        shipment = frappe.new_doc("Export Shipment")
        shipment.customer = so.customer
        shipment.sales_order = so.name
        shipment.packing_list_no = packing.name
        if so.get("lc_proforma"):
            shipment.lc_proforma = so.lc_proforma
            shipment.lc_no = so.get("lc_no")
        shipment.bill_of_lading_no = spec["bol"]
        shipment.container_no = spec["container"]
        shipment.shipping_line = spec["shipping_line"]
        shipment.vessel = spec["vessel"]
        shipment.port_of_loading = spec["pol"]
        shipment.port_of_discharge = spec["pod"]
        shipment.etd = add_days(nowdate(), -8 + i * 3)
        shipment.eta = add_days(nowdate(), 12 + i * 3)
        shipment.shipment_status = spec["status"]
        try:
            shipment.insert(ignore_permissions=True)
        except Exception:
            frappe.db.rollback()
            shipment = frappe.new_doc("Export Shipment")
            shipment.customer = so.customer
            shipment.sales_order = so.name
            shipment.packing_list_no = packing.name
            shipment.bill_of_lading_no = spec["bol"]
            shipment.container_no = spec["container"]
            shipment.shipping_line = spec["shipping_line"]
            shipment.vessel = spec["vessel"]
            shipment.port_of_loading = spec["pol"]
            shipment.port_of_discharge = spec["pod"]
            shipment.etd = add_days(nowdate(), -8 + i * 3)
            shipment.eta = add_days(nowdate(), 12 + i * 3)
            shipment.insert(ignore_permissions=True)
        created_shipments.append(shipment.name)

        # Link the packing list back to the shipment
        frappe.db.set_value("Export Packing Details", packing.name,
                            "export_shipment", shipment.name)

    frappe.db.commit()
    return {"packings": created_packings, "shipments": created_shipments}


def clear_demo_data():
    """Remove demo Export Shipments and Export Packing Details."""
    for dt in ("Export Shipment", "Export Packing Details"):
        for name in frappe.get_all(dt, pluck="name"):
            frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
    frappe.db.commit()
    return "cleared"
