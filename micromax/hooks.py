import frappe
from . import __version__ as __version__

app_name = "micromax"
app_title = "MicroMax"
app_logo_url = "/assets/micromax/images/micromax-logo.svg"
app_publisher = "MicroMax"
app_description = "MicroMax Import/Export customization for ERPNext"
app_email = "dev@example.com"
app_license = "mit"
required_apps = ["erpnext"]
app_home = "/desk/micromax"

add_to_apps_screen = [
    {
        "name": "micromax",
        "logo": "/assets/micromax/images/micromax-logo.svg",
        "title": "MicroMax",
        "route": "/desk/micromax",
    }
]


@frappe.whitelist()
def get_linked_parent_docs(
    doctype: str,
    parenttype: str,
    purchase_order: str | None = None,
    link_field: str | None = None,
    link_value: str | None = None,
    extra_filters: str | dict | None = None,
):
    """Return distinct parent document names of a given parenttype whose child
    rows reference the given value on a link field.

    The link lives on the child table (e.g. Purchase Receipt Item.purchase_order,
    Purchase Invoice Item.purchase_receipt, or Payment Entry Reference.reference_name),
    but the REST ``get_list`` API strips the ``parent`` field for child tables.
    This server-side helper does the lookup and returns deduplicated parent names.

    :param doctype: child DocType to search, e.g. "Purchase Receipt Item"
    :param parenttype: parent DocType, e.g. "Purchase Receipt"
    :param purchase_order: legacy kwarg, kept for backwards compatibility with
        older frontend builds — equivalent to ``link_field="purchase_order"``.
    :param link_field: child-row fieldname to filter on, e.g. "purchase_order"
    :param link_value: the value to match, e.g. a Purchase Order name
    :param extra_filters: optional dict (or JSON string) of additional exact-match
        filters, e.g. {"reference_doctype": "Purchase Invoice"} when the same
        link_field/value pair could plausibly match rows belonging to more than
        one parent kind (Payment Entry Reference is shared by many doctypes).
    """
    field = link_field or "purchase_order"
    value = link_value if link_value is not None else purchase_order
    if not value:
        return []

    filters = {field: value, "parenttype": parenttype}
    if extra_filters:
        if isinstance(extra_filters, str):
            extra_filters = frappe.parse_json(extra_filters)
        filters.update(extra_filters)

    parent_names = frappe.get_all(
        doctype,
        filters=filters,
        fields=["parent"],
        limit_page_length=200,
    )
    return list(dict.fromkeys(r.parent for r in parent_names if r.parent))
#--------------------------------
# Docs / website
doc_typewise_controller_methods = {}
#--------------------------------

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["dt", "in", ["Item", "Supplier", "Customer", "Sales Order", "Sales Order Item", "CRM Lead"]]],
    },
]

before_migrate = [
    "micromax.install.make_custom_fields",
    "micromax.install.create_workflow",
]

after_install = [
    "micromax.install.make_custom_fields",
    "micromax.install.create_workflow",
    "micromax.install.create_roles",
    "micromax.install.create_dashboard",
    "micromax.install.create_workspace",
]

scheduler_events = {
    "daily": [
        "micromax.micromax_export.utils.alerts.process_lc_alerts",
    ],
    "cron": {
        # CRM follow-up (CRM Task.due_date) and calendar reminder (Event.starts_on)
        # delivery — the installed crm app has no job that ever reads either field.
        "*/5 * * * *": ["micromax.crm_reminders.send_due_reminders"],
    },
}

# For each DocType created by this app, no doc_events hooks are strictly needed,
# but a clean on_trash guard keeps data integrity:
doc_events = {
    "LC Proforma": {
        "on_update": "micromax.micromax_export.utils.lc_proforma.update_from_status",
    },
}