"""At-a-glance figures for master records (Item, Customer, Supplier and their groups) on the React form.

Every doctype returns the same shape so one panel renders them all:
	{"period": {...}, "tiles": [{label, value, fmt, sub, tone}], "trend": {"title", "series": [{key, label}], "data": [...]},
	 "lists": [{"title", "rows": [{label, value, fmt, sub}]}]}
Figures cover the current Jul–Jun fiscal year unless noted."""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_years, flt, getdate, nowdate


def _fy():
	t = getdate(nowdate())
	start = getdate(f"{t.year if t.month >= 7 else t.year - 1}-07-01")
	return start, add_years(start, 1) - timedelta(days=1), t


def _months(f):
	out, d = [], f
	for _i in range(12):
		out.append(d.strftime("%Y-%m"))
		d = add_years(d, 0).replace(day=1)
		d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
	return out


def _label(m):
	return getdate(f"{m}-01").strftime("%b %y")


def _tile(label, value, fmt="number", sub=None, tone="primary"):
	return {"label": label, "value": flt(value, 2) if isinstance(value, (int, float)) or value is None else value, "fmt": fmt, "sub": sub, "tone": tone}


def _row(label, value, fmt="number", sub=None):
	return {"label": label, "value": flt(value, 2), "fmt": fmt, "sub": sub}


def _trend(title, series, monthly, months):
	return {"title": title, "series": series, "data": [{"month": _label(m), **{s["key"]: flt((monthly.get(m) or {}).get(s["key"]), 2) for s in series}} for m in months]}


@frappe.whitelist()
def get_master_insights(doctype, name):
	frappe.get_doc(doctype, name).check_permission("read")
	fn = {"Item": _item, "Customer": _customer, "Supplier": _supplier, "Item Group": _item_group,
	      "Customer Group": _customer_group, "Supplier Group": _supplier_group}.get(doctype)
	if not fn:
		frappe.throw(_("No insights for {0}").format(doctype))
	f, t, today = _fy()
	out = fn(name, {"f": f, "t": t, "today": today, "n": name}, _months(f))
	out["period"] = {"from": str(f), "to": str(t)}
	return out


