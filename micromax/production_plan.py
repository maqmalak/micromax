"""Raw materials (Material Request Plan Items) for Production Plans, via ERPNext's own BOM explosion.

ERPNext only fills `mr_items` when someone presses "Get Raw Materials for Purchase" on a draft plan, so plans
created by import or API come up with an empty raw-material table. These helpers compute the rows for the React
form (draft: returned to the form; submitted: written straight to the plan) and back-fill existing plans."""

from collections import defaultdict

import frappe
from erpnext.manufacturing.doctype.production_plan.production_plan import get_items_for_material_requests
from frappe.utils import flt


def _compute(doc):
	rows = get_items_for_material_requests(doc.as_dict()) or []
	meta = frappe.get_meta("Material Request Plan Item")
	fields = {df.fieldname for df in meta.fields}
	return [{k: v for k, v in r.items() if k in fields} for r in rows if flt(r.get("quantity")) > 0]


@frappe.whitelist()
def get_raw_materials(doc):
	"""Raw-material rows for an unsaved/draft plan (the form applies them to its `mr_items` table)."""
	doc = frappe.get_doc(frappe.parse_json(doc) if isinstance(doc, str) else doc)
	frappe.has_permission("Production Plan", "write", throw=True)
	return _compute(doc)


@frappe.whitelist()
def fill_raw_materials(name, replace=1):
	"""(Re)build `mr_items` on a saved plan, including submitted ones (the table is informational after submit:
	material requests are raised from it). Returns the number of rows written."""
	doc = frappe.get_doc("Production Plan", name)
	doc.check_permission("write")
	if doc.docstatus == 2:
		frappe.throw(frappe._("Cancelled plans cannot be changed."))
	return _write(doc, int(replace))


def _write(doc, replace=1):
	rows = _compute(doc)
	if not rows:
		return 0
	if replace:
		frappe.db.delete("Material Request Plan Item", {"parent": doc.name, "parenttype": "Production Plan", "parentfield": "mr_items"})
	for i, r in enumerate(rows, 1):
		child = frappe.new_doc("Material Request Plan Item", parent_doc=doc, parentfield="mr_items")
		child.update(r)
		child.idx = i
		child.docstatus = doc.docstatus
		child.db_insert()
	frappe.db.set_value("Production Plan", doc.name, "modified", frappe.utils.now(), update_modified=False)
	return len(rows)


def backfill(only_empty=True, limit=None):
	"""bench execute micromax.production_plan.backfill — fill raw materials on every non-cancelled plan without them."""
	cond = "and not exists (select 1 from `tabMaterial Request Plan Item` m where m.parent = p.name and m.parenttype = 'Production Plan')" if only_empty else ""
	names = frappe.db.sql_list(f"select p.name from `tabProduction Plan` p where p.docstatus < 2 {cond} order by p.posting_date {'limit %d' % int(limit) if limit else ''}")
	done = rows = failed = 0
	for n in names:
		try:
			rows += _write(frappe.get_doc("Production Plan", n))
			done += 1
		except Exception:
			failed += 1
			frappe.db.rollback()
			continue
		if done % 100 == 0:
			frappe.db.commit()
	frappe.db.commit()
	return {"plans": done, "rows": rows, "failed": failed}


