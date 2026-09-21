import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
    make_custom_fields()
    make_crm_notification_email_option()
    make_crm_organization_employee_options()
    make_crm_organization_address_freetext()
    create_roles()
    create_workflow()


def make_crm_notification_email_option():
    """Widen CRM Notification.type with an "Email" option via a Property Setter.

    micromax.crm_reminders (mail send/error pings) and
    micromax.crm_mail_notifications (inbound-email pings) insert CRM
    Notification rows with type="Email". The vendored crm app's doctype only
    allows Mention/Task/Assignment/WhatsApp, so without this setter every such
    insert fails with:
    'Type cannot be "Email". It should be one of "Mention", "Task",
    "Assignment", "WhatsApp"'.

    make_property_setter() deletes any existing setter for the same
    doctype/field/property before inserting and clears the doctype cache, so
    this is idempotent — safe to run on every install/migrate. Registered in
    both before_migrate and after_install in hooks.py.
    """
    make_property_setter(
        "CRM Notification",
        "type",
        "options",
        "Mention\nTask\nAssignment\nWhatsApp\nEmail",
        "Select",
    )


def make_crm_organization_employee_options():
    """Add an "Above 500" bucket to CRM Organization.no_of_employees via a Property Setter.

    The React Organizations form offers 1-10 / 11-50 / 51-200 / 201-500 / Above 500. The crm app's
    Select only allows "1-10 ... 201-500", "501-1000" and "1000+", and Frappe rejects a Select value
    that isn't in its option list, so "Above 500" would fail on save without this. The crm app's own
    "501-1000" / "1000+" are kept so existing records stay valid.

    make_property_setter() replaces any previous setter for the same doctype/field/property, so this is
    idempotent — registered in both before_migrate and after_install in hooks.py.
    """
    make_property_setter(
        "CRM Organization",
        "no_of_employees",
        "options",
        "1-10\n11-50\n51-200\n201-500\n501-1000\n1000+\nAbove 500",
        "Select",
    )


def make_crm_organization_address_freetext():
    """Turn CRM Organization.address from a Link to "Address" into free-text Small Text.

    The crm app declares it as a Link to the Address doctype, so typing plain text in the React
    Organizations form failed on save with "Could not find Address: <text>". The Lead form's address
    (a micromax custom field) is already Small Text; this makes Organization match.

    A Property Setter alone doesn't change the DB column (varchar(140) for a Link), so this also runs
    updatedb() to widen it to text — otherwise a long address would be truncated/rejected. Existing
    values (Address doc names) are kept as-is. Idempotent; registered in before_migrate and
    after_install in hooks.py.

    Caveat: the crm app's optional ERPNext sync (erpnext_crm_settings.get_organization_address) still
    loads the value as an Address doc, so with that integration enabled a free-text address there
    would fail — it is disabled on this bench.
    """
    make_property_setter("CRM Organization", "address", "fieldtype", "Small Text", "Data")
    make_property_setter("CRM Organization", "address", "options", "", "Text")
    frappe.clear_cache(doctype="CRM Organization")
    frappe.db.updatedb("CRM Organization")


