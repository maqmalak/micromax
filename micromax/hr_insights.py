"""Attendance and Leave insights for the React HR pages.

Both pages filter by period, department and branch. Attendance.department / Leave Application.department
are often blank on older records, so department and branch come from the employee (joined), and every
query here works on the whole period in SQL rather than on a page of rows in the browser.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, getdate

PRESENT = ("Present", "Work From Home")
MAX_DAYS = 400
DEFAULT_GRACE_MINUTES = 30

# Punctuality from each record's in/out time against its shift (`s` = Shift Type joined on a.shift) and the
# shift's grace periods — so records made before late/early marking was switched on are counted too. The
# native late_entry / early_exit flags always count. Times further than 4 h from the shift's start (check-in) or
# end (check-out) belong to some other shift — mismatched data — and are not classified.
_START = "timestamp(a.attendance_date, s.start_time)"
_END = "(timestamp(a.attendance_date, s.end_time) + interval (s.end_time <= s.start_time) day)"
_IN_WINDOW = f"(a.in_time between {_START} - interval 4 hour and {_START} + interval 4 hour)"
_OUT_WINDOW = f"(a.out_time between {_END} - interval 4 hour and {_END})"
LATE_SQL = f"""(ifnull(a.late_entry, 0) = 1 or ifnull({_IN_WINDOW}
    and a.in_time > {_START} + interval ifnull(s.late_entry_grace_period, 0) minute, 0))"""
EARLY_IN_SQL = f"ifnull({_IN_WINDOW} and a.in_time < {_START}, 0)"
EARLY_OUT_SQL = f"""(ifnull(a.early_exit, 0) = 1 or ifnull({_OUT_WINDOW}
    and a.out_time < {_END} - interval ifnull(s.early_exit_grace_period, 0) minute, 0))"""
LATE_MIN_SQL = f"if({_IN_WINDOW} and a.in_time > {_START}, timestampdiff(minute, {_START}, a.in_time), null)"
EARLY_MIN_SQL = f"if({_OUT_WINDOW} and a.out_time < {_END}, timestampdiff(minute, a.out_time, {_END}), null)"
# Overtime only counts on shifts that allow it: HRMS's own actual_overtime_duration when auto attendance set it;
# otherwise the time checked out after the shift's end (up to 8 h later — beyond that it's another shift's punch);
# otherwise the hours worked beyond the shift's length. Under 15 minutes is noise, not overtime.
_SHIFT_HOURS = f"(timestampdiff(minute, {_START}, {_END}) / 60)"
_AFTER_END = f"(timestampdiff(minute, {_END}, a.out_time) / 60)"
OT_SQL = f"""(case when ifnull(s.allow_overtime, 0) = 0 then 0
    when ifnull(a.actual_overtime_duration, 0) > 0 then a.actual_overtime_duration
    when a.out_time is not null and {_AFTER_END} >= 0.25 and {_AFTER_END} <= 8 then {_AFTER_END}
    when ifnull(a.working_hours, 0) - {_SHIFT_HOURS} >= 0.25 then a.working_hours - {_SHIFT_HOURS}
    else 0 end)"""
SHIFT_JOIN = "left join `tabShift Type` s on s.name = a.shift"


def _check(doctype):
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("Not permitted to read {0}").format(_(doctype)), frappe.PermissionError)


def _period(from_date, to_date):
    f, t = getdate(from_date), getdate(to_date)
    if t < f:
        f, t = t, f
    if (t - f).days > MAX_DAYS:
        frappe.throw(_("Choose a period of at most {0} days").format(MAX_DAYS))
    return f, t


EMPLOYEE_FILTERS = ("gender", "marital_status", "employment_type", "designation")


def _extra(alias, extra):
    """Optional filters: employee attributes (`e.gender` …) and, for attendance, the record's shift."""
    cond, p = "", {}
    for field in EMPLOYEE_FILTERS:
        if extra.get(field):
            cond += f" and e.{field} = %({field})s"
            p[field] = extra[field]
    if extra.get("shift"):
        cond += f" and {alias}.shift = %(shift)s"
        p["shift"] = extra["shift"]
    return cond, p


def _where(alias, company=None, department=None, branch=None, employee=None, search=None):
    """Shared `and ...` conditions on the document (`alias`) and its employee (`e`), plus their params."""
    cond, p = [], {}
    if company:
        cond.append(f"{alias}.company = %(company)s")
        p["company"] = company
    if department:
        cond.append("e.department = %(department)s")
        p["department"] = department
    if branch:
        cond.append("e.branch = %(branch)s")
        p["branch"] = branch
    if employee:
        cond.append(f"{alias}.employee = %(employee)s")
        p["employee"] = employee
    if search:
        cond.append(f"({alias}.employee like %(search)s or {alias}.employee_name like %(search)s)")
        p["search"] = f"%{search}%"
    return "".join(f" and {c}" for c in cond), p


def _active_employees(company=None, department=None, branch=None):
    cond, p = _where("e", company, department, branch)
    return set(frappe.db.sql_list(f"select e.name from `tabEmployee` e where e.status = 'Active' {cond}", p))


def _active_headcount(company=None, department=None, branch=None):
    return len(_active_employees(company, department, branch))


# ----------------------------------------------------------------------------- attendance
@frappe.whitelist()
def attendance_overview(from_date, to_date, company=None, department=None, branch=None, employee=None, search=None, **extra):
    _check("Attendance")
    f, t = _period(from_date, to_date)
    cond, p = _where("a", company, department, branch, employee, search)
    xc, xp = _extra("a", extra)
    cond += xc
    p.update(xp)
    p.update(f=f, t=t)
    base = f"""from `tabAttendance` a left join `tabEmployee` e on e.name = a.employee {SHIFT_JOIN}
        where a.docstatus = 1 and a.attendance_date between %(f)s and %(t)s {cond}"""

    # One pass over the period: on a large Attendance table every extra range scan costs about a second, so
    # fetch the rows once and aggregate here rather than running a query per figure.
    rows = frappe.db.sql(
        f"""select a.employee, a.employee_name, a.attendance_date, a.status, {LATE_SQL},
            {EARLY_OUT_SQL}, ifnull(a.working_hours, 0), ifnull(e.department, ''), e.designation, e.image,
            {EARLY_IN_SQL}, {LATE_MIN_SQL}, {EARLY_MIN_SQL}, {OT_SQL}
        {base}""",
        p,
    )

    keys = {"Present": "present", "Absent": "absent", "On Leave": "on_leave", "Half Day": "half_day", "Work From Home": "wfh"}
    blank = lambda: {"present": 0, "absent": 0, "on_leave": 0, "half_day": 0, "wfh": 0, "late": 0, "early_out": 0, "early_in": 0}  # noqa: E731
    by_status, daily, depts, people = {}, {}, {}, {}
    late = early = early_in = hours_n = 0
    hours = late_min = early_min = 0.0
    late_min_n = early_min_n = 0
    ot_total, ot_records, ot_depts, ot_daily = 0.0, 0, {}, {}
    for emp, emp_name, d, status, is_late, is_early, wh, dept, desig, image, is_early_in, lmin, emin, ot in rows:
        is_late, is_early, is_early_in = int(is_late or 0), int(is_early or 0), int(is_early_in or 0)
        ot = float(ot or 0)
        if ot:
            ot_total += ot
            ot_records += 1
            od = ot_depts.setdefault(dept, {"department": dept, "hours": 0.0, "records": 0, "employees": set()})
            od["hours"] += ot
            od["records"] += 1
            od["employees"].add(emp)
            ot_daily[d] = ot_daily.get(d, 0.0) + ot
        by_status[status] = by_status.get(status, 0) + 1
        key = keys.get(status)
        day_row = daily.setdefault(d, blank())
        if key:
            day_row[key] += 1
        day_row["late"] += is_late
        day_row["early_out"] += is_early
        day_row["early_in"] += is_early_in
        late += is_late
        early += is_early
        early_in += is_early_in
        if lmin is not None and is_late:
            late_min += lmin
            late_min_n += 1
        if emin is not None and is_early:
            early_min += emin
            early_min_n += 1
        if wh:
            hours += wh
            hours_n += 1
        dp = depts.setdefault(dept, {"department": dept, "total": 0, "present": 0.0, "absent": 0, "on_leave": 0, "late": 0, "early_out": 0})
        dp["early_out"] += is_early
        dp["total"] += 1
        dp["present"] += 1 if status in PRESENT else 0.5 if status == "Half Day" else 0
        dp["absent"] += status == "Absent"
        dp["on_leave"] += status == "On Leave"
        dp["late"] += is_late
        pp = people.setdefault(emp, {"employee": emp, "employee_name": emp_name, "department": dept, "designation": desig, "image": image,
                                     "days": 0, "absent": 0, "on_leave": 0, "late": 0, "early_out": 0, "late_minutes": 0,
                                     "ot_hours": 0.0, "ot_days": 0})
        if ot:
            pp["ot_hours"] += ot
            pp["ot_days"] += 1
        pp["early_out"] += is_early
        pp["late_minutes"] += (lmin or 0) if is_late else 0
        pp["days"] += 1
        pp["absent"] += status == "Absent"
        pp["on_leave"] += status == "On Leave"
        pp["late"] += is_late

    days = []
    day = f
    while day <= t:
        days.append({"date": str(day), **daily.get(day, blank()), "ot_hours": round(ot_daily.get(day, 0.0), 2)})
        day += timedelta(days=1)
    top_absent = sorted((x for x in people.values() if x["absent"]), key=lambda x: (-x["absent"], -x["late"]))[:10]
    # Per-employee attendance levels (as on the attendance dashboards the mill used before): how often each
    # employee was present, and how often absent, over the records marked for them in the period.
    present_levels = {"Excellent": 0, "Good": 0, "Fair": 0, "Poor": 0}
    absence_levels = {"Very Low": 0, "Low": 0, "Moderate": 0, "High": 0}
    for x in people.values():
        if not x["days"]:
            continue
        rate = (x["days"] - x["absent"] - x["on_leave"]) / x["days"] * 100
        present_levels["Excellent" if rate >= 95 else "Good" if rate >= 85 else "Fair" if rate >= 75 else "Poor"] += 1
        a_rate = x["absent"] / x["days"] * 100
        absence_levels["Very Low" if a_rate < 5 else "Low" if a_rate < 10 else "Moderate" if a_rate < 20 else "High"] += 1
    top_late = sorted((x for x in people.values() if x["late"] or x["early_out"]), key=lambda x: (-x["late"], -x["early_out"], -x["late_minutes"]))[:10]
    active = _active_employees(company, department, branch) if not (employee or search) else None

    return {
        "from_date": str(f),
        "to_date": str(t),
        "by_status": by_status,
        "records": len(rows),
        "employees": len(people),
        "late": int(late),
        "early": int(early),
        "early_in": int(early_in),
        "avg_late_minutes": round(late_min / late_min_n) if late_min_n else 0,
        "avg_early_minutes": round(early_min / early_min_n) if early_min_n else 0,
        "top_late": top_late,
        "overtime": {
            "enabled_shifts": frappe.db.count("Shift Type", {"allow_overtime": 1}),
            "hours": round(ot_total, 2),
            "records": ot_records,
            "employees": sum(1 for x in people.values() if x["ot_hours"]),
            "by_department": sorted(
                ({**x, "hours": round(x["hours"], 2), "employees": len(x["employees"])} for x in ot_depts.values()),
                key=lambda x: -x["hours"],
            ),
            "top": [
                {k: (round(v, 2) if k == "ot_hours" else v) for k, v in x.items() if k in ("employee", "employee_name", "department", "designation", "image", "ot_hours", "ot_days", "days")}
                for x in sorted((x for x in people.values() if x["ot_hours"]), key=lambda x: -x["ot_hours"])[:10]
            ],
        },
        "present_levels": present_levels,
        "absence_levels": absence_levels,
        "avg_hours": round(hours / hours_n, 2) if hours_n else 0,
        # Attendance also exists for people who have since left: compare marked vs headcount on active staff only.
        "headcount": len(active) if active is not None else None,
        "active_marked": len(active & people.keys()) if active is not None else None,
        "daily": days,
        "by_department": sorted(depts.values(), key=lambda x: -x["total"]),
        "top_absentees": top_absent,
    }


