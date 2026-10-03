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
