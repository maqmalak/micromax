"""Daily demo data: continues micromax.demo_data one day at a time, so the demo company always looks current.

    bench --site micromaxerp execute micromax.demo_daily.run --kwargs "{'company': 'MicroMax Spinning Demo'}"
    bench --site demo        execute micromax.demo_daily.run --kwargs "{'company': 'MicroMax Erp Pvt Ltd.'}"
    bench --site <site>      execute micromax.demo_daily.status --kwargs "{'company': '...'}"

Run daily by the scheduler (hooks: cron 23:00) on sites whose site_config has `demo_daily_company`:

    bench --site micromaxerp set-config demo_daily_company "MicroMax Spinning Demo"

Each run generates every day from the day after the last one generated up to today (so a missed day is caught
up), with the same logic as the one-shot generator: sales orders (most working days), Mon/Thu production plans
-> work orders -> material transfer / manufacture stock entries, fibre buying every working day when stock is
short net of open orders (material request -> purchase order -> receipt -> invoice), deliveries, invoices and payments with realistic lags — so some orders stay undelivered and
some invoices unpaid — month-end overheads, waste sales, attendance, payroll and depreciation.

What the one-shot generator only does at month end is done daily here for attendance: employee check-ins
against each employee's shift (early check-ins, late entries, early exits), Present / Half Day / Absent /
On Leave per day. A month's payroll runs on its last day; a month left without payroll (e.g. the run before
stopped on the 30th) is caught up on the next run.

Follow-ups scheduled for later days (deliver, invoice, pay, receive, salary payment...) are saved with the run
state (DefaultValue `micromax_demo_daily::<company>`) and run on their day. The first run also picks up
documents the earlier run left open (unpaid invoices, purchase orders to receive, work orders, unplanned orders).
"""

import json
import random

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, get_last_day, getdate, now, nowdate

STATE_KEY = "micromax_demo_daily::{}"
LOCK_SECONDS = 4 * 3600


# ============================================================================ entry points
def scheduled():
    """Scheduler hook: queue today's run on sites configured for it (no-op elsewhere, e.g. the school site)."""
    company = frappe.conf.get("demo_daily_company")
    if not company:
        return
    frappe.enqueue("micromax.demo_daily.run", queue="long", timeout=LOCK_SECONDS, job_id=f"demo_daily::{company}",
                   deduplicate=True, company=company)


def status(company=None):
    company = company or frappe.conf.get("demo_daily_company")
    st = _load(company)
    q = st.get("queue", [])
    out = {"company": company, "last_day": st.get("last_day"), "origin": st.get("origin"), "queued": len(q),
           "next_due": min((e[0] for e in q), default=None), "unplanned_orders": len(st.get("unplanned", []))}
    print(json.dumps(out, indent=1, default=str))
    return out


def run(company=None, upto=None, template="spinning_mill", origin="2025-07-01", start=None):
    """Generate each missing day up to `upto` (default today; never later). `start` forces the first day."""
    from micromax import demo_data as dd

    company = company or frappe.conf.get("demo_daily_company")
    if not company or not frappe.db.exists("Company", company):
        frappe.throw(f"Company not found: {company!r}. Pass company='...' or set demo_daily_company in site_config.")
    lock = frappe.cache.make_key(f"demo_daily_lock::{company}")
    if not frappe.cache.set(lock, now(), ex=LOCK_SECONDS, nx=True):
        print(f"Another daily run for {company} is in progress — skipping.")
        return
    try:
        return _run(dd, company, upto, template, origin, start)
    finally:
        frappe.cache.delete(lock)