# ----------------------------------------------------------------------------- operations & costing tabs
@frappe.whitelist()
def get_plan_insights(doc):
	"""Planned vs actual operations and cost for a plan (saved or not).

	Planned: each assembly item's BOM scaled to its planned qty — raw material, operating and secondary-item cost,
	and the BOM's operations (minutes × qty ÷ BOM qty, at the operation's hour rate).
	Actual (saved plans): the plan's work orders — job-card hours and cost per operation, material consumed and
	additional costs on their Manufacture stock entries."""
	doc = frappe.parse_json(doc) if isinstance(doc, str) else doc
	frappe.has_permission("Production Plan", "read", throw=True)
	items = [r for r in (doc.get("po_items") or []) if r.get("bom_no") and flt(r.get("planned_qty"))]
	boms = {b.name: b for b in frappe.get_all("BOM", filters={"name": ["in", list({r["bom_no"] for r in items}) or [""]]},
	                                          fields=["name", "item", "item_name", "quantity", "uom", "raw_material_cost", "operating_cost",
	                                                  "secondary_items_cost", "total_cost", "with_operations"])}
	bom_ops = defaultdict(list)
	for o in frappe.get_all("BOM Operation", filters={"parent": ["in", list(boms) or [""]], "parenttype": "BOM"},
	                        fields=["parent", "operation", "workstation", "workstation_type", "time_in_mins", "hour_rate", "operating_cost", "sequence_id", "idx"],
	                        order_by="idx"):
		bom_ops[o.parent].append(o)

	lines, ops = [], {}
	for r in items:
		b = boms.get(r["bom_no"])
		if not b:
			continue
		f = flt(r["planned_qty"]) / flt(b.quantity or 1)
		lines.append({"item_code": r.get("item_code"), "item_name": b.item_name, "bom_no": b.name, "uom": b.uom, "planned_qty": flt(r["planned_qty"]),
		              "produced_qty": flt(r.get("produced_qty")), "raw_material_cost": flt(b.raw_material_cost) * f,
		              "operating_cost": flt(b.operating_cost) * f, "secondary_items_cost": flt(b.secondary_items_cost) * f,
		              "total_cost": flt(b.total_cost) * f, "unit_cost": flt(b.total_cost) / flt(b.quantity or 1), "has_operations": bool(bom_ops.get(b.name))})
		for o in bom_ops.get(b.name, []):
			op = ops.setdefault(o.operation, {"operation": o.operation, "seq": o.sequence_id or o.idx, "workstations": set(),
			                                   "planned_hours": 0.0, "planned_cost": 0.0, "hour_rate": flt(o.hour_rate),
			                                   "actual_std_hours": 0.0, "actual_hours": 0.0, "actual_cost": 0.0, "cards": 0, "completed": 0})
			op["workstations"].add(o.workstation or o.workstation_type or "")
			op["planned_hours"] += flt(o.time_in_mins) * f / 60
			op["planned_cost"] += flt(o.time_in_mins) * f / 60 * flt(o.hour_rate)

	actual = {"work_orders": 0, "produced": 0.0, "material_cost": 0.0, "additional_cost": 0.0, "operation_cost": 0.0}
	name = doc.get("name")
	if name and not str(name).startswith("new-") and frappe.db.exists("Production Plan", name):
		wos = frappe.db.sql_list("select name from `tabWork Order` where production_plan = %s and docstatus = 1", name)
		if wos:
			actual["work_orders"] = len(wos)
			for j in frappe.db.sql("""select operation, count(*) n, sum(status = 'Completed') done, sum(time_required) / 60 std,
					sum(total_time_in_mins) / 60 act, sum(total_time_in_mins / 60 * hour_rate) cost, min(sequence_id) seq,
					group_concat(distinct workstation) ws
				from `tabJob Card` where work_order in %(w)s and docstatus < 2 group by operation""", {"w": wos}, as_dict=True):
				op = ops.setdefault(j.operation, {"operation": j.operation, "seq": j.seq or 99, "workstations": set(), "planned_hours": 0.0,
				                                   "planned_cost": 0.0, "hour_rate": 0.0, "actual_std_hours": 0.0, "actual_hours": 0.0,
				                                   "actual_cost": 0.0, "cards": 0, "completed": 0})
				op.update(actual_std_hours=flt(j.std), actual_hours=flt(j.act), actual_cost=flt(j.cost), cards=j.n, completed=int(j.done or 0))
				op["workstations"].update((j.ws or "").split(","))
				actual["operation_cost"] += flt(j.cost)
			se = frappe.db.sql("""select sum(se.fg_completed_qty) produced, sum(se.total_outgoing_value) material, sum(se.total_additional_costs) additional
				from `tabStock Entry` se where se.docstatus = 1 and se.purpose = 'Manufacture' and se.work_order in %(w)s""", {"w": wos}, as_dict=True)[0]
			actual.update(produced=flt(se.produced), material_cost=flt(se.material), additional_cost=flt(se.additional))

	planned_qty = sum(l["planned_qty"] for l in lines)
	planned_total = sum(l["total_cost"] for l in lines)
	actual_total = actual["material_cost"] + actual["additional_cost"] + actual["operation_cost"]
	op_rows = []
	for o in sorted(ops.values(), key=lambda x: (x["seq"] or 99)):
		o["workstations"] = ", ".join(sorted(w for w in o["workstations"] if w))[:200]
		o["efficiency"] = round(o["actual_std_hours"] / o["actual_hours"] * 100, 1) if o["actual_hours"] else None
		for k in ("planned_hours", "planned_cost", "actual_std_hours", "actual_hours", "actual_cost"):
			o[k] = round(o[k], 2)
		op_rows.append(o)
	return {
		"lines": lines,
		"operations": op_rows,
		"planned": {"qty": planned_qty, "raw_material_cost": sum(l["raw_material_cost"] for l in lines),
		            "operating_cost": sum(l["operating_cost"] for l in lines), "secondary_items_cost": sum(l["secondary_items_cost"] for l in lines),
		            "total_cost": planned_total, "unit_cost": planned_total / planned_qty if planned_qty else 0,
		            "hours": round(sum(o["planned_hours"] for o in op_rows), 1)},
		"actual": {**actual, "total_cost": actual_total, "unit_cost": actual_total / actual["produced"] if actual["produced"] else 0,
		           "hours": round(sum(o["actual_hours"] for o in op_rows), 1)},
		"boms_without_operations": sorted({l["bom_no"] for l in lines if not l["has_operations"]}),
	}