FLAG_SQL = {"late": LATE_SQL, "early_out": EARLY_OUT_SQL, "early_in": EARLY_IN_SQL, "overtime": f"{OT_SQL} > 0"}
ATTENDANCE_SORT = {"attendance_date": "a.attendance_date", "employee_name": "a.employee_name", "status": "a.status", "department": "e.department"}


@frappe.whitelist()
def attendance_records(from_date, to_date, company=None, department=None, branch=None, employee=None, search=None,
                       status=None, start=0, page_length=50, sort_by="attendance_date", sort_order="desc", flag=None, **extra):
    _check("Attendance")
    f, t = _period(from_date, to_date)
    cond, p = _where("a", company, department, branch, employee, search)
    xc, xp = _extra("a", extra)
    cond += xc
    p.update(xp)
    if status:
        cond += " and a.status = %(status)s"
        p["status"] = status
    if flag in FLAG_SQL:
        cond += f" and {FLAG_SQL[flag]}"
    p.update(f=f, t=t, start=cint(start), size=min(cint(page_length) or 50, 500))
    # Join Employee only when its department/branch filter (or sort) needs it — the join makes MariaDB
    # sort the whole period instead of walking the attendance_date index.
    needs_employee = department or branch or sort_by == "department" or any(extra.get(f) for f in EMPLOYEE_FILTERS)
    join = "left join `tabEmployee` e on e.name = a.employee" if needs_employee else ""
    base = f"""from `tabAttendance` a {join} {SHIFT_JOIN}
        where a.docstatus < 2 and a.attendance_date between %(f)s and %(t)s {cond}"""
    order = ATTENDANCE_SORT.get(sort_by, "a.attendance_date")
    direction = "asc" if sort_order == "asc" else "desc"
    rows = frappe.db.sql(
        f"""select a.name, a.employee, a.employee_name, a.attendance_date, a.status, a.leave_type, a.shift,
            a.late_entry, a.early_exit, a.in_time, a.out_time, a.working_hours, a.docstatus,
            {LATE_SQL} late_checkin, {EARLY_OUT_SQL} early_checkout, {EARLY_IN_SQL} early_checkin,
            {LATE_MIN_SQL} late_by, {EARLY_MIN_SQL} early_by, {OT_SQL} overtime_hours, s.start_time shift_start, s.end_time shift_end
        {base} order by {order} {direction}, a.name {direction} limit %(start)s, %(size)s""",
        p, as_dict=True,
    )
    _add_employee_details(rows)
    for r in rows:
        r.shift_start = str(r.shift_start) if r.shift_start is not None else None
        r.shift_end = str(r.shift_end) if r.shift_end is not None else None
    total = frappe.db.sql(f"select count(*) {base}", p)[0][0]
    return {"rows": rows, "total": total}


def _add_employee_details(rows):
    """Department / designation / branch / photo from each row's employee (one query for the page)."""
    emps = {r.employee for r in rows if r.employee}
    if not emps:
        return
    info = {
        e.name: e
        for e in frappe.get_all("Employee", filters={"name": ["in", list(emps)]}, fields=["name", "department", "designation", "branch", "image"])
    }
    for r in rows:
        e = info.get(r.employee) or {}
        r.update(department=e.get("department"), designation=e.get("designation"), branch=e.get("branch"), image=e.get("image"))


# ----------------------------------------------------------------------------- leave
@frappe.whitelist()
def leave_overview(from_date, to_date, company=None, department=None, branch=None, employee=None, search=None, leave_type=None):
    """Leave applications that overlap the period."""
    _check("Leave Application")
    f, t = _period(from_date, to_date)
    cond, p = _where("la", company, department, branch, employee, search)
    if leave_type:
        cond += " and la.leave_type = %(leave_type)s"
        p["leave_type"] = leave_type
    p.update(f=f, t=t)
    base = f"""from `tabLeave Application` la left join `tabEmployee` e on e.name = la.employee
        where la.docstatus < 2 and la.from_date <= %(t)s and la.to_date >= %(f)s {cond}"""
    approved = f"{base} and la.status = 'Approved' and la.docstatus = 1"

    by_status = frappe.db.sql(f"select la.status, count(*) n, sum(la.total_leave_days) days {base} group by la.status", p, as_dict=True)
    by_type = frappe.db.sql(
        f"""select la.leave_type, count(*) applications, sum(la.total_leave_days) days, count(distinct la.employee) employees
        {approved} group by la.leave_type order by days desc""",
        p, as_dict=True,
    )
    by_dept = frappe.db.sql(
        f"""select ifnull(e.department, '') department, sum(la.total_leave_days) days, count(distinct la.employee) employees
        {approved} group by e.department order by days desc""",
        p, as_dict=True,
    )
    top = frappe.db.sql(
        f"""select la.employee, max(la.employee_name) employee_name, max(e.department) department, max(e.designation) designation,
            max(e.image) image, count(*) applications, sum(la.total_leave_days) days
        {approved} group by la.employee order by days desc limit 10""",
        p, as_dict=True,
    )
    spans = frappe.db.sql(f"select la.from_date, la.to_date {approved}", p, as_dict=True)

    # Employees away per day of the period (approved leave covering that day).
    away = {}
    for s in spans:
        day, end = max(s.from_date, f), min(s.to_date, t)
        while day <= end:
            away[day] = away.get(day, 0) + 1
            day += timedelta(days=1)
    daily = []
    day = f
    while day <= t:
        daily.append({"date": str(day), "away": away.get(day, 0)})
        day += timedelta(days=1)

    # Allocation vs approved leave, per leave type, for allocations that overlap the period.
    acond, ap = _where("al", company, department, branch, employee, search)
    if leave_type:
        acond += " and al.leave_type = %(leave_type)s"
        ap["leave_type"] = leave_type
    ap.update(f=f, t=t)
    alloc = frappe.db.sql(
        f"""select al.leave_type, sum(al.total_leaves_allocated) allocated, count(distinct al.employee) employees
        from `tabLeave Allocation` al left join `tabEmployee` e on e.name = al.employee
        where al.docstatus = 1 and al.from_date <= %(t)s and al.to_date >= %(f)s {acond} group by al.leave_type""",
        ap, as_dict=True,
    )

    pending = frappe.db.sql(
        f"""select la.name, la.employee, la.employee_name, la.leave_type, la.from_date, la.to_date, la.total_leave_days,
            la.posting_date, e.department, e.image
        {base} and la.status = 'Open' and la.docstatus = 0 order by la.from_date asc limit 8""",
        p, as_dict=True,
    )

    return {
        "from_date": str(f),
        "to_date": str(t),
        "by_status": {r.status: {"count": r.n, "days": float(r.days or 0)} for r in by_status},
        "by_type": by_type,
        "by_department": by_dept,
        "top_takers": top,
        "daily": daily,
        "allocations": alloc,
        "pending": pending,
        "headcount": _active_headcount(company, department, branch) if not (employee or search) else None,
    }


LEAVE_SORT = {"from_date": "la.from_date", "employee_name": "la.employee_name", "status": "la.status", "total_leave_days": "la.total_leave_days", "leave_type": "la.leave_type"}