def _run(dd, company, upto, template, origin, start):
    today = getdate(nowdate())
    upto = min(getdate(upto or today), today)
    st = _load(company)
    first_run = not st
    st.setdefault("origin", str(origin))
    last = getdate(add_days(start, -1)) if start else \
        getdate(st.get("last_day") or _last_generated_day(company) or add_days(upto, -1))
    if last >= upto:
        print(f"{company}: already generated up to {last}.")
        return {"company": company, "last_day": str(last)}

    ctx = dd.prepare(company, st["origin"], upto, seed=f"{company}|{upto}", template=template)
    ctx.t.setdefault("planning", {})["open_tail_days"] = 0     # daily: no artificial "still on the floor" tail
    _stores_masters(ctx)
    ctx.unplanned = list(st.get("unplanned", []))
    ctx.so_mill.update(st.get("so_mill", {}))
    ctx.wo_mill.update(st.get("wo_mill", {}))
    queue = [_decode(e) for e in st.get("queue", [])]
    if first_run:
        queue += _adopt_open_documents(ctx, add_days(last, 1))
        ctx.log(f"First daily run: picked up {len(queue)} follow-ups for documents left open.")

    saved = dd._tune_manufacturing_settings()
    try:
        with dd._inline_background_jobs():
            d = add_days(last, 1)
            while getdate(d) <= upto:
                queue = _one_day(dd, ctx, getdate(d), queue)
                st.update({"last_day": str(d), "queue": [_encode(e) for e in queue], "unplanned": ctx.unplanned,
                           "so_mill": _recent(ctx.so_mill), "wo_mill": _recent(ctx.wo_mill)})
                _save(company, st)
                frappe.db.commit()
                d = add_days(d, 1)
    finally:
        dd._restore_manufacturing_settings(saved)
        frappe.db.commit()
    ctx.log(f"Done: {company} up to {upto}. Created: {ctx.counts}. Queued for later days: {len(queue)}")
    return ctx.summary()


# ============================================================================ one day
def _one_day(dd, ctx, day, queue):
    from micromax import demo_modules

    ctx.end = day
    ctx.rnd = random.Random(f"{ctx.company}|{day}")
    rnd, f = ctx.rnd, ctx.t.season[day.month]
    events = [e for e in queue if getdate(e[0]) <= day]
    later = [e for e in queue if getdate(e[0]) > day]

    def at(d, order, fn, *args):
        (events if getdate(d) <= day else later).append((getdate(d), order, fn, args))

    ctx.at = at
    _catch_up_month(ctx, day)                               # last month's attendance / payroll, if never run
    # The day plan of demo_data.transactions, made daily: sales orders and fibre buying every working day
    # (Sunday off), production planning Mon / Thu.
    working = day.weekday() != 6
    if working and rnd.random() < min(1.0, f + 0.25):
        at(day, 1, dd._sales_order, day)
        if rnd.random() < f - 0.6:
            at(day, 1, dd._sales_order, day)
    if day.weekday() in (0, 3):
        at(day, 1.5, dd._production_plan, day)
    if working:
        at(day, 0, dd._replenish_fibre, day)          # orders only what's short, net of open purchase orders
    if day.day == 1:
        at(day, -1, demo_modules.month_start, day)
    if working:
        at(day, 0.5, daily_stores, day)               # consumables issued; reorder -> MR -> PO
    at(day, 9.4, daily_attendance, day)
    if add_days(day, 1).month != day.month:
        at(day, 8.5, dd._sell_waste, day)
        at(day, 9, dd._month_end_overheads, day)
        at(day, 9.5, demo_modules.month_end, day)

    while events:
        events.sort(key=lambda e: (getdate(e[0]), e[1]))
        d, _o, fn, args = events.pop(0)
        fn = _fn(fn)
        ctx._current_day = day
        try:
            try:
                fn(ctx, *args)
            except frappe.QueryDeadlockError:
                frappe.db.rollback()
                fn(ctx, *args)
            frappe.db.commit()
        except Exception as e:
            frappe.db.rollback()
            ctx.log(f"  ! {day} {fn.__name__}: {str(e)[:220]}")
            ctx.bump("errors")
    ctx.log(f"{day}: {ctx.counts}")
    return later


def _catch_up_month(ctx, day):
    """A finished month without payroll (the previous run stopped before its month-end): mark the missing
    attendance and run the payroll now, dated the month's last day."""
    from micromax import demo_modules

    prev_last = add_days(day.replace(day=1), -1)
    if getdate(prev_last) < getdate(ctx.start) or not (ctx.t.get("payroll") and hasattr(ctx, "salary_structure")):
        return
    if frappe.db.exists("Payroll Entry", {"company": ctx.company, "end_date": prev_last, "docstatus": 1}):
        return
    ctx._current_day = prev_last
    try:
        demo_modules._attendance(ctx, prev_last)
        demo_modules._payroll(ctx, prev_last)
        frappe.db.commit()
        ctx.log(f"  caught up payroll for {getdate(prev_last):%b %Y}")
    except Exception as e:
        frappe.db.rollback()
        ctx.log(f"  ! payroll catch-up {prev_last}: {str(e)[:220]}")
        ctx.bump("errors")


