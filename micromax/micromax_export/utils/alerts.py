import frappe
from frappe.utils import getdate, today


def _create_todo(owner, subject, description, reference_type, reference_name, date):
    existing = frappe.db.exists(
        "ToDo",
        {
            "reference_type": reference_type,
            "reference_name": reference_name,
            "description": ["like", "%" + subject + "%"],
        },
    )
    if existing:
        return
    todo = frappe.get_doc(
        {
            "doctype": "ToDo",
            "owner": owner or "Administrator",
            "role": "All",
            "subject": subject,
            "description": description,
            "reference_type": reference_type,
            "reference_name": reference_name,
            "date": date,
            "status": "Open",
        }
    )
    todo.insert(ignore_permissions=True)


def process_lc_alerts():
    """Daily job: LC expiry + latest-shipment-date alerts (30/15/7 days and overdue)."""
    today_dt = getdate(today())
    filters = {"docstatus": ("<", 2), "lc_status": ("not in", ["Closed", "Confirmed"])}
    docs = frappe.db.get_all(
        "LC Proforma",
        filters=filters,
        fields=["name", "lc_expiry_date", "latest_shipment_date", "next_action_by"],
        order_by="creation asc",
    )
    if not docs:
        return

    for d in docs:
        owner = d.get("next_action_by") or "Administrator"
        if d.get("lc_expiry_date"):
            days = (getdate(d.lc_expiry_date) - today_dt).days
            for threshold, label in (
                (30, "LC expires in 30 days"),
                (15, "LC expires in 15 days"),
                (7, "LC expires in 7 days"),
            ):
                if 0 <= days <= threshold:
                    _create_todo(
                        owner,
                        f"{d.name} — {label}",
                        f"LC Proforma {d.name} expires on {d.lc_expiry_date}.",
                        "LC Proforma",
                        d.name,
                        d.lc_expiry_date,
                    )
            if days < 0:
                _create_todo(
                    owner,
                    f"{d.name} — LC has expired",
                    f"LC Proforma {d.name} expired on {d.lc_expiry_date}.",
                    "LC Proforma",
                    d.name,
                    d.lc_expiry_date,
                )

        if d.get("latest_shipment_date"):
            ship_days = (getdate(d.latest_shipment_date) - today_dt).days
            for threshold, label in (
                (30, "Shipment date in 30 days"),
                (15, "Shipment date in 15 days"),
                (7, "Shipment date in 7 days"),
            ):
                if 0 <= ship_days <= threshold:
                    _create_todo(
                        owner,
                        f"{d.name} — {label}",
                        f"Latest shipment date for LC Proforma {d.name} is {d.latest_shipment_date}.",
                        "LC Proforma",
                        d.name,
                        d.latest_shipment_date,
                    )
            if ship_days < 0:
                _create_todo(
                    owner,
                    f"{d.name} — Shipment date has passed",
                    f"Latest shipment date for LC Proforma {d.name} ({d.latest_shipment_date}) has passed.",
                    "LC Proforma",
                    d.name,
                    d.latest_shipment_date,
                )

        frappe.db.commit()
