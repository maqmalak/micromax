import frappe
from frappe.model.document import Document


class MicromaxDefaults(Document):
	pass


def get_micromax_defaults() -> dict:
	"""The current site-wide defaults (theme, page size, view, docstatus filter) —
	call this from the frontend's boot info / a whitelisted API instead of
	reading the Single doctype directly, so callers get plain values without
	needing read access to the doctype itself."""
	doc = frappe.get_cached_doc("Micromax Defaults")
	return {
		"theme": doc.theme,
		"table_row": doc.table_row,
		"view": doc.view,
		"filter": doc.filter,
	}
