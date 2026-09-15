import frappe
from frappe.utils import add_to_date, escape_html, format_datetime, get_datetime, now_datetime

# The installed Frappe CRM app has no scheduled job that ever reads
# `CRM Task.due_date` or `Event.starts_on` (confirmed against
# apps/crm/crm/hooks.py's scheduler_events — it only covers invitation
# expiry, view-settings cleanup, telemetry, and Facebook lead sync), so a
# "Reminder" scheduled on the CRM calendar, or a follow-up Task's due date,
# never actually notifies anyone. This module is the fix: a small scheduled
# job, kept entirely in this custom app so the vendored crm app is never
# touched.

# How long before something is due that we start reminding (minutes).
REMINDER_LOOKAHEAD_MINUTES = 15
# Stop surfacing an Event reminder this long after it started (minutes) —
# keeps the query (and the notification list) from accumulating ancient events.
EVENT_STALE_AFTER_MINUTES = 60

OPEN_TASK_STATUSES = ["Backlog", "Todo", "In Progress"]


def send_due_reminders():
	"""Entry point for the `*/5 * * * *` cron hook in this app's hooks.py."""
	now = now_datetime()
	window_end = add_to_date(now, minutes=REMINDER_LOOKAHEAD_MINUTES)
	_remind_tasks(now, window_end)
	_remind_events(now, window_end)


def _notify(to_user: str | None, notification_text: str, message: str, ref_doctype: str, ref_name: str):
	"""Insert a CRM Notification directly, rather than via
	`crm.fcrm.doctype.crm_notification.crm_notification.notify_user()`: that
	helper silently no-ops when `owner == assigned_to` — exactly the common
	case for a self-assigned follow-up or reminder, which is precisely who
	most needs the ping.

	Dedup is done with an explicit filters dict that does NOT include a
	`doctype` key — confirmed against the live backend that
	`frappe.db.exists("CRM Notification", filters)` silently returns no match
	whenever the filters dict itself also carries a `doctype` entry (even for
	a row that was just inserted with those exact values). The upstream
	`notify_user()` helper builds its dedupe dict the same broken way
	(`frappe._dict(doctype="CRM Notification", ...)` passed straight to
	`frappe.db.exists`), so this same bug likely affects it too — not
	something to fix in the vendored app, just something to avoid here.
	"""
	if not to_user:
		return
	# Respect the user's own "Enable System Notification" toggle (core Frappe
	# `Notification Settings`, one doc per user — exposed on the Settings ->
	# Notifications page). A user who never touched this setting has no doc
	# yet, and the field defaults to 1, so absence means enabled.
	settings_enabled = frappe.db.get_value("Notification Settings", to_user, "enabled")
	if settings_enabled is not None and not settings_enabled:
		return
	dedupe_filters = {
		"to_user": to_user,
		"type": "Task",
		"notification_text": notification_text,
		"reference_doctype": ref_doctype,
		"reference_name": ref_name,
	}
	if frappe.db.exists("CRM Notification", dedupe_filters):
		return
	frappe.get_doc(
		{
			"doctype": "CRM Notification",
			"from_user": to_user,
			"to_user": to_user,
			"type": "Task",
			"message": message,
			"notification_text": notification_text,
			"notification_type_doctype": ref_doctype,
			"notification_type_doc": ref_name,
			"reference_doctype": ref_doctype,
			"reference_name": ref_name,
		}
	).insert(ignore_permissions=True)
	frappe.publish_realtime("crm_notification", user=to_user)


def _remind_tasks(now, window_end):
	tasks = frappe.get_all(
		"CRM Task",
		filters=[
			["status", "in", OPEN_TASK_STATUSES],
			["due_date", "is", "set"],
			["due_date", "<=", window_end],
		],
		fields=["name", "title", "due_date", "assigned_to", "reference_doctype", "reference_docname"],
		limit_page_length=0,
	)
	for t in tasks:
		if not t.assigned_to:
			continue
		overdue = get_datetime(t.due_date) < now
		when = "overdue" if overdue else "due soon"
		title = escape_html(t.title or t.name)
		_notify(
			t.assigned_to,
			f"Follow-up {when}: {t.title or t.name}",
			f"<p>Your follow-up “{title}” is {when} ({format_datetime(t.due_date)}).</p>",
			t.reference_doctype or "CRM Task",
			t.reference_docname or t.name,
		)


def _remind_events(now, window_end):
	events = frappe.get_all(
		"Event",
		filters=[
			["status", "=", "Open"],
			["starts_on", "<=", window_end],
			["starts_on", ">=", add_to_date(now, minutes=-EVENT_STALE_AFTER_MINUTES)],
		],
		fields=["name", "subject", "starts_on", "owner"],
		limit_page_length=0,
	)
	for e in events:
		subject = escape_html(e.subject or e.name)
		_notify(
			e.owner,
			f"Reminder: {e.subject or e.name}",
			f"<p>Reminder “{subject}” starts at {format_datetime(e.starts_on)}.</p>",
			"Event",
			e.name,
		)