@frappe.whitelist()
def leave_records(from_date, to_date, company=None, department=None, branch=None, employee=None, search=None,
                  leave_type=None, status=None, start=0, page_length=50, sort_by="from_date", sort_order="desc"):
    _check("Leave Application")
    f, t = _period(from_date, to_date)
    cond, p = _where("la", company, department, branch, employee, search)
    if leave_type:
        cond += " and la.leave_type = %(leave_type)s"
        p["leave_type"] = leave_type
    if status:
        cond += " and la.status = %(status)s"
        p["status"] = status
    p.update(f=f, t=t, start=cint(start), size=min(cint(page_length) or 50, 500))
    base = f"""from `tabLeave Application` la left join `tabEmployee` e on e.name = la.employee
        where la.docstatus < 2 and la.from_date <= %(t)s and la.to_date >= %(f)s {cond}"""
    order = LEAVE_SORT.get(sort_by, "la.from_date")
    direction = "asc" if sort_order == "asc" else "desc"
    rows = frappe.db.sql(
        f"""select la.name, la.employee, la.employee_name, la.leave_type, la.from_date, la.to_date, la.total_leave_days,
            la.half_day, la.status, la.docstatus, la.posting_date, la.description, la.leave_approver,
            e.department, e.designation, e.branch, e.image
        {base} order by {order} {direction}, la.name limit %(start)s, %(size)s""",
        p, as_dict=True,
    )
    total = frappe.db.sql(f"select count(*) {base}", p)[0][0]
    return {"rows": rows, "total": total}


# ----------------------------------------------------------------------------- shifts
@frappe.whitelist()
def shift_overview(date=None, company=None, department=None, branch=None):
    """One day's shift picture: every shift type with its hours, who is assigned to it that day, their check-ins,
    attendance and late entries, plus the active staff with no shift and the shift requests waiting."""
    _check("Shift Assignment")
    day = getdate(date) if date else getdate()
    cond, p = _where("sa", company, department, branch)
    p["d"] = day
    types = frappe.get_all(
        "Shift Type",
        fields=["name", "start_time", "end_time", "color", "enable_auto_attendance", "late_entry_grace_period",
                "early_exit_grace_period", "enable_late_entry_marking", "enable_early_exit_marking", "holiday_list",
                "begin_check_in_before_shift_start_time", "allow_check_out_after_shift_end_time", "last_sync_of_checkin",
                "allow_overtime", "overtime_type"],
        order_by="start_time asc",
    )
    assigned = frappe.db.sql(
        f"""select sa.shift_type, ifnull(e.department, '') department, count(distinct sa.employee) n
        from `tabShift Assignment` sa join `tabEmployee` e on e.name = sa.employee
        where sa.docstatus = 1 and sa.status = 'Active' and sa.start_date <= %(d)s
            and (sa.end_date is null or sa.end_date >= %(d)s) and e.status = 'Active' {cond}
        group by sa.shift_type, e.department""",
        p, as_dict=True,
    )
    on_shift = frappe.db.sql(
        f"""select count(distinct sa.employee) from `tabShift Assignment` sa join `tabEmployee` e on e.name = sa.employee
        where sa.docstatus = 1 and sa.status = 'Active' and sa.start_date <= %(d)s
            and (sa.end_date is null or sa.end_date >= %(d)s) and e.status = 'Active' {cond}""",
        p,
    )[0][0]

    acond, ap = _where("a", company, department, branch)
    ap["d"] = day
    att = frappe.db.sql(
        f"""select ifnull(a.shift, '') shift, a.status, count(*) n, sum({LATE_SQL}) late, sum({EARLY_OUT_SQL}) early,
            sum({EARLY_IN_SQL}) early_in
        from `tabAttendance` a join `tabEmployee` e on e.name = a.employee {SHIFT_JOIN}
        where a.docstatus = 1 and a.attendance_date = %(d)s and e.status = 'Active' {acond}
        group by a.shift, a.status""",
        ap, as_dict=True,
    )
    ccond, cp = _where("c", None, department, branch)
    cp.update(start=f"{day} 00:00:00", end=f"{day} 23:59:59")
    checkins = frappe.db.sql(
        f"""select ifnull(c.shift, '') shift, count(*) n, count(distinct c.employee) employees,
            sum(c.log_type = 'IN') ins, sum(c.log_type = 'OUT') outs
        from `tabEmployee Checkin` c join `tabEmployee` e on e.name = c.employee
        where c.time between %(start)s and %(end)s {ccond} {'and e.company = %(company)s' if company else ''}
        group by c.shift""",
        {**cp, **({"company": company} if company else {})}, as_dict=True,
    )
    rcond, rp = _where("r", company, department, branch)
    pending = frappe.db.sql(
        f"""select count(*) from `tabShift Request` r join `tabEmployee` e on e.name = r.employee
        where r.docstatus = 0 and r.status = 'Draft' {rcond}""",
        rp,
    )[0][0]

    by_type = {}
    for t in types:
        by_type[t.name] = {**t, "start_time": str(t.start_time), "end_time": str(t.end_time),
                           "last_sync_of_checkin": str(t.last_sync_of_checkin or "") or None,
                           "assigned": 0, "departments": {}, "attendance": {}, "late": 0, "early": 0, "early_in": 0, "checkins": 0, "checked_in": 0}
    for r in assigned:
        b = by_type.get(r.shift_type)
        if b:
            b["assigned"] += r.n
            b["departments"][r.department] = b["departments"].get(r.department, 0) + r.n
    no_shift = {"attendance": {}, "late": 0, "early": 0, "early_in": 0, "checkins": 0, "checked_in": 0}
    for r in att:
        b = by_type.get(r.shift, no_shift)
        b["attendance"][r.status] = b["attendance"].get(r.status, 0) + r.n
        b["late"] += int(r.late or 0)
        b["early"] += int(r.early or 0)
        b["early_in"] += int(r.early_in or 0)
    for r in checkins:
        b = by_type.get(r.shift, no_shift)
        b["checkins"] += r.n
        b["checked_in"] += r.employees

    headcount = _active_headcount(company, department, branch)
    return {
        "date": str(day),
        "shifts": list(by_type.values()),
        "without_shift_type": no_shift,
        "headcount": headcount,
        "on_shift": on_shift,
        "unassigned": max(0, headcount - on_shift),
        "pending_requests": pending,
    }


@frappe.whitelist(methods=["POST"])
def set_shift_grace(minutes=DEFAULT_GRACE_MINUTES, late=1, early=1, shift_types=None):
    """Turn on late-entry / early-exit marking with the same grace period on the given (default: all) shift types."""
    if not frappe.has_permission("Shift Type", "write"):
        frappe.throw(_("Not permitted to change Shift Types"), frappe.PermissionError)
    minutes = cint(minutes)
    if minutes < 0 or minutes > 240:
        frappe.throw(_("Grace period must be between 0 and 240 minutes"))
    names = frappe.parse_json(shift_types) if shift_types else frappe.get_all("Shift Type", pluck="name")
    values = {}
    if cint(late):
        values.update(enable_late_entry_marking=1, late_entry_grace_period=minutes)
    if cint(early):
        values.update(enable_early_exit_marking=1, early_exit_grace_period=minutes)
    # Only these four fields change, and none of them takes part in Shift Type validation (which checks the
    # shift's times and check-in windows) — so write them directly. A full save would also re-run validation
    # on settings the shift already had, and fail on any shift with an unrelated, pre-existing problem.
    for name in names:
        frappe.db.set_value("Shift Type", name, values)
    return {"updated": names, "minutes": minutes}