def _build_custom_fields():
    """Returns a dict keyed by doctype with a list of field dicts."""
    data = {}

    def add(dt, fieldname, label, fieldtype, options, insert_after, section, description=None):
        field = {
            "fieldname": fieldname,
            "label": label,
            "fieldtype": fieldtype,
            "options": options,
            "insert_after": insert_after,
        }
        if description:
            field["description"] = description
        data.setdefault(dt, []).append(field)

    # ---------------------------------------------------------- Item
    # Customs Information
    add("Item", "hs_code", "HS Code", "Data", None, "hs_section_break", "Customs Information")
    add("Item", "customs_tariff_description", "Customs Tariff Description", "Small Text", None, "hs_code", "Customs Information")
    add("Item", "country_of_origin", "Country of Origin", "Link", "Country", "customs_tariff_description", "Customs Information")
    add("Item", "export_control_code", "Export Control Code", "Data", None, "country_of_origin", "Customs Information")
    add("Item", "import_license_required", "Import License Required", "Check", None, "export_control_code", "Customs Information")
    add("Item", "export_license_required", "Export License Required", "Check", None, "import_license_required", "Customs Information")
    # MicroMax Information
    add("Item", "fabric_material_composition", "Fabric/Material Composition", "Data", None, "customs_division_break", None)
    add("Item", "gsm", "GSM", "Data", None, "fabric_material_composition", None)
    add("Item", "commercial_description", "Commercial Description", "Text", None, "gsm", None)
    add("Item", "customs_description", "Customs Description", "Text", None, "commercial_description", None)
    add("Item", "export_uom", "Export UOM", "Link", "UOM", "customs_description", None)
    add("Item", "packing_type", "Packing Type", "Select", "\nBale\nCarton\nCase\nPolybag\nRoll\nOther", "export_uom", None)
    add("Item", "net_weight_per_unit", "Net Weight per Unit", "Float", None, "packing_type", None)
    add("Item", "gross_weight_per_unit", "Gross Weight per Unit", "Float", None, "net_weight_per_unit", None)
    # Duty Information
    add("Item", "import_duty_percent", "Import Duty %", "Percent", None, "duty_section_break", None)
    add("Item", "additional_duty_percent", "Additional Duty %", "Percent", None, "import_duty_percent", None)
    add("Item", "regulatory_remarks", "Regulatory Remarks", "Small Text", None, "additional_duty_percent", None)

    # ---------------------------------------------------------- Supplier
    add("Supplier", "supplier_import_code", "Supplier Import Code", "Data", None, "import_info_section", None)
    add("Supplier", "supplier_country", "Supplier Country", "Link", "Country", "supplier_import_code", None)
    add("Supplier", "import_license_no", "Import License No.", "Data", None, "supplier_country", None)
    add("Supplier", "default_port_of_loading", "Default Port of Loading", "Data", None, "import_license_no", None)
    add("Supplier", "default_port_of_discharge", "Default Port of Discharge", "Data", None, "default_port_of_loading", None)
    add("Supplier", "default_incoterm", "Default Incoterm", "Select", "EXW\nFCA\nFAS\nFOB\nCFR\nCIF\nCPT\nCIP\nDAP\nDPU\nDDP", "default_port_of_discharge", None)
    add("Supplier", "import_payment_terms", "Import Payment Terms", "Link", "Payment Terms Template", "default_incoterm", None)
    add("Supplier", "clearing_agent", "Clearing Agent", "Link", "Supplier", "import_payment_terms", None)
    add("Supplier", "bank_lc_reference", "Bank/LC Reference", "Data", None, "clearing_agent", None)
    add("Supplier", "suppliers_item_code", "Supplier's Item Code", "Data", None, "bank_lc_reference", None)
    add("Supplier", "suppliers_item_desc", "Supplier's Item Description", "Small Text", None, "suppliers_item_code", None)

    # ---------------------------------------------------------- Customer (Buyer)
    add("Customer", "buyer_code", "Buyer Code", "Data", None, "export_info_section", None)
    add("Customer", "buyer_country", "Buyer Country", "Link", "Country", "buyer_code", None)
    add("Customer", "export_license_requirement", "Export License Requirement", "Check", None, "buyer_country", None)
    add("Customer", "buyer_registration_no", "Buyer Registration No.", "Data", None, "export_license_requirement", None)
    add("Customer", "default_port_of_loading", "Default Port of Loading", "Data", None, "buyer_registration_no", None)
    add("Customer", "default_port_of_discharge", "Default Port of Discharge", "Data", None, "default_port_of_loading", None)
    add("Customer", "default_incoterm", "Default Incoterm", "Select", "EXW\nFCA\nFAS\nFOB\nCFR\nCIF\nCPT\nCIP\nDAP\nDPU\nDDP", "default_port_of_discharge", None)
    add("Customer", "export_payment_terms", "Export Payment Terms", "Link", "Payment Terms Template", "default_incoterm", None)
    add("Customer", "export_bank", "Export Bank", "Data", None, "export_payment_terms", None)
    add("Customer", "beneficiary_ref", "Beneficiary Reference", "Data", None, "export_bank", None)
    add("Customer", "shipping_doc_requirement", "Shipping Document Requirement", "Small Text", None, "beneficiary_ref", None)

    # ---------------------------------------------------------- Sales Order (Export Section)
    add("Sales Order", "export_order_flag", "Export Order", "Data", None, "export_section_break", None)
    add("Sales Order", "buyer_po_no", "Buyer PO No.", "Data", None, "export_order_flag", None)
    add("Sales Order", "lc_proforma", "LC Proforma", "Link", "LC Proforma", "buyer_po_no", None)
    add("Sales Order", "lc_no", "LC No.", "Data", None, "lc_proforma", None)
    add("Sales Order", "lc_date", "LC Date", "Date", None, "lc_no", None)
    add("Sales Order", "lc_issuing_bank", "LC Issuing Bank", "Data", None, "lc_date", None)
    add("Sales Order", "lc_advising_bank", "LC Advising Bank", "Data", None, "lc_issuing_bank", None)
    add("Sales Order", "lc_amount", "LC Amount", "Currency", None, "lc_advising_bank", None)
    add("Sales Order", "lc_currency", "LC Currency", "Link", "Currency", "lc_amount", None)
    add("Sales Order", "lc_expiry_date", "LC Expiry Date", "Date", None, "lc_currency", None)
    add("Sales Order", "latest_shipment_date", "Latest Shipment Date", "Date", None, "lc_expiry_date", None)
    add("Sales Order", "port_of_loading", "Port of Loading", "Data", None, "latest_shipment_date", None)
    add("Sales Order", "port_of_discharge", "Port of Discharge", "Data", None, "port_of_loading", None)
    add("Sales Order", "final_destination", "Final Destination", "Data", None, "port_of_discharge", None)
    add("Sales Order", "incoterm", "Incoterm", "Select", "EXW\nFCA\nFAS\nFOB\nCFR\nCIF\nCPT\nCIP\nDAP\nDPU\nDDP", "final_destination", None)
    add("Sales Order", "shipment_mode", "Shipment Mode", "Select", "\nSea\nAir\nRoad\nRail\nMultimodal", "incoterm", None)
    add("Sales Order", "country_of_destination", "Country of Destination", "Link", "Country", "shipment_mode", None)
    add("Sales Order", "export_status", "Export Status", "Select", "\nPlanned\nIn Production\nReady to Ship\nShipped\nClosed", "country_of_destination", None)

    # ---------------------------------------------------------- Buying Settings
    # A dedicated "Landed Cost" tab holding one Expense Account per charge
    # type, used when an Import Cost Sheet generates a Landed Cost Voucher.
    # A general fallback account covers any charge without its own account set.
    add("Buying Settings", "landed_cost_tab", "Landed Cost", "Tab Break", None, "transaction_naming_html", None)
    add("Buying Settings", "landed_cost_charges_section", "Charge-wise Expense Accounts", "Section Break", None, "landed_cost_tab", None)
    add("Buying Settings", "freight_expense_account", "Freight Expense Account", "Link", "Account", "landed_cost_charges_section", None)
    add("Buying Settings", "customs_duty_expense_account", "Customs Duty Expense Account", "Link", "Account", "freight_expense_account", None)
    add("Buying Settings", "sales_tax_expense_account", "Sales Tax Expense Account", "Link", "Account", "customs_duty_expense_account", None)
    add("Buying Settings", "clearing_charges_expense_account", "Clearing Charges Expense Account", "Link", "Account", "sales_tax_expense_account", None)
    add("Buying Settings", "port_charges_expense_account", "Port Charges Expense Account", "Link", "Account", "clearing_charges_expense_account", None)
    add("Buying Settings", "landed_cost_charges_col_break", None, "Column Break", None, "port_charges_expense_account", None)
    add("Buying Settings", "insurance_expense_account", "Insurance Expense Account", "Link", "Account", "landed_cost_charges_col_break", None)
    add("Buying Settings", "additional_duty_expense_account", "Additional Duty Expense Account", "Link", "Account", "insurance_expense_account", None)
    add("Buying Settings", "regulatory_duty_expense_account", "Regulatory Duty Expense Account", "Link", "Account", "additional_duty_expense_account", None)
    add("Buying Settings", "other_charges_expense_account", "Other Charges Expense Account", "Link", "Account", "regulatory_duty_expense_account", None)
    add("Buying Settings", "landed_cost_fallback_section", "General Fallback", "Section Break", None, "other_charges_expense_account", None)
    add(
        "Buying Settings",
        "default_landed_cost_expense_account",
        "Default Landed Cost Expense Account",
        "Link",
        "Account",
        "landed_cost_fallback_section",
        None,
        description=(
            "Used for any landed cost charge above that doesn't have its own Expense "
            "Account set (and as the fallback when an Import Cost Sheet's own Expense "
            "Account field is also blank)."
        ),
    )

    # ---------------------------------------------------------- Sales Order Item
    add("Sales Order Item", "style_no", "Style No.", "Data", None, "micromax_export_break", None)
    add("Sales Order Item", "buyer_style_no", "Buyer Style No.", "Data", None, "style_no", None)
    add("Sales Order Item", "buyer_color", "Buyer Color", "Data", None, "buyer_style_no", None)
    add("Sales Order Item", "buyer_size", "Buyer Size", "Data", None, "buyer_color", None)
    add("Sales Order Item", "season", "Season", "Data", None, "buyer_size", None)
    add("Sales Order Item", "hs_code", "HS Code", "Data", None, "season", None)
    add("Sales Order Item", "country_of_origin", "Country of Origin", "Link", "Country", "hs_code", None)
    add("Sales Order Item", "export_quantity", "Export Quantity", "Float", None, "country_of_origin", None)
    add("Sales Order Item", "carton_quantity", "Carton Quantity", "Int", None, "export_quantity", None)
    add("Sales Order Item", "net_weight", "Net Weight", "Float", None, "carton_quantity", None)
    add("Sales Order Item", "gross_weight", "Gross Weight", "Float", None, "net_weight", None)

    # ---------------------------------------------------------- CRM Lead (fundraising tracker)
    # These map fields from the donor-prospecting spreadsheet that have no
    # equivalent on the stock CRM Lead doctype (Segment/City/Expected Amount
    # reuse the existing industry/territory/annual_revenue fields instead).
    add("CRM Lead", "fundraising_section", "Fundraising Details", "Section Break", None, "territory", None)
    add("CRM Lead", "priority", "Priority", "Select", "\nA+\nA\nB\nC", "fundraising_section", None)
    add("CRM Lead", "csr_department", "CSR/ESG Department", "Data", None, "priority", None)
    add("CRM Lead", "address", "Address", "Small Text", None, "csr_department", None)
    add("CRM Lead", "fundraising_column_break", None, "Column Break", None, "address", None)
    add("CRM Lead", "focus_area", "Focus Area", "Data", None, "fundraising_column_break", None)
    add("CRM Lead", "education_focus", "Education Focus", "Data", None, "focus_area", None)
    add("CRM Lead", "proposed_ask", "Proposed Ask", "Small Text", None, "education_focus", None)
    add("CRM Lead", "first_contact_date", "First Contact", "Date", None, "proposed_ask", None)
    add("CRM Lead", "remarks", "Remarks", "Small Text", None, "first_contact_date", None)

    return data

