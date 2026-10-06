"""Demo projects for the spinning mill: phases (group tasks), dependent tasks and milestones, in every state.

    bench --site micromaxerp execute micromax.demo_projects.generate --kwargs "{'company': 'MicroMax Spinning Demo'}"
    bench --site demo        execute micromax.demo_projects.generate --kwargs "{'company': 'MicroMax Erp Pvt Ltd.'}"

Five projects, dated around today so they always show the full range:
  Solar Power Plant 1.2 MW            closed, finished on time
  Export Program (customer)           closed, finished late
  Ring Frame Modernisation            running, on schedule
  ERP Rollout Phase 2 — HR & Payroll  running, late (past its end date, overdue tasks)
  Blowroom Dust Extraction Upgrade    open, not started
Re-running skips projects that already exist. The daily job (micromax.demo_daily) moves the running ones on:
tasks start when their dependencies finish, progress rises, and finish on (or, for the late project, after) time.
"""

import random

import frappe
from frappe.utils import add_days, cint, flt, getdate, nowdate

TAG = "MicroMax demo data"

# subject, phase, start offset (days from project start), duration (days), depends on (subjects), milestone
PROJECTS = [
    {
        "name": "Solar Power Plant 1.2 MW", "profile": "done_ontime", "start": -210, "type": "Internal", "priority": "High",
        "department": "Engineering", "cost": 165_000_000,
        "tasks": [
            ("Energy audit & load study", "Feasibility", 0, 14, [], False),
            ("Net-metering application (DISCO)", "Feasibility", 10, 30, ["Energy audit & load study"], False),
            ("Feasibility approved", "Feasibility", 40, 0, ["Net-metering application (DISCO)"], True),
            ("EPC contractor selection", "Procurement", 41, 20, ["Feasibility approved"], False),
            ("Panels & inverters delivered", "Procurement", 61, 35, ["EPC contractor selection"], False),
            ("Roof structure & mounting", "Installation", 70, 40, ["EPC contractor selection"], False),
            ("Panel installation & cabling", "Installation", 110, 45, ["Panels & inverters delivered", "Roof structure & mounting"], False),
            ("Grid synchronisation & testing", "Commissioning", 156, 14, ["Panel installation & cabling"], False),
            ("Plant commissioned", "Commissioning", 171, 0, ["Grid synchronisation & testing"], True),
        ],
    },
    {
        "name": "Export Program — {customer}", "profile": "done_late", "start": -150, "type": "External", "priority": "High",
        "department": "Sales", "cost": 12_500_000,
        "tasks": [
            ("Buyer specification & sampling", "Development", 0, 15, [], False),
            ("Lab dips & yarn trials approved", "Development", 15, 12, ["Buyer specification & sampling"], True),
            ("LC opened by buyer", "Commercial", 20, 10, ["Buyer specification & sampling"], False),
            ("Production — lot 1", "Production", 30, 25, ["Lab dips & yarn trials approved", "LC opened by buyer"], False),
            ("Production — lot 2", "Production", 55, 25, ["Production — lot 1"], False),
            ("Pre-shipment inspection", "Shipping", 80, 5, ["Production — lot 2"], False),
            ("Shipment & documents to bank", "Shipping", 86, 10, ["Pre-shipment inspection"], False),
            ("Program closed (payment realised)", "Shipping", 100, 0, ["Shipment & documents to bank"], True),
        ],
    },
    {
        "name": "Ring Frame Modernisation (RF 13–20)", "profile": "running", "start": -60, "type": "Internal", "priority": "High",
        "department": "Production", "cost": 48_000_000,
        "tasks": [
            ("Machine survey & spare parts list", "Planning", 0, 10, [], False),
            ("Vendor quotations & PO", "Planning", 8, 15, ["Machine survey & spare parts list"], False),
            ("Plan approved", "Planning", 24, 0, ["Vendor quotations & PO"], True),
            ("Compact spinning kits — RF 13–16", "Retrofit", 25, 30, ["Plan approved"], False),
            ("Compact spinning kits — RF 17–20", "Retrofit", 50, 30, ["Compact spinning kits — RF 13–16"], False),
            ("Drive & inverter upgrade", "Retrofit", 40, 35, ["Plan approved"], False),
            ("Trial runs & yarn quality check", "Commissioning", 80, 14, ["Compact spinning kits — RF 17–20", "Drive & inverter upgrade"], False),
            ("Operator training", "Commissioning", 85, 10, ["Compact spinning kits — RF 13–16"], False),
            ("Handover to production", "Commissioning", 95, 0, ["Trial runs & yarn quality check", "Operator training"], True),
        ],
    },
    {
        "name": "ERP Rollout Phase 2 — HR & Payroll", "profile": "late", "start": -120, "type": "Internal", "priority": "Medium",
        "department": "Human Resources", "cost": 6_500_000,
        "tasks": [
            ("Process mapping workshops", "Design", 0, 14, [], False),
            ("Salary structures & tax slabs", "Design", 14, 21, ["Process mapping workshops"], False),
            ("Design signed off", "Design", 36, 0, ["Salary structures & tax slabs"], True),
            ("Biometric devices & check-in sync", "Build", 37, 25, ["Design signed off"], False),
            ("Employee master data migration", "Build", 37, 30, ["Design signed off"], False),
            ("Parallel payroll run (2 months)", "Testing", 68, 30, ["Employee master data migration", "Biometric devices & check-in sync"], False),
            ("Leave & attendance policies go-live", "Go-live", 90, 10, ["Parallel payroll run (2 months)"], False),
            ("Go-live — payroll on ERP", "Go-live", 105, 0, ["Leave & attendance policies go-live"], True),
        ],
    },
    {
        "name": "Blowroom Dust Extraction Upgrade", "profile": "not_started", "start": 12, "type": "Internal", "priority": "Medium",
        "department": "Engineering", "cost": 9_800_000,
        "tasks": [
            ("Air-flow survey", "Survey", 0, 7, [], False),
            ("Filter house design", "Survey", 7, 14, ["Air-flow survey"], False),
            ("Design approved", "Survey", 21, 0, ["Filter house design"], True),
            ("Ducting fabrication", "Installation", 22, 25, ["Design approved"], False),
            ("Fans & filter installation", "Installation", 40, 15, ["Ducting fabrication"], False),
            ("Dust level test & handover", "Installation", 56, 5, ["Fans & filter installation"], True),
        ],
    },
]


