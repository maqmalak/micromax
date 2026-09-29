"""Payment Entry "at a glance" and ledger tab: party and bank balances, where the money was allocated (and what each
referenced document still owes), and the GL entries the payment posted."""

import frappe
from frappe.utils import flt


@frappe.whitelist()
def get_payment_insights(name):
	pe = frappe.get_doc("Payment Entry", name)
	pe.check_permission("read")
	bank = pe.paid_from if pe.payment_type == "Pay" else pe.paid_to
	balance = lambda acc, **f: flt(frappe.db.sql(  # noqa: E731
		f"""select sum(debit - credit) from `tabGL Entry` where account = %(a)s and is_cancelled = 0 and company = %(c)s
		{'and party_type = %(pt)s and party = %(p)s' if f else ''}""", {"a": acc, "c": pe.company, **f})[0][0])
	party_bal = None
	if pe.party_type and pe.party:
		party_bal = flt(frappe.db.sql("""select sum(debit - credit) from `tabGL Entry` where party_type = %s and party = %s and company = %s and is_cancelled = 0""",
		                              (pe.party_type, pe.party, pe.company))[0][0])
	refs = []
	for r in pe.references:
		if not (r.reference_name and frappe.db.exists(r.reference_doctype, r.reference_name)):
			continue
		m = frappe.get_meta(r.reference_doctype)
		cur = frappe.db.get_value(r.reference_doctype, r.reference_name,
		                          ["grand_total", "status"] + (["outstanding_amount"] if m.has_field("outstanding_amount") else []), as_dict=True)
		total = flt(r.total_amount) or flt(cur.grand_total)
		due_now = flt(cur.get("outstanding_amount")) if "outstanding_amount" in cur else None
		# What else settled this document: other payment entries, journal entries, credit/debit notes.
		other_pe = frappe.db.sql("""select count(distinct p.name) n, sum(r.allocated_amount) v from `tabPayment Entry Reference` r join `tabPayment Entry` p on p.name = r.parent
			where r.reference_doctype = %s and r.reference_name = %s and p.docstatus = 1 and p.name != %s""", (r.reference_doctype, r.reference_name, name), as_dict=True)[0]
		other_je = frappe.db.sql("""select count(distinct j.name) n, sum(abs(a.debit_in_account_currency - a.credit_in_account_currency)) v
			from `tabJournal Entry Account` a join `tabJournal Entry` j on j.name = a.parent
			where a.reference_type = %s and a.reference_name = %s and j.docstatus = 1""", (r.reference_doctype, r.reference_name), as_dict=True)[0]
		returns = frappe.db.sql(f"""select count(*) n, sum(abs(grand_total)) v from `tab{r.reference_doctype}` where return_against = %s and docstatus = 1""",
		                        r.reference_name, as_dict=True)[0] if m.has_field("return_against") else frappe._dict(n=0, v=0)
		allocated = flt(r.allocated_amount)
		others = max(total - allocated - (due_now or 0), 0) if due_now is not None else flt(other_pe.v) + flt(other_je.v) + flt(returns.v)
		parts = [f"{int(other_pe.n)} other payment{'s' if other_pe.n != 1 else ''}" if other_pe.n else None,
		         f"{int(other_je.n)} journal entr{'ies' if other_je.n != 1 else 'y'}" if other_je.n else None,
		         f"{int(returns.n)} return{'s' if returns.n != 1 else ''}" if returns.n else None]
		refs.append({"doctype": r.reference_doctype, "name": r.reference_name, "bill_no": r.get("bill_no"), "due_date": str(r.get("due_date") or ""),
		             "total": total, "allocated": allocated, "this_pct": round(allocated / total * 100, 1) if total else 0,
		             "others": round(others, 2), "others_pct": round(others / total * 100, 1) if total else 0, "others_detail": ", ".join(p for p in parts if p) or None,
		             "due_now": due_now, "due_pct": round((due_now or 0) / total * 100, 1) if total else 0, "status": cur.status})
	gl = frappe.db.sql("""select account, party, debit, credit, against_voucher_type, against_voucher, cost_center, remarks from `tabGL Entry`
		where voucher_type = 'Payment Entry' and voucher_no = %s and is_cancelled = 0 order by debit desc, credit desc""", name, as_dict=True)
	return {
		"bank_account": bank, "bank_balance": balance(bank) if bank else None,
		"party_balance": party_bal,
		"allocated": flt(pe.total_allocated_amount), "unallocated": flt(pe.unallocated_amount), "paid": flt(pe.paid_amount),
		"references": refs,
		"gl": gl, "gl_debit": sum(flt(g.debit) for g in gl), "gl_credit": sum(flt(g.credit) for g in gl),
	}