def create_roles():
    for role in ["MicroMax Administrator", "Export Manager", "Import Manager", "Commercial Manager"]:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
    # Assign the micromax workflow roles to Administrator so the workflow
    # action buttons (Submit / Approve / ...) actually render in the UI.
    for role in ["Export Manager", "MicroMax Administrator"]:
        if not frappe.db.exists(
            "Has Role",
            {"parent": "Administrator", "role": role, "parenttype": "User"},
        ):
            d = frappe.new_doc("Has Role")
            d.parent = "Administrator"
            d.parenttype = "User"
            d.parentfield = "roles"
            d.role = role
            d.insert(ignore_permissions=True)


def create_workflow():
    if frappe.db.exists("Workflow", "LC Proforma Workflow"):
        return
    state_names = [
        "Draft", "Submitted", "Buyer Approval", "LC Requested",
        "LC Received", "Confirmed", "Closed",
    ]
    action_names = [
        "Submit", "Send for Buyer Approval", "Approve", "Reject",
        "Record LC", "Confirm", "Close", "Reopen",
    ]
    # Workflow State + Workflow Action Master must exist before the workflow.
    for st in state_names:
        if not frappe.db.exists("Workflow State", st):
            frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": st}).insert(ignore_permissions=True)
    for action in action_names:
        if not frappe.db.exists("Workflow Action Master", action):
            frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(ignore_permissions=True)

    states = [
        {"state": "Draft", "doc_status": 0, "allow_edit": "Export Manager"},
        {"state": "Submitted", "doc_status": 1, "allow_edit": "Export Manager"},
        {"state": "Buyer Approval", "doc_status": 1, "allow_edit": "Export Manager"},
        {"state": "LC Requested", "doc_status": 1, "allow_edit": "Export Manager"},
        {"state": "LC Received", "doc_status": 1, "allow_edit": "Export Manager"},
        {"state": "Confirmed", "doc_status": 1, "allow_edit": "Export Manager"},
        {"state": "Closed", "doc_status": 1, "allow_edit": "MicroMax Administrator"},
    ]
    transitions = [
        {"state": "Draft", "action": "Submit", "next_state": "Submitted", "allowed": "Export Manager"},
        {"state": "Submitted", "action": "Send for Buyer Approval", "next_state": "Buyer Approval", "allowed": "Export Manager"},
        {"state": "Buyer Approval", "action": "Approve", "next_state": "LC Requested", "allowed": "Export Manager"},
        {"state": "Buyer Approval", "action": "Reject", "next_state": "Draft", "allowed": "Export Manager"},
        {"state": "LC Requested", "action": "Record LC", "next_state": "LC Received", "allowed": "Export Manager"},
        {"state": "LC Received", "action": "Confirm", "next_state": "Confirmed", "allowed": "Export Manager"},
        {"state": "Confirmed", "action": "Close", "next_state": "Closed", "allowed": "MicroMax Administrator"},
        {"state": "Confirmed", "action": "Reopen", "next_state": "LC Received", "allowed": "MicroMax Administrator"},
    ]
    doc = frappe.get_doc(
        {
            "doctype": "Workflow",
            "document_type": "LC Proforma",
            "workflow_name": "LC Proforma Workflow",
            "is_active": 1,
            "override_status": 0,
            "states": states,
            "transitions": transitions,
        }
    )
    doc.insert(ignore_permissions=True)
    doc.submit()
    frappe.db.commit()

