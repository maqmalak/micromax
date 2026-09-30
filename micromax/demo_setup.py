"""Company setup for micromax.demo_data (template section "company_setup"): PKR as the default currency, cost
centres (head office + mills), bank and cash-in-hand accounts with Bank Account records, a waste-sales income
account, tax categories on the tax templates and — after every master exists — a numbered chart of accounts.

Every step is idempotent and skips what the company already has, so it is safe on a company with its own setup
(e.g. a chart of accounts that is already numbered keeps its numbers; only new accounts get the next free one).
"""

from collections import defaultdict

import frappe
from frappe.utils import cint, flt


def setup(ctx):
    cs = ctx.t.get("company_setup")
    if not cs:
        return
    _currency(ctx, cs.get("currency") or "PKR")
    _cost_centers(ctx, cs.get("cost_centers") or [])
    _banks_and_cash(ctx, cs)
    _waste_sales(ctx, cs.get("waste_sales"))
    _tax_categories(ctx, cs.get("tax_categories"))
    _planners(ctx)


# ----------------------------------------------------------------------------- currency
def _currency(ctx, currency):
    if frappe.db.exists("Currency", currency):
        frappe.db.set_value("Currency", currency, "enabled", 1)
    if frappe.db.get_value("Company", ctx.company, "default_currency") != currency and not frappe.db.exists(
            "GL Entry", {"company": ctx.company}):
        frappe.db.set_value("Company", ctx.company, "default_currency", currency)
    if not frappe.db.get_single_value("Global Defaults", "default_currency"):
        frappe.db.set_single_value("Global Defaults", "default_currency", currency)
    ctx.currency = frappe.db.get_value("Company", ctx.company, "default_currency")


# ----------------------------------------------------------------------------- cost centres
def _cost_centers(ctx, rows):
    c = ctx.company
    root = frappe.db.get_value("Cost Center", {"company": c, "is_group": 1, "parent_cost_center": ["in", ["", None]]}, "name")
    ctx.cc = {}
    for r in rows:
        name = frappe.db.get_value("Cost Center", {"company": c, "cost_center_name": r["name"], "is_group": 0}, "name")
        if not name:
            doc = frappe.get_doc({"doctype": "Cost Center", "cost_center_name": r["name"], "company": c,
                                  "parent_cost_center": root, "is_group": 0})
            from micromax import demo_data
            demo_data._fill(ctx, doc)
            doc.insert(ignore_permissions=True)
            name = doc.name
        ctx.cc[r["key"]] = name
    if ctx.cc.get("main"):
        frappe.db.set_value("Company", c, "cost_center", ctx.cc["main"])
        for f in ("round_off_cost_center", "depreciation_cost_center"):
            if frappe.get_meta("Company").has_field(f) and not frappe.db.get_value("Company", c, f):
                frappe.db.set_value("Company", c, f, ctx.cc["main"])
        ctx.cost_center = ctx.cc["main"]


def mill_cc(ctx, key=None):
    """A mill's cost centre (random by the template's mill shares when no key is given); head office if none."""
    cc = getattr(ctx, "cc", {}) or {}
    if key and cc.get(key):
        return cc[key]
    share = (ctx.t.get("planning") or {}).get("mill_share") or {}
    keys = [k for k in share if cc.get(k)]
    if not keys:
        return ctx.cost_center
    return cc[ctx.rnd.choices(keys, weights=[share[k] for k in keys])[0]]


# ----------------------------------------------------------------------------- banks and cash
def _group(ctx, *names, root_type="Asset"):
    for n in names:
        g = frappe.db.get_value("Account", {"company": ctx.company, "is_group": 1, "account_name": n}, "name")
        if g:
            return g
    for n in names:
        g = frappe.db.get_value("Account", {"company": ctx.company, "is_group": 1, "root_type": root_type,
                                            "account_name": ["like", f"%{n}%"]}, "name")
        if g:
            return g
    return frappe.db.get_value("Account", {"company": ctx.company, "is_group": 1, "root_type": root_type,
                                           "parent_account": ["in", ["", None]]}, "name")


def _leaf(ctx, account_name, parent, account_type=None, root_type=None):
    name = frappe.db.get_value("Account", {"company": ctx.company, "account_name": account_name, "is_group": 0}, "name")
    if name:
        return name
    return frappe.get_doc({"doctype": "Account", "account_name": account_name, "parent_account": parent, "company": ctx.company,
                           "is_group": 0, **({"account_type": account_type} if account_type else {})}).insert(ignore_permissions=True).name


