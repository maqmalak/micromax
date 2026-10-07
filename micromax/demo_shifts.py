"""Shift data for the demo company: locations (mill gates, geofenced), weekly schedules, rotating shift assignments
for the roster, shift requests, and check-in coordinates.

    bench --site <site> execute micromax.demo_shifts.generate --kwargs "{'company': '...'}"

- Shift Locations: the mill's gates and the head office, with coordinates and a check-in radius.
- Shift Schedules: each mill shift Monday–Saturday every week; general-shift staff get a Shift Schedule Assignment
  (HRMS then keeps creating their shifts ahead).
- Shift Assignments for the current month: A / B / C staff rotate weekly (A → B → C), general staff on the general
  shift — at their department's location. This is what the HRMS Roster shows.
- Shift Requests: a few pending (approvals inbox), one approved, one rejected.
- Employee Checkins get latitude / longitude at the gate they punched (a few metres of GPS scatter).
"""

import random

import frappe
from frappe.utils import add_days, get_first_day, get_last_day, getdate, now, nowdate

LOCATIONS = {
    # Faisalabad mill (gates) and Lahore head office
    "Mill Gate 1 — Main": (31.418715, 73.079109, 150),
    "Spinning Hall Gate": (31.419420, 73.080310, 100),
    "Head Office — Lahore": (31.520370, 74.358749, 200),
}
DEVICE_LOCATION = {"Gate-1": "Mill Gate 1 — Main", "Gate-2": "Spinning Hall Gate", "Mill-Gate": "Mill Gate 1 — Main"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
OFFICE_DEPTS = ("Administration", "Quality Lab")


def _geo(lat, lng):
    return frappe.as_json({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [lng, lat]}}]})


def locations():
    for name, (lat, lng, radius) in LOCATIONS.items():
        if not frappe.db.exists("Shift Location", name):
            frappe.get_doc({"doctype": "Shift Location", "location_name": name, "latitude": lat, "longitude": lng,
                            "checkin_radius": radius, "geolocation": _geo(lat, lng)}).insert(ignore_permissions=True)
    frappe.db.commit()


def _company_shifts(company):
    return [s for s, in frappe.db.sql("""select distinct default_shift from tabEmployee where company = %s and status = 'Active'
        and ifnull(default_shift, '') != '' order by default_shift""", company)]


def schedules(company):
    made = {}
    for shift in _company_shifts(company):
        name = f"{shift} — Mon to Sat"
        if not frappe.db.exists("Shift Schedule", name):
            doc = frappe.get_doc({"doctype": "Shift Schedule", "shift_type": shift, "frequency": "Every Week",
                                  "repeat_on_days": [{"day": d} for d in WEEKDAYS]})
            doc.name = name
            doc.flags.name_set = True
            doc.insert(ignore_permissions=True, set_name=name)
            doc.submit()
        made[shift] = name
    frappe.db.commit()
    return made


def _location_for(dept):
    if any(d in (dept or "") for d in OFFICE_DEPTS):
        return "Head Office — Lahore" if "Administration" in (dept or "") else "Mill Gate 1 — Main"
    return "Spinning Hall Gate"


def assignments(company, sched, month_start=None):
    """Rotating weekly assignments for A/B/C staff, a month-long general shift for the rest."""
    start = get_first_day(getdate(month_start or nowdate()))
    end = get_last_day(start)
    rotation = [s for s in _company_shifts(company) if s.endswith((" A", " B", " C"))]
    rotation.sort()
    general = [s for s in _company_shifts(company) if s not in rotation]
    made = 0
    emps = frappe.get_all("Employee", {"company": company, "status": "Active", "default_shift": ["is", "set"]},
                          ["name", "employee_name", "department", "default_shift", "date_of_joining"])
    for e in emps:
        if frappe.db.exists("Shift Assignment", {"employee": e.name, "docstatus": 1, "start_date": ["<=", end],
                                                 "end_date": [">=", start]}):
            continue
        first = max(start, getdate(e.date_of_joining))
        loc = _location_for(e.department)
        blocks = []
        if e.default_shift in rotation:
            base = rotation.index(e.default_shift)
            wk = add_days(start, -start.weekday())                 # the Monday of the month's first week
            i = 0
            while wk <= end:
                b_start, b_end = max(wk, first), min(add_days(wk, 6), end)
                if b_start <= b_end:
                    blocks.append((rotation[(base + i) % len(rotation)], b_start, b_end))
                wk, i = add_days(wk, 7), i + 1
        else:
            blocks.append((e.default_shift, first, end))
            if e.default_shift in sched and not frappe.db.exists("Shift Schedule Assignment", {"employee": e.name}):
                frappe.get_doc({"doctype": "Shift Schedule Assignment", "employee": e.name, "company": company,
                                "shift_schedule": sched[e.default_shift], "shift_location": loc, "shift_status": "Active",
                                "enabled": 1, "create_shifts_after": end}).insert(ignore_permissions=True)
        for shift, b_start, b_end in blocks:
            sa = frappe.get_doc({"doctype": "Shift Assignment", "employee": e.name, "company": company, "shift_type": shift,
                                 "start_date": b_start, "end_date": b_end, "status": "Active", "shift_location": loc})
            sa.flags.ignore_permissions = True
            sa.insert()
            sa.submit()
            made += 1
        frappe.db.commit()
    return made


