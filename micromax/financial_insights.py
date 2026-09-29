"""Insight headers for the React financial reports (General Ledger, Trial Balance, Balance Sheet, Cash Flow).

Straight from GL Entry (cancelled entries excluded); same InsightsPanel shape as selling_insights, plus an optional
"chart": {"title", "series": [{key, label, color?}], "data": [...], "money": bool, "xKey"}."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate

from micromax.selling_insights import _bar, _row, _tile

# Period Closing Vouchers move the year's P&L into equity; they are bookkeeping, not activity, so they are left out.
C = "is_cancelled = 0 and company = %(co)s and voucher_type != 'Period Closing Voucher'"
IDX = "use index (micromax_gl_analysis)"      # (company, posting_date, is_cancelled, voucher_type, account, debit, credit)
IDX_P = "use index (micromax_gl_party)"       # (company, posting_date, is_cancelled, voucher_no, account, party_type, party, debit, credit)
CACHE_TTL = 30 * 60


def _months(f, t):
	out, d = [], get_first_day(f)
	while d <= t and len(out) < 36:
		out.append(d.strftime("%Y-%m"))
		d = add_months(d, 1)
	return out


def _lbl(m):
	return getdate(f"{m}-01").strftime("%b %y")


@frappe.whitelist()
def get_report_insights(report, company, from_date=None, to_date=None, account=None, party_type=None, party=None, voucher_no=None):
	frappe.has_permission("GL Entry", "read", throw=True)
	t = getdate(to_date) if to_date else getdate()
	f = getdate(from_date) if from_date else get_first_day(add_months(t, -11))
	a = {"co": company, "f": f, "t": t, "acc": account, "pt": party_type, "p": party, "vn": voucher_no}
	key = f"micromax-fin-insights:{report}:{company}:{f}:{t}:{account}:{party_type}:{party}:{voucher_no}"
	hit = frappe.cache.get_value(key)
	if hit:
		return hit
	out = {"gl": _gl, "trial_balance": _tb, "balance_sheet": _bs, "cash_flow": _cf}[report](a)
	frappe.cache.set_value(key, out, expires_in_sec=CACHE_TTL)
	return out


# ----------------------------------------------------------------------------- General Ledger
def _gl(a):
	w = C
	filtered = bool(a["acc"] or a["p"] or a["vn"])
	if a["acc"]:
		lft, rgt = frappe.db.get_value("Account", a["acc"], ["lft", "rgt"])
		a["accs"] = frappe.db.sql_list("select name from `tabAccount` where lft >= %s and rgt <= %s", (lft, rgt))
		w += " and account in %(accs)s"
	if a["pt"] and a["p"]:
		w += " and party_type = %(pt)s and party = %(p)s"
	if a["vn"]:
		w += " and voucher_no = %(vn)s"
	sql = frappe.db.sql
	idx = IDX_P if (a["p"] or a["vn"]) else IDX
	opening = flt(sql(f"select sum(debit - credit) from `tabGL Entry` {idx} where {w} and posting_date < %(f)s", a)[0][0]) if filtered else 0
	# One grouped pass gives the totals, the months, the voucher types and the accounts.
	cube = sql(f"""select date_format(posting_date, '%%Y-%%m') m, voucher_type, account, sum(debit) d, sum(credit) c, count(*) n
		from `tabGL Entry` {idx} where {w} and posting_date between %(f)s and %(t)s group by m, voucher_type, account""", a, as_dict=True)
	d = sum(flt(r.d) for r in cube)
	c = sum(flt(r.c) for r in cube)
	n = sum(r.n for r in cube)
	monthly, vt, accounts = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0]), defaultdict(lambda: [0.0, 0.0, 0])
	for r in cube:
		monthly[r.m][0] += flt(r.d); monthly[r.m][1] += flt(r.c)
		vt[r.voucher_type][0] += flt(r.d) + flt(r.c); vt[r.voucher_type][1] += r.n
		accounts[r.account][0] += flt(r.d); accounts[r.account][1] += flt(r.c); accounts[r.account][2] += r.n
	parties = sql(f"""select concat(party_type, ': ', party) label, sum(debit - credit) v from `tabGL Entry` {IDX_P} where {w} and posting_date between %(f)s and %(t)s
		and ifnull(party, '') != '' group by party_type, party order by abs(sum(debit - credit)) desc limit 8""", a, as_dict=True)
	big = sql(f"""select voucher_type, voucher_no, posting_date, account, debit, credit from `tabGL Entry` where {w} and posting_date between %(f)s and %(t)s
		order by greatest(debit, credit) desc limit 6""", a, as_dict=True) if filtered else []
	months = _months(a["f"], a["t"])
	top_vt = sorted(vt.items(), key=lambda x: -x[1][0])[:6]
	top_acc = sorted(accounts.items(), key=lambda x: -(x[1][0] + x[1][1]))[:25]
	from urllib.parse import urlencode
	gl_href = lambda acc: "/accounting/reports/general-ledger?" + urlencode({"account": acc, "company": a["co"], "from_date": str(a["f"]), "to_date": str(a["t"])})  # noqa: E731
	return {
		"tiles": [
			_tile(_("Opening balance"), opening if filtered else None, "money", _("before {0}").format(a["f"]) if filtered else _("filter an account or party to see balances"), "primary"),
			_tile(_("Debits"), d, "money", _("{0} entries").format(n), "sky"),
			_tile(_("Credits"), c, "money", _("{0} voucher types").format(len(vt)), "violet"),
			_tile(_("Closing balance") if filtered else _("Net movement"), opening + d - c, "money", _("Dr") if opening + d - c >= 0 else _("Cr"), "emerald" if filtered or abs(d - c) < 0.5 else "amber"),
		],
		"bars": [_bar(k, v[0] / (d + c) * 100 if d + c else 0, _("{0} lines").format(v[1]), "primary") for k, v in top_vt],
		"lists": [
			{"title": _("Largest entries"), "rows": [_row(f"{r.voucher_no}", max(flt(r.debit), flt(r.credit)), "money", f"{r.posting_date} · {r.account[:40]}",
			                                             {"doctype": r.voucher_type, "name": r.voucher_no}) for r in big]},
			{"title": _("Parties (net)"), "rows": [_row(r.label, r.v, "money") for r in parties]},
		],
		"tables": [{
			"title": _("Summary by account"),
			"subtitle": _("{0} accounts moved · top {1} by activity").format(len(accounts), len(top_acc)),
			"columns": [{"key": "account", "label": _("Account"), "hrefKey": "href"}, {"key": "entries", "label": _("Entries"), "fmt": "number", "align": "right"},
			            {"key": "debit", "label": _("Debit"), "fmt": "money", "align": "right"}, {"key": "credit", "label": _("Credit"), "fmt": "money", "align": "right"},
			            {"key": "net", "label": _("Net"), "fmt": "drcr", "align": "right"}, {"key": "share", "label": _("Share of activity"), "fmt": "share"}],
			"rows": [{"account": k, "href": gl_href(k), "entries": v[2], "debit": round(v[0], 2), "credit": round(v[1], 2), "net": round(v[0] - v[1], 2),
			          "share": round((v[0] + v[1]) / (d + c) * 100, 2) if d + c else 0} for k, v in top_acc],
			"total": {"account": _("Total (all accounts)"), "entries": n, "debit": round(d, 2), "credit": round(c, 2), "net": round(d - c, 2)},
		}],
		"chart": {"title": _("Debits and credits by month"), "xKey": "month", "money": True,
		          "series": [{"key": "debit", "label": _("Debit")}, {"key": "credit", "label": _("Credit"), "color": "hsl(35 92% 50%)"}],
		          "data": [{"month": _lbl(m), "debit": round(monthly[m][0], 2), "credit": round(monthly[m][1], 2)} for m in months]},
	}


# ----------------------------------------------------------------------------- Trial Balance
def _tb(a):
	sql = frappe.db.sql
	rows = sql(f"""select a.name, a.root_type, a.account_type,
			sum(if(g.posting_date < %(f)s, g.debit - g.credit, 0)) opening,
			sum(if(g.posting_date between %(f)s and %(t)s, g.debit, 0)) d, sum(if(g.posting_date between %(f)s and %(t)s, g.credit, 0)) c
		from `tabGL Entry` g use index (micromax_gl_analysis) join `tabAccount` a on a.name = g.account
		where g.is_cancelled = 0 and g.company = %(co)s and g.voucher_type != 'Period Closing Voucher' and g.posting_date <= %(t)s group by a.name""", a, as_dict=True)
	root = defaultdict(lambda: [0.0, 0.0, 0.0])
	for r in rows:
		r.closing = flt(r.opening) + flt(r.d) - flt(r.c)
		x = root[r.root_type or "Other"]
		x[0] += flt(r.d); x[1] += flt(r.c); x[2] += r.closing
	d, c = sum(x[0] for x in root.values()), sum(x[1] for x in root.values())
	odd = [r for r in rows if abs(r.closing) > 1 and ((r.root_type == "Asset" and r.closing < 0 and r.account_type not in ("Accumulated Depreciation",))
	                                                   or (r.root_type in ("Liability", "Equity") and r.closing > 0 and r.account_type not in ("Receivable",)))]
	big = sorted(rows, key=lambda r: -abs(r.closing))[:8]
	colours = {"Asset": "sky", "Liability": "amber", "Equity": "violet", "Income": "emerald", "Expense": "rose"}
	return {
		"tiles": [
			_tile(_("Period debits"), d, "money", _("{0} accounts moved").format(sum(1 for r in rows if flt(r.d) or flt(r.c))), "sky"),
			_tile(_("Period credits"), c, "money", _("balanced") if abs(d - c) < 0.5 else _("difference {0}").format(frappe.format(d - c, "Currency")), "emerald" if abs(d - c) < 0.5 else "rose"),
			_tile(_("Net profit (period)"), -(root["Income"][0] - root["Income"][1]) - (root["Expense"][0] - root["Expense"][1]), "money", _("income − expense movement"), "emerald"),
			_tile(_("Wrong-side balances"), len(odd), "number", _("assets in credit / liabilities in debit"), "rose" if odd else "emerald"),
		],
		"bars": [_bar(k, (v[0] + v[1]) / (d + c) * 100 if d + c else 0, f"closing {frappe.format(v[2], 'Currency')}", colours.get(k, "primary"))
		         for k, v in sorted(root.items(), key=lambda x: -(x[1][0] + x[1][1]))],
		"lists": [
			{"title": _("Largest closing balances"), "rows": [_row(r.name, r.closing, "money", f"{r.root_type} · {'Dr' if r.closing >= 0 else 'Cr'}") for r in big]},
			{"title": _("Check these (wrong side)"), "rows": [_row(r.name, r.closing, "money", f"{r.root_type} {'Dr' if r.closing >= 0 else 'Cr'}") for r in sorted(odd, key=lambda r: -abs(r.closing))[:8]]},
		],
	}


# ----------------------------------------------------------------------------- Balance Sheet
def _under(which, co):
	"""All (lft, rgt) ranges of "... Current Assets" / "... Current Liabilities" groups (a company can carry two account trees)."""
	return frappe.db.sql("""select lft, rgt from `tabAccount` where company = %s and is_group = 1 and account_name like %s and account_name not like %s""",
	                     (co, f"%Current {which}", "%Non%Current%"), as_dict=True)


def _bs(a):
	sql = frappe.db.sql
	bal = {r.name: r for r in sql(f"""select a.name, a.root_type, a.account_type, a.lft, sum(g.debit - g.credit) b
		from `tabGL Entry` g use index (micromax_gl_analysis) join `tabAccount` a on a.name = g.account
		where g.is_cancelled = 0 and g.company = %(co)s and g.posting_date <= %(t)s group by a.name""", a, as_dict=True)}
	s = lambda pred: sum(flt(r.b) for r in bal.values() if pred(r))  # noqa: E731
	assets = s(lambda r: r.root_type == "Asset")
	liab = -s(lambda r: r.root_type == "Liability")
	profit = -s(lambda r: r.root_type in ("Income", "Expense"))           # not yet closed to equity
	equity = -s(lambda r: r.root_type == "Equity") + profit
	ca_g, cl_g = _under("Assets", a["co"]), _under("Liabilities", a["co"])
	inside = lambda r, gs: any(g.lft <= flt(r.lft) <= g.rgt for g in gs)  # noqa: E731
	ca = s(lambda r: inside(r, ca_g)) if ca_g else s(lambda r: r.account_type in ("Cash", "Bank", "Receivable", "Stock"))
	cl = -s(lambda r: inside(r, cl_g)) if cl_g else -s(lambda r: r.account_type in ("Payable", "Tax"))
	stock = s(lambda r: r.account_type == "Stock")
	cash = s(lambda r: r.account_type in ("Cash", "Bank"))
	recv, pay = s(lambda r: r.account_type == "Receivable"), -s(lambda r: r.account_type == "Payable")
	fixed = s(lambda r: r.account_type in ("Fixed Asset", "Capital Work in Progress", "Accumulated Depreciation"))
	# month-end totals for the trend
	months = _months(a["f"], a["t"])
	mv = {r.m: r for r in sql(f"""select date_format(g.posting_date, '%%Y-%%m') m, sum(if(a.root_type = 'Asset', g.debit - g.credit, 0)) asset,
			sum(if(a.root_type = 'Liability', g.credit - g.debit, 0)) liab, sum(if(a.root_type in ('Equity', 'Income', 'Expense'), g.credit - g.debit, 0)) eq
		from `tabGL Entry` g join `tabAccount` a on a.name = g.account where g.is_cancelled = 0 and g.company = %(co)s and g.posting_date between %(f)s and %(t)s group by m""", a, as_dict=True)}
	start = {r.k: flt(r.v) for r in sql(f"""select a.root_type k, sum(g.debit - g.credit) v from `tabGL Entry` g join `tabAccount` a on a.name = g.account
		where g.is_cancelled = 0 and g.company = %(co)s and g.posting_date < %(f)s group by a.root_type""", a, as_dict=True)}
	run_a, run_l, run_e = start.get("Asset", 0), -start.get("Liability", 0), -(start.get("Equity", 0) + start.get("Income", 0) + start.get("Expense", 0))
	trend = []
	for m in months:
		r = mv.get(m) or {}
		run_a += flt(r.get("asset")); run_l += flt(r.get("liab")); run_e += flt(r.get("eq"))
		trend.append({"month": _lbl(m), "assets": round(run_a, 2), "liabilities": round(run_l, 2), "equity": round(run_e, 2)})
	ratio = lambda x, y: round(x / y, 2) if y else None  # noqa: E731
	cr, qr, de = ratio(ca, cl), ratio(ca - stock, cl), ratio(liab, equity)
	return {
		"tiles": [
			_tile(_("Total assets"), assets, "money", _("as of {0}").format(a["t"]), "sky"),
			_tile(_("Liabilities"), liab, "money", _("debt / equity {0}").format(de if de is not None else "—"), "amber" if de and de > 2 else "primary"),
			_tile(_("Equity (incl. current profit)"), equity, "money", _("current profit {0}").format(frappe.format(profit, "Currency")), "violet"),
			_tile(_("Working capital"), ca - cl, "money", _("current assets − current liabilities"), "emerald" if ca - cl > 0 else "rose"),
			_tile(_("Current ratio"), f"{cr:.2f}×" if cr is not None else None, "text", _("quick ratio {0}").format(f"{qr:.2f}×" if qr is not None else "—"), "emerald" if cr and cr >= 1.2 else "amber" if cr and cr >= 1 else "rose"),
			_tile(_("Cash & bank"), cash, "money", _("{0}% of assets").format(round(cash / assets * 100, 1) if assets else 0), "sky"),
			_tile(_("Receivables"), recv, "money", _("payables {0}").format(frappe.format(pay, "Currency")), "amber"),
			_tile(_("Inventory"), stock, "money", _("fixed assets {0}").format(frappe.format(fixed, "Currency")), "violet"),
		],
		"bars": [_bar(_("Current assets"), ca / assets * 100 if assets else 0, frappe.format(ca, "Currency"), "sky"),
		         _bar(_("Fixed & other assets"), (assets - ca) / assets * 100 if assets else 0, frappe.format(assets - ca, "Currency"), "violet"),
		         _bar(_("Funded by liabilities"), liab / assets * 100 if assets else 0, frappe.format(liab, "Currency"), "amber"),
		         _bar(_("Funded by equity"), equity / assets * 100 if assets else 0, frappe.format(equity, "Currency"), "emerald")],
		"lists": [],
		"chart": {"title": _("Month-end balances"), "xKey": "month", "money": True, "type": "line",
		          "series": [{"key": "assets", "label": _("Assets")}, {"key": "liabilities", "label": _("Liabilities"), "color": "hsl(35 92% 50%)"},
		                     {"key": "equity", "label": _("Equity"), "color": "hsl(160 84% 39%)"}], "data": trend},
	}


# ----------------------------------------------------------------------------- Cash Flow (from bank & cash accounts)
def _cf(a):
	sql = frappe.db.sql
	cash_accs = frappe.db.sql_list("select name from `tabAccount` where company = %s and account_type in ('Bank', 'Cash') and is_group = 0", a["co"])
	if not cash_accs:
		return {"tiles": [_tile(_("Cash accounts"), 0, "number", _("no Bank / Cash accounts"), "rose")], "bars": [], "lists": []}
	a["cash"] = cash_accs
	opening = flt(sql(f"select sum(debit - credit) from `tabGL Entry` {IDX} where {C} and account in %(cash)s and posting_date < %(f)s", a)[0][0])
	vouchers = sql(f"""select voucher_type, voucher_no, date_format(posting_date, '%%Y-%%m') m, sum(debit - credit) net from `tabGL Entry` {IDX_P}
		where {C} and account in %(cash)s and posting_date between %(f)s and %(t)s group by voucher_type, voucher_no, m""", a, as_dict=True)
	# The dominant non-cash line of each voucher decides whether the movement is operating, investing or financing.
	cash_vouchers = {v.voucher_no for v in vouchers}
	acc_meta = {r.name: r for r in sql("select name, root_type, account_type, account_name from `tabAccount` where company = %(co)s", a, as_dict=True)}
	counter = {}
	# Payment Entries (most cash movement): the counter-side is the party account on the payment itself.
	for pe in sql("""select name, payment_type, party_type, party, paid_from, paid_to from `tabPayment Entry`
			where company = %(co)s and docstatus = 1 and posting_date between %(f)s and %(t)s""", a, as_dict=True):
		acc = pe.paid_to if pe.payment_type == "Pay" else pe.paid_from
		m = acc_meta.get(acc) or frappe._dict()
		counter[pe.name] = frappe._dict(party_type=pe.party_type, party=pe.party, root_type=m.root_type, account_type=m.account_type, account_name=m.account_name)
	# Everything else touching cash (journal entries, POS invoices…): the largest non-cash line of the voucher.
	rest = [v.voucher_no for v in vouchers if v.voucher_no not in counter]
	for i in range(0, len(rest), 5000):
		for r in sql(f"""select voucher_no, account, party_type, party, sum(abs(debit - credit)) v from `tabGL Entry`
				where voucher_no in %(vs)s and is_cancelled = 0 and account not in %(cash)s group by voucher_no, account, party_type, party""",
		             {**a, "vs": rest[i:i + 5000]}, as_dict=True):
			if r.voucher_no not in counter or flt(r.v) > flt(counter[r.voucher_no].get("v")):
				m = acc_meta.get(r.account) or frappe._dict()
				counter[r.voucher_no] = frappe._dict(r, root_type=m.root_type, account_type=m.account_type, account_name=m.account_name)
	act = defaultdict(float)
	inflow = outflow = 0.0
	monthly = defaultdict(lambda: [0.0, 0.0])
	who_in, who_out, kind = defaultdict(float), defaultdict(float), defaultdict(float)
	for v in vouchers:
		n = flt(v.net)
		c = counter.get(v.voucher_no) or frappe._dict()
		cls = ("Investing" if c.account_type in ("Fixed Asset", "Capital Work in Progress") else
		       "Financing" if c.root_type == "Equity" or (c.root_type == "Liability" and c.account_type not in ("Payable", "Tax", "Receivable") and "loan" in (c.account_name or "").lower()) else "Operating")
		act[cls] += n
		if c.account_type in ("Bank", "Cash") or (c.root_type is None and not c):
			cls = "Transfers"
		label = f"{c.party_type}: {c.party}" if c.party else (c.account_name or v.voucher_type)
		if n >= 0:
			inflow += n; monthly[v.m][0] += n; who_in[label] += n
		else:
			outflow += -n; monthly[v.m][1] += -n; who_out[label] += -n
		kind[f"{'Receipts' if n >= 0 else 'Payments'} · {c.party_type or v.voucher_type}"] += abs(n)
	closing = opening + inflow - outflow
	months = _months(a["f"], a["t"])
	n_months = max(len([m for m in months if m in monthly]), 1)
	tot_move = inflow + outflow
	return {
		"tiles": [
			_tile(_("Opening cash & bank"), opening, "money", _("{0} accounts").format(len(cash_accs)), "primary"),
			_tile(_("Cash in"), inflow, "money", _("≈ {0} / month").format(frappe.format(inflow / n_months, "Currency")), "emerald"),
			_tile(_("Cash out"), outflow, "money", _("≈ {0} / month").format(frappe.format(outflow / n_months, "Currency")), "rose"),
			_tile(_("Closing cash & bank"), closing, "money", _("net change {0}").format(frappe.format(inflow - outflow, "Currency")), "emerald" if closing >= opening else "amber"),
			_tile(_("Operating"), act["Operating"], "money", _("customers, suppliers, wages, taxes"), "emerald" if act["Operating"] >= 0 else "rose"),
			_tile(_("Investing"), act["Investing"], "money", _("fixed assets / CWIP"), "violet"),
			_tile(_("Financing"), act["Financing"], "money", _("loans and equity"), "sky"),
			_tile(_("Cash cover"), round(closing / (outflow / n_months) * 30) if outflow else None, "days", _("days of average outflow the closing cash covers"), "rose" if outflow and closing / (outflow / n_months) * 30 < 15 else "amber"),
		],
		"bars": [_bar(k, v / tot_move * 100 if tot_move else 0, frappe.format(v, "Currency"), "emerald" if k.startswith("Receipts") else "rose")
		         for k, v in sorted(kind.items(), key=lambda x: -x[1])[:6]],
		"lists": [
			{"title": _("Largest sources of cash"), "rows": [_row(k, v, "money") for k, v in sorted(who_in.items(), key=lambda x: -x[1])[:8]]},
			{"title": _("Largest uses of cash"), "rows": [_row(k, v, "money") for k, v in sorted(who_out.items(), key=lambda x: -x[1])[:8]]},
		],
		"chart": {"title": _("Cash in vs cash out by month"), "xKey": "month", "money": True,
		          "series": [{"key": "in", "label": _("In"), "color": "hsl(160 84% 39%)"}, {"key": "out", "label": _("Out"), "color": "hsl(351 95% 59%)"}],
		          "data": [{"month": _lbl(m), "in": round(monthly[m][0], 2), "out": round(monthly[m][1], 2)} for m in months]},
	}


@frappe.whitelist()
def get_monthly_root_movement(company, from_date, to_date):
	"""Net movement per month per root type (Asset/Liability/Equity/Income/Expense), each in its natural direction —
	one indexed pass over GL Entry, cached. Feeds the Trial Balance "Monthly activity" chart and card sparklines."""
	frappe.has_permission("GL Entry", "read", throw=True)
	f, t = getdate(from_date), getdate(to_date)
	key = f"micromax-fin-monthly-root:{company}:{f}:{t}"
	hit = frappe.cache.get_value(key)
	if hit:
		return hit
	natural_debit = {"Asset", "Expense"}
	rows = frappe.db.sql(f"""select date_format(g.posting_date, '%%Y-%%m') m, a.root_type rt, sum(g.debit) d, sum(g.credit) c
		from `tabGL Entry` g {IDX} join `tabAccount` a on a.name = g.account
		where g.company = %(co)s and g.posting_date between %(f)s and %(t)s and g.is_cancelled = 0 and g.voucher_type != 'Period Closing Voucher'
		group by m, a.root_type""", {"co": company, "f": f, "t": t}, as_dict=True)
	by = defaultdict(lambda: {"Asset": 0.0, "Liability": 0.0, "Equity": 0.0, "Income": 0.0, "Expense": 0.0})
	for r in rows:
		if r.rt in by[r.m]:
			by[r.m][r.rt] += (flt(r.d) - flt(r.c)) if r.rt in natural_debit else (flt(r.c) - flt(r.d))
	out = [{"month": _lbl(m), **{k: round(v, 2) for k, v in by[m].items()}} for m in _months(f, t)]
	frappe.cache.set_value(key, out, expires_in_sec=CACHE_TTL)
	return out