def make_custom_fields():
    data = _build_custom_fields()
    if frappe.db.table_exists("Custom Field") and data:
        create_custom_fields(data, ignore_validate=True)

# ==================================================================== #
# Dashboard / Number Cards / Charts / Workspace
# ==================================================================== #


def _get_or_insert(doctype, name, data):
    if frappe.db.exists(doctype, name):
        return frappe.get_doc(doctype, name)
    doc = frappe.new_doc(doctype)
    doc.update(data)
    doc.name = name
    try:
        doc.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        doc = frappe.get_doc(doctype, name)
    return doc


NUMBER_CARDS = [
    ("MicroMax LC Proformas", "Count", "LC Proforma", None, "MicroMax"),
    ("MicroMax LC Value", "Sum", "LC Proforma", "lc_amount", "MicroMax"),
    ("MicroMax LC Pending", "Count", "LC Proforma", None, "MicroMax",
     [["lc_status", "in", ["Submitted", "Buyer Approval", "LC Requested", "LC Received"]]]),
    ("MicroMax LC Expiring", "Count", "LC Proforma", None, "MicroMax",
     [["lc_expiry_date", "Between", None, "next_gte_30"]]),
    ("MicroMax Export Orders", "Count", "Sales Order", None, "MicroMax",
     [["export_order_flag", "=", None, "is_set"]]),
    ("MicroMax Pending Export Orders", "Count", "Sales Order", None, "MicroMax",
     [["export_status", "in", ["Planned", "In Production", "Ready to Ship"]]]),
    ("MicroMax Shipments Pending", "Count", "Export Shipment", None, "MicroMax",
     [["shipment_status", "not in", ["Arrived", "Delivered", "Closed"]]]),
    ("MicroMax Shipments In Transit", "Count", "Export Shipment", None, "MicroMax",
     [["shipment_status", "=", "In Transit"]]),
    ("MicroMax Export Value", "Sum", "Sales Order", "grand_total", "MicroMax",
     [["export_order_flag", "=", None, "is_set"]]),
    ("MicroMax Pending Shipment Value", "Sum", "Sales Order", "grand_total", "MicroMax",
     [["export_status", "in", ["Planned", "In Production", "Ready to Ship"]]]),
]


