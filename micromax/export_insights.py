"""LC Proforma "at a glance": LC cover, execution against the export order, shipments and the deadlines."""

import frappe
from frappe.utils import date_diff, flt, getdate, nowdate


@frappe.whitelist()
def get_lc_insights(name):
	doc = frappe.get_doc("LC Proforma", name)
	doc.check_permission("read")
	today = getdate(nowdate())
	so = frappe.db.get_value("Sales Order", doc.export_order, ["name", "status", "grand_total", "currency", "per_delivered", "per_billed", "transaction_date"],
	                         as_dict=True) if doc.export_order else None
	inv = frappe.db.sql("""select count(distinct si.name) n, sum(si.grand_total) v, sum(si.outstanding_amount) o from `tabSales Invoice` si
		where si.docstatus = 1 and si.name in (select parent from `tabSales Invoice Item` where sales_order = %s)""", so.name if so else "", as_dict=True)[0]
	ships = frappe.db.sql("""select name, shipment_no, shipment_date, actual_shipment_date, shipment_status, vessel, bill_of_lading_no, container_no
		from `tabExport Shipment` where lc_proforma = %s order by shipment_date""", name, as_dict=True)
	proforma = flt(doc.total_proforma_value)
	lc = flt(doc.lc_amount)
	days = lambda d: date_diff(getdate(d), today) if d else None  # noqa: E731
	return {
		"proforma_value": proforma,
		"lc_amount": lc,
		"lc_cover": round(lc / proforma * 100, 1) if proforma and lc else None,
		"currency": doc.lc_currency or doc.currency,
		"days_to_shipment": days(doc.latest_shipment_date),
		"days_to_expiry": days(doc.lc_expiry_date),
		"order": so,
		"invoiced": flt(inv.v), "invoices": int(inv.n or 0), "outstanding": flt(inv.o),
		"shipments": [{**s, "shipment_date": str(s.shipment_date or ""), "actual_shipment_date": str(s.actual_shipment_date or "")} for s in ships],
		"shipped": sum(1 for s in ships if s.actual_shipment_date),
	}


@frappe.whitelist()
def get_shipment_insights(name):
	"""Export Shipment panel: packing (cartons, weights, CBM, container fill), the order's progress, LC and timing."""
	s = frappe.get_doc("Export Shipment", name)
	s.check_permission("read")
	pk = frappe.db.sql("""select name, total_cartons, total_pieces, total_net_weight, total_gross_weight, total_cbm from `tabExport Packing Details`
		where export_shipment = %s or name = %s""", (name, s.packing_list_no or ""), as_dict=True)
	cbm = sum(flt(p.total_cbm) for p in pk)
	so = frappe.db.get_value("Sales Order", s.sales_order, ["name", "status", "grand_total", "currency", "per_delivered", "per_billed", "delivery_date"], as_dict=True) if s.sales_order else None
	lc = frappe.db.get_value("LC Proforma", s.lc_proforma, ["name", "lc_no", "lc_amount", "lc_currency", "lc_expiry_date", "latest_shipment_date", "workflow_state"], as_dict=True) if s.lc_proforma else None
	today = getdate(nowdate())
	etd, eta = (getdate(s.etd) if s.etd else None), (getdate(s.eta) if s.eta else None)
	return {
		"packing": {"lists": [p.name for p in pk], "cartons": sum(int(p.total_cartons or 0) for p in pk), "pieces": sum(flt(p.total_pieces) for p in pk),
		            "net": sum(flt(p.total_net_weight) for p in pk), "gross": sum(flt(p.total_gross_weight) for p in pk), "cbm": cbm,
		            "containers_20": round(cbm / 33, 2), "containers_40": round(cbm / 67, 2)},
		"order": so, "lc": lc,
		"days_to_etd": (etd - today).days if etd else None, "days_to_eta": (eta - today).days if eta else None,
		"transit_days": (eta - etd).days if etd and eta else None,
		"late_vs_lc": date_diff(s.actual_shipment_date or s.shipment_date, lc.latest_shipment_date) if lc and lc.latest_shipment_date and (s.actual_shipment_date or s.shipment_date) else None,
	}