def _banks_and_cash(ctx, cs):
    c = ctx.company
    bank_group = _group(ctx, "Bank Accounts", "Bank")
    ctx.banks = []
    for b in cs.get("banks") or []:
        if not frappe.db.exists("Bank", b["bank"]):
            frappe.get_doc({"doctype": "Bank", "bank_name": b["bank"]}).insert(ignore_permissions=True)
        acc = _leaf(ctx, b["account"], bank_group, "Bank")
        if not frappe.db.exists("Bank Account", {"account": acc}):
            # Bank Account names are global ("<account name> - <bank>"): include the company so each company has its own
            ba = frappe.get_doc({"doctype": "Bank Account", "account_name": f"{b['account']} ({ctx.abbr})", "bank": b["bank"], "account": acc,
                                 "company": c, "is_company_account": 1, "bank_account_no": b.get("account_no"),
                                 "branch_code": b.get("branch"), "is_default": cint(b.get("default"))})
            ba.insert(ignore_permissions=True)
        ctx.banks.append(frappe._dict(account=acc, weight=flt(b.get("weight") or 1), payroll=cint(b.get("payroll")),
                                      default=cint(b.get("default"))))
    default = next((b.account for b in ctx.banks if b.default), ctx.banks[0].account if ctx.banks else None)
    if default:
        frappe.db.set_value("Company", c, "default_bank_account", default)
    cash_group = _group(ctx, "Cash In Hand", "Cash")
    ctx.cash = {}
    for r in cs.get("cash") or []:
        ctx.cash[r["key"]] = _leaf(ctx, r["account"], cash_group, "Cash")
        if cint(r.get("default")):
            frappe.db.set_value("Company", c, "default_cash_account", ctx.cash[r["key"]])
    ctx.cash_float = {r["key"]: flt(r.get("float")) for r in cs.get("cash") or []}


def bank(ctx, purpose=None):
    """A bank account for a payment: the payroll bank for salaries, otherwise one by weight."""
    banks = getattr(ctx, "banks", None) or []
    if not banks:
        return frappe.db.get_value("Company", ctx.company, "default_bank_account") or \
            frappe.db.get_value("Company", ctx.company, "default_cash_account")
    if purpose == "payroll":
        pay = [b for b in banks if b.payroll]
        if pay:
            return pay[0].account
    return ctx.rnd.choices(banks, weights=[b.weight for b in banks])[0].account


# ----------------------------------------------------------------------------- waste sales
def _waste_sales(ctx, ws):
    if not ws:
        return
    parent = _group(ctx, "Direct Income", "Income", root_type="Income")
    ctx.waste_income = _leaf(ctx, ws["account"], parent, "Income Account")
    for w in ctx.t.wastes:
        row = frappe.db.get_value("Item Default", {"parent": w["code"], "company": ctx.company}, "name")
        if row:
            frappe.db.set_value("Item Default", row, "income_account", ctx.waste_income)
    parent_cg = frappe.db.get_value("Customer Group", {"is_group": 1}, "name")
    from micromax import demo_data
    demo_data._ensure("Customer Group", "Waste Buyers", {"customer_group_name": "Waste Buyers", "parent_customer_group": parent_cg})
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or frappe.db.get_value("Territory", {}, "name")
    for cu in ws.get("customers") or []:
        demo_data._ensure("Customer", cu, {"customer_name": cu, "customer_group": "Waste Buyers", "customer_type": "Company",
                                           "territory": territory})


# ----------------------------------------------------------------------------- tax categories
def _tax_categories(ctx, tc):
    if not tc:
        return
    for title in set(v for k, v in tc.items() if not k.startswith("_")):
        if not frappe.db.exists("Tax Category", title):
            frappe.get_doc({"doctype": "Tax Category", "title": title}).insert(ignore_permissions=True)
    for dt, key in (("Sales Taxes and Charges Template", "sales"), ("Purchase Taxes and Charges Template", "purchase")):
        for name in frappe.get_all(dt, {"company": ctx.company, "tax_category": ["in", ["", None]]}, pluck="name"):
            frappe.db.set_value(dt, name, "tax_category", tc[key])
    ctx.tax_category = tc
    _party_tax_categories(ctx)