def create_dashboard():
    import json
    # Number Card / Dashboard Chart are optional reporting artefacts; some
    # minimal ERPNext environments don't include their tables. Gracefully skip.
    if not frappe.db.table_exists("Number Card") or not frappe.db.table_exists("Dashboard Chart"):
        return
    for row in NUMBER_CARDS:
        label, func, doc_type, value_field, module, extra_filters = (list(row) + [None] * 6)[:6]
        card_name = label
        f = {
            "label": label,
            "function": func,
            "document_type": doc_type,
            "show_percentage_stats": 0,
            "color": "#2490EF",
            "module": module,
        }
        if func == "Sum" and value_field:
            f["aggregate_function_based_on"] = value_field
        filters = extra_filters if extra_filters else []
        f["filters_json"] = json.dumps(filters)
        _get_or_insert("Number Card", card_name, f)

    # Charts
    charts = [
        ("MicroMax Monthly Export Value", "Sum", "Sales Order", "transaction_date", "grand_total", "Bar",
         [["export_order_flag", "=", None, "is_set"]]),
        ("MicroMax Buyer-wise Export Value", "Sum", "Sales Order", "customer", "grand_total", "Pie",
         [["export_order_flag", "=", None, "is_set"]]),
        ("MicroMax LC Status", "Group By", "LC Proforma", "lc_status", None, "Pie", None, "lc_status"),
        ("MicroMax Shipment Status", "Group By", "Export Shipment", "shipment_status", None, "Pie", None, "shipment_status"),
        ("MicroMax Country-wise Export", "Group By", "Sales Order", "country_of_destination", None, "Pie",
         [["export_order_flag", "=", None, "is_set"]], "country_of_destination"),
        ("MicroMax Buyer-wise Pending Orders", "Count", "Sales Order", "customer", None, "Bar",
         [["export_status", "in", ["Planned", "In Production", "Ready to Ship"]]], "customer"),
    ]
    for row in charts:
        chart_name, chart_type, doc_type, based_on, value_based_on, graph, filters, group_by = (
            list(row) + [None] * 8
        )[:8]
        import json
        c = {
            "chart_name": chart_name,
            "chart_type": chart_type,
            "document_type": doc_type,
            "name": chart_name,
            "is_public": 1,
            "type": graph,
            "timespan": "Last Year",
            "module": "MicroMax",
        }
        if based_on:
            c["based_on"] = based_on
        if value_based_on:
            c["value_based_on"] = value_based_on
        c["filters_json"] = json.dumps(filters or [])
        if group_by:
            c["group_by_based_on"] = group_by
        _get_or_insert("Dashboard Chart", chart_name, c)

    # Dashboard
    dash_name = "MicroMax Export Dashboard"
    if not frappe.db.exists("Dashboard", dash_name):
        dash = frappe.new_doc("Dashboard")
        dash.dashboard_name = dash_name
        dash.module = "MicroMax"
        dash.is_default = 1
        for c in charts:
            dash.append("charts", {"chart": c[0]})
        for nc in NUMBER_CARDS:
            dash.append("cards", {"card": nc[0]})
        dash.insert(ignore_permissions=True)


