"""Profit & loss simulator data: the company's P&L for a period as a value-driver tree of real accounts.

    Net profit = Gross margin - Indirect expenses
    Gross margin = Revenue - Direct expenses (cost of sales)

Revenue, Direct and Indirect expenses each expand into the chart-of-accounts groups and accounts under them that
carry postings in the period. Every node has a monthly series (income: credit - debit, expenses: debit - credit), so the
page can show sparklines and simulate any month or the whole period client-side.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate

from micromax.dashboards import COST_OF_SALES


def _months(f, t):
    out, d = [], getdate(f).replace(day=1)
    while d <= getdate(t):
        out.append((d.strftime("%Y-%m"), d.strftime("%b %Y")))
        d = getdate(add_months(d, 1))
    return out


def _clean_name(a):
    """Account name without a number prefix some charts keep in the name itself ("4110 - Yarn Sales")."""
    name = a.account_name or a.name
    if a.account_number and name.startswith(f"{a.account_number} - "):
        name = name[len(a.account_number) + 3:]
    return name


@frappe.whitelist()
def get_pnl_tree(company: str, from_date: str, to_date: str) -> dict:
    frappe.has_permission("GL Entry", "read", throw=True)
    f, t = getdate(from_date), getdate(to_date)
    if f > t:
        frappe.throw(_("From date must be before To date"))
    months = _months(f, t)
    idx = {k: i for i, (k, _l) in enumerate(months)}
    accts = frappe.get_all("Account", {"company": company, "root_type": ["in", ["Income", "Expense"]]},
                           ["name", "account_name", "account_number", "parent_account", "is_group", "root_type", "lft", "rgt"],
                           order_by="lft")
    by_name = {a.name: a for a in accts}
    rows = frappe.db.sql("""select g.account, date_format(g.posting_date, '%%Y-%%m') m, sum(g.debit) dr, sum(g.credit) cr
        from `tabGL Entry` g join `tabAccount` a on a.name = g.account
        where g.company = %s and g.is_cancelled = 0 and g.voucher_type != 'Period Closing Voucher'
          and g.posting_date between %s and %s and a.root_type in ('Income', 'Expense')
        group by g.account, m""", (company, f, t), as_dict=True)
    series = {}
    for r in rows:
        a = by_name.get(r.account)
        if not a or r.m not in idx:
            continue
        v = flt(r.cr) - flt(r.dr) if a.root_type == "Income" else flt(r.dr) - flt(r.cr)
        series.setdefault(r.account, [0.0] * len(months))[idx[r.m]] += v

    roots = {a.name for a in accts if not a.parent_account or a.parent_account not in by_name}
    kids = {}
    for a in accts:
        if a.parent_account in by_name:
            kids.setdefault(a.parent_account, []).append(a.name)

    def build(name):
        """Node for an account (or group with activity below it); None when nothing posted."""
        a = by_name[name]
        if a.is_group:
            children = [n for n in (build(c) for c in kids.get(name, [])) if n]
            if not children:
                return None
            if len(children) == 1 and children[0].get("kids"):
                return children[0]            # a group with a single sub-group adds nothing: skip a level
            s = [sum(c["series"][i] for c in children) for i in range(len(months))]
            return {"id": name, "name": _clean_name(a), "number": a.account_number or "", "kids": children, "series": s}
        s = series.get(name)
        if not s or not any(abs(x) >= 1 for x in s):        # rounding-only accounts (Round Off) add noise
            return None
        return {"id": name, "name": _clean_name(a), "number": a.account_number or "", "series": s}

    # level-2 groups (children of the Income / Expense roots) decide the section
    revenue, direct, indirect = [], [], []
    for root in roots:
        for top in kids.get(root, []) or [root]:
            node = build(top)
            if not node:
                continue
            if by_name[root].root_type == "Income":
                revenue.append(node)
            elif COST_OF_SALES.search(by_name[top].account_name or ""):
                direct.append(node)
            else:
                indirect.append(node)

    def section(sid, name, children):
        children = [c for c in children if c]
        if len(children) == 1 and children[0].get("kids"):
            children = children[0]["kids"]   # skip a lone wrapper group ("Direct Income")
        return {"id": sid, "name": name, "kids": children,
                "series": [sum(c["series"][i] for c in children) for i in range(len(months))]}

    return {
        "company": company, "currency": frappe.get_cached_value("Company", company, "default_currency"),
        "from": str(f), "to": str(t), "months": [{"key": k, "label": lbl} for k, lbl in months],
        "revenue": section("rev", _("Total revenue"), revenue),
        "direct": section("dir", _("Direct expenses"), direct),
        "indirect": section("ind", _("Indirect expenses"), indirect),
    }
