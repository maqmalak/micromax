"""At-a-glance panels for Sales Order, Delivery Note and Sales Invoice, and a generic voucher ledger (GL + stock).

Shape (rendered by the React InsightsPanel):
	{"tiles": [{label, value, fmt, sub, tone}], "bars": [{label, pct, sub, tone}], "lists": [{title, rows: [{label, value, fmt, sub, link?}]}]}"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate


def _tile(label, value, fmt="money", sub=None, tone="primary"):
	return {"label": label, "value": value if isinstance(value, str) or value is None else flt(value, 2), "fmt": fmt, "sub": sub, "tone": tone}


def _bar(label, pct, sub=None, tone="primary"):
	return {"label": label, "pct": round(min(max(flt(pct), 0), 100), 1), "sub": sub, "tone": tone}


def _row(label, value, fmt="money", sub=None, link=None):
	return {"label": label, "value": flt(value, 2), "fmt": fmt, "sub": sub, "link": link}


def _valuation(items):
	"""Cost estimate: stock qty × the item's current average valuation (bins, else the item record)."""
	codes = list({i.item_code for i in items if i.item_code})
	if not codes:
		return {}
	rates = {r.item_code: flt(r.v) for r in frappe.db.sql("""select item_code, sum(stock_value) / nullif(sum(actual_qty), 0) v from `tabBin`
		where item_code in %(c)s and actual_qty > 0 group by item_code""", {"c": codes}, as_dict=True)}
	for c in codes:
		rates[c] = rates.get(c) or flt(frappe.db.get_value("Item", c, "valuation_rate"))
	return rates


def _margin_tile(revenue, cost, note):
	if not revenue:
		return _tile(_("Gross margin"), None, "percent", note)
	m = (revenue - cost) / revenue * 100
	return _tile(_("Gross margin"), round(m, 1), "percent", f"{frappe.format(revenue - cost, 'Currency')} · {note}", "emerald" if m >= 10 else "amber" if m >= 0 else "rose")


def _customer_outstanding(customer, company):
	return flt(frappe.db.sql("""select sum(outstanding_amount) from `tabSales Invoice` where customer = %s and company = %s and docstatus = 1""", (customer, company))[0][0])


@frappe.whitelist()
def get_selling_insights(doctype, name):
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	fn = {"Sales Order": _so, "Delivery Note": _dn, "Sales Invoice": _si}.get(doctype)
	if not fn:
		frappe.throw(_("No insights for {0}").format(doctype))
	return fn(doc)


def _so(doc):
	today = getdate(nowdate())
	rates = _valuation(doc.items)
	cost = sum(flt(i.stock_qty) * rates.get(i.item_code, 0) for i in doc.items)
	left = date_diff(doc.delivery_date, today) if doc.delivery_date else None
	pending = flt(doc.per_delivered) < 100 and doc.docstatus == 1 and doc.status not in ("Closed", "Completed")
	due = (_("{0} days late").format(-left) if left is not None and left < 0 else _("in {0} days").format(left)) if left is not None else "—"
	items = sorted(doc.items, key=lambda i: -flt(i.base_net_amount))[:8]
	return {
		"tiles": [
			_tile(_("Order value"), doc.base_grand_total, "money", _("{0} lines · {1} qty").format(len(doc.items), frappe.format(flt(doc.total_qty), "Float")), "sky"),
			_margin_tile(flt(doc.base_net_total), cost, _("at current valuation")),
			_tile(_("Delivery"), due if pending else _("Done") if flt(doc.per_delivered) >= 100 else "—", "text", str(doc.delivery_date or ""),
			      "rose" if pending and left is not None and left < 0 else "amber" if pending and left is not None and left <= 7 else "emerald"),
			_tile(_("Advance received"), doc.advance_paid, "money", _("customer owes {0} overall").format(frappe.format(_customer_outstanding(doc.customer, doc.company), "Currency")), "violet"),
		],
		"bars": [_bar(_("Delivered"), doc.per_delivered, None, "sky"), _bar(_("Billed"), doc.per_billed, None, "emerald"),
		         *([_bar(_("Picked"), doc.per_picked, None, "violet")] if flt(doc.get("per_picked")) else [])],
		"lists": [{"title": _("Line progress (delivered / ordered)"), "rows": [
			_row(i.item_name or i.item_code, flt(i.delivered_qty) / flt(i.qty) * 100 if flt(i.qty) else 0, "percent",
			     f"{frappe.format(flt(i.delivered_qty), 'Float')} / {frappe.format(flt(i.qty), 'Float')} {i.uom or ''}") for i in items]}],
	}


def _stock_cost(voucher_type, voucher_no):
	return -flt(frappe.db.sql("""select sum(stock_value_difference) from `tabStock Ledger Entry` where voucher_type = %s and voucher_no = %s and is_cancelled = 0""",
	                          (voucher_type, voucher_no))[0][0])


