import frappe
from frappe.query_builder import DocType


@frappe.whitelist()
def get_linked_parent_docs(doctype: str, parenttype: str, purchase_order: str):
    """Return distinct parent document names of a given parenttype whose child
    rows reference the given Purchase Order.

    The link lives on the child table (e.g. Purchase Receipt Item.purchase_order),
    but the REST ``get_list`` API strips the ``parent`` field for child tables.
    This server-side helper does the lookup and returns deduplicated parent names.

    :param doctype: child DocType to search, e.g. "Purchase Receipt Item"
    :param parenttype: parent DocType, e.g. "Purchase Receipt"
    :param purchase_order: the Purchase Order name to link against
    """
    if not purchase_order:
        return []

    child = DocType(doctype)
    rows = (
        frappe.qb.from_(child)
        .select(child.parent)
        .where(child.purchase_order == purchase_order)
        .distinct()
        .limit(200)
        .run(as_dict=True)
    )
    return [r["parent"] for r in rows if r.get("parent")]
