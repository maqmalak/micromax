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
    # MicroMax Information
    add("Item", "fabric_material_composition", "Fabric/Material Composition", "Data", None, "customs_division_break", None)
    add("Item", "gsm", "GSM", "Data", None, "fabric_material_composition", None)
    add("Item", "commercial_description", "Commercial Description", "Text", None, "gsm", None)
    add("Item", "customs_description", "Customs Description", "Text", None, "commercial_description", None)
    add("Item", "export_uom", "Export UOM", "Link", "UOM", "customs_description", None)
    add("Item", "packing_type", "Packing Type", "Select", "\nBale\nCarton\nCase\nPolybag\nRoll\nOther", "export_uom", None)
    add("Item", "net_weight_per_unit", "Net Weight per Unit", "Float", None, "packing_type", None)
    add("Item", "gross_weight_per_unit", "Gross Weight per Unit", "Float", None, "net_weight_per_unit", None)

    # ---------------------------------------------------------- Sales Order
    # LC Proforma is a MicroMax doctype; the rest of the export section lives in mm_core.custom_fields.
    add("Sales Order", "lc_proforma", "LC Proforma", "Link", "LC Proforma", "buyer_po_no", None)

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

    # ---------------------------------------------------------- Account
    # Cost-per-spindle (CPS) tagging. Expense accounts flagged here are the ones
    # the Production dashboard's cost-per-spindle figures are built from, so the
    # mill can say which costs (power, wages, spares …) count against the spindle
    # count. A plain Check so it survives a Chart of Accounts import and can be
    # filtered on later (Account.cps_applicable = 1).
    add(
        "Account",
        "cps_applicable",
        "CPS Applicable",
        "Check",
        None,
        "include_in_gross",
        None,
        description=(
            "Count this account's expenses towards the cost-per-spindle figures "
            "on the Production dashboard."
        ),
    )

    # ---------------------------------------------------------- BOM
    # Production (own fibre, mill-owned yarn) vs Conversion (customer-supplied fibre / third-party yarn);
    # set from the item's group in mfg_logic.bom_before_validate, filterable in list views.
    data.setdefault("BOM", []).append({
        "fieldname": "bom_category", "label": "BOM Category", "fieldtype": "Select", "options": "Production\nConversion",
        "insert_after": "bom_type", "read_only": 1, "allow_on_submit": 1, "in_list_view": 1, "in_standard_filter": 1,
        "description": "Conversion = the item is third-party / conversion stock (customer-supplied fibre).",
    })

    # ---------------------------------------------------------- Spinning production (Work Order / Downtime / Workstation)
    # Spindle, yield, OPS and waste figures computed in mfg_logic.work_order_validate and read by the Production and
    # WO Analysis dashboards (micromax.dashboards._production / _wo_analysis). They used to exist only as site-level
    # Custom Fields, so a fresh site had none of these columns and both dashboards failed with "Unknown column".
    for dt, field in (
        ("Work Order", {"fieldname": "spindle_allocated", "label": "Spindle Allocated", "fieldtype": "Int", "insert_after": "bom_no", "non_negative": 1, "reqd": 1}),
        ("Work Order", {"fieldname": "bags", "label": "Bags", "fieldtype": "Float", "insert_after": "qty", "read_only": 1}),
        ("Work Order", {"fieldname": "work_order_date", "label": "Work Order Date", "fieldtype": "Date", "insert_after": "project", "read_only": 1, "fetch_from": "production_plan.posting_date", "reqd": 1}),
        ("Work Order", {"fieldname": "section_break_29", "fieldtype": "Section Break", "insert_after": "required_items"}),
        ("Work Order", {"fieldname": "material_required", "label": "Material Required", "fieldtype": "Float", "insert_after": "section_break_29", "read_only": 1}),
        ("Work Order", {"fieldname": "material_issued", "label": "Material Issued", "fieldtype": "Float", "insert_after": "material_required", "read_only": 1}),
        ("Work Order", {"fieldname": "target_yield", "label": "Target Yield", "fieldtype": "Percent", "insert_after": "material_issued", "read_only": 1}),
        ("Work Order", {"fieldname": "section_break_40", "fieldtype": "Section Break", "insert_after": "target_yield"}),
        ("Work Order", {"fieldname": "target_waste", "label": "Target Waste", "fieldtype": "Float", "insert_after": "target_yield", "read_only": 1}),
        ("Work Order", {"fieldname": "total_downtime", "label": "Total Downtime", "fieldtype": "Float", "insert_after": "section_break_40", "read_only": 1, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "target_waste_percentage", "label": "Target Waste Percentage", "fieldtype": "Percent", "insert_after": "target_waste", "read_only": 1}),
        ("Work Order", {"fieldname": "column_break_35", "fieldtype": "Column Break", "insert_after": "target_waste_percentage"}),
        ("Work Order", {"fieldname": "target_ops", "label": "Target OPS", "fieldtype": "Float", "insert_after": "column_break_35", "read_only": 1}),
        ("Work Order", {"fieldname": "spindle_required", "label": "Spindle Required", "fieldtype": "Float", "insert_after": "target_ops", "read_only": 1, "precision": 9}),
        ("Work Order", {"fieldname": "frame_required", "label": "Frame Required", "fieldtype": "Float", "insert_after": "spindle_required", "read_only": 1, "precision": 9}),
        ("Work Order", {"fieldname": "per_shift_frame_required", "label": "Per Shift Frame Required", "fieldtype": "Float", "insert_after": "frame_required", "read_only": 1, "precision": 9}),
        ("Work Order", {"fieldname": "actual_section", "fieldtype": "Section Break", "insert_after": "per_shift_frame_required"}),
        ("Work Order", {"fieldname": "actual_waste", "label": "Actual Waste", "fieldtype": "Float", "insert_after": "actual_section", "read_only": 1, "precision": 9, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "actual_waste_percentage", "label": "Actual Waste Percentage", "fieldtype": "Percent", "insert_after": "actual_waste", "read_only": 1, "precision": 9, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "actual_yield", "label": "Actual Yield", "fieldtype": "Percent", "insert_after": "actual_waste_percentage", "read_only": 1, "precision": 9, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "column_break_44", "fieldtype": "Column Break", "insert_after": "actual_yield"}),
        ("Work Order", {"fieldname": "actual_ops", "label": "Actual OPS", "fieldtype": "Float", "insert_after": "column_break_44", "read_only": 1, "precision": 9, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "spindle_worked", "label": "Spindle Worked", "fieldtype": "Float", "insert_after": "actual_ops", "read_only": 1, "precision": 9, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "actual_frame_required", "label": "Actual Frame Required", "fieldtype": "Float", "insert_after": "spindle_worked", "read_only": 1, "precision": 9, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "actual_per_shift_frame_required", "label": "Actual Per Shift Frame Required", "fieldtype": "Float", "insert_after": "actual_frame_required", "read_only": 1, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "stopage_in_minutes", "label": "Stopage In Minutes", "fieldtype": "Int", "insert_after": "actual_per_shift_frame_required", "read_only": 1, "allow_on_submit": 1}),
        ("Work Order", {"fieldname": "frame_stopage", "label": "Frame Stopage", "fieldtype": "Int", "insert_after": "stopage_in_minutes", "read_only": 1, "allow_on_submit": 1}),
        ("Downtime Entry", {"fieldname": "work_order", "label": "Work Order", "fieldtype": "Link", "options": "Work Order", "insert_after": "naming_series", "read_only": 1}),
        ("Workstation", {"fieldname": "spindles", "label": "Spindles", "fieldtype": "Int", "insert_after": "column_break_3", "non_negative": 1, "reqd": 1}),
    ):
        data.setdefault(dt, []).append(field)
    # ---------------------------------------------------------- Spinning BOM / blend / machine fields
    # Blend ratio, yield, waste, OPS and spindle fields on BOM, BOM Item, Work Order Item, Stock Entry and
    # Workstation that micromax.mfg_logic reads and writes. Like the Work Order block above they used to exist
    # only as site-level Custom Fields (from the old hik apps), so a fresh site could not hold a Spinning BOM.
    for dt, field in (
        ("BOM", {"fieldname": "sales_order", "label": "Sales Order", "fieldtype": "Link", "options": "Sales Order", "insert_after": "project", "read_only": 1}),
        ("BOM", {"fieldname": "bom_type", "label": "BOM Type", "fieldtype": "Select", "options": "Spinning\nWeaving\nDyeing\nCutting\nStitching\nPacking", "insert_after": "quantity", "default": "Spinning"}),
        ("BOM", {"fieldname": "main_operation", "label": "Main Operation", "fieldtype": "Link", "options": "Operation", "insert_after": "operations_section"}),
        ("BOM", {"fieldname": "section_break_34", "fieldtype": "Section Break", "insert_after": "scrap_items"}),
        ("BOM", {"fieldname": "invisible_lost_percentage", "label": "Invisible Lost Percentage", "fieldtype": "Percent", "insert_after": "section_break_34"}),
        ("BOM", {"fieldname": "material_required", "label": "Material Required", "fieldtype": "Float", "insert_after": "invisible_lost_percentage", "read_only": 1}),
        ("BOM", {"fieldname": "material_issued", "label": "Material Issued", "fieldtype": "Float", "insert_after": "material_required", "read_only": 1}),
        ("BOM", {"fieldname": "target_yield", "label": "Target Yield", "fieldtype": "Percent", "insert_after": "material_issued", "read_only": 1}),
        ("BOM", {"fieldname": "target_waste", "label": "Target Waste", "fieldtype": "Float", "insert_after": "target_yield", "read_only": 1}),
        ("BOM", {"fieldname": "target_waste_percentage", "label": "Target Waste Percentage", "fieldtype": "Percent", "insert_after": "target_waste", "read_only": 1}),
        ("BOM", {"fieldname": "column_break_39", "fieldtype": "Column Break", "insert_after": "target_waste_percentage"}),
        ("BOM", {"fieldname": "invisible_lose_qty", "label": "Invisible Lose Qty", "fieldtype": "Float", "insert_after": "column_break_39", "non_negative": 1}),
        ("BOM", {"fieldname": "target_ops", "label": "Target OPS", "fieldtype": "Float", "insert_after": "invisible_lose_qty"}),
        ("BOM", {"fieldname": "spindle_required", "label": "Spindle Required", "fieldtype": "Float", "insert_after": "target_ops", "read_only": 1, "precision": "9"}),
        ("BOM", {"fieldname": "frame_required", "label": "Frame Required", "fieldtype": "Float", "insert_after": "spindle_required", "read_only": 1, "precision": "9"}),
        ("BOM", {"fieldname": "per_shift_frame_required", "label": "Per Shift Frame Required", "fieldtype": "Float", "insert_after": "frame_required", "read_only": 1, "precision": "9"}),
        ("BOM", {"fieldname": "invisible_lose_percentage", "label": "Invisible Lose Percentage", "fieldtype": "Percent", "insert_after": "per_shift_frame_required", "depends_on": "eval:doc.bom_type == 'Spinning';"}),
        ("BOM Item", {"fieldname": "blend_ratio", "label": "Blend Ratio", "fieldtype": "Percent", "insert_after": "allow_alternative_item", "in_list_view": 1, "reqd": 1}),
        ("BOM Item", {"fieldname": "item_yield", "label": "Yield", "fieldtype": "Percent", "insert_after": "qty", "in_list_view": 1}),
        ("BOM Item", {"fieldname": "gross_up_qty", "label": "G/U QTY", "fieldtype": "Float", "insert_after": "item_yield", "read_only": 1, "in_list_view": 1}),
        ("Work Order Item", {"fieldname": "blend_ratio", "label": "Blend Ratio", "fieldtype": "Percent", "insert_after": "qty_section", "read_only": 1, "in_list_view": 1}),
        ("Work Order Item", {"fieldname": "material_required", "label": "Material Required", "fieldtype": "Float", "insert_after": "blend_ratio", "read_only": 1}),
        ("Work Order Item", {"fieldname": "item_yield", "label": "Item Yield", "fieldtype": "Percent", "insert_after": "material_required", "read_only": 1}),
        ("Work Order Item", {"fieldname": "waste", "label": "Waste", "fieldtype": "Float", "insert_after": "available_qty_at_wip_warehouse", "read_only": 1, "precision": "9", "allow_on_submit": 1}),
        ("Work Order Item", {"fieldname": "waste_percentage", "label": "Waste Percentage", "fieldtype": "Percent", "insert_after": "waste", "read_only": 1, "precision": "9", "allow_on_submit": 1}),
        ("Workstation", {"fieldname": "operation", "label": "Operation", "fieldtype": "Link", "options": "Operation", "insert_after": "column_break_3"}),
        ("Workstation", {"fieldname": "out_of_order", "label": "Out Of Order", "fieldtype": "Int", "insert_after": "spindles", "non_negative": 1, "reqd": 1}),
        ("Workstation", {"fieldname": "capacity_per_day", "label": "Capacity Per Day", "fieldtype": "Int", "insert_after": "out_of_order", "read_only": 1, "non_negative": 1, "reqd": 1}),
        ("Workstation", {"fieldname": "spinning_section", "label": "Spinning", "fieldtype": "Section Break", "insert_after": "capacity_per_day", "depends_on": "eval:doc.workstation_type == 'Spinning';"}),
        ("Workstation", {"fieldname": "column_break_8", "fieldtype": "Column Break", "insert_after": "out_of_order"}),
        ("Workstation", {"fieldname": "shifts_per_day", "label": "Shifts Per Day", "fieldtype": "Int", "insert_after": "column_break_8"}),
        ("Workstation", {"fieldname": "other_details", "label": "Other Details", "fieldtype": "Tab Break", "insert_after": "working_hours"}),
        ("Workstation", {"fieldname": "operator", "label": "Operator", "fieldtype": "Link", "options": "Employee", "insert_after": "other_details"}),
        ("Workstation", {"fieldname": "column_break_aihn4", "fieldtype": "Column Break", "insert_after": "operator"}),
        ("Workstation", {"fieldname": "assistant", "label": "Assistant", "fieldtype": "Link", "options": "Employee", "insert_after": "column_break_aihn4"}),
        ("Operation", {"fieldname": "capacity", "label": "Capacity", "fieldtype": "Float", "insert_after": "workstation", "read_only": 1}),
        ("Operation", {"fieldname": "operation_type", "label": "Operation Type", "fieldtype": "Select", "options": "Spinning\nWeaving\nDying\nCutting\nStitching\nPacking", "insert_after": "capacity"}),
        ("Production Plan Item", {"fieldname": "item_name", "label": "Item Name", "fieldtype": "Data", "insert_after": "item_code", "read_only": 1, "fetch_from": "item_code.item_name", "in_list_view": 1}),
        ("Production Plan Item", {"fieldname": "section_break_14", "fieldtype": "Section Break", "insert_after": "produced_qty"}),
        ("Production Plan Item", {"fieldname": "ops", "label": "OPS", "fieldtype": "Float", "insert_after": "section_break_14", "read_only": 1, "fetch_from": "bom_no.target_ops"}),
        ("Production Plan Item", {"fieldname": "spindle_required", "label": "Spindle Required", "fieldtype": "Float", "insert_after": "ops", "read_only": 1, "non_negative": 1}),
        ("Production Plan Item", {"fieldname": "frame_required", "label": "Frame Required", "fieldtype": "Float", "insert_after": "spindle_required", "read_only": 1, "non_negative": 1}),
        ("Production Plan Item", {"fieldname": "column_break_18", "fieldtype": "Column Break", "insert_after": "frame_required"}),
        ("Production Plan Item", {"fieldname": "frame_allocated_per_day", "label": "Frame Allocated Per Day", "fieldtype": "Float", "insert_after": "column_break_18", "non_negative": 1}),
        ("Production Plan Item", {"fieldname": "total_days_required", "label": "Total Days Required", "fieldtype": "Data", "insert_after": "frame_allocated_per_day", "read_only": 1}),
        ("Stock Entry", {"fieldname": "stopage_in_minutes", "label": "Stopage In Minutes", "fieldtype": "Int", "insert_after": "work_order", "read_only": 1, "depends_on": "eval:doc.purpose == 'Manufacture';"}),
        ("Stock Entry", {"fieldname": "frame_stopage", "label": "Frame Stopage", "fieldtype": "Int", "insert_after": "stopage_in_minutes", "read_only": 1, "depends_on": "eval:doc.purpose == 'Manufacture';"}),
        ("Stock Entry", {"fieldname": "strips", "label": "Strips", "fieldtype": "Float", "insert_after": "purchase_receipt_no", "depends_on": "eval:doc.stock_entry_type == 'Conversion Production';"}),
        ("Stock Entry", {"fieldname": "section_break_46", "fieldtype": "Section Break", "insert_after": "value_difference"}),
        ("Stock Entry", {"fieldname": "total_input_qty", "label": "Total Input Qty", "fieldtype": "Float", "insert_after": "section_break_46", "read_only": 1}),
        ("Stock Entry", {"fieldname": "process_loss", "label": "Process Loss", "fieldtype": "Float", "insert_after": "total_input_qty", "read_only": 1}),
        ("Stock Entry", {"fieldname": "column_break_49", "fieldtype": "Column Break", "insert_after": "process_loss"}),
        ("Stock Entry", {"fieldname": "total_output_qty", "label": "Total Output Qty", "fieldtype": "Float", "insert_after": "column_break_49", "read_only": 1}),
        ("Stock Entry", {"fieldname": "process_loss_section", "label": "Process Loss Section", "fieldtype": "Section Break", "insert_after": "total_output_qty"}),
        ("Stock Entry Detail", {"fieldname": "waste", "label": "Waste", "fieldtype": "Float", "insert_after": "job_card_item", "read_only": 1, "precision": "9"}),
        ("Stock Entry Detail", {"fieldname": "waste_percentage", "label": "Waste Percentage", "fieldtype": "Percent", "insert_after": "waste", "read_only": 1, "precision": "9"}),
        ("Sales Order Item", {"fieldname": "bags", "label": "Bags", "fieldtype": "Float", "insert_after": "qty", "in_list_view": 1, "non_negative": 1, "reqd": 1}),
    ):
        data.setdefault(dt, []).append(field)

    # ---------------------------------------------------------- Branch location
    # Address and map coordinates, read by the React HR Branches page to pin every branch on a map.
    for field in (
        {"fieldname": "location_section", "label": "Location", "fieldtype": "Section Break", "insert_after": "branch"},
        {"fieldname": "branch_address", "label": "Address", "fieldtype": "Small Text", "insert_after": "location_section"},
        {"fieldname": "city", "label": "City", "fieldtype": "Data", "insert_after": "branch_address", "in_list_view": 1},
        {"fieldname": "location_column", "fieldtype": "Column Break", "insert_after": "city"},
        {"fieldname": "latitude", "label": "Latitude", "fieldtype": "Float", "precision": "6", "insert_after": "location_column"},
        {"fieldname": "longitude", "label": "Longitude", "fieldtype": "Float", "precision": "6", "insert_after": "latitude"},
    ):
        data.setdefault("Branch", []).append(field)

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

