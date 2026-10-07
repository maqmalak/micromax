"""Overtime for the demo company: overtime types + component, overtime hours on attendance, Overtime Slips, payroll.

    # all in one: remove the month's payroll, add overtime, run payroll again (loans / advances deducted again)
    bench --site <site> execute micromax.demo_overtime.redo_month --kwargs "{'company': '...', 'month': '2026-09', 'confirm': 1}"

HRMS overtime: Attendance carries the day's overtime type and hours; an Overtime Slip collects an employee's month,
values it (hourly rate of Basic Salary × multiplier) and on submit creates an Additional Salary in the Overtime
component, which the payroll run picks up.
"""

import random

import frappe
from frappe.utils import cint, flt, getdate, now

COMPONENT = "Overtime"
TYPES = {
    # Factories Act (Pakistan): overtime at twice the ordinary rate
    "Normal Overtime": {"standard_multiplier": 2.0, "maximum_overtime_hours_allowed": 4, "applicable_for_weekend": 0,
                        "applicable_for_public_holiday": 0},
    "Holiday Overtime": {"standard_multiplier": 2.0, "maximum_overtime_hours_allowed": 8, "applicable_for_weekend": 1,
                         "weekend_multiplier": 2.5, "applicable_for_public_holiday": 1, "public_holiday_multiplier": 2.5},
}
PRODUCTION = ("Ring Spinning", "Auto Cone", "Blow Room", "Drawing", "Maintenance")


def _has_structure(employee, on):
    """Overtime is valued from the salary structure in force on the month's first day (joiners mid-month have none)."""
    from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import get_assigned_salary_structure

    return bool(get_assigned_salary_structure(employee, on))


def setup(company):
    """Overtime salary component (earning, on the same expense account as Basic Salary) and the overtime types."""
    account = frappe.db.get_value("Salary Component Account", {"parent": "Basic Salary", "company": company}, "account")
    if not frappe.db.exists("Salary Component", COMPONENT):
        frappe.get_doc({"doctype": "Salary Component", "salary_component": COMPONENT, "salary_component_abbr": "OT", "type": "Earning",
                        "depends_on_payment_days": 0, "is_tax_applicable": 1, "description": "Overtime (from Overtime Slips)",
                        "accounts": [{"company": company, "account": account}] if account else []}).insert(ignore_permissions=True)
    elif account and not frappe.db.exists("Salary Component Account", {"parent": COMPONENT, "company": company}):
        comp = frappe.get_doc("Salary Component", COMPONENT)
        comp.append("accounts", {"company": company, "account": account})
        comp.save(ignore_permissions=True)
    for name, vals in TYPES.items():
        if not frappe.db.exists("Overtime Type", name):
            frappe.get_doc({"doctype": "Overtime Type", "__newname": name, "name": name, "overtime_salary_component": COMPONENT,
                            "overtime_calculation_method": "Salary Component Based",
                            "applicable_salary_component": [{"salary_component": "Basic Salary"}], **vals}).insert(ignore_permissions=True,
                                                                                                                  set_name=name)
    frappe.db.commit()
    print(f"Overtime component '{COMPONENT}' and types {', '.join(TYPES)} ready")


def mark_attendance(company, month, staff=20, share=0.35):
    """Overtime hours on present days of production staff: 1–4 h on about a third of their days (Holiday Overtime on
    days that fall on a holiday, otherwise Normal Overtime)."""
    start = getdate(f"{month}-01")
    rnd = random.Random(f"ot|{company}|{month}")
    hol_list = frappe.db.get_value("Company", company, "default_holiday_list")
    holidays = set(frappe.get_all("Holiday", {"parent": hol_list}, pluck="holiday_date")) if hol_list else set()
    emps = [e for e in frappe.get_all("Employee", {"company": company, "status": "Active"}, ["name", "department"])
            if any(p in (e.department or "") for p in PRODUCTION) and _has_structure(e.name, start)]
    rnd.shuffle(emps)
    chosen = emps[: cint(staff)]
    from frappe.utils import get_last_day

    end = get_last_day(start)
    marked = 0
    for e in chosen:
        for a in frappe.get_all("Attendance", {"employee": e.name, "docstatus": 1, "status": "Present",
                                                "attendance_date": ["between", [start, end]]}, ["name", "attendance_date", "working_hours"]):
            if rnd.random() > share:
                continue
            hours = rnd.choice([1, 1.5, 2, 2, 2.5, 3, 3, 4])
            ot_type = "Holiday Overtime" if a.attendance_date in holidays else "Normal Overtime"
            frappe.db.set_value("Attendance", a.name, {"overtime_type": ot_type, "actual_overtime_duration": hours,
                                                        "standard_working_hours": 8, "working_hours": flt(a.working_hours or 8) + hours},
                                update_modified=False)
            marked += 1
    frappe.db.commit()
    print(f"Overtime marked on {marked} attendance days for {len(chosen)} employees")
    return [e.name for e in chosen]


