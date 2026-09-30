import frappe
from frappe.sessions import get_csrf_token


@frappe.whitelist()
def get_csrf_token_for_session() -> str:
    """Expose the current session's CSRF token to the decoupled frontend.

    Frappe normally injects ``window.csrf_token`` server-side into HTML pages
    it renders itself; ``frappe.sessions.get_csrf_token`` is not whitelisted
    for direct API access. The micromax frontend is served by Vite in dev (not
    by Frappe), so it has no such injection point and needs an explicit,
    minimal, whitelisted way to fetch the token for the logged-in session.
    """
    return get_csrf_token()


@frappe.whitelist()
def gl_filter_options(company: str, party_type: str | None = None, txt: str | None = None) -> dict:
    """Choices for the General Ledger filters, limited to what the company actually has in its ledger:
    parties of `party_type` it has posted against, and (for the voucher picker) its voucher numbers matching `txt`."""
    frappe.has_permission("GL Entry", "read", throw=True)
    out = {"parties": [], "vouchers": []}
    if party_type:
        out["parties"] = frappe.db.sql_list(
            """select distinct party from `tabGL Entry` where company = %s and party_type = %s and is_cancelled = 0
            and ifnull(party, '') != '' order by party""", (company, party_type))
    if txt is not None:
        out["vouchers"] = frappe.db.sql(
            """select voucher_no, max(voucher_type) voucher_type from `tabGL Entry` where company = %s and is_cancelled = 0
            and voucher_no like %s group by voucher_no order by max(posting_date) desc limit 20""",
            (company, f"%{txt}%"), as_dict=True)
    return out
