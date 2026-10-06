"""Redo one month's payroll for the demo company, with salary loans and advances recovered from salary.

    # 1. remove the month's payroll run, its salary slips and vouchers (accrual + salary payment)
    bench --site <site> execute micromax.demo_payroll.remove_payroll --kwargs "{'company': '...', 'month': '2026-09', 'confirm': 1}"
    # 2. salary loans (instalments from that month) and advances (some recovered in that month's salary)
    bench --site <site> execute micromax.demo_payroll.seed_loans --kwargs "{'company': '...', 'month': '2026-09'}"
    # 3. run the month again (slips pick up the loan / advance deductions automatically) and pay salaries
    bench --site <site> execute micromax.demo_payroll.rerun_payroll --kwargs "{'company': '...', 'month': '2026-09'}"
    # or all three:
    bench --site <site> execute micromax.demo_payroll.redo_month --kwargs "{'company': '...', 'month': '2026-09', 'confirm': 1}"
"""

import math
import random

import frappe
from frappe.utils import add_days, cint, flt, get_first_day, get_last_day, getdate, now, nowdate

ADVANCE_COMPONENT = "Advance Recovery"
ADVANCE_PURPOSES = ["Travel advance — buyer visit", "Festival advance (Eid)", "Medical advance", "Site visit expenses",
                    "Utility bill advance", "Uniform and safety shoes"]


def _month(month):
    start = get_first_day(getdate(f"{month}-01"))
    return start, get_last_day(start)


def remove_payroll(company, month, confirm=0):
    """Cancel and delete the month's Payroll Entry: HRMS deletes its salary slips and cancels its journal entries
    (accrual and salary payment), which are then deleted too."""
    if not cint(confirm):
        print("Pass confirm=1 to delete the payroll run, its salary slips and vouchers.")
        return
    frappe.set_user("Administrator")
    start, end = _month(month)
    for pe_name in frappe.get_all("Payroll Entry", {"company": company, "start_date": start, "end_date": end, "docstatus": ["<", 2]}, pluck="name"):
        jes = frappe.db.sql("""select distinct j.name from `tabJournal Entry` j join `tabJournal Entry Account` a on a.parent = j.name
            where a.reference_type = 'Payroll Entry' and a.reference_name = %s""", pe_name, pluck=True)
        pe = frappe.get_doc("Payroll Entry", pe_name)
        if pe.docstatus == 1:
            pe.flags.ignore_permissions = True
            pe._cancel()        # direct: HRMS's cancel() queues a background job when the run has over 50 salary slips
        for je in jes:
            if frappe.db.get_value("Journal Entry", je, "docstatus") == 1:
                frappe.get_doc("Journal Entry", je).cancel()
            frappe.delete_doc("Journal Entry", je, force=True, ignore_permissions=True)
        for slip in frappe.get_all("Salary Slip", {"payroll_entry": pe_name}, pluck="name"):
            if frappe.db.get_value("Salary Slip", slip, "docstatus") == 1:
                frappe.get_doc("Salary Slip", slip).cancel()
            frappe.delete_doc("Salary Slip", slip, force=True, ignore_permissions=True)
        frappe.delete_doc("Payroll Entry", pe_name, force=True, ignore_permissions=True)
        frappe.db.commit()
        print(f"Removed {pe_name}: salary slips and {len(jes)} journal entries ({', '.join(jes)})")


def _advance_component(company, account):
    if not frappe.db.exists("Salary Component", ADVANCE_COMPONENT):
        frappe.get_doc({"doctype": "Salary Component", "salary_component": ADVANCE_COMPONENT, "salary_component_abbr": "ADVR",
                        "type": "Deduction", "depends_on_payment_days": 0, "description": "Employee advance recovered from salary",
                        "accounts": [{"company": company, "account": account}]}).insert(ignore_permissions=True)
    elif not frappe.db.exists("Salary Component Account", {"parent": ADVANCE_COMPONENT, "company": company}):
        comp = frappe.get_doc("Salary Component", ADVANCE_COMPONENT)
        comp.append("accounts", {"company": company, "account": account})
        comp.save(ignore_permissions=True)
    return ADVANCE_COMPONENT