# ----------------------------------------------------------------------------- Item
def _item(name, a, months):
	sql = frappe.db.sql
	bins = sql("""select warehouse, actual_qty, projected_qty, stock_value, reserved_qty, ordered_qty from `tabBin`
		where item_code = %(n)s and (actual_qty != 0 or projected_qty != 0) order by stock_value desc""", a, as_dict=True)
	uom = frappe.db.get_value("Item", name, "stock_uom") or ""
	pur = sql("""select sum(i.stock_qty) q, sum(i.base_net_amount) v, count(distinct p.name) n from `tabPurchase Invoice Item` i join `tabPurchase Invoice` p on p.name = i.parent
		where i.item_code = %(n)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s""", a, as_dict=True)[0]
	sal = sql("""select sum(i.stock_qty) q, sum(i.base_net_amount) v, count(distinct p.customer) c from `tabSales Invoice Item` i join `tabSales Invoice` p on p.name = i.parent
		where i.item_code = %(n)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s""", a, as_dict=True)[0]
	cons = sql("""select sum(d.transfer_qty) q from `tabStock Entry Detail` d join `tabStock Entry` s on s.name = d.parent
		where d.item_code = %(n)s and s.docstatus = 1 and ifnull(d.s_warehouse, '') != '' and ifnull(d.t_warehouse, '') = ''
		  and s.purpose in ('Manufacture', 'Material Issue', 'Material Consumption for Manufacture') and s.posting_date between %(f)s and %(t)s""", a, as_dict=True)[0]
	made = sql("""select sum(d.transfer_qty) q from `tabStock Entry Detail` d join `tabStock Entry` s on s.name = d.parent
		where d.item_code = %(n)s and s.docstatus = 1 and s.purpose = 'Manufacture' and d.is_finished_item = 1 and s.posting_date between %(f)s and %(t)s""", a, as_dict=True)[0]
	last = sql("""select p.supplier, p.posting_date, i.base_net_rate rate from `tabPurchase Receipt Item` i join `tabPurchase Receipt` p on p.name = i.parent
		where i.item_code = %(n)s and p.docstatus = 1 order by p.posting_date desc limit 1""", a, as_dict=True)
	open_po = sql("""select sum(greatest(i.stock_qty - i.received_qty * i.conversion_factor, 0)) q from `tabPurchase Order Item` i join `tabPurchase Order` p on p.name = i.parent
		where i.item_code = %(n)s and p.docstatus = 1 and p.status not in ('Closed', 'Completed')""", a, as_dict=True)[0]
	open_so = sql("""select sum(greatest(i.stock_qty - i.delivered_qty * i.conversion_factor, 0)) q from `tabSales Order Item` i join `tabSales Order` p on p.name = i.parent
		where i.item_code = %(n)s and p.docstatus = 1 and p.status not in ('Closed', 'Completed')""", a, as_dict=True)[0]
	boms = sql("select count(distinct parent) from `tabBOM Item` where item_code = %(n)s and parenttype = 'BOM'", a)[0][0]
	on_hand = sum(flt(b.actual_qty) for b in bins)
	value = sum(flt(b.stock_value) for b in bins)
	use = flt(cons.q) + flt(sal.q)
	cover = on_hand / (use / 365) if use else None
	monthly = {}
	for key, q in (("purchased", """select date_format(p.posting_date, '%%Y-%%m') m, sum(i.stock_qty) v from `tabPurchase Invoice Item` i join `tabPurchase Invoice` p on p.name = i.parent
			where i.item_code = %(n)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s group by m"""),
	               ("sold", """select date_format(p.posting_date, '%%Y-%%m') m, sum(i.stock_qty) v from `tabSales Invoice Item` i join `tabSales Invoice` p on p.name = i.parent
			where i.item_code = %(n)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s group by m"""),
	               ("consumed", """select date_format(s.posting_date, '%%Y-%%m') m, sum(d.transfer_qty) v from `tabStock Entry Detail` d join `tabStock Entry` s on s.name = d.parent
			where d.item_code = %(n)s and s.docstatus = 1 and ifnull(d.s_warehouse, '') != '' and ifnull(d.t_warehouse, '') = ''
			  and s.purpose in ('Manufacture', 'Material Issue', 'Material Consumption for Manufacture') and s.posting_date between %(f)s and %(t)s group by m""")):
		for r in sql(q, a, as_dict=True):
			monthly.setdefault(r.m, {})[key] = flt(r.v)
	prices = sql("""select price_list, price_list_rate, currency from `tabItem Price` where item_code = %(n)s order by selling, price_list limit 6""", a, as_dict=True)
	return {
		"tiles": [
			_tile(_("On hand"), on_hand, "number", f"{uom} in {len([b for b in bins if flt(b.actual_qty)])} warehouses", "sky"),
			_tile(_("Stock value"), value, "money", _("at valuation rate"), "emerald"),
			_tile(_("Stock cover"), round(cover) if cover is not None else None, "days", _("days at this year's usage"), "amber" if cover is not None and cover < 30 else "primary"),
			_tile(_("Purchased (FY)"), pur.q, "number", f"{frappe.format(flt(pur.v), 'Currency')} · {int(pur.n or 0)} invoices", "violet"),
			_tile(_("Sold (FY)"), sal.q, "number", f"{frappe.format(flt(sal.v), 'Currency')} · {int(sal.c or 0)} customers", "emerald"),
			_tile(_("Consumed (FY)"), cons.q, "number", _("issued to production"), "rose"),
			_tile(_("Produced (FY)"), made.q, "number", _("finished-goods output"), "sky"),
			_tile(_("Open orders"), flt(open_po.q), "number", f"to receive · {frappe.format(flt(open_so.q), 'Float')} to deliver", "amber"),
		],
		"trend": _trend(_("Purchased, sold and consumed by month"), [{"key": "purchased", "label": _("Purchased")}, {"key": "sold", "label": _("Sold")},
		                                                            {"key": "consumed", "label": _("Consumed")}], monthly, months),
		"lists": [
			{"title": _("Stock by warehouse"), "rows": [_row(b.warehouse, b.actual_qty, "number", frappe.format(flt(b.stock_value), "Currency")) for b in bins[:8]]},
			{"title": _("Prices"), "rows": [_row(p.price_list, p.price_list_rate, "money", p.currency) for p in prices]
			 + ([_row(_("Last purchase · {0}").format(last[0].supplier), last[0].rate, "money", str(last[0].posting_date))] if last else [])},
			{"title": _("Where it's used"), "rows": [_row(_("BOMs using this item"), boms, "number"),
			                                          _row(_("Average purchase rate (FY)"), flt(pur.v) / flt(pur.q) if flt(pur.q) else 0, "money"),
			                                          _row(_("Average selling rate (FY)"), flt(sal.v) / flt(sal.q) if flt(sal.q) else 0, "money")]},
		],
	}


