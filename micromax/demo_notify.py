"""Assign a document to the demo users the way the desk does — an open ToDo plus an "Assignment" bell
notification — synchronously (Frappe queues the notification as a background job; the demo generator wants it
now) and never as a self-assignment, which Frappe silently skips."""

import frappe
from frappe.utils import getdate, nowdate

DIRECTOR = "director@micromaxonline.uk"


def demo_users():
    """Who gets the demo's assignments: Administrator (local / admin logins) and the demo director, if present."""
    users = ["Administrator"]
    for u in (frappe.conf.get("demo_hr_approver"), DIRECTOR):
        if u and u not in users and frappe.db.get_value("User", u, "enabled"):
            users.append(u)
    return users


def _assigner_for(user):
    for u in ([DIRECTOR, "Administrator"] if user == "Administrator" else ["Administrator"]):
        if u != user and frappe.db.get_value("User", u, "enabled"):
            return u
    return None


def assign(doctype, name, description, date=None, users=None, priority="Medium"):
    """Open ToDo + bell notification for each user (skipped when they already have an open ToDo for it)."""
    for user in users or demo_users():
        if frappe.db.exists("ToDo", {"reference_type": doctype, "reference_name": name, "allocated_to": user, "status": "Open"}):
            continue
        by = _assigner_for(user) or "Administrator"
        todo = frappe.get_doc({"doctype": "ToDo", "allocated_to": user, "reference_type": doctype, "reference_name": name,
                               "description": description, "date": getdate(date or nowdate()), "priority": priority,
                               "assigned_by": by, "status": "Open"})
        todo.flags.ignore_permissions = True
        todo.insert()
        if by == user:
            continue
        notify(doctype, name, user, by)


def notify(doctype, name, user, by):
    """The "X assigned a new task … to you" bell notification. make_notification_logs looks users up by EMAIL
    (Administrator's user name isn't one), so pass the address."""
    from frappe.desk.doctype.notification_log.notification_log import make_notification_logs
    from frappe.desk.form.assign_to import get_title, get_title_html

    email = frappe.db.get_value("User", user, "email") or user
    title = get_title_html(get_title(doctype, name))
    make_notification_logs({"type": "Assignment", "document_type": doctype, "document_name": name, "from_user": by,
                            "subject": f"<b>{frappe.utils.get_fullname(by)}</b> assigned a new task <b>{doctype}</b> {title} to you"},
                           [email])
