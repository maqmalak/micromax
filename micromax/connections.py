"""Linked documents for the React form's "Connections" tab (the desk's dashboard links, as data).

Each group is a SQL returning (name, status, date, detail) rows that reference the document; groups the user
cannot read are skipped."""

import frappe
from frappe import _

LIMIT = 50


def _wo_names(plan):
	return frappe.db.sql_list("select name from `tabWork Order` where production_plan = %s and docstatus < 2", plan)


def _plan_groups(name):
	wos = _wo_names(name) or [""]
	return [
		(_("Manufacturing"), "Work Order", """select name, status, work_order_date date, concat(item_name, ' · ', round(produced_qty), ' / ', round(qty)) detail
			from `tabWork Order` where production_plan = %(n)s and docstatus < 2 order by work_order_date desc""", {}),
		(_("Manufacturing"), "Job Card", """select name, status, posting_date date, concat(operation, ' · ', ifnull(workstation, '')) detail
			from `tabJob Card` where work_order in %(w)s and docstatus < 2 order by posting_date desc, sequence_id""", {"w": wos}),
		(_("Stock"), "Stock Entry", """select name, stock_entry_type status, posting_date date, work_order detail
			from `tabStock Entry` where work_order in %(w)s and docstatus < 2 order by posting_date desc""", {"w": wos}),
		(_("Stock"), "Material Request", """select distinct p.name, p.status, p.transaction_date date, p.material_request_type detail
			from `tabMaterial Request` p join `tabMaterial Request Item` i on i.parent = p.name
			where i.production_plan = %(n)s and p.docstatus < 2 order by p.transaction_date desc""", {}),
		(_("Sales"), "Sales Order", """select distinct so.name, so.status, so.transaction_date date, so.customer detail from `tabSales Order` so
			where so.name in (select sales_order from `tabProduction Plan Sales Order` where parent = %(n)s
			                  union select sales_order from `tabProduction Plan Item` where parent = %(n)s and ifnull(sales_order, '') != '')
			order by so.transaction_date desc""", {}),
	]


def _bom_groups(name):
	return [
		(_("Item"), "Item", """select i.name, if(i.disabled, 'Disabled', 'Enabled') status, null date, i.item_name detail
			from `tabItem` i where i.name = (select item from `tabBOM` where name = %(n)s)""", {}),
		(_("Item"), "BOM", """select distinct b.name, if(b.is_active, if(b.is_default, 'Default', 'Active'), 'Inactive') status, date(b.creation) date,
			concat('Uses this BOM · ', b.item_name) detail
			from `tabBOM` b join `tabBOM Item` i on i.parent = b.name where i.bom_no = %(n)s and b.name != %(n)s and b.docstatus < 2""", {}),
		(_("Manufacturing"), "Production Plan", """select distinct p.name, p.status, p.posting_date date, concat(round(i.planned_qty), ' planned') detail
			from `tabProduction Plan` p join `tabProduction Plan Item` i on i.parent = p.name
			where i.bom_no = %(n)s and p.docstatus < 2 order by p.posting_date desc""", {}),
		(_("Manufacturing"), "Work Order", """select name, status, work_order_date date, concat(round(produced_qty), ' / ', round(qty)) detail
			from `tabWork Order` where bom_no = %(n)s and docstatus < 2 order by work_order_date desc""", {}),
		(_("Manufacturing"), "Job Card", """select name, status, posting_date date, concat(operation, ' · ', ifnull(workstation, '')) detail
			from `tabJob Card` where bom_no = %(n)s and docstatus < 2 order by posting_date desc""", {}),
		(_("Stock"), "Stock Entry", """select name, stock_entry_type status, posting_date date, work_order detail
			from `tabStock Entry` where bom_no = %(n)s and docstatus < 2 order by posting_date desc""", {}),
	]