def _party_tax_categories(ctx):
    t, tc = ctx.t, getattr(ctx, "tax_category", None)
    if not tc:
        return
    export = {c["name"] for c in (t.get("export") or {}).get("customers", [])}
    customers = list(t.customers) + list(t.conversion.get("customers", [])) + list(export) \
        + list(((t.get("company_setup") or {}).get("waste_sales") or {}).get("customers", []))
    for cu in customers:
        if frappe.db.exists("Customer", cu):
            party_tax_category(ctx, "Customer", cu, export=cu in export)
    imports = {s_["name"] for s_ in (t.get("import") or {}).get("suppliers", [])}
    for su in list(t.suppliers) + list(imports):
        if frappe.db.exists("Supplier", su) and not frappe.db.get_value("Supplier", su, "tax_category"):
            frappe.db.set_value("Supplier", su, "tax_category", tc.get("unregistered") if su in imports else tc["purchase"])


def party_tax_category(ctx, doctype, name, export=False):
    tc = getattr(ctx, "tax_category", None)
    if tc and not frappe.db.get_value(doctype, name, "tax_category"):
        frappe.db.set_value(doctype, name, "tax_category", tc["export" if export else ("sales" if doctype == "Customer" else "purchase")])


# ----------------------------------------------------------------------------- numbered chart of accounts
ROOT_DIGIT = {"Asset": "1", "Liability": "2", "Equity": "3", "Income": "4", "Expense": "5"}


def number_accounts(ctx):
    """Give every unnumbered account of the company a number in a 4-digit scheme (1000 Assets → 1100 Current Assets →
    1110 Bank Accounts → 1111 MCB Bank…; deeper or crowded levels continue as 1111-1, 1111-2), keeping numbers that
    already exist. Accounts are renamed "number - name - abbr" (ERPNext's own format); names cached on `ctx` follow."""
    if not cint((ctx.t.get("company_setup") or {}).get("number_accounts")):
        return {}
    from erpnext.accounts.doctype.account.account import get_account_autoname
    accts = frappe.get_all("Account", {"company": ctx.company},
                           ["name", "account_name", "account_number", "parent_account", "root_type", "is_group", "lft"], order_by="lft")
    kids = defaultdict(list)
    for a in accts:
        kids[a.parent_account or ""].append(a)
    used = {a.account_number for a in accts if a.account_number}
    plan = {}

    def next_free(parent_no, depth, i):
        if parent_no.isdigit() and len(parent_no) == 4 and depth <= 3:
            step = 10 ** (3 - depth)
            base = int(parent_no)
            for k in range(1, 10):
                cand = str(base + k * step)
                if cand not in used and cand[: 4 - (3 - depth) - 1] == parent_no[: 4 - (3 - depth) - 1]:
                    return cand
        k = i
        while f"{parent_no}-{k}" in used:
            k += 1
        return f"{parent_no}-{k}"

    def walk(node, number, depth):
        for i, child in enumerate(kids.get(node.name, []), start=1):
            no = child.account_number or next_free(number, depth, i)
            if not child.account_number:
                used.add(no)
                plan[child.name] = (child.account_name, no)
            walk(child, no, depth + 1)

    for root in kids[""]:
        no = root.account_number or f"{ROOT_DIGIT.get(root.root_type, '9')}000"
        if not root.account_number and no not in used:
            used.add(no)
            plan[root.name] = (root.account_name, no)
        walk(root, no, 1)

    renamed = {}
    for name, (account_name, no) in plan.items():
        frappe.db.set_value("Account", name, "account_number", no, update_modified=False)
        new_name = get_account_autoname(no, account_name, ctx.company)
        if new_name != name:
            frappe.rename_doc("Account", name, new_name, force=1)
            renamed[name] = new_name
    if renamed:
        _remap(ctx, renamed)
        ctx.log(f"Numbered {len(plan)} accounts ({len(renamed)} renamed).")
    return renamed


def _remap(ctx, renamed):
    """Replace old account names cached anywhere on the context (strings, lists, dicts, frappe._dict rows)."""
    def fix(v):
        if isinstance(v, str):
            return renamed.get(v, v)
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            for k in list(v.keys()):
                v[k] = fix(v[k])
            return v
        return v
    for k, v in list(ctx.__dict__.items()):
        if k in ("t", "rnd"):
            continue
        setattr(ctx, k, fix(v))