def create_workspace():
    ws_name = "MicroMax"
    # Repair any existing workspace: ensure it is public and routable at
    # /desk/micromax so it shows in the sidebar and app switcher.
    if frappe.db.exists("Workspace", ws_name):
        frappe.db.set_value("Workspace", ws_name, {"public": 1, "is_hidden": 0})
        return
    import json
    links = [
        {"label": "Export ", "type": "Card Break"},
        {"label": "LC Proforma", "link_to": "LC Proforma", "link_type": "DocType", "onboard": 1, "type": "Link"},
        {"label": "Export Shipment", "link_to": "Export Shipment", "link_type": "DocType", "onboard": 1, "type": "Link"},
        {"label": "Export Packing Details", "link_to": "Export Packing Details", "link_type": "DocType", "onboard": 0, "type": "Link"},
        {"label": "Sales Order", "link_to": "Sales Order", "link_type": "DocType", "onboard": 1, "type": "Link"},
        {"label": "Customer / Buyer", "link_to": "Customer", "link_type": "DocType", "onboard": 0, "type": "Link"},
        {"label": "Import ", "type": "Card Break"},
        {"label": "Import Shipment", "link_to": "Import Shipment", "link_type": "DocType", "onboard": 1, "type": "Link"},
        {"label": "Import Cost Sheet", "link_to": "Import Cost Sheet", "link_type": "DocType", "onboard": 0, "type": "Link"},
        {"label": "Supplier", "link_to": "Supplier", "link_type": "DocType", "onboard": 0, "type": "Link"},
        {"label": "Purchase Order", "link_to": "Purchase Order", "link_type": "DocType", "onboard": 0, "type": "Link"},
        {"label": "Masters ", "type": "Card Break"},
        {"label": "Item ", "link_to": "Item", "link_type": "DocType", "onboard": 1, "type": "Link"},
        {"label": "Reports ", "type": "Card Break"},
        {"label": "LC Proforma Register", "link_to": "LC Proforma Register", "link_type": "Report", "onboard": 0, "type": "Link"},
        {"label": "Settings ", "type": "Card Break"},
    ]
    content = [
        {"id": "header1", "type": "header", "data": {"text": "<span class=\"h4\"><b>MicroMax</b></span>", "col": 12}},
        {"id": "card1", "type": "card", "data": {"card_name": "Export ", "col": 4}},
        {"id": "card2", "type": "card", "data": {"card_name": "Import ", "col": 4}},
        {"id": "card3", "type": "card", "data": {"card_name": "Masters ", "col": 4}},
        {"id": "card4", "type": "card", "data": {"card_name": "Reports ", "col": 4}},
        {"id": "card5", "type": "card", "data": {"card_name": "Settings ", "col": 4}},
    ]
    ws = frappe.new_doc("Workspace")
    ws.label = ws_name
    ws.title = ws_name
    ws.module = "MicroMax"
    ws.content = json.dumps(content)
    ws.app = "micromax"
    ws.type = "Workspace"
    ws.standard = 1
    ws.public = 1
    ws.is_hidden = 0
    ws.icon = "shirt"
    for l in links:
        ws.append("links", l)
    ws.insert(ignore_permissions=True)
