import frappe


def update_from_status(doc, method=None):
    """Event hook: keep `lc_status` in sync with the workflow state."""
    state = doc.get("workflow_state")
    if state:
        doc.lc_status = state