# ----------------------------------------------------------------------------- setup
@frappe.whitelist()
def setup_status(company=None):
    """Checklist for the HR & Payroll setup page: each master (how many exist) and the gaps that break day-to-day
    work — active employees with no department, shift, leave policy or salary structure, and missing defaults."""
    _check("Employee")
    today = getdate()
    co = {"company": company} if company else {}
    count = lambda dt, filters=None: frappe.db.count(dt, {**(filters or {})}) if frappe.db.exists("DocType", dt) else 0  # noqa: E731
    active = _active_employees(company)
    n_active = len(active)

    def missing(field):
        cond = "and company = %(c)s" if company else ""
        return frappe.db.sql(f"select count(*) from `tabEmployee` where status = 'Active' and ifnull({field}, '') = '' {cond}", {"c": company})[0][0]

    def covered(sql):
        return len(active & set(frappe.db.sql_list(sql, {"d": today, "c": company})))

    ccond = "and company = %(c)s" if company else ""
    with_shift = covered(f"""select employee from `tabShift Assignment` where docstatus = 1 and status = 'Active'
        and start_date <= %(d)s and (end_date is null or end_date >= %(d)s) {ccond}""")
    with_policy = covered("""select employee from `tabLeave Policy Assignment` where docstatus = 1
        and effective_from <= %(d)s and effective_to >= %(d)s""")
    with_structure = covered(f"select employee from `tabSalary Structure Assignment` where docstatus = 1 and from_date <= %(d)s {ccond}")
    company_doc = frappe.db.get_value("Company", company, ["default_holiday_list", "default_payroll_payable_account", "cost_center"], as_dict=True) if company else None
    hr, payroll = frappe.get_single("HR Settings"), frappe.get_single("Payroll Settings")

    steps = [
        # Organisation
        dict(group="Organisation", key="departments", title="Departments", to="/hr/departments", count=count("Department", {**co, "is_group": 0}),
             issue=missing("department"), issue_text="active employees with no department", issue_to="/hr/employees"),
        dict(group="Organisation", key="designations", title="Designations", to="/hr/designations", count=count("Designation"),
             issue=missing("designation"), issue_text="active employees with no designation", issue_to="/hr/employees"),
        dict(group="Organisation", key="branches", title="Branches", to="/hr/branches", count=count("Branch"),
             issue=frappe.db.sql("select count(*) from `tabBranch` where ifnull(latitude, 0) = 0 and ifnull(longitude, 0) = 0")[0][0]
             if frappe.get_meta("Branch").has_field("latitude") else 0,
             issue_text="branches not on the map", issue_to="/hr/branches"),
        dict(group="Organisation", key="employment_types", title="Employment Types", to="/hr/employment-types", count=count("Employment Type"),
             issue=missing("employment_type"), issue_text="active employees with no employment type", issue_to="/hr/employees"),
        dict(group="Organisation", key="grades", title="Employee Grades", to="/hr/employee-grades", count=count("Employee Grade"), optional=True),
        # Time & attendance
        dict(group="Time & attendance", key="holiday_lists", title="Holiday Lists", to="/hr/holiday-lists",
             count=count("Holiday List", {"to_date": [">=", today]}),
             issue=0 if (not company_doc or company_doc.default_holiday_list) else 1, issue_text="company has no default holiday list", issue_to="/settings/company"),
        dict(group="Time & attendance", key="shift_types", title="Shift Types", to="/hr/shift-types", count=count("Shift Type"),
             issue=count("Shift Type", {"enable_late_entry_marking": 0}), issue_text="shifts without late check-in marking", issue_to="/hr/shifts"),
        dict(group="Time & attendance", key="shift_assignments", title="Shift Assignments", to="/hr/shift-assignments", count=with_shift,
             issue=n_active - with_shift, issue_text="active employees with no shift today", issue_to="/hr/shift-assignments", count_label="employees on a shift"),
        dict(group="Time & attendance", key="overtime", title="Overtime Types", to="/payroll/overtime-types", count=count("Overtime Type"), optional=True,
             detail=f"{count('Shift Type', {'allow_overtime': 1})} shifts allow overtime"),
        # Leave
        dict(group="Leave", key="leave_types", title="Leave Types", to="/hr/leave-types", count=count("Leave Type")),
        dict(group="Leave", key="leave_periods", title="Leave Period", to="/hr/leave-periods",
             count=count("Leave Period", {**co, "is_active": 1, "from_date": ["<=", today], "to_date": [">=", today]}),
             count_label="active for today"),
        dict(group="Leave", key="leave_policies", title="Leave Policies", to="/hr/leave-policies", count=count("Leave Policy", {"docstatus": 1})),
        dict(group="Leave", key="leave_policy_assignments", title="Leave Policy Assignments", to="/hr/leave-policy-assignments", count=with_policy,
             issue=n_active - with_policy, issue_text="active employees with no leave policy for today", issue_to="/hr/leave-policy-assignments", count_label="employees covered"),
        # Payroll
        dict(group="Payroll", key="salary_components", title="Salary Components", to="/payroll/salary-components", count=count("Salary Component")),
        dict(group="Payroll", key="salary_structures", title="Salary Structures", to="/payroll/salary-structures", count=count("Salary Structure", {**co, "docstatus": 1, "is_active": "Yes"})),
        dict(group="Payroll", key="structure_assignments", title="Structure Assignments", to="/payroll/salary-structure-assignments", count=with_structure,
             issue=n_active - with_structure, issue_text="active employees with no salary structure", issue_to="/payroll", count_label="employees covered"),
        dict(group="Payroll", key="payroll_period", title="Payroll Period", to="/payroll/periods",
             count=frappe.db.sql(f"select count(*) from `tabPayroll Period` where start_date <= %(d)s and end_date >= %(d)s {ccond}", {"d": today, "c": company})[0][0],
             count_label="covering today"),
        dict(group="Payroll", key="income_tax", title="Income Tax Slabs", to="/payroll/income-tax-slabs", count=count("Income Tax Slab", {"docstatus": 1, "disabled": 0}), optional=True),
        dict(group="Payroll", key="company_defaults", title="Company payroll defaults", to="/settings/company",
             count=int(bool(company_doc and company_doc.default_payroll_payable_account)),
             count_label="payroll payable account set", issue=0 if (not company_doc or company_doc.default_payroll_payable_account) else 1,
             issue_text="no default payroll payable account on the company", issue_to="/settings/company"),
        # Settings
        dict(group="Settings", key="hr_settings", title="HR Settings", to="/hr/settings", count=1, count_label="configured",
             detail=f"Naming by {hr.emp_created_by or '—'} · leave approver {'required' if hr.leave_approver_mandatory_in_leave_application else 'optional'}"),
        dict(group="Settings", key="payroll_settings", title="Payroll Settings", to="/payroll/settings", count=1, count_label="configured",
             detail=f"Working days from {payroll.payroll_based_on or '—'}" + (f", unmarked = {payroll.consider_unmarked_attendance_as}" if payroll.payroll_based_on == "Attendance" else "")),
    ]
    for st in steps:
        st.setdefault("issue", 0)
        st["issue"] = max(0, st["issue"] or 0)
        st["done"] = bool(st["count"]) and not st["issue"]
    return {"headcount": n_active, "steps": steps}


