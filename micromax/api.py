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
