"""Scaffold generator: writes the micromax DocType JSON files with correct
fraction/hand-verifiable output via json.dump. Run once: python3 build.py"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
APPR = os.path.dirname(HERE)  # apps/micromax


def F(fieldname, label, fieldtype, **kw):
    d = {"fieldname": fieldname, "fieldtype": fieldtype}
    if label:
        d["label"] = label
    d.update(kw)
    return d


def section(fieldname, label=None, collapsible=False, **kw):
    d = {"fieldname": fieldname, "fieldtype": "Section Break"}
    if label:
        d["label"] = label
    if collapsible:
        d["collapsible"] = 1
    d.update(kw)
    return d


def column(fieldname):
    return {"fieldname": fieldname, "fieldtype": "Column Break"}


def header(name, module, field_order, is_submittable=False, title_field=None,
           quick_entry=False, custom=None, actions=None, links=None,
           permissions=None, autoname="format:", icheck=0, istable=False):
    base = {
        "actions": actions or [],
        "allow_rename": 0,
        "autoname": autoname,
        "creation": "2026-01-01 00:00:00.000000",
        "doctype": "DocType",
        "engine": "InnoDB",
        "field_order": field_order,
        "istable": istable,
        "is_submittable": 1 if is_submittable else 0,
        "links": links or [],
        "modified": "2026-01-01 00:00:00.000000",
        "modified_by": "Administrator",
        "module": module,
        "name": name,
        "owner": "Administrator",
        "permissions": permissions or [],
        "quick_entry": 1 if quick_entry else 0,
        "sort_field": "modified",
        "sort_order": "DESC",
        "track_changes": 1,
    }
    if title_field:
        base["title_field"] = title_field
    if custom:
        base.update(custom)
    return base


def write(name, data):
    path = None
    # locate module folder from module string
    mod_folder = {
        "MicroMax Export": "micromax_export",
        "MicroMax Import": "micromax_import",
    }[data["module"]]
    doctype = name.lower().replace(" ", "_")
    d = os.path.join(APPR, "micromax", mod_folder, "doctype", doctype)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, doctype + ".json")
    with open(path, "w") as f:
        json.dump(data, f, indent=1)
    return path


# ============================================================
# ROLE helper list for standard permissions
def perms(*roles, extra=None):
    out = []
    for role in roles:
        p = {
            "amend": 1, "cancel": 1, "create": 1, "delete": 1, "email": 1,
            "export": 1, "print": 1, "read": 1, "report": 1, "role": role,
            "select": 1, "sharing": 1, "submit": 1, "write": 1,
        }
        if extra:
            p.update(extra)
        out.append(p)
    return out

# ============================================================
# LC PROFORMA ITEM (child)
LC_PROFORMA_ITEM_FIELDS = [
    F("item", "Item", "Link", options="Item", reqd=1, in_list_view=1),
    F("item_name", "Item Name", "Data", read_only=1, in_list_view=1),
    F("description", "Description", "Text"),
    column("column_break_0"),
    F("buyer_style_no", "Buyer Style No.", "Data"),
    F("style_no", "Style No.", "Data"),
    F("color", "Color", "Data", in_list_view=1),
    F("size", "Size", "Data", in_list_view=1),
    section("section_break_0", "Customs"),
    F("hs_code", "HS Code", "Data"),
    F("country_of_origin", "Country of Origin", "Link", options="Country"),
    column("column_break_1"),
    F("quantity", "Quantity", "Float", reqd=1, in_list_view=1),
    F("uom", "UOM", "Link", options="UOM"),
    F("rate", "Rate", "Currency", options="currency", in_list_view=1),
    F("amount", "Amount", "Currency", options="currency", read_only=1, in_list_view=1),
    F("net_weight", "Net Weight", "Float"),
    F("gross_weight", "Gross Weight", "Float"),
    F("cartons", "Cartons", "Int"),
]

LC_PROFORMA_ITEM = header(
    "LC Proforma Item", "MicroMax Export", [f["fieldname"] for f in LC_PROFORMA_ITEM_FIELDS],
    istable=True, quick_entry=True,
)
LC_PROFORMA_ITEM["fields"] = LC_PROFORMA_ITEM_FIELDS

# ============================================================
# LC PROFORMA (parent)
LC_PROFORMA_FIELDS = [
    F("proforma_no", "Proforma No.", "Data", read_only=1),
    F("proforma_date", "Proforma Date", "Date", default="Today", reqd=1, in_list_view=1),
    F("company", "Company", "Link", options="Company", reqd=1, in_list_view=1),
    section("section_break_identification", "Identification"),
    F("customer", "Customer / Buyer", "Link", options="Customer", reqd=1, in_standard_filter=1),
    F("buyer_po_no", "Buyer PO No.", "Data"),
    F("export_order", "Export Order", "Link", options="Sales Order", read_only=1),
    F("currency", "Currency", "Link", options="Currency", reqd=1, in_list_view=1),
    F("exchange_rate", "Exchange Rate", "Float", precision="6"),
    section("section_break_lc", "LC Information"),
    F("lc_required", "LC Required", "Check", default=0),
    F("lc_no", "LC No.", "Data"),
    F("lc_date", "LC Date", "Date"),
    F("lc_type", "LC Type", "Select", options="\nIrrevocable\nRevocable\nStandby\nConfirmed Irrevocable"),
    F("lc_issuing_bank", "LC Issuing Bank", "Data"),
    F("lc_advising_bank", "LC Advising Bank", "Data"),
    F("lc_confirming_bank", "LC Confirming Bank", "Data"),
    F("lc_amount", "LC Amount", "Currency", options="lc_currency"),
    F("lc_currency", "LC Currency", "Link", options="Currency"),
    F("lc_expiry_date", "LC Expiry Date", "Date"),
    F("lc_expiry_place", "LC Expiry Place", "Data"),
    F("latest_shipment_date", "Latest Shipment Date", "Date"),
    F("partial_shipment_allowed", "Partial Shipment Allowed", "Check", default=0),
    F("transshipment_allowed", "Transshipment Allowed", "Check", default=0),
    section("section_break_shipment", "Shipment Information"),
    F("port_of_loading", "Port of Loading", "Data"),
    F("port_of_discharge", "Port of Discharge", "Data"),
    F("final_destination", "Final Destination", "Data"),
    F("country_of_destination", "Country of Destination", "Link", options="Country"),
    F("shipment_mode", "Shipment Mode", "Select", options="\nSea\nAir\nRoad\nRail\nMultimodal"),
    F("incoterm", "Incoterm", "Select", options="EXW\nFCA\nFAS\nFOB\nCFR\nCIF\nCPT\nCIP\nDAP\nDPU\nDDP"),
    F("payment_terms", "Payment Terms", "Link", options="Payment Terms Template"),
    section("section_break_banking", "Banking"),
    F("beneficiary_bank", "Beneficiary Bank", "Data"),
    F("bank_account", "Bank Account", "Link", options="Bank Account"),
    F("swift_code", "Swift Code", "Data"),
    F("bank_branch", "Bank Branch", "Data"),
    F("correspondent_bank", "Correspondent Bank", "Data"),
    section("section_break_items", "Items"),
    F("lc_proforma_items", "LC Proforma Items", "Table", options="LC Proforma Item", reqd=1),
    section("section_break_totals", "Totals"),
    F("total_quantity", "Total Quantity", "Float", read_only=1),
    F("total_cartons", "Total Cartons", "Int", read_only=1),
    F("total_net_weight", "Total Net Weight", "Float", read_only=1),
    F("total_gross_weight", "Total Gross Weight", "Float", read_only=1),
    F("total_proforma_value", "Total Proforma Value", "Currency", options="currency", read_only=1),
    F("amended_from", "Amended From", "Link", options="LC Proforma", no_copy=1, print_hide=1, read_only=1),
    F("lc_status", "LC Status", "Data", read_only=1, in_list_view=1, in_standard_filter=1),
]

LC_PROFORMA = header(
    "LC Proforma", "MicroMax Export", [f["fieldname"] for f in LC_PROFORMA_FIELDS],
    is_submittable=True, title_field="proforma_no",
    actions=[{"action_name": "Create Sales Order", "create": 1, "group": "Create",
              "hidden": 0, "label": "Create Sales Order"}],
    links=[
        {"group": "Sales Order", "link_doctype": "Sales Order", "link_fieldname": "lc_proforma"},
        {"group": "Export Shipment", "link_doctype": "Export Shipment", "link_fieldname": "lc_proforma"},
    ],
    permissions=perms("System Manager", "Export Manager", "MicroMax Administrator"),
    autoname="format:LC-PROF-{YYYY}-{###}",
)
LC_PROFORMA["fields"] = LC_PROFORMA_FIELDS
LC_PROFORMA["permissions"].append({
    "email": 1, "export": 1, "print": 1, "read": 1, "report": 1, "role": "Commercial Manager",
})

DOCS = [LC_PROFORMA_ITEM, LC_PROFORMA]


def main():
    for doc in DOCS:
        print("wrote", write(doc["name"], doc))


if __name__ == "__main__":
    main()

# ============================================================
# IMPORT SHIPMENT
IMPORT_SHIPMENT_FIELDS = [
    F("shipment_no", "Shipment No.", "Data", read_only=1, in_list_view=1),
    F("shipment_date", "Shipment Date", "Date", default="Today"),
    section("section_break_links", "Links"),
    F("supplier", "Supplier", "Link", options="Supplier", reqd=1, in_standard_filter=1),
    F("purchase_order", "Purchase Order", "Link", options="Purchase Order"),
    F("purchase_receipt", "Purchase Receipt", "Link", options="Purchase Receipt"),
    F("import_cost_sheet", "Import Cost Sheet", "Link", options="Import Cost Sheet", read_only=1),
    section("section_break_vessel", "Vessel / Container"),
    F("bill_of_lading", "Bill of Lading", "Data"),
    F("container_no", "Container No.", "Data"),
    F("shipping_line", "Shipping Line", "Data"),
    F("vessel", "Vessel", "Data"),
    F("port_of_loading", "Port of Loading", "Data"),
    F("port_of_discharge", "Port of Discharge", "Data"),
    F("etd", "ETD", "Datetime"),
    F("eta", "ETA", "Datetime"),
    F("actual_arrival", "Actual Arrival", "Date"),
    section("section_break_clearing", "Clearing & Customs"),
    F("clearing_agent", "Clearing Agent", "Link", options="Supplier"),
    F("customs_declaration_no", "Customs Declaration No.", "Data"),
    F("duty_amount", "Duty Amount", "Currency"),
    F("tax_amount", "Tax Amount", "Currency"),
    F("clearance_date", "Clearance Date", "Date"),
    section("section_break_status", "Status"),
    F("shipment_status", "Shipment Status", "Select", in_list_view=1,
      options="\nPlanned\nBooking\nStuffing\nShipped\nIn Transit\nArrived\nDelivered\nClosed"),
]

IMPORT_SHIPMENT = header(
    "Import Shipment", "MicroMax Import", [f["fieldname"] for f in IMPORT_SHIPMENT_FIELDS],
    title_field="shipment_no", autoname="format:IMPSHIP-{YYYY}-{###}",
    permissions=perms("System Manager", "Import Manager", "MicroMax Administrator"),
)
IMPORT_SHIPMENT["fields"] = IMPORT_SHIPMENT_FIELDS

# ============================================================
# IMPORT COST SHEET ITEM (child)
IMPORT_COST_ITEM_FIELDS = [
    F("item", "Item", "Link", options="Item", reqd=1, in_list_view=1),
    F("item_name", "Item Name", "Data", read_only=1, in_list_view=1),
    F("quantity", "Quantity", "Float"),
    F("uom", "UOM", "Link", options="UOM"),
    column("column_break_0"),
    F("purchase_value", "Purchase Value", "Currency"),
    F("freight", "Freight", "Currency"),
    F("insurance", "Insurance", "Currency"),
    F("customs_duty", "Customs Duty", "Currency"),
    column("column_break_1"),
    F("additional_duty", "Additional Duty", "Currency"),
    F("sales_tax", "Sales Tax", "Currency"),
    F("regulatory_duty", "Regulatory Duty", "Currency"),
    F("clearing_charges", "Clearing Charges", "Currency"),
    F("port_charges", "Port Charges", "Currency"),
    F("other_charges", "Other Charges", "Currency"),
    section("section_break_totals", "Totals"),
    F("total_landed_cost", "Total Landed Cost", "Currency", read_only=1, in_list_view=1),
    F("landed_cost_per_unit", "Landed Cost Per Unit", "Currency", read_only=1, in_list_view=1),
]

IMPORT_COST_ITEM = header(
    "Import Cost Sheet Item", "MicroMax Import",
    [f["fieldname"] for f in IMPORT_COST_ITEM_FIELDS], istable=True, quick_entry=True,
)
IMPORT_COST_ITEM["fields"] = IMPORT_COST_ITEM_FIELDS


# ============================================================
# IMPORT COST SHEET
IMPORT_COST_FIELDS = [
    F("cost_sheet_date", "Cost Sheet Date", "Date", default="Today", in_list_view=1),
    F("company", "Company", "Link", options="Company", reqd=1),
    section("section_break_links", "Links"),
    F("supplier", "Supplier", "Link", options="Supplier", reqd=1, in_standard_filter=1),
    F("purchase_order", "Purchase Order", "Link", options="Purchase Order"),
    F("import_shipment", "Import Shipment", "Link", options="Import Shipment"),
    F("purchase_receipt", "Purchase Receipt", "Link", options="Purchase Receipt"),
    column("column_break_0"),
    F("currency", "Currency", "Link", options="Currency", reqd=1, default="USD"),
    section("section_break_items", "Items"),
    F("import_cost_sheet_items", "Import Cost Sheet Items", "Table",
      options="Import Cost Sheet Item", reqd=1),
    section("section_break_summary", "Summary"),
    F("total_purchase_value", "Total Purchase Value", "Currency", options="currency", read_only=1),
    F("total_landed_cost", "Total Landed Cost", "Currency", options="currency", read_only=1, in_list_view=1),
]

IMPORT_COST_SHEET = header(
    "Import Cost Sheet", "MicroMax Import", [f["fieldname"] for f in IMPORT_COST_FIELDS],
    title_field="name", autoname="format:IMPCOS-{YYYY}-{###}",
    permissions=perms("System Manager", "Import Manager", "MicroMax Administrator"),
)
IMPORT_COST_SHEET["fields"] = IMPORT_COST_FIELDS

# ============================================================
# EXPORT SHIPMENT
EXPORT_SHIPMENT_FIELDS = [
    F("shipment_no", "Shipment No.", "Data", read_only=1, in_list_view=1),
    F("shipment_date", "Shipment Date", "Date", default="Today"),
    section("section_break_links", "Links"),
    F("customer", "Customer / Buyer", "Link", options="Customer", reqd=1, in_standard_filter=1),
    F("sales_order", "Sales Order", "Link", options="Sales Order"),
    F("lc_proforma", "LC Proforma", "Link", options="LC Proforma"),
    F("lc_no", "LC No.", "Data"),
    F("commercial_invoice_no", "Commercial Invoice No.", "Data"),
    F("packing_list_no", "Packing List No.", "Data"),
    section("section_break_vessel", "Vessel / Container"),
    F("bill_of_lading_no", "Bill of Lading No.", "Data"),
    F("container_no", "Container No.", "Data"),
    F("shipping_line", "Shipping Line", "Data"),
    F("vessel", "Vessel", "Data"),
    F("port_of_loading", "Port of Loading", "Data"),
    F("port_of_discharge", "Port of Discharge", "Data"),
    F("final_destination", "Final Destination", "Data"),
    F("etd", "ETD", "Datetime"),
    F("eta", "ETA", "Datetime"),
    F("actual_shipment_date", "Actual Shipment Date", "Date"),
    section("section_break_status", "Status"),
    F("shipment_status", "Shipment Status", "Select", in_list_view=1, in_standard_filter=1,
      options="\nPlanned\nBooking\nStuffing\nShipped\nIn Transit\nArrived\nDelivered\nClosed"),
]

EXPORT_SHIPMENT = header(
    "Export Shipment", "MicroMax Export", [f["fieldname"] for f in EXPORT_SHIPMENT_FIELDS],
    title_field="shipment_no", autoname="format:EXPSHIP-{YYYY}-{###}",
    permissions=perms("System Manager", "Export Manager", "MicroMax Administrator"),
)
EXPORT_SHIPMENT["fields"] = EXPORT_SHIPMENT_FIELDS


# ============================================================
# EXPORT PACKING DETAILS ITEM (child)
PACKING_ITEM_FIELDS = [
    F("carton_no", "Carton No.", "Data", in_list_view=1),
    F("item", "Item", "Link", options="Item", in_list_view=1),
    F("style", "Style", "Data"),
    F("color", "Color", "Data", in_list_view=1),
    F("size", "Size", "Data", in_list_view=1),
    F("quantity", "Quantity", "Float", in_list_view=1),
    F("pieces_per_carton", "Pieces Per Carton", "Int"),
    F("net_weight", "Net Weight", "Float"),
    F("gross_weight", "Gross Weight", "Float"),
    F("dimensions", "Dimensions", "Data"),
    F("volume_cbm", "Volume/CBM", "Float"),
]

PACKING_ITEM = header(
    "Export Packing Details Item", "MicroMax Export",
    [f["fieldname"] for f in PACKING_ITEM_FIELDS], istable=True, quick_entry=True,
)
PACKING_ITEM["fields"] = PACKING_ITEM_FIELDS


# ============================================================
# EXPORT PACKING DETAILS
PACKING_FIELDS = [
    F("packing_no", "Packing No.", "Data", read_only=1, in_list_view=1),
    F("packing_date", "Packing Date", "Date", default="Today"),
    section("section_break_links", "Links"),
    F("sales_order", "Sales Order", "Link", options="Sales Order", reqd=1),
    F("export_shipment", "Export Shipment", "Link", options="Export Shipment"),
    F("customer", "Customer / Buyer", "Link", options="Customer"),
    F("lc_proforma", "LC Proforma", "Link", options="LC Proforma"),
    section("section_break_items", "Items"),
    F("export_packing_details", "Export Packing Details", "Table",
      options="Export Packing Details Item", reqd=1),
    section("section_break_totals", "Totals"),
    F("total_cartons", "Total Cartons", "Int", read_only=1),
    F("total_pieces", "Total Pieces", "Float", read_only=1),
    F("total_net_weight", "Total Net Weight", "Float", read_only=1),
    F("total_gross_weight", "Total Gross Weight", "Float", read_only=1),
    F("total_cbm", "Total CBM", "Float", read_only=1),
]

PACKING = header(
    "Export Packing Details", "MicroMax Export", [f["fieldname"] for f in PACKING_FIELDS],
    title_field="packing_no", autoname="format:EXPPAC-{YYYY}-{###}",
    permissions=perms("System Manager", "Export Manager", "MicroMax Administrator"),
)
PACKING["fields"] = PACKING_FIELDS

DOCS += [
    IMPORT_SHIPMENT,
    IMPORT_COST_ITEM,
    IMPORT_COST_SHEET,
    EXPORT_SHIPMENT,
    PACKING_ITEM,
    PACKING,
]

main()