# ----------------------------------------------------------------------------- employee profile
@frappe.whitelist()
def employee_insights(employee, months=12):
    """Everything the employee profile's Insights tab shows, in one call: attendance and punctuality by month,
    a day-by-day heatmap, usual in/out times, leave balances, pay history and latest payslip, current shift and
    salary structure, a career timeline and how the employee compares with their department."""
    if not frappe.has_permission("Employee", "read", doc=employee):
        frappe.throw(_("Not permitted to view this employee"), frappe.PermissionError)
    from frappe.utils import add_days, add_months, flt, get_first_day

    emp = frappe.db.get_value(
        "Employee", employee,
        ["name", "employee_name", "company", "department", "date_of_joining", "relieving_date", "status",
         "final_confirmation_date", "contract_end_date", "date_of_retirement", "ctc", "salary_currency"],
        as_dict=True,
    )
    if not emp:
        frappe.throw(_("Employee {0} not found").format(employee))
    today = getdate()
    months = max(1, min(cint(months) or 12, 36))
    start = get_first_day(add_months(today, -(months - 1)))
    p = {"emp": employee, "f": start, "t": today}

    # ---- attendance by month (+ punctuality + overtime, same rules as the Attendance page)
    month_rows = frappe.db.sql(
        f"""select date_format(a.attendance_date, '%%Y-%%m') m, a.status, count(*) n, sum({LATE_SQL}) late,
            sum({EARLY_OUT_SQL}) early_out, sum({EARLY_IN_SQL}) early_in, sum({OT_SQL}) ot,
            sum(if({LATE_SQL}, ifnull({LATE_MIN_SQL}, 0), 0)) late_min
        from `tabAttendance` a {SHIFT_JOIN}
        where a.docstatus = 1 and a.employee = %(emp)s and a.attendance_date between %(f)s and %(t)s
        group by m, a.status""",
        p, as_dict=True,
    )
    by_month = {}
    m = start
    while m <= today:
        k = str(m)[:7]
        by_month[k] = {"month": k, "present": 0, "absent": 0, "on_leave": 0, "half_day": 0, "wfh": 0,
                       "late": 0, "early_out": 0, "early_in": 0, "ot_hours": 0.0, "late_minutes": 0}
        m = add_months(m, 1)
    key = {"Present": "present", "Absent": "absent", "On Leave": "on_leave", "Half Day": "half_day", "Work From Home": "wfh"}
    for r in month_rows:
        b = by_month.get(r.m)
        if not b:
            continue
        if key.get(r.status):
            b[key[r.status]] += r.n
        b["late"] += int(r.late or 0)
        b["early_out"] += int(r.early_out or 0)
        b["early_in"] += int(r.early_in or 0)
        b["ot_hours"] = round(b["ot_hours"] + flt(r.ot), 2)
        b["late_minutes"] += int(r.late_min or 0)
    monthly = list(by_month.values())
    for b in monthly:
        marked = b["present"] + b["wfh"] + b["half_day"] + b["absent"] + b["on_leave"]
        b["marked"] = marked
        b["rate"] = round((b["present"] + b["wfh"] + b["half_day"] / 2) / marked * 100, 1) if marked else None

    # ---- day-by-day for the heatmap (last ~4 months)
    heat_from = add_days(today, -119)
    days = frappe.db.sql(
        f"""select a.attendance_date d, a.status, {LATE_SQL} late, {EARLY_OUT_SQL} early_out, a.in_time, a.out_time, a.leave_type
        from `tabAttendance` a {SHIFT_JOIN}
        where a.docstatus = 1 and a.employee = %(emp)s and a.attendance_date between %(f)s and %(t)s
        order by a.attendance_date, a.modified desc""",
        {"emp": employee, "f": heat_from, "t": today}, as_dict=True,
    )
    heat = {}
    for d in days:
        heat.setdefault(str(d.d), {"date": str(d.d), "status": d.status, "late": int(d.late or 0), "early_out": int(d.early_out or 0),
                                    "in": str(d.in_time)[11:16] if d.in_time else None, "out": str(d.out_time)[11:16] if d.out_time else None,
                                    "leave_type": d.leave_type})

    # ---- usual arrival / departure (last 90 days with times)
    times = frappe.db.sql(
        """select avg(time_to_sec(time(in_time))) i, avg(time_to_sec(time(out_time))) o, avg(nullif(working_hours, 0)) h, count(in_time) n
        from `tabAttendance` where docstatus = 1 and employee = %(emp)s and attendance_date between %(f)s and %(t)s""",
        {"emp": employee, "f": add_days(today, -89), "t": today}, as_dict=True,
    )[0]
    hhmm = lambda sec: f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}" if sec is not None else None  # noqa: E731

    # ---- department comparison (this year)
    year_start = today.replace(month=1, day=1)
    def rate_for(cond, params):
        r = frappe.db.sql(
            f"""select sum(a.status in ('Present', 'Work From Home')) + sum(a.status = 'Half Day') / 2 p, count(*) n, sum({LATE_SQL}) late
            from `tabAttendance` a {SHIFT_JOIN} where a.docstatus = 1 and a.attendance_date between %(f)s and %(t)s and {cond}""",
            {**params, "f": year_start, "t": today}, as_dict=True,
        )[0]
        return {"rate": round(flt(r.p) / r.n * 100, 1) if r.n else None, "late_per_100": round(flt(r.late) / r.n * 100, 1) if r.n else None, "records": r.n}
    mine = rate_for("a.employee = %(emp)s", {"emp": employee})
    dept = rate_for("a.employee in (select name from `tabEmployee` where department = %(dept)s and status = 'Active')", {"dept": emp.department}) if emp.department else None

    # ---- leave balances (HRMS's own calculation) + recent applications
    balances = []
    try:
        from hrms.hr.doctype.leave_application.leave_application import get_leave_details
        details = get_leave_details(employee, today) or {}
        for lt, v in (details.get("leave_allocation") or {}).items():
            balances.append({"leave_type": lt, **v})
    except Exception:
        frappe.clear_last_message()
    leave_apps = frappe.get_all(
        "Leave Application",
        filters={"employee": employee, "docstatus": ["<", 2]},
        fields=["name", "leave_type", "from_date", "to_date", "total_leave_days", "status", "docstatus"],
        order_by="from_date desc", limit=8,
    )
    leave_by_type = frappe.db.sql(
        """select leave_type, sum(total_leave_days) days from `tabLeave Application`
        where employee = %(emp)s and docstatus = 1 and status = 'Approved' and from_date >= %(f)s group by leave_type""",
        {"emp": employee, "f": year_start}, as_dict=True,
    )

    # ---- pay
    slips = frappe.get_all(
        "Salary Slip",
        filters={"employee": employee, "docstatus": 1},
        fields=["name", "start_date", "end_date", "gross_pay", "total_deduction", "net_pay", "currency", "payment_days", "total_working_days"],
        order_by="start_date desc", limit=months * 3,
    )
    # One point per payroll month (an employee can have more than one slip in a month, e.g. arrears runs).
    pay_months = {}
    for sl in slips:
        k = str(sl.start_date)[:7]
        pm = pay_months.setdefault(k, {"month": k, "gross_pay": 0.0, "total_deduction": 0.0, "net_pay": 0.0, "slips": 0, "currency": sl.currency})
        pm["gross_pay"] += flt(sl.gross_pay)
        pm["total_deduction"] += flt(sl.total_deduction)
        pm["net_pay"] += flt(sl.net_pay)
        pm["slips"] += 1
    pay_by_month = sorted(pay_months.values(), key=lambda x: x["month"])[-months:]
    latest = None
    if slips:
        doc = frappe.get_doc("Salary Slip", slips[0].name)
        latest = {
            "name": doc.name, "start_date": str(doc.start_date), "end_date": str(doc.end_date), "currency": doc.currency,
            "gross_pay": doc.gross_pay, "total_deduction": doc.total_deduction, "net_pay": doc.net_pay,
            "payment_days": doc.payment_days, "total_working_days": doc.total_working_days,
            "earnings": [{"component": d.salary_component, "amount": d.amount} for d in doc.earnings if d.amount],
            "deductions": [{"component": d.salary_component, "amount": d.amount} for d in doc.deductions if d.amount],
        }
    ssa = frappe.get_all(
        "Salary Structure Assignment",
        filters={"employee": employee, "docstatus": 1},
        fields=["name", "salary_structure", "from_date", "base", "variable", "currency", "income_tax_slab"],
        order_by="from_date desc", limit=10,
    )

    # ---- shift
    shift = frappe.db.sql(
        """select sa.name, sa.shift_type, sa.start_date, sa.end_date, st.start_time, st.end_time, st.late_entry_grace_period,
            st.enable_late_entry_marking, st.allow_overtime
        from `tabShift Assignment` sa left join `tabShift Type` st on st.name = sa.shift_type
        where sa.employee = %(emp)s and sa.docstatus = 1 and sa.status = 'Active' and sa.start_date <= %(d)s
            and (sa.end_date is null or sa.end_date >= %(d)s)
        order by sa.start_date desc limit 1""",
        {"emp": employee, "d": today}, as_dict=True,
    )
    last_shift = shift[0] if shift else frappe.db.get_value(
        "Attendance", {"employee": employee, "docstatus": 1, "shift": ["is", "set"]}, "shift", order_by="attendance_date desc")

    # ---- money owed either way
    claims = frappe.db.sql(
        """select sum(if(docstatus = 0, total_claimed_amount, 0)) pending, sum(if(docstatus = 1, total_claimed_amount - ifnull(total_amount_reimbursed, 0), 0)) unpaid
        from `tabExpense Claim` where employee = %(emp)s and docstatus < 2""", {"emp": employee}, as_dict=True)[0]
    advances = frappe.db.sql(
        """select sum(paid_amount - ifnull(claimed_amount, 0) - ifnull(return_amount, 0)) outstanding
        from `tabEmployee Advance` where employee = %(emp)s and docstatus = 1""", {"emp": employee}, as_dict=True)[0]

    # ---- career timeline
    timeline = [{"date": str(emp.date_of_joining), "kind": "joined", "title": "Joined", "detail": emp.company}] if emp.date_of_joining else []
    for r in frappe.get_all("Employee Promotion", filters={"employee": employee, "docstatus": 1}, fields=["name", "promotion_date", "revised_ctc", "current_ctc"]):
        changes = frappe.get_all("Employee Property History", filters={"parent": r.name, "parenttype": "Employee Promotion"}, fields=["property", "current", "new"])
        timeline.append({"date": str(r.promotion_date), "kind": "promotion", "title": "Promoted", "ref": r.name,
                         "detail": "; ".join(f"{c.property}: {c.current or '—'} → {c.new}" for c in changes)})
    for r in frappe.get_all("Employee Transfer", filters={"employee": employee, "docstatus": 1}, fields=["name", "transfer_date", "new_company"]):
        changes = frappe.get_all("Employee Property History", filters={"parent": r.name, "parenttype": "Employee Transfer"}, fields=["property", "current", "new"])
        timeline.append({"date": str(r.transfer_date), "kind": "transfer", "title": "Transferred", "ref": r.name,
                         "detail": "; ".join(f"{c.property}: {c.current or '—'} → {c.new}" for c in changes) or r.new_company})
    # Only real pay changes: re-submitting the same structure and base every month isn't a revision.
    prev = None
    for r in reversed(ssa):
        if prev and prev.salary_structure == r.salary_structure and flt(prev.base) == flt(r.base):
            continue
        change = ""
        if prev and flt(prev.base) and flt(r.base) != flt(prev.base):
            change = f" ({(flt(r.base) - flt(prev.base)) / flt(prev.base) * 100:+.1f}%)"
        timeline.append({"date": str(r.from_date), "kind": "salary", "title": "Salary structure" if prev is None else "Salary revised",
                         "ref": r.name, "detail": f"{r.salary_structure} · base {flt(r.base):,.0f} {r.currency or ''}".strip() + change})
        prev = r
    for field, kind, title in (("final_confirmation_date", "confirmed", "Confirmed"), ("contract_end_date", "contract", "Contract ends"),
                               ("relieving_date", "left", "Relieved"), ("date_of_retirement", "retire", "Retirement")):
        if emp.get(field):
            timeline.append({"date": str(emp[field]), "kind": kind, "title": title})
    timeline.sort(key=lambda x: x["date"])

    return {
        "employee": employee,
        "from_date": str(start),
        "to_date": str(today),
        "monthly": monthly,
        "heatmap": list(heat.values()),
        "heatmap_from": str(heat_from),
        "usual": {"in": hhmm(times.i), "out": hhmm(times.o), "hours": round(flt(times.h), 1) if times.h else None, "days": times.n},
        "compare": {"employee": mine, "department": dept, "department_name": emp.department},
        "leave_balances": balances,
        "leave_applications": leave_apps,
        "leave_by_type": leave_by_type,
        "pay_by_month": pay_by_month,
        "latest_slip": latest,
        "structures": ssa,
        "shift": shift[0] if shift else None,
        "last_shift": last_shift if not shift else None,
        "claims": {"pending": flt(claims.pending), "unpaid": flt(claims.unpaid), "advance_outstanding": flt(advances.outstanding)},
        "timeline": timeline,
    }


# ----------------------------------------------------------------------------- salary slips
def _income_tax_components():
    """Salary components that are income tax: flagged as the income-tax component, computed from taxable salary,
    or typed Tax. (The slip's own current_month_income_tax is HRMS's projection, and goes negative once more tax
    has been deducted than the year's slab total — so tax is read from these deduction lines instead.)"""
    has_it = frappe.get_meta("Salary Component").has_field("is_income_tax_component")
    has_ct = frappe.get_meta("Salary Component").has_field("component_type")
    cond = ["variable_based_on_taxable_salary = 1"]
    if has_it:
        cond.append("is_income_tax_component = 1")
    if has_ct:
        cond.append("component_type = 'Tax'")
    return set(frappe.db.sql_list(f"select name from `tabSalary Component` where type = 'Deduction' and ({' or '.join(cond)})"))