# ----------------------------------------------------------------------------- Customer / Supplier
def _party(name, a, months, *, kind):
	sql = frappe.db.sql
	inv, order, party, ref = ("Sales Invoice", "Sales Order", "customer", "transaction_date") if kind == "c" else ("Purchase Invoice", "Purchase Order", "supplier", "transaction_date")
	tot = sql(f"""select sum(base_net_total) v, count(*) n, max(posting_date) last from `tab{inv}`
		where {party} = %(n)s and docstatus = 1 and is_return = 0 and posting_date between %(f)s and %(t)s""", a, as_dict=True)[0]
	outst = sql(f"""select sum(outstanding_amount) o, sum(if(due_date < %(today)s, outstanding_amount, 0)) od, count(*) n from `tab{inv}`
		where {party} = %(n)s and docstatus = 1 and outstanding_amount > 0""", a, as_dict=True)[0]
	open_o = sql(f"""select sum(base_grand_total * (100 - per_billed) / 100) v, count(*) n from `tab{order}`
		where {party} = %(n)s and docstatus = 1 and status not in ('Closed', 'Completed', 'On Hold')""", a, as_dict=True)[0]
	items = sql(f"""select i.item_name label, sum(i.base_net_amount) v, sum(i.stock_qty) q from `tab{inv} Item` i join `tab{inv}` p on p.name = i.parent
		where p.{party} = %(n)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s group by i.item_code order by v desc limit 6""", a, as_dict=True)
	monthly = {r.m: {"value": flt(r.v)} for r in sql(f"""select date_format(posting_date, '%%Y-%%m') m, sum(base_net_total) v from `tab{inv}`
		where {party} = %(n)s and docstatus = 1 and posting_date between %(f)s and %(t)s group by m""", a, as_dict=True)}
	paid = sql(f"""select sum(base_paid_amount) v from `tabPayment Entry` where party_type = %(pt)s and party = %(n)s and docstatus = 1
		and posting_date between %(f)s and %(t)s""", {**a, "pt": "Customer" if kind == "c" else "Supplier"})[0][0]
	# Days the year's invoices actually span (the last invoice may be after today in forward-dated data).
	span_end = min(max(getdate(tot.last) if tot.last else a["today"], a["today"]), a["t"])
	days_elapsed = max((span_end - a["f"]).days, 1)
	dso = flt(outst.o) / (flt(tot.v) / days_elapsed) if flt(tot.v) else None
	tiles = [
		_tile(_("Sales (FY)") if kind == "c" else _("Purchases (FY)"), tot.v, "money", _("{0} invoices · last {1}").format(int(tot.n or 0), tot.last or "—"), "emerald" if kind == "c" else "violet"),
		_tile(_("Outstanding"), outst.o, "money", _("{0} open invoices").format(int(outst.n or 0)), "amber"),
		_tile(_("Overdue"), outst.od, "money", _("past due date"), "rose" if flt(outst.od) else "emerald"),
		_tile(_("Open orders"), open_o.v, "money", _("{0} orders not fully billed").format(int(open_o.n or 0)), "sky"),
		_tile(_("Received (FY)") if kind == "c" else _("Paid (FY)"), paid, "money", _("payment entries"), "primary"),
		_tile(_("Days outstanding"), round(dso) if dso is not None else None, "days", _("outstanding ÷ average daily {0}").format(_("sales") if kind == "c" else _("purchases")),
		      "rose" if dso and dso > 60 else "amber" if dso and dso > 30 else "emerald"),
	]
	lists = [{"title": _("Top items (FY)"), "rows": [_row(i.label, i.v, "money", frappe.format(flt(i.q), "Float")) for i in items]}]
	if kind == "c":
		limit = sql("select sum(credit_limit) from `tabCustomer Credit Limit` where parent = %(n)s and parenttype = 'Customer'", a)[0][0]
		if flt(limit):
			tiles.append(_tile(_("Credit used"), round(flt(outst.o) / flt(limit) * 100, 1), "percent", _("of {0} limit").format(frappe.format(flt(limit), "Currency")),
			                   "rose" if flt(outst.o) > flt(limit) else "emerald"))
	else:
		lt = sql("""select avg(datediff(pr.posting_date, po.transaction_date)) lead, avg(pr.posting_date <= poi.schedule_date) * 100 ontime, count(*) n
			from `tabPurchase Receipt Item` i join `tabPurchase Receipt` pr on pr.name = i.parent
			join `tabPurchase Order Item` poi on poi.name = i.purchase_order_item join `tabPurchase Order` po on po.name = poi.parent
			where pr.supplier = %(n)s and pr.docstatus = 1 and pr.posting_date between %(f)s and %(t)s""", a, as_dict=True)[0]
		if lt.n:
			tiles.append(_tile(_("Lead time"), round(flt(lt.lead), 1), "days", _("PO → receipt, {0}% on time").format(round(flt(lt.ontime))), "sky"))
	return {"tiles": tiles, "trend": _trend(_("Invoiced by month"), [{"key": "value", "label": _("Net value")}], monthly, months), "lists": lists}


def _customer(name, a, months):
	return _party(name, a, months, kind="c")


def _supplier(name, a, months):
	return _party(name, a, months, kind="s")


# ----------------------------------------------------------------------------- groups
def _descendants(doctype, name):
	lft, rgt = frappe.db.get_value(doctype, name, ["lft", "rgt"])
	return frappe.db.sql_list(f"select name from `tab{doctype}` where lft >= %s and rgt <= %s", (lft, rgt)) or [name]