# ----------------------------------------------------------------------------- planners
def _planners(ctx):
    """Production planners as system users (Manufacturing User); Production Plans and Work Orders are theirs."""
    names = (ctx.t.get("planning") or {}).get("planners") or []
    ctx.planners = []
    for full in names:
        first, _sp, last = full.partition(" ")
        email = f"{first.lower()}.{(last or 'planner').lower().replace(' ', '')}@micromax-demo.pk"
        if not frappe.db.exists("User", email):
            u = frappe.get_doc({"doctype": "User", "email": email, "first_name": first, "last_name": last,
                                "send_welcome_email": 0, "enabled": 1, "user_type": "System User"})
            u.flags.no_welcome_mail = True
            u.insert(ignore_permissions=True)
            for role in ("Manufacturing User", "Stock User"):
                if frappe.db.exists("Role", role):
                    u.add_roles(role)
        ctx.planners.append(email)
    # each planner has a steady share of the work (a senior planner takes more)
    ctx.planner_weights = [max(1, len(ctx.planners) - i) for i in range(len(ctx.planners))]


def planner(ctx):
    p = getattr(ctx, "planners", None)
    return ctx.rnd.choices(p, weights=ctx.planner_weights)[0] if p else None


# ----------------------------------------------------------------------------- shareholders' equity
def _equity_masters(ctx):
    eq = ctx.t.get("equity")
    if not eq:
        return None
    acc = eq["accounts"]
    equity_group = _group(ctx, "Equity", root_type="Equity")
    liab = _group(ctx, "Duties and Taxes", "Current Liabilities", root_type="Liability")
    ctx.eq = frappe._dict(
        capital=_leaf(ctx, acc["capital"], equity_group, "Equity"),
        premium=_leaf(ctx, acc["premium"], equity_group, "Equity"),
        dividends=_leaf(ctx, acc["dividends"], equity_group, "Equity"),
        payable=_leaf(ctx, acc["dividend_payable"], _group(ctx, "Current Liabilities", root_type="Liability")),
        wht=_leaf(ctx, acc["dividend_wht"], liab))
    if not frappe.db.exists("Share Type", eq["share_type"]):
        frappe.get_doc({"doctype": "Share Type", "title": eq["share_type"]}).insert(ignore_permissions=True)
    ctx.shareholders = []
    for sh in eq["shareholders"]:
        name = frappe.db.get_value("Shareholder", {"title": sh["name"], "company": ctx.company}, "name")
        if not name:
            name = frappe.get_doc({"doctype": "Shareholder", "title": sh["name"], "company": ctx.company,
                                   "folio_no": f"{ctx.abbr}/{sh['folio']}" if sh.get("folio") else None}).insert(ignore_permissions=True).name
        # folio numbers are unique across the site: prefixed with the company abbreviation
        ctx.shareholders.append(frappe._dict(name=name, title=sh["name"], shares=cint(sh["shares"]),
                                             folio=frappe.db.get_value("Shareholder", name, "folio_no")))
    return ctx.eq


def schedule_equity(ctx):
    """Called by the event loop set-up: opening capital on day one, the rights issue and the final dividend."""
    if not ctx.t.get("equity") or not _equity_masters(ctx):
        return
    number_accounts(ctx)                        # the new equity / dividend accounts get their numbers too
    from frappe.utils import add_days, add_months, getdate
    eq = ctx.t.equity
    if not frappe.db.exists("Share Transfer", {"company": ctx.company, "docstatus": 1}):
        ctx.at(ctx.start, -5, _issue_shares, "Opening share capital", 1.0, eq["face_value"])
    ri = eq.get("rights_issue")
    if ri:
        d = add_months(getdate(ctx.start).replace(day=1), cint(ri["month"]) - 1).replace(day=cint(ri.get("day") or 1))
        ctx.at(d, 0.2, _issue_shares, f"Rights issue at Rs {ri['rate']} — {ri.get('purpose', '')}", flt(ri["ratio"]), flt(ri["rate"]))
    dv = eq.get("dividend")
    if dv:
        # declared at the AGM after each tax year (Jul-Jun) that has closed inside the run
        y = getdate(ctx.start).year + (1 if getdate(ctx.start).month >= 7 else 0)
        while getdate(f"{y}-{dv['declare']}") <= getdate(ctx.end):
            ctx.at(getdate(f"{y}-{dv['declare']}"), 9.8, _declare_dividend, y)
            y += 1