def _user():
    for u in (frappe.conf.get("demo_hr_approver"), "director@micromaxonline.uk"):
        if u and frappe.db.get_value("User", u, "enabled"):
            return u
    return "Administrator"


def _department(company, name):
    return frappe.db.get_value("Department", {"company": company, "department_name": ["like", f"%{name}%"]}, "name")


def _customer(company):
    from micromax import demo_data as dd

    t = dd.load_template()
    for c in t.customers:
        if frappe.db.exists("Customer", c):
            return c
    return frappe.db.get_value("Customer", {"disabled": 0}, "name")


def generate(company=None, today=None):
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    company = company or frappe.conf.get("demo_daily_company") or frappe.defaults.get_global_default("company")
    today = getdate(today or nowdate())
    rnd, made = random.Random(f"projects|{company}"), []
    customer = _customer(company)
    for spec in PROJECTS:
        title = spec["name"].format(customer=customer or "Export Buyer")
        if frappe.db.exists("Project", {"project_name": title, "company": company}):
            continue
        made.append(_project(company, spec, title, customer, today, rnd))
        frappe.db.commit()
    print(f"Projects created for {company}: {made or 'none (already there)'}")
    return made


def _project(company, spec, title, customer, today, rnd):
    start = add_days(today, spec["start"])
    end = add_days(start, max(o + d for _s, _p, o, d, _dep, _m in spec["tasks"]))
    proj = frappe.get_doc({
        "doctype": "Project", "project_name": title, "company": company, "status": "Open",
        "expected_start_date": start, "expected_end_date": end, "priority": spec["priority"],
        "project_type": spec["type"] if frappe.db.exists("Project Type", spec["type"]) else None,
        "percent_complete_method": "Task Completion", "department": _department(company, spec["department"]),
        "customer": customer if spec["type"] == "External" else None, "estimated_costing": spec["cost"],
        "notes": f"<p>{TAG}</p>",
    })
    proj.insert(ignore_permissions=True)

    profile = spec["profile"]
    # phases as group tasks
    phases = {}
    for _s, phase, _o, _d, _dep, _m in spec["tasks"]:
        if phase not in phases:
            offs = [(o, o + d) for s, p, o, d, _x, _y in spec["tasks"] if p == phase]
            phases[phase] = _task(proj, phase, add_days(start, min(a for a, _b in offs)), add_days(start, max(b for _a, b in offs)),
                                  is_group=1)
    names = {}
    for subject, phase, off, dur, deps, milestone in spec["tasks"]:
        t_start, t_end = add_days(start, off), add_days(start, off + dur)
        names[subject] = _task(proj, subject, t_start, t_end, parent=phases[phase], milestone=milestone,
                               depends=[names[d] for d in deps])

    # status by profile and dates (dependencies first: they're in spec order)
    late_shift = {}
    for subject, phase, off, dur, deps, milestone in spec["tasks"]:
        t_start, t_end = getdate(add_days(start, off)), getdate(add_days(start, off + dur))
        shift = max([late_shift.get(d, 0) for d in deps] or [0])
        if profile == "done_late" and not milestone and rnd.random() < 0.6:
            shift += rnd.randint(2, 8)                         # the delays add up along the chain
        late_shift[subject] = shift
        if profile in ("done_ontime", "done_late"):
            _set(names[subject], "Completed", done_on=add_days(t_end, shift if profile == "done_late" else -rnd.randint(0, 2)),
                 started=t_start)
        elif profile == "not_started":
            continue
        else:
            _advance(names[subject], t_start, t_end, today, late=profile == "late", rnd=rnd)
    if profile in ("running", "late"):
        _mark_overdue(proj.name, today)
    for ph in phases.values():
        _roll_up_group(ph)

    proj.reload()
    if profile in ("done_ontime", "done_late"):
        last = max(getdate(frappe.db.get_value("Task", n, "completed_on") or end) for n in names.values())
        proj.db_set({"status": "Completed", "actual_start_date": start, "actual_end_date": last, "percent_complete": 100})
    elif profile != "not_started":
        proj.db_set({"actual_start_date": start})
    _update_percent(proj.name)
    return proj.name