def _item_group(name, a, months):
	a = {**a, "g": _descendants("Item Group", name)}
	sql = frappe.db.sql
	n_items = sql("select count(*) from `tabItem` where item_group in %(g)s and disabled = 0", a)[0][0]
	stock = sql("select sum(b.stock_value) v, sum(b.actual_qty) q from `tabBin` b join `tabItem` i on i.name = b.item_code where i.item_group in %(g)s", a, as_dict=True)[0]
	sal = sql("""select sum(i.base_net_amount) v from `tabSales Invoice Item` i join `tabSales Invoice` p on p.name = i.parent
		where i.item_group in %(g)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s""", a)[0][0]
	pur = sql("""select sum(i.base_net_amount) v from `tabPurchase Invoice Item` i join `tabPurchase Invoice` p on p.name = i.parent
		where i.item_group in %(g)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s""", a)[0][0]
	top = sql("""select i.item_name label, sum(b.stock_value) v, sum(b.actual_qty) q from `tabBin` b join `tabItem` i on i.name = b.item_code
		where i.item_group in %(g)s group by i.name order by v desc limit 8""", a, as_dict=True)
	monthly = {}
	for key, tbl in (("sold", "Sales Invoice"), ("purchased", "Purchase Invoice")):
		for r in sql(f"""select date_format(p.posting_date, '%%Y-%%m') m, sum(i.base_net_amount) v from `tab{tbl} Item` i join `tab{tbl}` p on p.name = i.parent
				where i.item_group in %(g)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s group by m""", a, as_dict=True):
			monthly.setdefault(r.m, {})[key] = flt(r.v)
	return {
		"tiles": [_tile(_("Items"), n_items, "number", _("{0} sub-groups").format(len(a["g"]) - 1), "sky"), _tile(_("Stock value"), stock.v, "money", None, "emerald"),
		          _tile(_("Sales (FY)"), sal, "money", None, "violet"), _tile(_("Purchases (FY)"), pur, "money", None, "amber")],
		"trend": _trend(_("Sales and purchases by month"), [{"key": "sold", "label": _("Sales")}, {"key": "purchased", "label": _("Purchases")}], monthly, months),
		"lists": [{"title": _("Largest stock holdings"), "rows": [_row(t.label, t.v, "money", frappe.format(flt(t.q), "Float")) for t in top]}],
	}


def _party_group(name, a, months, *, kind):
	gdt, pdt, inv, gfield = ("Customer Group", "Customer", "Sales Invoice", "customer_group") if kind == "c" else ("Supplier Group", "Supplier", "Purchase Invoice", "supplier_group")
	party = pdt.lower()
	a = {**a, "g": _descendants(gdt, name)}
	sql = frappe.db.sql
	n = sql(f"select count(*) from `tab{pdt}` where {gfield} in %(g)s and disabled = 0", a)[0][0]
	tot = sql(f"""select sum(p.base_net_total) v from `tab{inv}` p join `tab{pdt}` c on c.name = p.{party}
		where c.{gfield} in %(g)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s""", a)[0][0]
	out = sql(f"""select sum(p.outstanding_amount) from `tab{inv}` p join `tab{pdt}` c on c.name = p.{party} where c.{gfield} in %(g)s and p.docstatus = 1""", a)[0][0]
	top = sql(f"""select c.{party}_name label, sum(p.base_net_total) v, count(*) k from `tab{inv}` p join `tab{pdt}` c on c.name = p.{party}
		where c.{gfield} in %(g)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s group by c.name order by v desc limit 8""", a, as_dict=True)
	monthly = {r.m: {"value": flt(r.v)} for r in sql(f"""select date_format(p.posting_date, '%%Y-%%m') m, sum(p.base_net_total) v from `tab{inv}` p
		join `tab{pdt}` c on c.name = p.{party} where c.{gfield} in %(g)s and p.docstatus = 1 and p.posting_date between %(f)s and %(t)s group by m""", a, as_dict=True)}
	return {
		"tiles": [_tile(_("{0}s").format(pdt), n, "number", None, "sky"), _tile(_("Sales (FY)") if kind == "c" else _("Purchases (FY)"), tot, "money", None, "emerald"),
		          _tile(_("Outstanding"), out, "money", None, "amber")],
		"trend": _trend(_("Invoiced by month"), [{"key": "value", "label": _("Net value")}], monthly, months),
		"lists": [{"title": _("Largest {0}s (FY)").format(party), "rows": [_row(t.label, t.v, "money", _("{0} invoices").format(t.k)) for t in top]}],
	}


def _customer_group(name, a, months):
	return _party_group(name, a, months, kind="c")


def _supplier_group(name, a, months):
	return _party_group(name, a, months, kind="s")