def _wo_groups(name):
	return [
		(_("Manufacturing"), "Job Card", """select name, status, posting_date date, concat(operation, ' · ', ifnull(workstation, '')) detail
			from `tabJob Card` where work_order = %(n)s and docstatus < 2 order by sequence_id, posting_date""", {}),
		(_("Manufacturing"), "Downtime Entry", """select name, ifnull(stop_reason, 'Not set') status, date(from_time) date,
			concat(ifnull(workstation, ''), ' · ', round(downtime), ' min') detail
			from `tabDowntime Entry` where work_order = %(n)s and docstatus < 2 order by from_time desc""", {}),
		(_("Stock"), "Stock Entry", """select name, stock_entry_type status, posting_date date, concat(round(fg_completed_qty), ' qty') detail
			from `tabStock Entry` where work_order = %(n)s and docstatus < 2 order by posting_date desc""", {}),
		(_("Quality"), "Quality Inspection", """select name, status, report_date date, concat(inspection_type, ' · ', reference_name) detail
			from `tabQuality Inspection` where docstatus < 2 and (
				(reference_type = 'Stock Entry' and reference_name in (select name from `tabStock Entry` where work_order = %(n)s))
				or (reference_type = 'Job Card' and reference_name in (select name from `tabJob Card` where work_order = %(n)s)))
			order by report_date desc""", {}),
		(_("Planning"), "Production Plan", """select name, status, posting_date date, null detail from `tabProduction Plan`
			where name = (select production_plan from `tabWork Order` where name = %(n)s)""", {}),
		(_("Planning"), "BOM", """select name, if(is_active, if(is_default, 'Default', 'Active'), 'Inactive') status, null date, item_name detail
			from `tabBOM` where name = (select bom_no from `tabWork Order` where name = %(n)s)""", {}),
		(_("Planning"), "Sales Order", """select name, status, transaction_date date, customer detail from `tabSales Order`
			where name = (select sales_order from `tabWork Order` where name = %(n)s)""", {}),
	]


def _jc_groups(name):
	return [
		(_("Manufacturing"), "Work Order", """select name, status, work_order_date date, item_name detail from `tabWork Order`
			where name = (select work_order from `tabJob Card` where name = %(n)s)""", {}),
		(_("Manufacturing"), "Job Card", """select name, status, posting_date date, concat('Corrective · ', operation) detail
			from `tabJob Card` where for_job_card = %(n)s and docstatus < 2 order by posting_date desc""", {}),
		(_("Manufacturing"), "Downtime Entry", """select d.name, ifnull(d.stop_reason, 'Not set') status, date(d.from_time) date,
			concat(round(d.downtime), ' min') detail
			from `tabDowntime Entry` d join `tabJob Card` j on j.name = %(n)s
			where d.work_order = j.work_order and d.workstation = j.workstation and d.docstatus < 2 order by d.from_time desc""", {}),
		(_("Stock"), "Stock Entry", """select name, stock_entry_type status, posting_date date, work_order detail
			from `tabStock Entry` where job_card = %(n)s and docstatus < 2 order by posting_date desc""", {}),
		(_("Quality"), "Quality Inspection", """select name, status, report_date date, inspection_type detail from `tabQuality Inspection`
			where docstatus < 2 and ((reference_type = 'Job Card' and reference_name = %(n)s)
			                         or name = (select quality_inspection from `tabJob Card` where name = %(n)s))""", {}),
		(_("People"), "Employee", """select distinct e.name, e.status, null date, concat(e.employee_name, ' · ', round(sum(l.time_in_mins) / 60, 1), ' h') detail
			from `tabJob Card Time Log` l join `tabEmployee` e on e.name = l.employee
			where l.parent = %(n)s group by e.name, e.status, e.employee_name order by sum(l.time_in_mins) desc""", {}),
	]


SPECS = {"Production Plan": _plan_groups, "BOM": _bom_groups, "Work Order": _wo_groups, "Job Card": _jc_groups}


@frappe.whitelist()
def get_connections(doctype, name):
	if doctype not in SPECS:
		frappe.throw(_("No connections are defined for {0}").format(doctype))
	frappe.get_doc(doctype, name).check_permission("read")
	out = []
	for group, dt, sql, extra in SPECS[doctype](name):
		if not frappe.has_permission(dt, "read"):
			continue
		rows = frappe.db.sql(f"select * from ({sql}) x", {"n": name, **extra}, as_dict=True)
		out.append({"group": group, "doctype": dt, "label": _(dt), "count": len(rows),
		            "rows": [{k: (str(v) if v is not None and k == "date" else v) for k, v in r.items()} for r in rows[:LIMIT]]})
	return out