def seed_loans(company, month, loans=5, advances=4):
    """Salary loans (eligible borrower + guarantor, approved by the director, paid in the first days of the month,
    instalments from the same month's payroll) and plain advances (some recovered in full from that month's salary)."""
    from hrms.overrides.employee_payment_entry import get_payment_entry_for_employee

    from micromax import demo_daily
    from mm_core.approvals import act
    from mm_core.loans import check_eligibility, fix_advance_account

    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    start, end = _month(month)
    rnd = random.Random(f"loans|{company}|{month}")
    fix_advance_account(company)
    acc = frappe.db.get_value("Company", company, "default_employee_advance_account")
    cc = frappe.db.get_value("Company", company, "cost_center")
    currency = frappe.get_cached_value("Company", company, "default_currency")
    since = now()
    staff = frappe.get_all("Employee", {"company": company, "status": "Active", "employment_type": "Full-time",
                                         "date_of_joining": ["<", start]}, pluck="name")
    rnd.shuffle(staff)

    def pay(name, day):
        pe = get_payment_entry_for_employee("Employee Advance", name)
        pe.posting_date, pe.reference_no, pe.reference_date = day, f"ADV-{name[-5:]}", day
        pe.cost_center = pe.cost_center or cc
        pe.flags.ignore_permissions = True
        pe.insert()
        pe.submit()

    made, borrowers = [], set()
    base_keys = {"active", "type", "service", "salary", "open_loans", "history"}
    for emp in staff:
        if len([m for m in made if m[0] == "loan"]) >= cint(loans):
            break
        r0 = check_eligibility(emp)
        if not all(c["ok"] for c in r0["checks"] if c["key"] in base_keys) or r0["limits"]["eligible_amount"] < 20000:
            continue
        lim = r0["limits"]
        amount = max(10000, round(lim["eligible_amount"] * rnd.uniform(0.35, 0.9) / 5000) * 5000)
        months = min(lim["max_months"], max(math.ceil(amount / max(1, lim["max_installment"])), rnd.randint(4, lim["max_months"])))
        guarantor = next((g for g in staff if g != emp and g not in borrowers and all(
            c["ok"] for c in check_eligibility(emp, amount, months, g)["checks"] if c["key"].startswith("g_"))), None)
        if not guarantor:
            continue
        day = add_days(start, rnd.randint(1, 6))
        doc = frappe.get_doc({"doctype": "Employee Advance", "employee": emp, "company": company, "posting_date": day,
                              "currency": currency, "exchange_rate": 1, "advance_amount": amount, "advance_account": acc,
                              "purpose": f"Salary loan — {rnd.choice(demo_daily.LOAN_PURPOSES)}", "mm_is_loan": 1,
                              "mm_installment_months": months, "mm_guarantor": guarantor, "mm_first_deduction": start})
        doc.insert(ignore_permissions=True)
        with demo_daily.as_approver():
            act("Employee Advance", doc.name, "Approve")
        pay(doc.name, add_days(day, 1))                          # instalments set up on payment (mm_core.loans)
        borrowers.add(emp)
        made.append(("loan", doc.name, doc.employee_name, amount, months))
        frappe.db.commit()

    for emp in [e for e in staff if e not in borrowers][: cint(advances)]:
        day = add_days(start, rnd.randint(2, 15))
        amount = rnd.choice([5000, 8000, 10000, 12000, 15000, 20000])
        doc = frappe.get_doc({"doctype": "Employee Advance", "employee": emp, "company": company, "posting_date": day,
                              "currency": currency, "exchange_rate": 1, "advance_amount": amount, "advance_account": acc,
                              "purpose": rnd.choice(ADVANCE_PURPOSES), "repay_unclaimed_amount_from_salary": 1})
        doc.insert(ignore_permissions=True)
        doc.submit()
        pay(doc.name, day)
        recovered = rnd.random() < 0.7                           # most are recovered from this month's salary
        if recovered:
            add = frappe.get_doc({"doctype": "Additional Salary", "employee": emp, "company": company, "currency": currency,
                                  "salary_component": _advance_component(company, acc), "amount": amount, "payroll_date": end,
                                  "overwrite_salary_structure_amount": 0, "ref_doctype": "Employee Advance", "ref_docname": doc.name})
            add.insert(ignore_permissions=True)
            add.submit()
        made.append(("advance" + (" (recover in payroll)" if recovered else ""), doc.name, doc.employee_name, amount, None))
        frappe.db.commit()

    ctx = frappe._dict(rnd=rnd)
    demo_daily._stamp_users(ctx, since)
    for m in made:
        print(f"{m[0]:<32} {m[1]}  {m[2]:<22} Rs {m[3]:>9,.0f}" + (f"  {m[4]} months" if m[4] else ""))
    return made


def rerun_payroll(company, month, template="spinning_mill"):
    """Run the month's payroll again (salary slips include loan instalments / advance recoveries as Additional Salary)
    and pay salaries from the bank a few days after month end — as the payroll user."""
    from micromax import demo_daily, demo_data, demo_modules

    start, end = _month(month)
    if frappe.db.exists("Payroll Entry", {"company": company, "start_date": start, "end_date": end, "docstatus": 1}):
        print("A payroll run for this month already exists — run remove_payroll first.")
        return
    since = now()
    ctx = demo_data.prepare(company, "2025-07-01", nowdate(), seed=f"payroll|{company}|{month}", template=template)
    ctx.end = getdate(nowdate())
    ctx._current_day = end

    def at(d, order, fn, *args):                                  # run follow-ups (salary payment) right away, on their date
        prev = ctx._current_day
        ctx._current_day = getdate(d)
        try:
            fn(ctx, *args)
        finally:
            ctx._current_day = prev

    ctx.at = at
    demo_modules._payroll(ctx, end)
    frappe.db.commit()
    demo_daily._stamp_users(ctx, since)
    pe = frappe.db.get_value("Payroll Entry", {"company": company, "start_date": start, "end_date": end, "docstatus": 1}, "name")
    print(f"Payroll {pe}: {frappe.db.count('Salary Slip', {'payroll_entry': pe, 'docstatus': 1})} salary slips")
    rows = frappe.db.sql("""select ss.employee_name, sd.salary_component, sd.amount from `tabSalary Detail` sd
        join `tabSalary Slip` ss on ss.name = sd.parent
        where ss.payroll_entry = %s and sd.parentfield = 'deductions' and sd.salary_component in (%s, %s)
        order by sd.salary_component, ss.employee_name""", (pe, "Loan Recovery", ADVANCE_COMPONENT))
    print(f"Loan / advance deductions picked up automatically: {len(rows)}")
    for r in rows:
        print(f"   {r[0]:<24} {r[1]:<16} Rs {r[2]:>9,.0f}")
    return pe


def redo_month(company, month, confirm=0, loans=5, advances=4):
    if not cint(confirm):
        print("Pass confirm=1: deletes the month's payroll run and vouchers, adds loans / advances, runs payroll again.")
        return
    remove_payroll(company, month, confirm=1)
    seed_loans(company, month, loans=loans, advances=advances)
    return rerun_payroll(company, month)