@frappe.whitelist()
def salary_slip_insights(name):
    """The payslip page: the slip with each component's full-month vs paid amount, the attendance behind its
    payment days, what changed since the previous slip, how it compares with the department, year-to-date
    totals, income tax and the employee's last 12 months of pay."""
    doc = frappe.get_doc("Salary Slip", name)
    doc.check_permission("read")
    from frappe.utils import add_days, flt

    emp = frappe.db.get_value("Employee", doc.employee, ["image", "designation", "branch", "date_of_joining", "bank_name", "bank_ac_no", "salary_mode"], as_dict=True) or {}
    tax_components = _income_tax_components()
    comp_type = lambda rows: [  # noqa: E731
        {
            "component": r.salary_component, "abbr": r.abbr, "amount": flt(r.amount), "full_amount": flt(r.default_amount) or flt(r.amount),
            "additional": flt(r.additional_amount), "ytd": flt(r.year_to_date), "statistical": r.statistical_component,
            "depends_on_payment_days": r.depends_on_payment_days, "tax_applicable": r.is_tax_applicable,
            "is_income_tax": int(r.salary_component in tax_components),
        }
        for r in rows if flt(r.amount) or flt(r.default_amount)
    ]
    earnings, deductions = comp_type(doc.earnings), comp_type(doc.deductions)

    # Previous slip of the same employee → per-component change.
    prev_name = frappe.db.get_value(
        "Salary Slip", {"employee": doc.employee, "docstatus": 1, "start_date": ["<", doc.start_date], "name": ["!=", doc.name]},
        "name", order_by="start_date desc",
    )
    previous = None
    if prev_name:
        prev = frappe.get_doc("Salary Slip", prev_name)
        prev_amounts = {("e", r.salary_component): flt(r.amount) for r in prev.earnings}
        prev_amounts.update({("d", r.salary_component): flt(r.amount) for r in prev.deductions})
        changes = []
        for kind, rows in (("e", earnings), ("d", deductions)):
            seen = set()
            for r in rows:
                seen.add(r["component"])
                before = prev_amounts.get((kind, r["component"]), 0)
                if round(r["amount"] - before, 2):
                    changes.append({"component": r["component"], "kind": kind, "before": before, "now": r["amount"]})
            for (k, c), before in prev_amounts.items():
                if k == kind and c not in seen and before:
                    changes.append({"component": c, "kind": kind, "before": before, "now": 0})
        previous = {"name": prev.name, "start_date": str(prev.start_date), "gross_pay": flt(prev.gross_pay), "net_pay": flt(prev.net_pay),
                    "total_deduction": flt(prev.total_deduction), "payment_days": flt(prev.payment_days), "changes": changes}

    # The attendance behind the payment days.
    att = frappe.db.sql(
        """select status, count(*) n from `tabAttendance` where docstatus = 1 and employee = %s and attendance_date between %s and %s group by status""",
        (doc.employee, doc.start_date, doc.end_date), as_dict=True,
    )
    leave = frappe.db.sql(
        """select leave_type, sum(total_leave_days) days from `tabLeave Application` where docstatus = 1 and status = 'Approved'
        and employee = %s and from_date <= %s and to_date >= %s group by leave_type""",
        (doc.employee, doc.end_date, doc.start_date), as_dict=True,
    )
    # Pay lost to unpaid days: full-month amount less what was paid, on components prorated by payment days.
    lost = sum(r["full_amount"] - r["amount"] for r in earnings if r["depends_on_payment_days"] and r["full_amount"] > r["amount"])

    # Department comparison for the same payroll month.
    dept = None
    if doc.department:
        d = frappe.db.sql(
            """select count(*) n, avg(net_pay) net, avg(gross_pay) gross, sum(net_pay < %(net)s) below
            from `tabSalary Slip` where docstatus = 1 and department = %(dept)s and start_date = %(start)s""",
            {"dept": doc.department, "start": doc.start_date, "net": doc.net_pay}, as_dict=True,
        )[0]
        if d.n:
            dept = {"department": doc.department, "slips": d.n, "avg_net": flt(d.net), "avg_gross": flt(d.gross),
                    "percentile": round(flt(d.below) / d.n * 100) if d.n > 1 else None}

    # Year to date (payroll period if the slip has one, else calendar year).
    ytd_from = frappe.db.get_value("Payroll Period", doc.current_payroll_period, "start_date") if doc.get("current_payroll_period") else None
    ytd_from = ytd_from or getdate(doc.start_date).replace(month=1, day=1)
    ytd = frappe.db.sql(
        """select count(*) n, sum(gross_pay) gross, sum(total_deduction) ded, sum(net_pay) net from `tabSalary Slip`
        where docstatus = 1 and employee = %s and start_date between %s and %s""",
        (doc.employee, ytd_from, doc.start_date), as_dict=True,
    )[0]

    # Income tax: what this slip and the year actually deducted (from the tax lines), next to HRMS's annual figures.
    tax_list = list(tax_components) or [""]
    tax_ytd = frappe.db.sql(
        """select ifnull(sum(d.amount), 0) from `tabSalary Detail` d join `tabSalary Slip` s on s.name = d.parent and d.parenttype = 'Salary Slip'
        where s.docstatus = 1 and s.employee = %s and s.start_date between %s and %s and d.parentfield = 'deductions' and d.salary_component in %s""",
        (doc.employee, ytd_from, doc.start_date, tax_list),
    )[0][0]
    this_tax = sum(r["amount"] for r in deductions if r["is_income_tax"])
    if doc.docstatus != 1:
        tax_ytd = flt(tax_ytd) + this_tax  # a draft isn't in the submitted total yet
    annual_tax, till_date = flt(doc.get("total_income_tax")), flt(doc.get("income_tax_deducted_till_date"))
    slab = frappe.db.get_value(
        "Salary Structure Assignment", {"employee": doc.employee, "docstatus": 1, "from_date": ["<=", doc.start_date]}, "income_tax_slab", order_by="from_date desc")
    # A newer submitted slab already in effect for this slip's date but not the one assigned → likely out of date.
    newer_slab = None
    if slab:
        cur_from = frappe.db.get_value("Income Tax Slab", slab, "effective_from")
        newer_slab = frappe.db.get_value(
            "Income Tax Slab",
            {"docstatus": 1, "disabled": 0, "name": ["!=", slab], "effective_from": ["between", [add_days(cur_from, 1), doc.start_date]] if cur_from else ["<=", doc.start_date]},
            ["name", "effective_from"], order_by="effective_from desc", as_dict=True,
        ) if cur_from else None
    income_tax = {
        "newer_slab": {"name": newer_slab.name, "effective_from": str(newer_slab.effective_from)} if newer_slab else None,
        "this_slip": this_tax,
        "components": [r["component"] for r in deductions if r["is_income_tax"]],
        "effective_rate": round(this_tax / flt(doc.gross_pay) * 100, 2) if flt(doc.gross_pay) else None,
        "ytd": flt(tax_ytd),
        "slab": slab,
        "annual_taxable": flt(doc.get("annual_taxable_amount")),
        "annual_tax": annual_tax,
        "deducted_till_date": till_date,
        # Positive: still to deduct this year; negative: more already deducted than the slab total for the year.
        "balance": round(annual_tax - till_date, 2) if annual_tax or till_date else None,
    }

    history = frappe.db.sql(
        """select date_format(start_date, '%%Y-%%m') month, sum(gross_pay) gross, sum(total_deduction) ded, sum(net_pay) net, count(*) slips
        from `tabSalary Slip` where docstatus = 1 and employee = %s and start_date <= %s and start_date > date_sub(%s, interval 12 month)
        group by month order by month""",
        (doc.employee, doc.start_date, doc.start_date), as_dict=True,
    )

    return {
        "slip": {
            k: (str(doc.get(k)) if k in ("start_date", "end_date", "posting_date") and doc.get(k) else doc.get(k))
            for k in ("name", "employee", "employee_name", "company", "department", "designation", "branch", "status", "docstatus",
                      "posting_date", "start_date", "end_date", "salary_structure", "payroll_entry", "payroll_frequency", "currency",
                      "total_working_days", "payment_days", "leave_without_pay", "absent_days", "unmarked_days", "gross_pay",
                      "total_deduction", "net_pay", "rounded_total", "total_in_words", "mode_of_payment", "bank_name", "bank_account_no",
                      "journal_entry", "ctc", "annual_taxable_amount", "income_tax_deducted_till_date", "current_month_income_tax",
                      "future_income_tax_deductions", "total_income_tax", "gross_year_to_date", "year_to_date", "salary_withholding")
        },
        "employee": {"image": emp.get("image"), "designation": emp.get("designation"), "branch": emp.get("branch"),
                     "date_of_joining": str(emp.get("date_of_joining") or ""), "salary_mode": emp.get("salary_mode"),
                     "bank_name": emp.get("bank_name"), "bank_ac_no": emp.get("bank_ac_no")},
        "earnings": earnings,
        "deductions": deductions,
        "previous": previous,
        "attendance": {r.status: r.n for r in att},
        "leave": leave,
        "pay_lost_to_unpaid_days": round(lost, 2),
        "income_tax": income_tax,
        "department": dept,
        "ytd": {"from": str(ytd_from), "slips": ytd.n, "gross": flt(ytd.gross), "deductions": flt(ytd.ded), "net": flt(ytd.net)},
        "history": history,
    }


def _slip_where(from_date, to_date, company, department, branch, employee, search, payroll_entry):
    f, t = _period(from_date, to_date)
    cond, p = [], {"f": f, "t": t}
    if company:
        cond.append("s.company = %(company)s"); p["company"] = company
    if department:
        cond.append("s.department = %(department)s"); p["department"] = department
    if branch:
        cond.append("s.branch = %(branch)s"); p["branch"] = branch
    if employee:
        cond.append("s.employee = %(employee)s"); p["employee"] = employee
    if payroll_entry:
        cond.append("s.payroll_entry = %(payroll_entry)s"); p["payroll_entry"] = payroll_entry
    if search:
        cond.append("(s.employee like %(search)s or s.employee_name like %(search)s or s.name like %(search)s)"); p["search"] = f"%{search}%"
    return f, t, "".join(f" and {c}" for c in cond), p


