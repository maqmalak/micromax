import frappe
from frappe import _


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    conditions = []
    params = {}

    if filters.get("from_date"):
        conditions.append("l.proforma_date >= %(from_date)s")
        params["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("l.proforma_date <= %(to_date)s")
        params["to_date"] = filters.to_date
    if filters.get("buyer"):
        conditions.append("l.customer = %(buyer)s")
        params["buyer"] = filters.buyer
    if filters.get("company"):
        conditions.append("l.company = %(company)s")
        params["company"] = filters.company
    if filters.get("lc_status"):
        conditions.append("l.lc_status = %(lc_status)s")
        params["lc_status"] = filters.lc_status
    if filters.get("lc_expiry_date"):
        conditions.append("l.lc_expiry_date = %(lc_expiry_date)s")
        params["lc_expiry_date"] = filters.lc_expiry_date
    if filters.get("export_order"):
        conditions.append("l.export_order = %(export_order)s")
        params["export_order"] = filters.export_order

    where = " AND ".join(conditions) if conditions else "1=1"

    data = frappe.db.sql(
        f"""
        SELECT
            l.name as proforma_no,
            l.proforma_date as date,
            l.customer as buyer,
            l.buyer_po_no as buyer_po,
            l.export_order as export_order,
            l.lc_no as lc_no,
            l.lc_date as lc_date,
            l.lc_amount as lc_amount,
            l.lc_currency as currency,
            l.lc_issuing_bank as issuing_bank,
            l.lc_expiry_date as lc_expiry_date,
            l.latest_shipment_date as latest_shipment_date,
            l.port_of_loading as port_of_loading,
            l.port_of_discharge as port_of_discharge,
            l.incoterm as incoterm,
            COALESCE(l.lc_status, l.workflow_state, 'Draft') as status
        FROM `tabLC Proforma` l
        WHERE {where}
        ORDER BY l.proforma_date DESC, l.name ASC
        """,
        params,
        as_dict=True,
        debug=False,
    )
    return columns, data


def get_columns():
    return [
        {"label": _("Proforma No."), "fieldname": "proforma_no", "fieldtype": "Link", "options": "LC Proforma", "width": 140},
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 90},
        {"label": _("Buyer"), "fieldname": "buyer", "fieldtype": "Link", "options": "Customer", "width": 160},
        {"label": _("Buyer PO"), "fieldname": "buyer_po", "fieldtype": "Data", "width": 110},
        {"label": _("Export Order"), "fieldname": "export_order", "fieldtype": "Link", "options": "Sales Order", "width": 140},
        {"label": _("LC No."), "fieldname": "lc_no", "fieldtype": "Data", "width": 110},
        {"label": _("LC Date"), "fieldname": "lc_date", "fieldtype": "Date", "width": 90},
        {"label": _("LC Amount"), "fieldname": "lc_amount", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 90},
        {"label": _("Issuing Bank"), "fieldname": "issuing_bank", "fieldtype": "Data", "width": 150},
        {"label": _("LC Expiry Date"), "fieldname": "lc_expiry_date", "fieldtype": "Date", "width": 110},
        {"label": _("Latest Shipment Date"), "fieldname": "latest_shipment_date", "fieldtype": "Date", "width": 140},
        {"label": _("Port of Loading"), "fieldname": "port_of_loading", "fieldtype": "Data", "width": 130},
        {"label": _("Port of Discharge"), "fieldname": "port_of_discharge", "fieldtype": "Data", "width": 130},
        {"label": _("Incoterm"), "fieldname": "incoterm", "fieldtype": "Data", "width": 90},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
    ]