# ============================================================================ attendance with check-ins
_SHIFTS = {}


def _shift(name):
    if name not in _SHIFTS:
        s = frappe.db.get_value("Shift Type", name, ["start_time", "end_time", "late_entry_grace_period",
                                                    "early_exit_grace_period"], as_dict=True)
        _SHIFTS[name] = s
    return _SHIFTS[name]


def daily_attendance(ctx, day):
    """One day's attendance: check-in / check-out per present employee against their shift, plus Absent /
    On Leave. Rates from the template's hr section; late / early / half-day shares below."""
    from micromax import demo_modules

    hr = ctx.t.get("hr")
    if not hr or not hasattr(ctx, "employees"):
        return
    day = getdate(day)
    if frappe.db.exists("Holiday", {"parent": ctx.holiday_list, "holiday_date": day}):
        return
    rnd = ctx.rnd
    absent_rate = flt(hr["absence_rate"]) + (flt(hr.get("summer_absence_extra")) if day.month in ctx.t.load_shedding else 0)
    leave_rate = flt(hr["leave_rate"])
    marked = set(frappe.get_all("Attendance", {"company": ctx.company, "attendance_date": day, "docstatus": ["<", 2]},
                                pluck="employee"))
    info = {e.name: e for e in frappe.get_all("Employee", {"name": ["in", list(ctx.employees) or [""]]},
                                              ["name", "employee_name", "default_shift", "department", "status",
                                               "date_of_joining", "relieving_date"])}
    default_shift = next((s["name"] for s in hr["shifts"] if "General" in s["name"]), hr["shifts"][0]["name"])
    ts = now()
    att, chk, leaves = [], [], []
    for e in info.values():
        if e.name in marked or getdate(e.date_of_joining) > day or (e.relieving_date and getdate(e.relieving_date) < day):
            continue
        shift = e.default_shift or default_shift
        s = _shift(shift)
        if not s:
            continue
        att_name = f"HR-ATT-MMD-{frappe.generate_hash(length=10)}"
        r = rnd.random()
        if r < absent_rate:
            att.append((att_name, e, "Absent", None, shift, None, None, 0, 0, 0))
            continue
        if r < absent_rate + leave_rate:
            att.append((att_name, e, "On Leave", "Casual Leave", shift, None, None, 0, 0, 0))
            leaves.append(e)
            continue
        start = get_datetime(f"{day} {s.start_time}")
        end = get_datetime(f"{day} {s.end_time}")
        if end <= start:                                     # night shift ends the next morning
            end = frappe.utils.add_to_date(end, days=1)
        grace_in, grace_out = cint(s.late_entry_grace_period) or 15, cint(s.early_exit_grace_period) or 15
        k = rnd.random()
        if k < 0.07:                                         # late entry, past the grace period
            in_off = rnd.randint(grace_in + 1, grace_in + 90)
        elif k < 0.20:                                       # early check-in
            in_off = -rnd.randint(15, 50)
        else:
            in_off = rnd.randint(-12, min(20, grace_in - 1))
        k = rnd.random()
        if k < 0.03:                                         # left half-way (half day)
            out_off = -rnd.randint(4 * 60, 5 * 60)
        elif k < 0.08:                                       # early exit, before the grace period
            out_off = -rnd.randint(grace_out + 1, grace_out + 120)
        else:
            out_off = rnd.randint(-min(10, grace_out - 1), 45)
        t_in = frappe.utils.add_to_date(start, minutes=in_off, seconds=rnd.randint(0, 59))
        t_out = frappe.utils.add_to_date(end, minutes=out_off, seconds=rnd.randint(0, 59))
        hours = round((t_out - t_in).total_seconds() / 3600, 2)
        status = "Half Day" if hours < 4.5 else "Present"
        late, early = int(in_off > grace_in), int(out_off < -grace_out)
        att.append((att_name, e, status, None, shift, t_in, t_out, hours, late, early))
        device = rnd.choice(["Gate-1", "Gate-2", "Mill-Gate"])
        for log_type, t in (("IN", t_in), ("OUT", t_out)):
            chk.append((f"EMP-CKIN-MMD-{frappe.generate_hash(length=10)}", e.name, e.employee_name, log_type, t, shift,
                        device, att_name, 1, start, end, ts, ts, "Administrator", "Administrator"))

    has_hd = frappe.get_meta("Attendance").has_field("half_day_status")
    fields = ["name", "naming_series", "employee", "employee_name", "status", "attendance_date", "company",
              "department", "docstatus", "creation", "modified", "owner", "modified_by", "leave_type", "shift",
              "in_time", "out_time", "working_hours", "late_entry", "early_exit"] + (["half_day_status"] if has_hd else [])
    rows = []
    for (n, e, status, lt, shift, t_in, t_out, hours, late, early) in att:
        row = [n, "HR-ATT-.YYYY.-", e.name, e.employee_name, status, day, ctx.company, e.department, 1, ts, ts,
               "Administrator", "Administrator", lt, shift, t_in, t_out, hours, late, early]
        if has_hd:
            row.append("Absent" if status == "Half Day" else None)
        rows.append(tuple(row))
    if rows:
        frappe.db.bulk_insert("Attendance", fields, rows)
        ctx.bump("Attendance", len(rows))
    if chk:
        frappe.db.bulk_insert("Employee Checkin", ["name", "employee", "employee_name", "log_type", "time", "shift",
                                                   "device_id", "attendance", "skip_auto_attendance", "shift_start",
                                                   "shift_end", "creation", "modified", "owner", "modified_by"], chk)
        ctx.bump("Employee Checkin", len(chk))
    for k, label in ((lambda a: a[8], "Late entries"), (lambda a: a[9], "Early exits"), (lambda a: a[2] == "Half Day", "Half days")):
        n = sum(1 for a in att if k(a))
        if n:
            ctx.bump(label, n)
    if leaves:                                               # approved casual leave, as the month-end path writes it
        cols = ["name", "naming_series", "employee", "employee_name", "leave_type", "company", "department",
                "from_date", "to_date", "posting_date", "total_leave_days", "status", "docstatus", "description",
                "creation", "modified", "owner", "modified_by"]
        frappe.db.bulk_insert("Leave Application", cols, [
            (f"HR-LAP-MMD-{frappe.generate_hash(length=10)}", "HR-LAP-.YYYY.-", e.name, e.employee_name, "Casual Leave",
             ctx.company, e.department, day, day, day, 1, "Approved", 1, demo_modules._dd().DEMO_TAG, ts, ts,
             "Administrator", "Administrator") for e in leaves])
        ctx.bump("Leave Application", len(leaves))