@frappe.whitelist()
def salary_slips_overview(from_date, to_date, company=None, department=None, branch=None, employee=None, search=None, payroll_entry=None):
    """Payroll insights for slips whose period starts in [from, to]: totals, change vs the same-length period
    before, department split, net-pay bands, component mix, top earners, status and pay lost to unpaid days."""
    _check("Salary Slip")
    from frappe.utils import flt
    f, t, cond, p = _slip_where(from_date, to_date, company, department, branch, employee, search, payroll_entry)
    base = f"from `tabSalary Slip` s where s.docstatus < 2 and s.start_date between %(f)s and %(t)s {cond}"
    sub = f"{base} and s.docstatus = 1"

    tot = frappe.db.sql(
        f"""select count(*) n, count(distinct s.employee) employees, sum(s.base_gross_pay) gross, sum(s.base_total_deduction) ded,
            sum(s.base_net_pay) net, sum(s.payment_days) paid_days, sum(s.total_working_days) work_days,
            sum(s.leave_without_pay) lwp, sum(s.absent_days) absent {sub}""", p, as_dict=True)[0]
    # Comparison period: the same number of calendar months before when the range is whole months (payroll
    # periods start on the 1st, so shifting by days would miss them), otherwise the same number of days before.
    from frappe.utils import add_months, get_last_day
    if f.day == 1 and t == getdate(get_last_day(t)):
        n_months = (t.year - f.year) * 12 + t.month - f.month + 1
        pp = {**p, "f": getdate(add_months(f, -n_months)), "t": f - timedelta(days=1)}
    else:
        days = (t - f).days + 1
        pp = {**p, "f": f - timedelta(days=days), "t": f - timedelta(days=1)}
    prev = frappe.db.sql(f"""select count(*) n, sum(s.base_gross_pay) gross, sum(s.base_net_pay) net, sum(s.base_total_deduction) ded
        from `tabSalary Slip` s where s.docstatus = 1 and s.start_date between %(f)s and %(t)s {cond}""", pp, as_dict=True)[0]
    status = frappe.db.sql(f"select s.status, count(*) n, sum(s.base_net_pay) net {base} group by s.status", p, as_dict=True)
    by_dept = frappe.db.sql(f"""select ifnull(s.department, '') department, count(*) n, sum(s.base_gross_pay) gross, sum(s.base_net_pay) net,
        avg(s.base_net_pay) avg_net {sub} group by s.department order by gross desc""", p, as_dict=True)
    bands = frappe.db.sql(f"""select case when s.base_net_pay < 15000 then 0 when s.base_net_pay < 25000 then 1 when s.base_net_pay < 35000 then 2
            when s.base_net_pay < 50000 then 3 when s.base_net_pay < 100000 then 4 else 5 end b, count(*) n {sub} group by b""", p, as_dict=True)
    band_labels = ["< 15k", "15–25k", "25–35k", "35–50k", "50–100k", "100k +"]
    comp = frappe.db.sql(f"""select d.parentfield pf, d.salary_component c, sum(d.amount) amt, count(distinct d.parent) slips
        from `tabSalary Detail` d join `tabSalary Slip` s on s.name = d.parent and d.parenttype = 'Salary Slip'
        where s.docstatus = 1 and s.start_date between %(f)s and %(t)s {cond} and ifnull(d.statistical_component, 0) = 0
        group by d.parentfield, d.salary_component order by amt desc""", p, as_dict=True)
    tax_list = list(_income_tax_components()) or [""]
    tax_total = frappe.db.sql(f"""select ifnull(sum(d.amount), 0), count(distinct d.parent) from `tabSalary Detail` d
        join `tabSalary Slip` s on s.name = d.parent and d.parenttype = 'Salary Slip'
        where s.docstatus = 1 and s.start_date between %(f)s and %(t)s {cond} and d.parentfield = 'deductions'
            and d.amount > 0 and d.salary_component in %(tax)s""", {**p, "tax": tax_list})[0]
    lost = frappe.db.sql(f"""select sum(d.default_amount - d.amount) from `tabSalary Detail` d
        join `tabSalary Slip` s on s.name = d.parent and d.parenttype = 'Salary Slip'
        where s.docstatus = 1 and s.start_date between %(f)s and %(t)s {cond} and d.parentfield = 'earnings'
            and d.depends_on_payment_days = 1 and d.default_amount > d.amount""", p)[0][0]
    top = frappe.db.sql(f"""select s.employee, max(s.employee_name) employee_name, max(s.department) department, max(s.designation) designation,
        sum(s.base_net_pay) net, sum(s.base_gross_pay) gross, count(*) slips {sub} group by s.employee order by net desc limit 8""", p, as_dict=True)
    images = dict(frappe.get_all("Employee", filters={"name": ["in", [x.employee for x in top] or [""]]}, fields=["name", "image"], as_list=True))
    for x in top:
        x.image = images.get(x.employee)
    trend = frappe.db.sql(f"""select date_format(s.start_date, '%%Y-%%m') month, sum(s.base_gross_pay) gross, sum(s.base_net_pay) net,
        sum(s.base_total_deduction) ded, count(*) n from `tabSalary Slip` s
        where s.docstatus = 1 and s.start_date > date_sub(%(t)s, interval 12 month) and s.start_date <= %(t)s {cond}
        group by month order by month""", p, as_dict=True)

    return {
        "from_date": str(f), "to_date": str(t),
        "totals": {"slips": tot.n, "employees": tot.employees, "gross": flt(tot.gross), "deductions": flt(tot.ded), "net": flt(tot.net),
                   "payment_days": flt(tot.paid_days), "working_days": flt(tot.work_days), "lwp": flt(tot.lwp), "absent": flt(tot.absent),
                   "pay_lost": flt(lost), "income_tax": flt(tax_total[0]), "taxed_slips": tax_total[1]},
        "previous": {"slips": prev.n, "gross": flt(prev.gross), "net": flt(prev.net), "deductions": flt(prev.ded), "from": str(pp["f"]), "to": str(pp["t"])},
        "status": {r.status: {"count": r.n, "net": flt(r.net)} for r in status},
        "by_department": by_dept,
        "bands": [{"band": band_labels[i], "slips": next((r.n for r in bands if r.b == i), 0)} for i in range(len(band_labels))],
        "earnings": [{"component": r.c, "amount": flt(r.amt), "slips": r.slips} for r in comp if r.pf == "earnings"],
        "deductions": [{"component": r.c, "amount": flt(r.amt), "slips": r.slips} for r in comp if r.pf == "deductions"],
        "top_earners": top,
        "trend": trend,
    }


SLIP_SORT = {"start_date": "s.start_date", "employee_name": "s.employee_name", "net_pay": "s.base_net_pay", "gross_pay": "s.base_gross_pay",
             "payment_days": "s.payment_days", "status": "s.status", "department": "s.department"}


@frappe.whitelist()
def salary_slip_records(from_date, to_date, company=None, department=None, branch=None, employee=None, search=None, payroll_entry=None,
                        status=None, start=0, page_length=50, sort_by="start_date", sort_order="desc"):
    _check("Salary Slip")
    f, t, cond, p = _slip_where(from_date, to_date, company, department, branch, employee, search, payroll_entry)
    if status:
        cond += " and s.status = %(status)s"
        p["status"] = status
    p.update(start=cint(start), size=min(cint(page_length) or 50, 500))
    base = f"from `tabSalary Slip` s where s.docstatus < 2 and s.start_date between %(f)s and %(t)s {cond}"
    order = SLIP_SORT.get(sort_by, "s.start_date")
    direction = "asc" if sort_order == "asc" else "desc"
    rows = frappe.db.sql(
        f"""select s.name, s.employee, s.employee_name, s.department, s.designation, s.branch, s.start_date, s.end_date, s.status, s.docstatus,
            s.currency, s.gross_pay, s.total_deduction, s.net_pay, s.payment_days, s.total_working_days, s.leave_without_pay, s.absent_days,
            s.payroll_entry, s.salary_structure
        {base} order by {order} {direction}, s.name {direction} limit %(start)s, %(size)s""",
        p, as_dict=True,
    )
    _add_employee_details(rows)
    total = frappe.db.sql(f"select count(*) {base}", p)[0][0]
    return {"rows": rows, "total": total}


