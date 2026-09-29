"""Journal Entry "at a glance" (InsightsPanel shape — see selling_insights.py)."""

import frappe
from frappe import _
from frappe.utils import flt

from micromax.selling_insights import _bar, _row, _tile


@frappe.whitelist()
def get_je_insights(name):
	je = frappe.get_doc("Journal Entry", name)
	je.check_permission("read")
	accs = {a.name: a for a in frappe.get_all("Account", filters={"name": ["in", [r.account for r in je.accounts] or [""]]},
	                                          fields=["name", "root_type", "account_type", "account_name"])}
	by_root, bank, parties, refs = {}, 0.0, {}, []
	for r in je.accounts:
		a = accs.get(r.account) or frappe._dict()
		k = a.root_type or _("Other")
		by_root.setdefault(k, [0.0, 0.0])
		by_root[k][0] += flt(r.debit)
		by_root[k][1] += flt(r.credit)
		if a.account_type in ("Bank", "Cash"):
			bank += flt(r.debit) - flt(r.credit)
		if r.party:
			parties[f"{r.party_type}: {r.party}"] = parties.get(f"{r.party_type}: {r.party}", 0) + flt(r.debit) - flt(r.credit)
		if r.reference_type and r.reference_name and frappe.db.exists(r.reference_type, r.reference_name):
			out = frappe.db.get_value(r.reference_type, r.reference_name, "outstanding_amount") if frappe.get_meta(r.reference_type).has_field("outstanding_amount") else None
			refs.append(_row(r.reference_name, flt(r.debit) or flt(r.credit), "money", f"{r.reference_type} · now due {frappe.format(flt(out), 'Currency')}" if out is not None else r.reference_type,
			                 {"doctype": r.reference_type, "name": r.reference_name}))
	diff = flt(je.total_debit) - flt(je.total_credit)
	return {
		"tiles": [
			_tile(_("Total debit"), je.total_debit, "money", _("{0} lines").format(len(je.accounts)), "sky"),
			_tile(_("Total credit"), je.total_credit, "money", _("balanced") if abs(diff) < 0.005 else _("difference {0}").format(frappe.format(diff, "Currency")), "emerald" if abs(diff) < 0.005 else "rose"),
			_tile(_("Bank / cash movement"), bank, "money", _("into bank/cash") if bank > 0 else _("out of bank/cash") if bank < 0 else _("no bank or cash line"), "violet"),
			_tile(_("Parties"), len(parties), "number", ", ".join(list(parties)[:2]) or _("none"), "amber"),
		],
		"bars": [_bar(k, (v[0] + v[1]) / (flt(je.total_debit) + flt(je.total_credit)) * 100 if (je.total_debit or je.total_credit) else 0,
		              f"Dr {frappe.format(v[0], 'Currency')} · Cr {frappe.format(v[1], 'Currency')}", {"Asset": "sky", "Liability": "amber", "Income": "emerald", "Expense": "rose", "Equity": "violet"}.get(k, "primary"))
		         for k, v in sorted(by_root.items(), key=lambda x: -(x[1][0] + x[1][1]))],
		"lists": [{"title": _("Parties (net debit)"), "rows": [_row(k, v, "money") for k, v in sorted(parties.items(), key=lambda x: -abs(x[1]))[:8]]},
		          {"title": _("Settles"), "rows": refs[:8]}],
	}