def make_slips(company, month, employees=None):
    """One Overtime Slip per employee for the month (hours pulled from attendance), submitted → Additional Salary."""
    from frappe.utils import get_last_day

    start = getdate(f"{month}-01")
    end = get_last_day(start)
    if employees is None:
        employees = frappe.db.sql("""select distinct employee from `tabAttendance` where company = %s and docstatus = 1
            and attendance_date between %s and %s and ifnull(overtime_type, '') != '' and actual_overtime_duration > 0""",
                                  (company, start, end), pluck=True)
    made, total = [], 0.0
    for emp in employees:
        if frappe.db.exists("Overtime Slip", {"employee": emp, "start_date": start, "docstatus": ["!=", 2]}):
            continue
        if not _has_structure(emp, start):           # joined mid-month: no structure to value overtime — drop the hours
            frappe.db.sql("""update `tabAttendance` set overtime_type = null, actual_overtime_duration = 0
                where employee = %s and attendance_date between %s and %s""", (emp, start, end))
            frappe.db.commit()
            continue
        slip = frappe.get_doc({"doctype": "Overtime Slip", "employee": emp, "company": company, "posting_date": end,
                               "start_date": start, "end_date": end})
        slip.flags.ignore_permissions = True
        # rows from attendance before the first save (the details table is mandatory)
        slip.create_overtime_details_row_for_attendance(slip.get_attendance_records())
        if not slip.overtime_details:
            continue
        slip.total_overtime_duration = sum(flt(d.overtime_duration) for d in slip.overtime_details)
        slip.insert()
        slip.submit()
        amount = flt(frappe.db.get_value("Additional Salary", {"ref_doctype": "Overtime Slip", "ref_docname": slip.name, "docstatus": 1},
                                         "amount"))
        total += amount
        made.append((slip.name, slip.employee_name, slip.total_overtime_duration, amount))
        frappe.db.commit()
    for m in made:
        print(f"{m[0]}  {m[1]:<22} {m[2]:>5g} h   Rs {m[3]:>9,.0f}")
    print(f"Overtime slips: {len(made)}, overtime pay Rs {total:,.0f}")
    return made


def redo_month(company, month, confirm=0):
    """Remove the month's payroll, add overtime (types, attendance hours, slips), run payroll again."""
    from micromax import demo_daily, demo_payroll

    if not cint(confirm):
        print("Pass confirm=1: removes the month's payroll run and vouchers, adds overtime slips, runs payroll again.")
        return
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    since = now()
    demo_payroll.remove_payroll(company, month, confirm=1)
    setup(company)
    employees = mark_attendance(company, month)
    make_slips(company, month, employees)
    demo_daily._stamp_users(frappe._dict(rnd=random.Random(1)), since)
    pe = demo_payroll.rerun_payroll(company, month)
    rows = frappe.db.sql("""select count(*), sum(sd.amount) from `tabSalary Detail` sd join `tabSalary Slip` ss on ss.name = sd.parent
        where ss.payroll_entry = %s and sd.parentfield = 'earnings' and sd.salary_component = %s""", (pe, COMPONENT))[0]
    print(f"Overtime on salary slips: {rows[0]} slips, Rs {flt(rows[1]):,.0f}")
    return pe
