"""Production schedule for the React Gantt view: Production Plan → Work Orders → Job Card operations,
each with a planned and an actual time span."""

from datetime import datetime, timedelta

import frappe
from frappe.utils import flt, get_datetime, getdate, now_datetime

MAX_WORK_ORDERS = 600


def _stream(fg_warehouse):
	w = (fg_warehouse or "").lower().replace(" ", "")
	return "Conversion" if "thirdparty" in w else "Mill 2" if "mill2" in w else "Mill 1"


def _iso(d):
	return d.isoformat(timespec="minutes") if isinstance(d, datetime) else (f"{d}T00:00" if d else None)


@frappe.whitelist()
def get_schedule(from_date, to_date, company=None, stream=None, status=None, search=None):
	"""Work orders whose planned or actual span overlaps [from_date, to_date], grouped by production plan.
	Planned span: planned start → planned end (or expected delivery date). Actual span: actual start → actual end,
	or → now while the order is still running."""
	frappe.has_permission("Work Order", throw=True)
	f, t = getdate(from_date), getdate(to_date)
	now = now_datetime()
	cond, args = [], {"f": f, "t": t, "te": datetime.combine(t, datetime.max.time())}
	if company:
		cond.append("w.company = %(co)s")
		args["co"] = company
	if status:
		cond.append("w.status = %(st)s")
		args["st"] = status
	if search:
		cond.append("(w.name like %(q)s or w.item_name like %(q)s or w.production_plan like %(q)s or w.production_item like %(q)s)")
		args["q"] = f"%{search}%"
	where = (" and " + " and ".join(cond)) if cond else ""
	wos = frappe.db.sql(
		f"""select w.name, w.production_plan, w.production_item, w.item_name, w.status, w.qty, w.produced_qty, w.fg_warehouse,
			w.planned_start_date ps, w.planned_end_date pe, w.expected_delivery_date edd, w.actual_start_date ast, w.actual_end_date aen,
			w.work_order_date d
		from `tabWork Order` w
		where w.docstatus = 1
		  and coalesce(w.actual_start_date, w.planned_start_date) <= %(te)s
		  and greatest(coalesce(w.actual_end_date, w.actual_start_date, w.planned_start_date),
		               coalesce(w.planned_end_date, w.expected_delivery_date, w.planned_start_date)) >= %(f)s
		  {where}
		order by coalesce(w.actual_start_date, w.planned_start_date), w.name
		limit {MAX_WORK_ORDERS + 1}""",
		args,
		as_dict=True,
	)
	truncated = len(wos) > MAX_WORK_ORDERS
	wos = wos[:MAX_WORK_ORDERS]
	if stream:
		wos = [w for w in wos if _stream(w.fg_warehouse) == stream]

	ops = {}
	if wos:
		for j in frappe.db.sql(
			"""select name, work_order, operation, workstation, status, sequence_id, time_required req, total_time_in_mins act,
				expected_start_date es, expected_end_date ee, actual_start_date ast, actual_end_date aen, is_corrective_job_card rework
			from `tabJob Card` where docstatus < 2 and work_order in %(w)s order by sequence_id, expected_start_date""",
			{"w": [w.name for w in wos]},
			as_dict=True,
		):
			a_end = j.aen or (now if j.status in ("Work In Progress", "On Hold") and j.ast else None)
			ops.setdefault(j.work_order, []).append({
				"id": j.name, "label": j.operation, "workstation": j.workstation, "status": j.status, "rework": bool(j.rework),
				"planned": [_iso(j.es), _iso(j.ee)] if j.es and j.ee else None,
				"actual": [_iso(j.ast), _iso(a_end)] if j.ast and a_end else None,
				"open_ended": not j.aen and bool(j.ast),
				"efficiency": round(flt(j.req) / flt(j.act) * 100, 1) if j.status == "Completed" and flt(j.req) and flt(j.act) else None,
			})

	plans = {}
	for w in wos:
		ps = get_datetime(w.ps) if w.ps else (datetime.combine(w.d, datetime.min.time()) if w.d else None)
		pe = get_datetime(w.pe) if w.pe else (datetime.combine(w.edd, datetime.max.time().replace(microsecond=0)) if w.edd else None)
		if ps and (not pe or pe <= ps):
			pe = ps + timedelta(days=1)
		ast = get_datetime(w.ast) if w.ast else None
		aen = get_datetime(w.aen) if w.aen else None
		done = w.status in ("Completed", "Closed")
		if ast and aen and aen < ast:  # shifted demo dates: never draw a negative bar
			aen = ast + timedelta(hours=12)
		open_ended = bool(ast) and not aen and not done
		if open_ended:
			aen = max(now, ast + timedelta(hours=1))
		if ast and not aen:
			aen = ast + timedelta(hours=12)
		late = bool(done and aen and w.edd and aen.date() > w.edd) or bool(not done and w.edd and getdate(now) > w.edd)
		row = {
			"id": w.name, "label": w.item_name or w.production_item, "item": w.production_item, "status": w.status,
			"stream": _stream(w.fg_warehouse), "qty": flt(w.qty), "produced": flt(w.produced_qty),
			"progress": round(min(flt(w.produced_qty) / flt(w.qty) * 100, 100), 1) if flt(w.qty) else 0,
			"planned": [_iso(ps), _iso(pe)] if ps else None,
			"actual": [_iso(ast), _iso(aen)] if ast else None,
			"open_ended": open_ended, "due": str(w.edd) if w.edd else None, "late": late,
			"ops": ops.get(w.name, []),
		}
		key = w.production_plan or ""
		plans.setdefault(key, []).append(row)

	pp_meta = {}
	names = [k for k in plans if k]
	if names:
		pp_meta = {p.name: p for p in frappe.db.sql(
			"""select name, status, posting_date, total_planned_qty, total_produced_qty from `tabProduction Plan` where name in %(n)s""",
			{"n": names}, as_dict=True)}

	def span(rows, key):
		pts = [r[key] for r in rows if r[key]]
		return [min(p[0] for p in pts), max(p[1] for p in pts)] if pts else None

	out = []
	for key, rows in plans.items():
		m = pp_meta.get(key)
		qty, prod = sum(r["qty"] for r in rows), sum(r["produced"] for r in rows)
		out.append({
			"id": key or "__unplanned__", "label": key or "Work orders without a plan", "is_plan": bool(key),
			"status": m.status if m else ("Completed" if all(r["status"] in ("Completed", "Closed") for r in rows) else "In Process"),
			"posting_date": str(m.posting_date) if m else None,
			"qty": qty, "produced": prod, "progress": round(min(prod / qty * 100, 100), 1) if qty else 0,
			"planned": span(rows, "planned"), "actual": span(rows, "actual"),
			"late": any(r["late"] for r in rows), "streams": sorted({r["stream"] for r in rows}),
			"work_orders": rows,
		})
	out.sort(key=lambda p: (not p["is_plan"], (p["planned"] or p["actual"] or [""])[0]))
	return {
		"from": str(f), "to": str(t), "now": _iso(now), "truncated": truncated,
		"plans": out,
		"totals": {
			"plans": sum(1 for p in out if p["is_plan"]),
			"work_orders": len(wos),
			"operations": sum(len(v) for v in ops.values()),
			"in_process": sum(1 for w in wos if w.status in ("In Process", "Not Started", "Stopped")),
			"late": sum(1 for p in out for r in p["work_orders"] if r["late"]),
		},
	}