# ----------------------------------------------------------------------------- work order tabs
@frappe.whitelist()
def get_wo_insights(name):
	"""Work Order "Operations" / "Performance" tabs: job-card execution per operation, material movements by
	stock-entry purpose, and downtime by reason."""
	frappe.get_doc("Work Order", name).check_permission("read")
	ops = frappe.db.sql("""select operation, min(sequence_id) seq, count(*) cards, sum(status = 'Completed') completed,
			sum(time_required) / 60 std_hours, sum(total_time_in_mins) / 60 actual_hours, sum(total_time_in_mins / 60 * hour_rate) cost,
			sum(process_loss_qty) loss, max(for_quantity) qty, group_concat(distinct workstation separator ', ') workstations,
			min(actual_start_date) started, max(actual_end_date) finished, sum(is_corrective_job_card) rework
		from `tabJob Card` where work_order = %s and docstatus < 2 group by operation order by seq, started""", name, as_dict=True)
	for o in ops:
		o.efficiency = round(flt(o.std_hours) / flt(o.actual_hours) * 100, 1) if flt(o.actual_hours) and flt(o.std_hours) else None
		o.loss_pct = round(flt(o.loss) / flt(o.qty) * 100, 2) if flt(o.qty) else None
	stock = frappe.db.sql("""select purpose, count(*) entries, sum(fg_completed_qty) qty, sum(total_outgoing_value) outgoing,
			sum(total_incoming_value) incoming, sum(total_additional_costs) additional
		from `tabStock Entry` where work_order = %s and docstatus = 1 group by purpose""", name, as_dict=True)
	downtime = frappe.db.sql("""select ifnull(stop_reason, 'Not set') reason, count(*) stops, sum(downtime) minutes
		from `tabDowntime Entry` where work_order = %s and docstatus < 2 group by stop_reason order by minutes desc""", name, as_dict=True)
	return {"operations": ops, "stock": stock, "downtime": downtime}
