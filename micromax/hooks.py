import frappe
from . import __version__ as __version__

app_name = "micromax"
app_title = "MicroMax"
app_logo_url = "/assets/micromax/images/micromax-logo.svg"
app_publisher = "MicroMax"
app_description = "MicroMax Import/Export customization for ERPNext"
app_email = "dev@example.com"
app_license = "mit"
# mm_core holds the Custom Fields shared with the other sites on this bench (Customer, Supplier, Sales Order...).
required_apps = ["erpnext", "mm_core"]
app_home = "/desk/micromax"

add_to_apps_screen = [
    {
        "name": "micromax",
        "logo": "/assets/micromax/images/micromax-logo.svg",
        "title": "MicroMax",
        "route": "/desk/micromax",
    }
]


# Moved to mm_core.api (every site's React record pages use it); imported here so the old API path
# micromax.hooks.get_linked_parent_docs keeps working (same whitelisted function object).
from mm_core.api import get_linked_parent_docs  # noqa: E402,F401
#--------------------------------
# Docs / website
doc_typewise_controller_methods = {}
#--------------------------------

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["dt", "in", ["Item", "Supplier", "Customer", "Sales Order", "Sales Order Item", "CRM Lead", "Account"]]],
    },
]

before_migrate = [
    "micromax.install.make_custom_fields",
    "micromax.install.add_hr_indexes",
    "micromax.install.make_crm_notification_email_option",
    "micromax.install.make_crm_organization_employee_options",
    "micromax.install.make_crm_organization_address_freetext",
    "micromax.install.create_workflow",
]

after_install = [
    "micromax.install.make_custom_fields",
    "micromax.install.add_hr_indexes",
    "micromax.install.make_crm_notification_email_option",
    "micromax.install.make_crm_organization_employee_options",
    "micromax.install.make_crm_organization_address_freetext",
    "micromax.install.create_workflow",
    "micromax.install.create_roles",
    "micromax.install.create_dashboard",
    "micromax.install.create_workspace",
]

scheduler_events = {
    "daily": [
        "micromax.micromax_export.utils.alerts.process_lc_alerts",
    ],
    # The CRM reminder / mail-status job (every 5 min) moved to mm_core's hooks (mm_core.crm_reminders).
}

# For each DocType created by this app, no doc_events hooks are strictly needed,
# but a clean on_trash guard keeps data integrity:
doc_events = {
    # Spinning-mill calculations (blend ratio, yield, waste, spindles/frames) — see mfg_logic.py.
    "BOM": {
        "before_validate": "micromax.mfg_logic.bom_before_validate",
    },
    "Work Order": {
        "validate": "micromax.mfg_logic.work_order_validate",
    },
    "LC Proforma": {
        "on_update": "micromax.micromax_export.utils.lc_proforma.update_from_status",
    },
    # Communication.after_insert (inbound-email pings) and CRM Task.on_trash (notification cleanup) moved to
    # mm_core's hooks, so every site has them — and demo doesn't run them twice.
}