# ----------------------------------------------------------------------------- payroll run
@frappe.whitelist()
def payroll_run_insights(name):
    """One payroll run, analysed: totals and how they moved since the same scope (company / branch / department /
    designation / grade) was paid the period before, who joined or dropped out, department split, components,
    pay bands, the biggest individual changes, attendance-driven pay loss, tax, accounting and anything to fix
    before paying (missing slips, zero / negative net, no bank account)."""
    pe = frappe.get_doc("Payroll Entry", name)
    pe.check_permission("read")
    from frappe.utils import add_days, add_months, flt

    slips = frappe.db.sql(
        """select s.name, s.employee, s.employee_name, s.department, s.designation, s.docstatus, s.status, s.gross_pay, s.total_deduction,
            s.net_pay, s.payment_days, s.total_working_days, s.absent_days, s.leave_without_pay, s.journal_entry, s.currency
        from `tabSalary Slip` s where s.payroll_entry = %s and s.docstatus < 2""",
        name, as_dict=True,
    )
    live = [s for s in slips if s.docstatus == 1] or slips  # drafts until the slips are submitted
    sum_ = lambda rows, k: sum(flt(r[k]) for r in rows)  # noqa: E731
    totals = {
        "slips": len(slips), "submitted": sum(1 for s in slips if s.docstatus == 1), "draft": sum(1 for s in slips if s.docstatus == 0),
        "employees_in_run": len(pe.employees), "gross": sum_(live, "gross_pay"), "deductions": sum_(live, "total_deduction"), "net": sum_(live, "net_pay"),
        "payment_days": sum_(live, "payment_days"), "working_days": sum_(live, "total_working_days"), "absent": sum_(live, "absent_days"),
        "lwp": sum_(live, "leave_without_pay"),
    }
    slip_names = [s.name for s in live] or [""]
    tax_list = list(_income_tax_components()) or [""]
    comp = frappe.db.sql(
        """select d.parentfield pf, d.salary_component c, sum(d.amount) amt, count(distinct d.parent) n, sum(greatest(ifnull(d.default_amount, 0) - d.amount, 0) * d.depends_on_payment_days) lost
        from `tabSalary Detail` d where d.parenttype = 'Salary Slip' and d.parent in %s and ifnull(d.statistical_component, 0) = 0 and d.amount != 0
        group by d.parentfield, d.salary_component order by amt desc""",
        (slip_names,), as_dict=True,
    )
    totals["income_tax"] = sum(flt(r.amt) for r in comp if r.pf == "deductions" and r.c in tax_list)
    totals["pay_lost"] = sum(flt(r.lost) for r in comp if r.pf == "earnings")

    # ---- same scope, previous period (all slips, not one run: a period can be paid in several runs)
    scope, sp = ["s.company = %(company)s"], {"company": pe.company}
    for field in ("branch", "department", "designation"):
        if pe.get(field):
            scope.append(f"s.{field} = %({field})s")
            sp[field] = pe.get(field)
    months = max(1, round(((getdate(pe.end_date) - getdate(pe.start_date)).days + 1) / 30))
    prev_from = getdate(add_months(pe.start_date, -months)) if pe.payroll_frequency in (None, "", "Monthly") else add_days(pe.start_date, -((getdate(pe.end_date) - getdate(pe.start_date)).days + 1))
    sp.update(pf=prev_from, pt=add_days(pe.start_date, -1))
    prev_rows = frappe.db.sql(
        f"""select s.employee, max(s.employee_name) employee_name, max(s.department) department, sum(s.gross_pay) gross, sum(s.net_pay) net,
            sum(s.total_deduction) ded, sum(s.payment_days) paid
        from `tabSalary Slip` s where s.docstatus = 1 and s.start_date between %(pf)s and %(pt)s and {' and '.join(scope)} group by s.employee""",
        sp, as_dict=True,
    )
    prev_by = {r.employee: r for r in prev_rows}
    now_by = {}
    for s_ in live:
        n = now_by.setdefault(s_.employee, {"employee": s_.employee, "employee_name": s_.employee_name, "department": s_.department,
                                            "designation": s_.designation, "net": 0.0, "gross": 0.0, "paid": 0.0, "working": 0.0})
        n["net"] += flt(s_.net_pay)
        n["gross"] += flt(s_.gross_pay)
        n["paid"] += flt(s_.payment_days)
        n["working"] += flt(s_.total_working_days)
    joined = [now_by[e] for e in now_by if e not in prev_by]
    left = [prev_by[e] for e in prev_by if e not in now_by]
    changes = []
    for e, n in now_by.items():
        p = prev_by.get(e)
        if p and flt(p.net) and abs(n["net"] - flt(p.net)) >= 1:
            changes.append({**n, "prev_net": flt(p.net), "change": n["net"] - flt(p.net), "pct": (n["net"] - flt(p.net)) / flt(p.net) * 100,
                            "prev_paid": flt(p.paid)})
    changes.sort(key=lambda x: x["change"])
    previous = {
        "from": str(prev_from), "to": str(sp["pt"]), "employees": len(prev_rows),
        "gross": sum(flt(r.gross) for r in prev_rows), "net": sum(flt(r.net) for r in prev_rows), "deductions": sum(flt(r.ded) for r in prev_rows),
    } if prev_rows else None
    stayed = [e for e in now_by if e in prev_by]
    bridge = None
    if previous:
        # Net-pay bridge: previous total → leavers out → joiners in → change for people paid both times → this run.
        bridge = {
            "previous": previous["net"],
            "leavers": -sum(flt(r.net) for r in left),
            "joiners": sum(n["net"] for n in joined),
            "existing": sum(now_by[e]["net"] - flt(prev_by[e].net) for e in stayed),
            "current": totals["net"],
        }

    # ---- department split and pay bands
    depts = {}
    for s_ in live:
        dd = depts.setdefault(s_.department or "", {"department": s_.department or "", "slips": 0, "gross": 0.0, "net": 0.0, "paid": 0.0, "working": 0.0})
        dd["slips"] += 1
        dd["gross"] += flt(s_.gross_pay)
        dd["net"] += flt(s_.net_pay)
        dd["paid"] += flt(s_.payment_days)
        dd["working"] += flt(s_.total_working_days)
    prev_dept = {}
    for r in prev_rows:
        prev_dept[r.department or ""] = prev_dept.get(r.department or "", 0.0) + flt(r.net)
    for k, dd in depts.items():
        dd["prev_net"] = prev_dept.get(k)
    bands_def = [("< 15k", 15000), ("15–25k", 25000), ("25–35k", 35000), ("35–50k", 50000), ("50–100k", 100000), ("100k +", float("inf"))]
    bands = [{"band": b, "slips": 0} for b, _ in bands_def]
    for s_ in live:
        for i, (_, hi) in enumerate(bands_def):
            if flt(s_.net_pay) < hi:
                bands[i]["slips"] += 1
                break
    nets = sorted(flt(s_.net_pay) for s_ in live)
    median = nets[len(nets) // 2] if nets else 0

    # ---- things to fix before paying
    have_slip = {s_.employee for s_ in slips}
    missing = [{"employee": r.employee, "employee_name": r.employee_name, "department": r.department} for r in pe.employees if r.employee not in have_slip]
    zero_net = [{"name": s_.name, "employee": s_.employee, "employee_name": s_.employee_name, "net_pay": flt(s_.net_pay)} for s_ in live if flt(s_.net_pay) <= 0]
    no_days = [{"name": s_.name, "employee": s_.employee, "employee_name": s_.employee_name} for s_ in live if not flt(s_.payment_days)]
    emp_info = {e.name: e for e in frappe.get_all("Employee", filters={"name": ["in", list(now_by) or [""]]}, fields=["name", "salary_mode", "bank_name", "bank_ac_no", "image", "status"])}
    no_bank = [{"employee": e, "employee_name": now_by[e]["employee_name"]} for e, info in emp_info.items() if info.salary_mode == "Bank" and not info.bank_ac_no]
    no_mode = [{"employee": e, "employee_name": now_by[e]["employee_name"]} for e, info in emp_info.items() if not info.salary_mode]
    not_active = [{"employee": e, "employee_name": now_by[e]["employee_name"], "status": info.status} for e, info in emp_info.items() if info.status != "Active"]
    pay_modes = {}
    for info in emp_info.values():
        mode = info.salary_mode or "Not set"
        pay_modes[mode] = pay_modes.get(mode, 0) + 1
    withheld = [r.employee for r in pe.employees if r.get("is_salary_withheld")]

    # ---- accounting
    jes = sorted({s_.journal_entry for s_ in live if s_.journal_entry})
    je_rows = frappe.get_all("Journal Entry", filters={"name": ["in", jes or [""]]}, fields=["name", "voucher_type", "posting_date", "total_debit", "docstatus"])
    bank = frappe.db.sql(
        """select je.name, je.posting_date, je.total_debit, je.docstatus from `tabJournal Entry` je
        where je.docstatus < 2 and je.voucher_type = 'Bank Entry' and exists (select 1 from `tabJournal Entry Account` a
            where a.parent = je.name and a.reference_type = 'Payroll Entry' and a.reference_name = %s)""",
        name, as_dict=True,
    )

    top = sorted(now_by.values(), key=lambda x: -x["net"])[:5]
    for x in top + changes[:5] + changes[-5:] + joined[:8]:
        x["image"] = emp_info.get(x["employee"], {}).get("image") if x["employee"] in emp_info else None

    return {
        "run": {"name": pe.name, "status": pe.status, "docstatus": pe.docstatus, "start_date": str(pe.start_date), "end_date": str(pe.end_date),
                "posting_date": str(pe.posting_date), "frequency": pe.payroll_frequency, "branch": pe.branch, "department": pe.department,
                "designation": pe.designation, "currency": pe.currency, "payment_account": pe.payment_account},
        "totals": totals,
        "median_net": median,
        "previous": previous,
        "bridge": bridge,
        "joined": joined[:20],
        "joined_count": len(joined),
        "left": [{"employee": r.employee, "employee_name": r.employee_name, "department": r.department, "net": flt(r.net)} for r in left[:20]],
        "left_count": len(left),
        "biggest_drops": [c for c in changes[:5] if c["change"] < 0],
        "biggest_rises": [c for c in reversed(changes[-5:]) if c["change"] > 0],
        "by_department": sorted(depts.values(), key=lambda x: -x["gross"]),
        "earnings": [{"component": r.c, "amount": flt(r.amt), "slips": r.n, "lost": flt(r.lost)} for r in comp if r.pf == "earnings"],
        "deductions": [{"component": r.c, "amount": flt(r.amt), "slips": r.n, "is_income_tax": int(r.c in tax_list)} for r in comp if r.pf == "deductions"],
        "bands": bands,
        "top_earners": top,
        "pay_modes": pay_modes,
        "issues": {"missing_slips": missing[:20], "missing_count": len(missing), "zero_net": zero_net[:20], "no_payment_days": no_days[:20],
                   "no_bank_account": no_bank[:20], "no_bank_count": len(no_bank), "no_salary_mode": no_mode[:20], "no_salary_mode_count": len(no_mode),
                   "not_active": not_active[:20], "not_active_count": len(not_active), "withheld": withheld},
        "accounting": {"accrual": je_rows, "bank_entries": bank},
    }