def _task(proj, subject, start, end, parent=None, is_group=0, milestone=False, depends=None):
    t = frappe.get_doc({
        "doctype": "Task", "subject": subject, "project": proj.name, "company": proj.company, "status": "Open",
        "exp_start_date": f"{getdate(start)} 09:00:00", "exp_end_date": f"{getdate(end)} 17:00:00",
        "is_group": is_group, "parent_task": parent, "is_milestone": 1 if milestone else 0,
        "priority": "High" if milestone else "Medium", "department": proj.department,
        "expected_time": 0 if milestone or is_group else max(8, (getdate(end) - getdate(start)).days * 6),
        "depends_on": [{"task": d} for d in (depends or [])],
        "description": f"<p>{subject} — {proj.project_name}</p>",
    })
    t.flags.ignore_permissions = True
    t.insert()
    return t.name


def _set(name, status, done_on=None, started=None, progress=None):
    vals = {"status": status}
    if status == "Completed":
        vals.update({"progress": 100, "completed_on": done_on, "act_end_date": done_on, "completed_by": _user()})
    if started:
        vals["act_start_date"] = started
    if progress is not None:
        vals["progress"] = progress
    frappe.db.set_value("Task", name, vals, update_modified=False)


def _deps_done(name):
    deps = frappe.get_all("Task Depends On", {"parent": name}, pluck="task")
    return all(frappe.db.get_value("Task", d, "status") in ("Completed", "Cancelled") for d in deps)


def _advance(name, t_start, t_end, today, late=False, rnd=None):
    """Status of one task on `today`: done, in progress, overdue (late project) or not started."""
    rnd = rnd or random.Random(name)
    if not _deps_done(name) or t_start > today:
        return
    # Late project: work that fell due in the last ~5 weeks is still open (overdue); older work got done, late.
    if late and t_end < today and (today - t_end).days <= 35 and rnd.random() < 0.6:
        _set(name, "Overdue", started=t_start, progress=rnd.randint(40, 85))
        _assign(name)
        return
    if t_end < today:
        _set(name, "Completed", done_on=add_days(t_end, rnd.randint(1, 6) if late else -rnd.randint(0, 2)), started=t_start)
        return
    span = max(1, (t_end - t_start).days)
    _set(name, "Working", started=t_start, progress=min(95, round(100 * (today - t_start).days / span * (0.8 if late else 1))))
    _assign(name)