def requests(company):
    """A few shift requests next month: three pending, one approved, one rejected."""
    from micromax.demo_notify import demo_users

    if frappe.db.exists("Shift Request", {"company": company}):
        return 0
    rnd = random.Random(f"shiftreq|{company}")
    shifts = _company_shifts(company)
    nxt = get_first_day(add_days(get_last_day(getdate(nowdate())), 1))
    emps = frappe.get_all("Employee", {"company": company, "status": "Active", "default_shift": ["is", "set"]}, ["name", "default_shift"])
    rnd.shuffle(emps)
    approver = demo_users()[-1]
    # HRMS: the approver must be the employee's Shift Request Approver (or a department shift approver)
    frappe.db.sql("""update tabEmployee set shift_request_approver = %s where company = %s and status = 'Active'
        and ifnull(shift_request_approver, '') = ''""", (approver, company))
    plan = ["Draft", "Draft", "Draft", "Approved", "Rejected"]
    for i, (e, status) in enumerate(zip(emps, plan)):
        other = rnd.choice([s for s in shifts if s != e.default_shift])
        from_d = add_days(nxt, i * 2)
        doc = frappe.get_doc({"doctype": "Shift Request", "employee": e.name, "company": company, "shift_type": other,
                              "from_date": from_d, "to_date": add_days(from_d, 5), "approver": approver, "status": status})
        doc.flags.ignore_permissions = True
        doc.insert()
        if status != "Draft":                      # only the request's approver may approve / reject it
            prev = frappe.session.user
            frappe.set_user(approver)
            try:
                doc.submit()
            finally:
                frappe.set_user(prev)
    frappe.db.commit()
    return len(plan)


def checkin_coordinates(company):
    """Latitude / longitude on the company's check-ins at the gate they were punched (device), with GPS scatter."""
    for device, loc in DEVICE_LOCATION.items():
        lat, lng, _r = LOCATIONS[loc]
        frappe.db.sql("""update `tabEmployee Checkin` c join tabEmployee e on e.name = c.employee
            set c.latitude = %s + (rand() - 0.5) * 0.0008, c.longitude = %s + (rand() - 0.5) * 0.0008
            where e.company = %s and c.device_id = %s and ifnull(c.latitude, 0) = 0""", (lat, lng, company, device))
    lat, lng, _r = LOCATIONS["Mill Gate 1 — Main"]
    frappe.db.sql("""update `tabEmployee Checkin` c join tabEmployee e on e.name = c.employee
        set c.latitude = %s + (rand() - 0.5) * 0.0008, c.longitude = %s + (rand() - 0.5) * 0.0008
        where e.company = %s and ifnull(c.latitude, 0) = 0""", (lat, lng, company))
    frappe.db.commit()
    return frappe.db.sql("""select count(*) from `tabEmployee Checkin` c join tabEmployee e on e.name = c.employee
        where e.company = %s and c.latitude != 0""", company)[0][0]


def generate(company, month_start=None):
    from micromax import demo_daily

    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    since = now()
    locations()
    sched = schedules(company)
    n = assignments(company, sched, month_start)
    r = requests(company)
    c = checkin_coordinates(company)
    demo_daily._stamp_users(frappe._dict(rnd=random.Random(1)), since)
    print(f"Shift locations {len(LOCATIONS)}, schedules {len(sched)}, shift assignments {n}, shift requests {r}, "
          f"check-ins with coordinates {c}")