def _dn(doc):
	cost = _stock_cost("Delivery Note", doc.name) if doc.docstatus == 1 else sum(flt(i.stock_qty) * r for i, r in ((i, _valuation(doc.items).get(i.item_code, 0)) for i in doc.items))
	sos = sorted({i.against_sales_order for i in doc.items if i.against_sales_order})
	lead = None
	if sos:
		d = frappe.db.sql("select min(transaction_date) from `tabSales Order` where name in %(s)s", {"s": sos})[0][0]
		lead = date_diff(doc.posting_date, d) if d else None
	return {
		"tiles": [
			_tile(_("Delivered value"), doc.base_grand_total, "money", _("{0} qty").format(frappe.format(flt(doc.total_qty), "Float")), "sky"),
			_tile(_("Cost of goods"), cost, "money", _("from the stock ledger") if doc.docstatus == 1 else _("estimated at valuation"), "violet"),
			_margin_tile(flt(doc.base_net_total), cost, _("on this delivery")),
			_tile(_("Order to dispatch"), lead, "days", _("from {0}").format(", ".join(sos[:2])) if sos else _("not against an order"), "amber" if lead and lead > 14 else "emerald"),
		],
		"bars": [_bar(_("Billed"), doc.per_billed, None, "emerald"), *([_bar(_("Returned"), doc.per_returned, None, "rose")] if flt(doc.get("per_returned")) else [])],
		"lists": [{"title": _("Dispatched from"), "rows": [
			_row(w, sum(flt(i.stock_qty) for i in doc.items if i.warehouse == w), "number", _("{0} lines").format(sum(1 for i in doc.items if i.warehouse == w)))
			for w in sorted({i.warehouse for i in doc.items if i.warehouse})]},
			{"title": _("Transport"), "rows": [_row(x, 0, "text", y) for x, y in ((_("Transporter"), doc.get("transporter_name")), (_("Vehicle"), doc.get("vehicle_no")),
			                                                                       (_("LR / Bilty"), doc.get("lr_no")), (_("Driver"), doc.get("driver_name"))) if y]}],
	}


def _si(doc):
	today = getdate(nowdate())
	pays = frappe.db.sql("""select p.name, p.posting_date, r.allocated_amount, p.mode_of_payment from `tabPayment Entry Reference` r join `tabPayment Entry` p on p.name = r.parent
		where r.reference_doctype = 'Sales Invoice' and r.reference_name = %s and p.docstatus = 1 order by p.posting_date""", doc.name, as_dict=True)
	paid = flt(doc.grand_total) - flt(doc.outstanding_amount)
	left = date_diff(doc.due_date, today) if doc.due_date else None
	cost = _stock_cost("Sales Invoice", doc.name) if doc.update_stock and doc.docstatus == 1 else 0
	if not cost:
		dns = [i.delivery_note for i in doc.items if i.get("delivery_note")]
		cost = sum(_stock_cost("Delivery Note", d) for d in set(dns)) if dns and len(set(dns)) == 1 and len(doc.items) else 0
		if not cost:
			rates = _valuation(doc.items)
			cost = sum(flt(i.stock_qty) * rates.get(i.item_code, 0) for i in doc.items)
	days_to_pay = date_diff(pays[-1].posting_date, doc.posting_date) if pays and flt(doc.outstanding_amount) <= 0.01 else None
	status_due = (_("Paid") if flt(doc.outstanding_amount) <= 0.01 else _("{0} days overdue").format(-left) if left is not None and left < 0 else _("due in {0} days").format(left) if left is not None else "—")
	return {
		"tiles": [
			_tile(_("Invoice total"), doc.grand_total, "money", _("net {0} · tax {1}").format(frappe.format(flt(doc.net_total), "Currency"), frappe.format(flt(doc.total_taxes_and_charges), "Currency")), "sky"),
			_tile(_("Outstanding"), doc.outstanding_amount, "money", status_due, "emerald" if flt(doc.outstanding_amount) <= 0.01 else "rose" if left is not None and left < 0 else "amber"),
			_margin_tile(flt(doc.base_net_total), cost, _("vs cost of goods")),
			_tile(_("Days to collect"), days_to_pay, "days", _("{0} payments").format(len(pays)) if pays else _("no payments yet"), "emerald" if days_to_pay is not None and days_to_pay <= 30 else "amber"),
		],
		"bars": [_bar(_("Paid"), paid / flt(doc.grand_total) * 100 if flt(doc.grand_total) else 0, frappe.format(paid, "Currency"), "emerald")],
		"lists": [{"title": _("Payments received"), "rows": [_row(p.name, p.allocated_amount, "money", f"{p.posting_date} · {p.mode_of_payment or ''}", {"doctype": "Payment Entry", "name": p.name}) for p in pays]}],
	}


@frappe.whitelist()
def get_voucher_ledgers(voucher_type, voucher_no):
	"""GL and stock ledger entries a submitted voucher posted (for any form's Ledger tab)."""
	frappe.get_doc(voucher_type, voucher_no).check_permission("read")
	gl = frappe.db.sql("""select account, party, debit, credit, against_voucher_type, against_voucher, cost_center from `tabGL Entry`
		where voucher_type = %s and voucher_no = %s and is_cancelled = 0 order by debit desc, credit desc""", (voucher_type, voucher_no), as_dict=True)
	sle = frappe.db.sql("""select item_code, warehouse, actual_qty, valuation_rate, stock_value_difference, qty_after_transaction, stock_uom
		from `tabStock Ledger Entry` where voucher_type = %s and voucher_no = %s and is_cancelled = 0 order by item_code""", (voucher_type, voucher_no), as_dict=True)
	return {"gl": gl, "sle": sle, "debit": sum(flt(g.debit) for g in gl), "credit": sum(flt(g.credit) for g in gl), "stock_value": sum(flt(s.stock_value_difference) for s in sle)}