def _assign(name):
    from micromax.demo_notify import assign

    t = frappe.db.get_value("Task", name, ["subject", "exp_end_date", "status"], as_dict=True)
    assign("Task", name, f"{t.subject}{' — overdue' if t.status == 'Overdue' else ''}", date=getdate(t.exp_end_date),
           priority="High" if t.status == "Overdue" else "Medium")


def _roll_up_group(name):
    kids = frappe.get_all("Task", {"parent_task": name}, ["status", "progress", "completed_on", "act_start_date"])
    if not kids:
        return
    if all(k.status == "Completed" for k in kids):
        _set(name, "Completed", done_on=max(getdate(k.completed_on) for k in kids if k.completed_on),
             started=min((getdate(k.act_start_date) for k in kids if k.act_start_date), default=None))
    elif any(k.status == "Overdue" for k in kids):
        _set(name, "Overdue", progress=round(sum(flt(k.progress) for k in kids) / len(kids)))
    elif any(k.status in ("Working", "Completed") for k in kids):
        _set(name, "Working", progress=round(sum(flt(k.progress) for k in kids) / len(kids)))


def _mark_overdue(project, today):
    """Open / working tasks past their end date are Overdue (what ERPNext's daily job does)."""
    for name in frappe.get_all("Task", {"project": project, "is_group": 0, "status": ["in", ["Open", "Working"]],
                                        "exp_end_date": ["<", f"{getdate(today)} 00:00:00"]}, pluck="name"):
        frappe.db.set_value("Task", name, "status", "Overdue", update_modified=False)
        _assign(name)


def _update_percent(project):
    total = frappe.db.count("Task", {"project": project, "is_group": 0})
    done = frappe.db.count("Task", {"project": project, "is_group": 0, "status": "Completed"})
    frappe.db.set_value("Project", project, "percent_complete", round(100 * done / total, 1) if total else 0, update_modified=False)


# ============================================================================ daily (from micromax.demo_daily)
def daily(ctx, day):
    """Move the running demo projects on: tasks start when dependencies finish, progress, finish (late project:
    a few slip to Overdue). Closed / not-started projects start or close on their dates."""
    day = getdate(day)
    for p in frappe.get_all("Project", {"company": ctx.company, "notes": ["like", f"%{TAG}%"], "status": "Open"},
                            ["name", "expected_start_date", "expected_end_date"]):
        late = "ERP Rollout" in p.name
        for t in frappe.get_all("Task", {"project": p.name, "is_group": 0, "status": ["in", ["Open", "Working", "Overdue"]]},
                                ["name", "exp_start_date", "exp_end_date", "status"], order_by="exp_start_date"):
            s, e = getdate(t.exp_start_date), getdate(t.exp_end_date)
            if t.status == "Overdue":
                if ctx.rnd.random() < 0.15:                                  # overdue work eventually gets done
                    _set(t.name, "Completed", done_on=day)
                continue
            _advance(t.name, s, e, day, late=late, rnd=ctx.rnd)
        _mark_overdue(p.name, day)
        for g in frappe.get_all("Task", {"project": p.name, "is_group": 1}, pluck="name"):
            _roll_up_group(g)
        _update_percent(p.name)
        if not frappe.db.exists("Task", {"project": p.name, "is_group": 0, "status": ["not in", ["Completed", "Cancelled"]]}):
            frappe.db.set_value("Project", p.name, {"status": "Completed", "actual_end_date": day}, update_modified=False)
            ctx.bump("Project completed")


def purge(company=None, confirm=0):
    """Delete the demo projects (tagged) and their tasks / to-dos — e.g. to regenerate them."""
    if not cint(confirm):
        print("Pass confirm=1 to delete the demo projects.")
        return
    company = company or frappe.conf.get("demo_daily_company")
    for p in frappe.get_all("Project", {"company": company, "notes": ["like", f"%{TAG}%"]}, pluck="name"):
        for t in frappe.get_all("Task", {"project": p}, pluck="name", order_by="lft desc"):
            frappe.db.delete("ToDo", {"reference_type": "Task", "reference_name": t})
            frappe.db.delete("Task Depends On", {"parent": t})
            frappe.db.delete("Task", {"name": t})
        frappe.delete_doc("Project", p, force=True, ignore_permissions=True)
    frappe.db.commit()
    print(f"Demo projects removed for {company}")