# ============================================================================ stores & spares (daily)
def _stores_masters(ctx):
    """Items, warehouse, suppliers and expense account for the template's `stores` section (created when missing)."""
    from micromax import demo_data as dd, demo_modules

    st = ctx.t.get("stores")
    if not st:
        return
    c, abbr = ctx.company, ctx.abbr
    parent_ig = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ["in", ["", None]]}, "name")
    dd._ensure("Item Group", st["item_group"], {"item_group_name": st["item_group"], "parent_item_group": parent_ig})
    wh = frappe.db.get_value("Warehouse", {"company": c, "warehouse_name": st["warehouse"], "is_group": 0}, "name")
    if not wh:
        parent_wh = frappe.db.get_value("Warehouse", {"company": c, "is_group": 1, "parent_warehouse": ["in", ["", None]]}, "name")
        wh = dd._ensure("Warehouse", f"{st['warehouse']} - {abbr}", {"warehouse_name": st["warehouse"], "company": c,
                                                                    "parent_warehouse": parent_wh}).name
    ctx.wh["stores"] = wh
    ctx.stores_expense = demo_modules._acc(ctx, st["expense_account"], ["Direct Expenses", "Manufacturing Expenses", "Expenses"], "Expense")
    dd._ensure("Supplier Group", st["supplier_group"], {"supplier_group_name": st["supplier_group"],
               "parent_supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 1}, "name")})
    for s in st["suppliers"]:
        dd._ensure("Supplier", s, {"supplier_name": s, "supplier_group": st["supplier_group"], "supplier_type": "Company"})
    for it in st["items"]:
        if not frappe.db.exists("UOM", it["uom"]):
            frappe.get_doc({"doctype": "UOM", "uom_name": it["uom"]}).insert(ignore_permissions=True)
        if not frappe.db.exists("Item", it["code"]):
            frappe.get_doc({"doctype": "Item", "item_code": it["code"], "item_name": it["name"], "item_group": st["item_group"],
                            "stock_uom": it["uom"], "is_stock_item": 1, "is_purchase_item": 1, "is_sales_item": 0,
                            "valuation_rate": it["rate"], "include_item_in_manufacturing": 0,
                            "item_defaults": [{"company": c, "default_warehouse": wh, "default_supplier": it.get("supplier"),
                                               "expense_account": ctx.stores_expense}]}).insert(ignore_permissions=True)
        elif not frappe.db.exists("Item Default", {"parent": it["code"], "company": c}):
            item = frappe.get_doc("Item", it["code"])
            item.append("item_defaults", {"company": c, "default_warehouse": wh, "default_supplier": it.get("supplier"),
                                          "expense_account": ctx.stores_expense})
            item.save(ignore_permissions=True)
    frappe.db.commit()


def daily_stores(ctx, day):
    """Working day: issue consumables to the floor; what falls below its reorder level is requested and ordered."""
    from micromax import demo_data as dd

    st = ctx.t.get("stores")
    if not st or "stores" not in ctx.wh:
        return
    rnd, wh = ctx.rnd, ctx.wh["stores"]
    # 1. Issue to production (only what is on hand)
    issue = []
    for it in st["items"]:
        qty = min(rnd.randint(*it["daily_use"]), int(dd._balance(ctx, it["code"], wh)))
        if qty > 0:
            issue.append({"item_code": it["code"], "qty": qty, "s_warehouse": wh, "expense_account": ctx.stores_expense,
                          "cost_center": ctx.cost_center})
    if issue:
        se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Issue", "purpose": "Material Issue",
                             "company": ctx.company, "items": issue})
        se.remarks = f"{dd.DEMO_TAG} · stores issued to the spinning floor"
        dd._submit(ctx, se, day, remarks=False)
    # 2. Reorder: below the level, net of what is already on order
    short = []
    for it in st["items"]:
        on_hand = dd._balance(ctx, it["code"], wh) + dd._on_order(ctx, it["code"]) + _requested(ctx, it["code"])
        if on_hand < it["reorder_level"]:
            short.append(it)
    if not short:
        return
    mr = frappe.get_doc({"doctype": "Material Request", "material_request_type": "Purchase", "company": ctx.company,
                         "transaction_date": day, "schedule_date": add_days(day, 5),
                         "items": [{"item_code": it["code"], "qty": it["reorder_qty"], "uom": it["uom"],
                                    "schedule_date": add_days(day, 5), "warehouse": wh} for it in short]})
    dd._insert_submit(ctx, mr)
    by_supplier = {}
    for it in short:
        by_supplier.setdefault(it.get("supplier") or rnd.choice(st["suppliers"]), []).append(it)
    for supplier, items in by_supplier.items():
        if rnd.random() < 0.25:                       # some requests wait a few days for approval / quotes
            ctx.at(add_days(day, rnd.randint(1, 4)), 0.4, order_stores, mr.name, supplier, [i["code"] for i in items])
        else:
            order_stores(ctx, mr.name, supplier, [i["code"] for i in items])