def _issue_shares(ctx, remark, ratio, rate):
    """Share Transfer (Issue) per shareholder + the Journal Entry for the cash received (premium above face value)."""
    from micromax import demo_data
    eq, day = ctx.t.equity, ctx._current_day
    face = flt(eq["face_value"])
    bank_acc = bank(ctx)
    last_no = cint(frappe.db.sql("select max(to_no) from `tabShare Transfer` where company=%s and docstatus=1", ctx.company)[0][0])
    total_shares = 0
    for sh in ctx.shareholders:
        n = int(round(sh.shares * ratio))
        if n <= 0:
            continue
        st = frappe.get_doc({"doctype": "Share Transfer", "transfer_type": "Issue", "date": day, "company": ctx.company,
                             "to_shareholder": sh.name, "to_folio_no": sh.folio, "share_type": eq["share_type"],
                             "from_no": last_no + 1, "to_no": last_no + n, "no_of_shares": n, "rate": rate, "amount": n * rate,
                             "equity_or_liability_account": ctx.eq.capital, "asset_account": bank_acc,
                             "remarks": f"{demo_data.DEMO_TAG} · {remark}"})
        st.insert(ignore_permissions=True)
        st.submit()
        last_no += n
        total_shares += n
        ctx.bump("Share Transfer")
    if not total_shares:
        return
    capital, premium = total_shares * face, total_shares * max(0.0, rate - face)
    je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Bank Entry", "company": ctx.company, "posting_date": day,
                         "cheque_no": f"SHR-{frappe.utils.cstr(day).replace('-', '')}", "cheque_date": day,
                         "user_remark": f"{demo_data.DEMO_TAG} · {remark}: {total_shares:,} shares",
                         "accounts": [{"account": bank_acc, "debit_in_account_currency": capital + premium, "cost_center": ctx.cost_center},
                                      {"account": ctx.eq.capital, "credit_in_account_currency": capital, "cost_center": ctx.cost_center}]
                         + ([{"account": ctx.eq.premium, "credit_in_account_currency": premium, "cost_center": ctx.cost_center}] if premium else [])})
    demo_data._insert_submit(ctx, je)


def _declare_dividend(ctx, tax_year_end):
    """Final cash dividend for the tax year ended 30 June: declared (Dividends Paid → Dividends Payable) and paid two weeks
    later from the bank, net of withholding tax (held as a liability for the FBR)."""
    from micromax import demo_data
    from frappe.utils import add_days
    dv, day = ctx.t.equity["dividend"], ctx._current_day
    shares = cint(frappe.db.sql("""select sum(no_of_shares) from `tabShare Transfer` where company=%s and docstatus=1
        and transfer_type='Issue'""", ctx.company)[0][0])
    gross = round(shares * flt(dv["per_share"]), 2)
    if gross <= 0:
        return
    label = f"final dividend Rs {dv['per_share']}/share for the year ended 30 June {tax_year_end}"
    je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": ctx.company, "posting_date": day,
                         "user_remark": f"{demo_data.DEMO_TAG} · {label} declared at the AGM",
                         "accounts": [{"account": ctx.eq.dividends, "debit_in_account_currency": gross, "cost_center": ctx.cost_center},
                                      {"account": ctx.eq.payable, "credit_in_account_currency": gross, "cost_center": ctx.cost_center}]})
    demo_data._insert_submit(ctx, je)
    ctx.at(add_days(day, cint(dv.get("pay_after_days") or 14)), 6, _pay_dividend, gross, label)


def _pay_dividend(ctx, gross, label):
    from micromax import demo_data
    dv, day = ctx.t.equity["dividend"], ctx._current_day
    wht = round(gross * flt(dv.get("wht")), 2)
    je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Bank Entry", "company": ctx.company, "posting_date": day,
                         "cheque_no": f"DIV-{ctx.rnd.randint(10000, 99999)}", "cheque_date": day,
                         "user_remark": f"{demo_data.DEMO_TAG} · {label} paid (withholding tax {flt(dv.get('wht')) * 100:g}%)",
                         "accounts": [{"account": ctx.eq.payable, "debit_in_account_currency": gross, "cost_center": ctx.cost_center},
                                      {"account": bank(ctx), "credit_in_account_currency": gross - wht, "cost_center": ctx.cost_center},
                                      {"account": ctx.eq.wht, "credit_in_account_currency": wht, "cost_center": ctx.cost_center}]})
    demo_data._insert_submit(ctx, je)