def _drop_fields_that_now_exist_natively(data):
    """A few fields defined below (Sales Order.incoterm, Item.country_of_origin)
    were added back when ERPNext didn't ship them. ERPNext v16 now has native
    equivalents (Sales Order.incoterm as Link -> Incoterm; Item.country_of_origin
    as Link -> Country) — creating our own Custom Field of the same fieldname on
    top of a native one leaves the doctype with a duplicate fieldname, which
    Frappe only surfaces later as `Fieldname X appears multiple times in rows`
    on some unrelated Custom Field save (e.g. another app's fixture import).
    Every `before_migrate` run re-adds these via create_custom_fields, so the
    guard has to live here — deleting the stray Custom Field once isn't enough.
    `insert_after` chains that pointed at a dropped field still resolve fine,
    since insert_after only needs a field of that name to exist, custom or not."""
    for dt, fields in list(data.items()):
        kept = []
        for field in fields:
            fieldname = field["fieldname"]
            if frappe.get_meta(dt).has_field(fieldname) and not frappe.db.exists(
                "Custom Field", f"{dt}-{fieldname}"
            ):
                continue  # native field of this name already exists — skip
            kept.append(field)
        data[dt] = kept
    return data


def _adopt_orphaned_custom_fields(data):
    """Custom Fields carried over from the old hik apps can still name their module (e.g. "Lucrum Payroll"),
    which no longer exists as a Module Def. Updating such a record fails link validation ("Could not find
    Module (for export)"), so hand any we are about to update to MicroMax first."""
    for dt, fields in data.items():
        for field in fields:
            name = f"{dt}-{field['fieldname']}"
            module = frappe.db.get_value("Custom Field", name, "module")
            if module and not frappe.db.exists("Module Def", module):
                frappe.db.set_value("Custom Field", name, "module", "MicroMax", update_modified=False)


def add_hr_indexes():
    """Employee Checkin has no index on `time`, so a one-day check-in count scans the whole table (~20 s at
    a million rows). The Shift and Attendance pages filter on it."""
    if frappe.db.table_exists("Employee Checkin"):
        frappe.db.add_index("Employee Checkin", ["time"])
        frappe.db.add_index("Employee Checkin", ["employee", "time"])


def make_custom_fields():
    data = _drop_fields_that_now_exist_natively(_build_custom_fields())
    if frappe.db.table_exists("Custom Field") and data:
        _adopt_orphaned_custom_fields(data)
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