def order_stores(ctx, mr_name, supplier, codes):
    """Purchase Order for a stores request; received in 1–6 days (then invoiced and paid by _receive_and_bill)."""
    from erpnext.stock.doctype.material_request.material_request import make_purchase_order
    from micromax import demo_data as dd

    rnd, day = ctx.rnd, ctx._current_day
    rates = {i["code"]: i["rate"] for i in ctx.t.stores["items"]}
    po = make_purchase_order(mr_name)
    po.items = [i for i in po.items if i.item_code in codes]
    if not po.items:
        return
    po.supplier, po.transaction_date = supplier, day
    po.schedule_date = add_days(day, rnd.randint(1, 6))
    for i in po.items:
        i.schedule_date = po.schedule_date
        i.rate = ctx.price(rates[i.item_code], day)
    dd._apply_tax(ctx, po, "Purchase")
    dd._insert_submit(ctx, po)
    ctx.bump("Stores PO")
    ctx.at(po.schedule_date, 0, dd._receive_and_bill, po.name)


def _requested(ctx, code):
    """Requested but not yet ordered (Material Requests waiting for their PO)."""
    return flt(frappe.db.sql("""select sum(greatest(i.stock_qty - i.ordered_qty, 0)) from `tabMaterial Request Item` i
        join `tabMaterial Request` m on m.name = i.parent where m.company = %s and m.docstatus = 1
        and m.status in ('Pending', 'Partially Ordered') and i.item_code = %s""", (ctx.company, code))[0][0])


# ============================================================================ first run: pick up open documents
def _adopt_open_documents(ctx, first_day):
    """Follow-ups the previous (one-shot) run dropped at its end date, so pipelines keep moving."""
    from micromax import demo_data as dd

    rnd, c, out = random.Random(f"adopt|{ctx.company}"), ctx.company, []
    since = add_days(first_day, -120)

    def later(lo, hi):
        return add_days(first_day, rnd.randint(lo, hi))

    for name in frappe.get_all("Sales Invoice", {"company": c, "docstatus": 1, "outstanding_amount": [">", 0],
                                                 "is_return": 0, "posting_date": [">=", since]}, pluck="name"):
        if rnd.random() < 0.85:
            out.append((later(0, 40), 5, dd._pay, ("Sales Invoice", name)))
    for name in frappe.get_all("Purchase Invoice", {"company": c, "docstatus": 1, "outstanding_amount": [">", 0],
                                                    "is_return": 0, "posting_date": [">=", since]}, pluck="name"):
        out.append((later(0, 30), 3, dd._pay, ("Purchase Invoice", name)))
    for name in frappe.get_all("Purchase Order", {"company": c, "docstatus": 1, "status": ["in", ["To Receive and Bill", "To Receive"]],
                                                  "transaction_date": [">=", since]}, pluck="name"):
        out.append((later(0, 6), 0, dd._receive_and_bill, (name,)))
    for dn in frappe.get_all("Delivery Note", {"company": c, "docstatus": 1, "status": "To Bill", "is_return": 0,
                                               "posting_date": [">=", since]}, pluck="name"):
        so = frappe.db.get_value("Delivery Note Item", {"parent": dn, "against_sales_order": ["is", "set"]}, "against_sales_order")
        if so and rnd.random() < 0.6:
            out.append((later(0, 10), 4.5, dd._invoice, (dn, so)))
    for wo, st in frappe.get_all("Work Order", {"company": c, "docstatus": 1, "status": ["in", ["Not Started", "In Process"]],
                                               "planned_start_date": [">=", since]}, ["name", "status"], as_list=True):
        out.append((later(0, 2), 2, dd._transfer if st == "Not Started" else dd._run_and_finish, (wo,)))
    planned = set(frappe.get_all("Work Order", {"company": c, "docstatus": ["<", 2], "sales_order": ["is", "set"]}, pluck="sales_order"))
    for so in frappe.get_all("Sales Order", {"company": c, "docstatus": 1, "status": "To Deliver and Bill", "per_delivered": 0,
                                             "transaction_date": [">=", add_days(first_day, -30)]}, pluck="name"):
        if so not in planned and so not in ctx.unplanned:
            ctx.unplanned.append(so)
    for pe in frappe.db.sql("""select pe.name from `tabPayroll Entry` pe where pe.company=%s and pe.docstatus=1
            and pe.end_date >= %s and not exists (select 1 from `tabJournal Entry Account` a join `tabJournal Entry` j on j.name=a.parent
            where a.reference_type='Payroll Entry' and a.reference_name=pe.name and j.voucher_type='Bank Entry' and j.docstatus=1)""",
                            (c, since), pluck=True):
        out.append((later(0, 3), 6, _pay_salaries_ref(), (pe,)))
    return out


def _pay_salaries_ref():
    from micromax import demo_modules
    return demo_modules._pay_salaries


# ============================================================================ state
def _last_generated_day(company):
    v = frappe.db.sql("select max(transaction_date) from `tabSales Order` where company=%s and docstatus=1 and transaction_date<=%s",
                      (company, nowdate()))[0][0]
    return str(v) if v else None


def _load(company):
    raw = frappe.db.get_global(STATE_KEY.format(company))
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _save(company, st):
    frappe.db.set_global(STATE_KEY.format(company), json.dumps(st, default=str))


def _recent(d, keep=600):
    return dict(list(d.items())[-keep:])


def _fn(f):
    return frappe.get_attr(f) if isinstance(f, str) else f


def _encode(e):
    d, order, fn, args = e
    ref = fn if isinstance(fn, str) else f"{fn.__module__}.{fn.__name__}"
    return [str(getdate(d)), order, ref, [{"__date__": str(a)} if hasattr(a, "isoformat") and not isinstance(a, str) else a for a in args]]


def _decode(e):
    d, order, ref, args = e
    return (getdate(d), order, ref, tuple(getdate(a["__date__"]) if isinstance(a, dict) and "__date__" in a else a for a in args))
