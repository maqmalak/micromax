"""Fresh demo data for a spinning mill, generated through the normal ERPNext documents.

    bench --site <site> execute micromax.demo_data.generate
    bench --site <site> execute micromax.demo_data.generate --kwargs "{'from_date': '2025-07-01', 'to_date': '2026-09-30'}"
    bench --site <site> execute micromax.demo_data.purge --kwargs "{'confirm': 1}"      # undo

Everything the demo is made of (items, blends, rates, parties, operations, conversion contracts, seasonality)
lives in a JSON template: micromax/demo_templates/<template>.json (default "spinning_mill"). Edit or copy it to
change the demo; this file only contains the logic. (It is deliberately not in micromax/fixtures/: Frappe imports
that folder on every migrate, and these records are company-specific transactions, not app fixtures.)

Defaults: the template's company ("MicroMax Erp Pvt Ltd.", else the site's default company), from 1 Jul 2025
(start of the previous fiscal year) to today, so month-by-month dashboard charts show a full year of trend.
Posting stops at today: ERPNext refuses future-dated Purchase Receipts.

What it does
------------
1. Masters (created only when missing, on the company's own defaults and chart of accounts): item groups,
   warehouses (incl. Third Party Fibre / Third Party Yarn), fibre / yarn / waste items with prices, suppliers,
   customers and conversion customers with a conversion Contract each, sales-tax templates, operations,
   workstations (ring frames with spindles) and a Spinning BOM per yarn (blend, yield, OPS, waste, operations).
2. Transactions, day by day, all submitted and posted in time order:
   - Buying:      Material Request -> Purchase Order -> Purchase Receipt -> Purchase Invoice -> Payment
   - Own yarn:    Sales Order -> Production Plan (Mon/Thu) -> Work Order -> Material Transfer -> Job Cards
                  -> Manufacture (yarn + waste) -> spinning actuals / downtime -> Delivery Note -> Sales Invoice -> Payment
   - Conversion:  customer's fibre received into Third Party Fibre (zero value) -> Sales Order at the conversion
                  charge -> the same plan / work order chain into Third Party Yarn -> Delivery -> Invoice -> Payment
   Month-by-month seasonality, price inflation, summer power cuts and a slowly improving mill make the trends.

Masters are safe to re-run; transactions refuse to run twice for a company unless force=1. Manufacturing
Settings changed for the run are restored at the end.
"""

import contextlib
import json
import os
import random

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, get_last_day, getdate, nowdate

from micromax import demo_modules, demo_setup

DEMO_TAG = "MicroMax demo data"
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "demo_templates")


def load_template(name="spinning_mill"):
    path = name if name.endswith(".json") else os.path.join(TEMPLATE_DIR, f"{name}.json")
    with open(path) as f:
        t = frappe._dict(json.load(f))
    t.season = {int(k): v for k, v in t.season.items()}
    t.load_shedding = {int(k): v for k, v in t.get("load_shedding", {}).items()}
    t.conversion = frappe._dict(t.get("conversion") or {})
    return t


@contextlib.contextmanager
def _inline_background_jobs():
    """Run the background jobs our documents trigger (stock reposting, search index...) inline for this command.
    Thousands of submits / cancels otherwise flood the queue ("Too many queued background jobs") on a site
    whose workers are busy or not running, and valuations would lag behind the data."""
    import frappe.utils.background_jobs as bj
    orig = bj.enqueue

    skip = ("email", "mail", "notification", "notify", "pdf", "print", "communication", "whatsapp", "sms")

    def enqueue(method, *args, **kwargs):
        name = (method if isinstance(method, str) else f"{getattr(method, '__module__', '')}.{getattr(method, '__qualname__', '')}").lower()
        if any(k in name for k in skip):          # demo data sends nothing and renders no PDFs
            return None
        kwargs["now"] = True
        try:
            return orig(method, *args, **kwargs)
        except Exception as e:                    # a side job must never sink the document that queued it
            print(f"    (background job {name} failed inline: {str(e)[:120]})")
            return None

    frappe.enqueue, bj.enqueue = enqueue, enqueue
    try:
        yield
    finally:
        frappe.enqueue, bj.enqueue = orig, orig


def _company(t, company=None):
    company = company or (t.company if frappe.db.exists("Company", t.get("company")) else None) \
        or frappe.defaults.get_global_default("company")
    if not company or not frappe.db.exists("Company", company):
        frappe.throw(f"Company not found: {company!r}. Pass company='...'.")
    return company


# ============================================================================ entry point
def generate(company=None, from_date="2025-07-01", to_date=None, seed=7, force=0, sales_every_days=2,
             template="spinning_mill"):
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    t = load_template(template)
    company = _company(t, company)
    to_date = getdate(to_date or nowdate())
    from_date = getdate(from_date)

    ctx = _Ctx(company, random.Random(cint(seed)), t)
    ctx.start, ctx.end = from_date, to_date
    ctx.log(f"Company {company} ({ctx.abbr}), {from_date} → {to_date}, template {template}")

    _ensure_fiscal_years(from_date, to_date, company)
    masters(ctx)
    frappe.db.commit()
    demo_setup.setup(ctx)              # cost centres, banks, cash, waste sales, tax categories, planners, PKR
    frappe.db.commit()
    demo_modules.masters(ctx)          # export / import / quality / HR / payroll / assets (per template sections)
    frappe.db.commit()
    demo_setup._party_tax_categories(ctx)
    demo_setup.number_accounts(ctx)    # numbered chart of accounts (new accounts only)
    frappe.db.commit()

    if not cint(force) and frappe.db.exists("Stock Entry", {"company": company, "docstatus": 1,
                                                            "remarks": ["like", f"%{DEMO_TAG}%"]}):
        ctx.log("Demo transactions already exist for this company — skipping (pass force=1 to add another run).")
        return ctx.summary()

    saved = _tune_manufacturing_settings()
    try:
        with _inline_background_jobs():
            transactions(ctx, from_date, to_date, cint(sales_every_days) or 2)
    finally:
        _restore_manufacturing_settings(saved)
        frappe.db.commit()
    ctx.log("Done.")
    return ctx.summary()


_CTX = None          # the running generator's context (for site-specific mandatory fields on masters)


class _Ctx:
    def __init__(self, company, rnd, t):
        global _CTX
        _CTX = self
        self.company, self.rnd, self.t = company, rnd, t
        self.abbr = frappe.db.get_value("Company", company, "abbr")
        self.currency = frappe.db.get_value("Company", company, "default_currency")
        self.cost_center = frappe.db.get_value("Company", company, "cost_center")
        self.counts, self.wh, self.boms, self.unplanned = {}, {}, {}, []
        self.wo_mill, self.so_mill = {}, {}
        self._current_day = None
        self.fibre = {f["code"]: f for f in t.fibres}
        self.yarn = {y["code"]: y for y in t.yarns}
        self.conv_yarn = {y["code"]: y for y in t.conversion.get("yarns", [])}
        self.conv_fibre = {f["code"]: f for f in t.conversion.get("fibres", [])}

    def months_in(self, day):
        """Months since the run started — drives price inflation and the slow OPS / yield improvement."""
        d = getdate(day)
        return (d.year - self.start.year) * 12 + d.month - self.start.month

    def price(self, rate, day):
        return round(rate * (1 + flt(self.t.get("price_inflation_per_month")) * self.months_in(day))
                     * self.rnd.uniform(0.97, 1.04), 2)

    def clock(self, day):
        """Posting times rise through the day in the order documents are made, so a delivery never lands
        before the manufacture entry that produced its stock."""
        if getattr(self, "_clock_day", None) != getdate(day):
            self._clock_day, self._clock_min = getdate(day), 8 * 60
        self._clock_min = min(self._clock_min + 3, 23 * 60 + 59)
        return f"{self._clock_min // 60:02d}:{self._clock_min % 60:02d}:00"

    def is_conversion(self, item_code):
        return item_code in self.conv_yarn

    def log(self, msg):
        print(msg)

    def bump(self, doctype, n=1):
        self.counts[doctype] = self.counts.get(doctype, 0) + n

    def summary(self):
        return {"company": self.company, "created": self.counts}


# ============================================================================ masters
def masters(ctx):
    t, c, abbr = ctx.t, ctx.company, ctx.abbr
    conv = t.conversion
    for uom in ("Kg", "Nos"):
        if not frappe.db.exists("UOM", uom):
            frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert()

    # Item groups ("Third Party" in the name tags conversion BOMs and stock — see mfg_logic.bom_category).
    root = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ["in", ["", None]]}, "name") or "All Item Groups"
    _ensure("Item Group", "Raw Fibre", {"item_group_name": "Raw Fibre", "parent_item_group": root, "is_group": 1})
    for g in sorted({f["group"] for f in t.fibres}):
        _ensure("Item Group", g, {"item_group_name": g, "parent_item_group": "Raw Fibre"})
    _ensure("Item Group", "Spun Yarn", {"item_group_name": "Spun Yarn", "parent_item_group": root})
    _ensure("Item Group", "Spinning Waste", {"item_group_name": "Spinning Waste", "parent_item_group": root})
    if conv:
        _ensure("Item Group", conv.fibre_group, {"item_group_name": conv.fibre_group, "parent_item_group": "Raw Fibre"})
        _ensure("Item Group", conv.yarn_group, {"item_group_name": conv.yarn_group, "parent_item_group": root})

    # Warehouses (incl. the third-party stores for customer-owned fibre and conversion yarn)
    parent_wh = frappe.db.get_value("Warehouse", {"company": c, "is_group": 1, "parent_warehouse": ["in", ["", None]]}, "name")
    for key, label in t.warehouses.items():
        if key.startswith("tp_") and not conv:
            continue
        name = f"{label} - {abbr}"
        _ensure("Warehouse", name, {"warehouse_name": label, "company": c, "parent_warehouse": parent_wh})
        ctx.wh[key] = name

    # Items + prices
    for f in t.fibres:
        _item(ctx, f["code"], f["name"], f["group"], ctx.wh["fibre"], f["rate"], buy=True)
    for y in t.yarns:
        _item(ctx, y["code"], y["name"], "Spun Yarn", ctx.wh["yarn"], y["rate"], sell=True)
    for w in t.wastes:
        _item(ctx, w["code"], w["name"], "Spinning Waste", ctx.wh["waste"], w["rate"], sell=True)
    for f in conv.get("fibres", []):          # customer-owned: no purchase, no value in the mill's books
        _item(ctx, f["code"], f["name"], conv.fibre_group, ctx.wh["tp_fibre"], 0, zero_value=True)
    for y in conv.get("yarns", []):           # priced at the conversion charge
        _item(ctx, y["code"], y["name"], conv.yarn_group, ctx.wh["tp_yarn"], y["charge"], sell=True)

    # Parties
    _ensure("Supplier Group", "Fibre Suppliers", {"supplier_group_name": "Fibre Suppliers",
            "parent_supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 1}, "name")})
    for s in t.suppliers:
        _ensure("Supplier", s, {"supplier_name": s, "supplier_group": "Fibre Suppliers", "supplier_type": "Company"})
    parent_cg = frappe.db.get_value("Customer Group", {"is_group": 1}, "name")
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or frappe.db.get_value("Territory", {}, "name")
    _ensure("Customer Group", "Yarn Customers", {"customer_group_name": "Yarn Customers", "parent_customer_group": parent_cg})
    for cu in t.customers:
        _ensure("Customer", cu, {"customer_name": cu, "customer_group": "Yarn Customers", "customer_type": "Company",
                                 "territory": territory})
    if conv:
        _ensure("Customer Group", conv.customer_group, {"customer_group_name": conv.customer_group,
                                                        "parent_customer_group": parent_cg})
        for cu in conv.customers:
            _ensure("Customer", cu, {"customer_name": cu, "customer_group": conv.customer_group,
                                     "customer_type": "Company", "territory": territory})

    ctx.sales_tax, ctx.purchase_tax = _tax_templates(ctx)

    ctx.overhead_accounts = _overhead_accounts(ctx)
    ctx.opening_account = frappe.db.get_value("Account", {"company": c, "is_group": 0, "account_type": "Temporary"}, "name")

    # Manufacture entries book operating cost as an additional cost; ERPNext needs this company default for it.
    if not frappe.db.get_value("Company", c, "default_operating_cost_account"):
        acc = frappe.db.get_value("Account", {"company": c, "is_group": 0,
                                              "account_name": ["like", "%Expenses Included In Valuation%"]}, "name")
        if acc:
            frappe.db.set_value("Company", c, "default_operating_cost_account", acc)

    # Operations + workstations (production_capacity high so parallel job cards never collide)
    ctx.ops = {}
    ws_meta = frappe.get_meta("Workstation")
    for o in t.operations:
        op = o["operation"]
        _ensure("Operation", op, {"name": op, "__newname": op})
        ctx.ops[op] = {"stations": [w["name"] for w in o["workstations"]], "mins": o["minutes_per_100kg"]}
        for w in o["workstations"]:
            data = {"workstation_name": w["name"], "hour_rate": w["hour_rate"], "production_capacity": 100}
            if ws_meta.has_field("spindles"):
                data["spindles"] = w.get("spindles", 0)
            if ws_meta.has_field("operation"):
                data["operation"] = op
            _ensure("Workstation", w["name"], data)
            if cint(frappe.db.get_value("Workstation", w["name"], "production_capacity")) < 100:
                frappe.db.set_value("Workstation", w["name"], "production_capacity", 100)
        if not frappe.db.get_value("Operation", op, "workstation"):
            frappe.db.set_value("Operation", op, "workstation", o["workstations"][0]["name"])

    # Spinning BOMs (own yarn and conversion yarn)
    for y in list(t.yarns) + list(conv.get("yarns", [])):
        bom = ctx.boms[y["code"]] = _bom(ctx, y["code"], y["blend"], y["yield"], y["ops"])
        # Default BOM is per item; make it this company's (a deleted or other company's default breaks orders).
        if frappe.db.get_value("Item", y["code"], "default_bom") != bom or not frappe.db.get_value("BOM", bom, "is_default"):
            frappe.db.sql("update `tabBOM` set is_default = 0 where item = %s and name != %s", (y["code"], bom))
            frappe.db.set_value("BOM", bom, "is_default", 1, update_modified=False)
            frappe.db.set_value("Item", y["code"], "default_bom", bom, update_modified=False)

    # One conversion contract per conversion customer, covering the run.
    for cu in conv.get("customers", []):
        _contract(ctx, cu)

    ctx.log(f"Masters ready: {len(t.fibres)} fibres, {len(t.yarns)} yarns, {len(conv.get('yarns', []))} conversion "
            f"yarns, {len(t.suppliers)} suppliers, {len(t.customers)} + {len(conv.get('customers', []))} customers, "
            f"{sum(len(o['workstations']) for o in t.operations)} workstations, {len(ctx.boms)} BOMs")


def _ensure(doctype, name, data):
    if frappe.db.exists(doctype, name):
        return frappe.get_doc(doctype, name)
    doc = frappe.get_doc({"doctype": doctype, **{k: v for k, v in data.items() if k != "__newname"}})
    if data.get("__newname"):
        doc.name = data["__newname"]
    if _CTX:
        _fill(_CTX, doc)
    doc.insert(ignore_permissions=True)
    return doc


def _item(ctx, code, name, group, warehouse, rate, buy=False, sell=False, zero_value=False):
    if not frappe.db.exists("Item", code):
        frappe.get_doc({
            "doctype": "Item", "item_code": code, "item_name": name, "item_group": group, "stock_uom": "Kg",
            "is_stock_item": 1, "include_item_in_manufacturing": 1, "valuation_rate": 0 if zero_value else rate,
            "is_purchase_item": 1 if buy else 0, "is_sales_item": 1 if sell else 0,
            "item_defaults": [{"company": ctx.company, "default_warehouse": warehouse}],
        }).insert(ignore_permissions=True)
    elif not frappe.db.exists("Item Default", {"parent": code, "company": ctx.company}):
        item = frappe.get_doc("Item", code)
        item.append("item_defaults", {"company": ctx.company, "default_warehouse": warehouse})
        item.save(ignore_permissions=True)
    for price_list, flag in (("Standard Buying", buy), ("Standard Selling", sell)):
        if flag and frappe.db.exists("Price List", price_list) and not frappe.db.exists(
            "Item Price", {"item_code": code, "price_list": price_list}
        ):
            frappe.get_doc({"doctype": "Item Price", "item_code": code, "price_list": price_list,
                            "price_list_rate": rate, "currency": ctx.currency}).insert(ignore_permissions=True)


def _overhead_accounts(ctx):
    """Leaf expense accounts for the month-end overheads, under whatever expense groups the chart has.
    Factory ones are flagged CPS-applicable (cost per spindle) where that field exists."""
    c = ctx.company

    def group(*hints):
        for h in hints:                  # exact group names first: "%Direct Expense%" would also match "Indirect Expenses"
            g = frappe.db.get_value("Account", {"company": c, "is_group": 1, "root_type": "Expense", "account_name": h}, "name")
            if g:
                return g
        for h in hints:
            g = frappe.db.get_value("Account", {"company": c, "is_group": 1, "root_type": "Expense",
                                                "account_name": ["like", f"{h}%"]}, "name")
            if g:
                return g
        return frappe.db.get_value("Account", {"company": c, "is_group": 1, "root_type": "Expense",
                                               "parent_account": ["in", ["", None]]}, "name")

    factory = group("Direct Expenses", "Cost of Sales", "Cost of Sale", "Manufacturing")
    other = group("Indirect Expenses", "Administrative", "Selling")
    cps = frappe.get_meta("Account").has_field("cps_applicable")
    out = {}
    oh = ctx.t.get("overheads") or {}
    for kind, parent in (("factory", factory), ("other", other)):
        for row in oh.get(kind, []):
            name = frappe.db.get_value("Account", {"company": c, "account_name": row["account"], "is_group": 0}, "name")
            if name and parent and frappe.db.get_value("Account", name, "parent_account") != parent:
                acc = frappe.get_doc("Account", name)       # factory overheads are manufacturing cost (gross margin)
                acc.parent_account = parent
                acc.save(ignore_permissions=True)
            if not name:
                name = frappe.get_doc({"doctype": "Account", "account_name": row["account"], "parent_account": parent,
                                       "company": c, "account_type": "Expense Account",
                                       **({"cps_applicable": 1} if cps and kind == "factory" else {})}).insert(ignore_permissions=True).name
            out[row["account"]] = name
    return out


def _contract(ctx, customer):
    if frappe.db.exists("Contract", {"party_type": "Customer", "party_name": customer, "docstatus": ["<", 2]}):
        return
    start = ctx.start or getdate("2025-07-01")
    doc = frappe.get_doc({"doctype": "Contract", "party_type": "Customer", "party_name": customer,
                          "start_date": start, "end_date": add_days(start, 729), "is_signed": 1,
                          "signee": f"Purchasing head, {customer}", "signed_on": get_datetime(f"{start} 11:00:00"),
                          "contract_terms": ctx.t.conversion.contract_terms})
    doc.flags.ignore_permissions = True
    doc.insert()
    if doc.meta.is_submittable:
        doc.submit()
    ctx.bump("Contract")


def _tax_templates(ctx):
    """Sales tax on sales and purchases, if the company's chart has a Duties and Taxes group."""
    c, abbr, rate = ctx.company, ctx.abbr, flt(ctx.t.get("tax_rate") or 18)
    parent = frappe.db.get_value("Account", {"company": c, "is_group": 1, "account_name": ["like", "%Duties and Taxes%"]}, "name")
    if not parent:
        ctx.log("No 'Duties and Taxes' group in the chart of accounts — documents will be created without tax.")
        return None, None
    out = []
    for kind, acc_name in (("Sales", f"Output Sales Tax {rate:g}%"), ("Purchase", f"Input Sales Tax {rate:g}%")):
        account = frappe.db.get_value("Account", {"company": c, "account_name": acc_name}, "name")
        if not account:
            account = frappe.get_doc({"doctype": "Account", "account_name": acc_name, "parent_account": parent,
                                      "company": c, "account_type": "Tax", "tax_rate": rate}).insert(ignore_permissions=True).name
        doctype = f"{kind} Taxes and Charges Template"
        title = f"GST {rate:g}% {kind}"
        name = f"{title} - {abbr}"
        if not frappe.db.exists(doctype, name):
            frappe.get_doc({"doctype": doctype, "title": title, "company": c, "taxes": [{
                "charge_type": "On Net Total", "account_head": account, "rate": rate,
                "description": f"Sales tax {rate:g}% ({kind.lower()})",
                **({"category": "Total", "add_deduct_tax": "Add"} if kind == "Purchase" else {}),
            }]}).insert(ignore_permissions=True)
        out.append(name)
    return out[0], out[1]


def _bom(ctx, yarn, blend, item_yield, ops):
    # The default flag is per item across companies, so look for this company's latest active BOM instead.
    existing = frappe.db.get_value("BOM", {"item": yarn, "company": ctx.company, "is_active": 1, "docstatus": 1},
                                   "name", order_by="creation desc")
    if existing:
        return existing
    meta = frappe.get_meta("BOM")
    spinning = meta.has_field("bom_type")
    bom = frappe.get_doc({
        "doctype": "BOM", "item": yarn, "company": ctx.company, "quantity": 100, "is_active": 1, "is_default": 1,
        "rm_cost_as_per": "Price List", "buying_price_list": "Standard Buying", "currency": ctx.currency,
        "with_operations": 1, "transfer_material_against": "Work Order",
        "items": [{"item_code": f, "qty": 100 * pct / 100, **({"blend_ratio": pct, "item_yield": item_yield} if spinning else {})}
                  for f, pct in blend.items()],
        "operations": [{"operation": o["operation"], "workstation": ctx.ops[o["operation"]]["stations"][0],
                        "time_in_mins": ctx.ops[o["operation"]]["mins"]} for o in ctx.t.operations],
    })
    # Conversion BOMs consume customer fibre at zero value, so their waste carries no cost either
    # (v16 refuses secondary items costing more than the raw material).
    free = all(f in ctx.conv_fibre for f in blend)
    waste = [(w["code"], w["per_100kg"], 0 if free else w["rate"]) for w in ctx.t.wastes]
    if meta.get_field("secondary_items"):          # ERPNext v16
        for code, q, r in waste:
            bom.append("secondary_items", {"item_code": code, "secondary_item_type": "Scrap", "uom": "Kg",
                                           "stock_uom": "Kg", "conversion_factor": 1, "qty": q, "stock_qty": q,
                                           "valuation_type": "Manual", "cost": q * r})
    elif meta.get_field("scrap_items"):
        for code, q, r in waste:
            bom.append("scrap_items", {"item_code": code, "stock_qty": q, "rate": r})
    if spinning:
        bom.update({"bom_type": "Spinning", "target_ops": ops, "invisible_lose_percentage": 2})
    bom.insert(ignore_permissions=True)
    bom.submit()
    ctx.bump("BOM")
    return bom.name


def _ensure_fiscal_years(from_date, to_date, company):
    from erpnext.accounts.utils import get_fiscal_year
    d = from_date
    while d <= to_date:
        try:
            get_fiscal_year(d, company=company)
        except Exception:
            # No year covers the date, the one that does is disabled, or it is restricted to other companies.
            existing = frappe.db.get_value("Fiscal Year", {"year_start_date": ["<=", d], "year_end_date": [">=", d]}, "name")
            if existing:
                fy = frappe.get_doc("Fiscal Year", existing)
                if fy.disabled:
                    fy.disabled = 0
                    print(f"Enabled fiscal year {fy.name}")
                if fy.get("companies") and not any(r.company == company for r in fy.companies):
                    fy.append("companies", {"company": company})
                fy.save(ignore_permissions=True)
            else:
                start = getdate(f"{d.year if d.month >= 7 else d.year - 1}-07-01")
                end = getdate(f"{start.year + 1}-06-30")
                frappe.get_doc({"doctype": "Fiscal Year", "year": f"{start.year}-{end.year}", "year_start_date": start,
                                "year_end_date": end}).insert(ignore_permissions=True)
            _forget_fiscal_years()
        d = add_days(d, 28)
    # Re-check with a clean cache: ERPNext keeps the per-company year list in Redis *and* in this process's memory, so a
    # stale copy would make HRMS / stock postings fail later on a year that was just enabled.
    _forget_fiscal_years()
    d = from_date
    while d <= to_date:
        get_fiscal_year(d, company=company)       # raises a clear FiscalYearError now, not halfway through the run
        d = add_days(d, 28)


def _forget_fiscal_years():
    frappe.db.commit()
    frappe.cache.delete_value("fiscal_years")
    local = getattr(frappe.local, "cache", None)
    if isinstance(local, dict):
        for k in [k for k in local if "fiscal_years" in str(k)]:
            local.pop(k, None)
    frappe.clear_cache()


def _tune_manufacturing_settings():
    ms = frappe.get_single("Manufacturing Settings")
    saved = {f: ms.get(f) for f in ("disable_capacity_planning", "transfer_extra_materials_percentage",
                                     "overproduction_percentage_for_work_order", "backflush_raw_materials_based_on")}
    frappe.db.set_single_value("Manufacturing Settings", {
        "disable_capacity_planning": 1,
        "transfer_extra_materials_percentage": 15,   # spinning issues material grossed up for yield loss
        "overproduction_percentage_for_work_order": 5,
        "backflush_raw_materials_based_on": "Material Transferred for Manufacture",
    })
    return saved


def _restore_manufacturing_settings(saved):
    frappe.db.set_single_value("Manufacturing Settings", {k: v for k, v in saved.items() if v is not None})


# ============================================================================ transactions
def transactions(ctx, from_date, to_date, sales_every_days):
    rnd, season = ctx.rnd, ctx.t.season
    _opening_stock(ctx, from_date)          # fibre on hand so the first lots can start on day one

    events = []  # (date, order, fn, args)

    def at(d, order, fn, *args):
        if getdate(d) <= to_date:
            events.append((getdate(d), order, fn, args))

    d, n = from_date, 0
    while d <= to_date:
        f = season[getdate(d).month]
        # Busy months (factor > 1) get an extra order now and then, quiet ones skip some.
        if n % sales_every_days == 0 and rnd.random() < min(1.0, f):
            at(d, 1, _sales_order, d)
            if rnd.random() < f - 1.0:
                at(d, 1, _sales_order, d)
        if getdate(d).weekday() in (0, 3):                 # Monday and Thursday planning runs
            at(d, 1.5, _production_plan, d)
        if getdate(d).weekday() == 0:                      # Mondays: replenish fibre
            at(d, 0, _replenish_fibre, d)
        if getdate(d).day == 1 or getdate(d) == from_date:
            at(d, -1, demo_modules.month_start, d)        # joiners / leavers
        if add_days(d, 1).month != getdate(d).month or getdate(d) == to_date:
            at(d, 8.5, _sell_waste, d)                    # waste sold before the month's overheads are booked
            at(d, 9, _month_end_overheads, d)             # last day of the month (or the last day generated)
            at(d, 9.5, demo_modules.month_end, d)         # attendance, payroll, quality reviews, depreciation
        d, n = add_days(d, 1), n + 1

    ctx.at = at
    demo_setup.schedule_equity(ctx)          # share capital, rights issue, dividends
    while events:
        events.sort(key=lambda e: (e[0], e[1]))
        day, _o, fn, args = events.pop(0)
        ctx._current_day = day
        try:
            try:
                fn(ctx, *args)
            except frappe.QueryDeadlockError:          # a worker / scheduler touched the same rows: retry once
                frappe.db.rollback()
                fn(ctx, *args)
            frappe.db.commit()
        except Exception as e:
            frappe.db.rollback()
            ctx.log(f"  ! {day} {fn.__name__}: {str(e)[:220]}")
            ctx.bump("errors")
    ctx.log(f"Created: {ctx.counts}")


def _fill(ctx, doc):
    """Company defaults any site may make mandatory: cost centre on the header and on every row, plus any field a
    site made mandatory itself (Custom Field / Property Setter) that the generator does not know about."""
    cc = ctx.cost_center
    if cc and doc.meta.has_field("cost_center") and not doc.get("cost_center"):
        doc.cost_center = cc
    for table in doc.meta.get_table_fields():
        for row in doc.get(table.fieldname) or []:
            if row.meta.has_field("cost_center") and not row.get("cost_center"):
                row.cost_center = cc
    for df in _site_required(doc.doctype):
        if doc.get(df.fieldname) in (None, ""):
            value = _site_default(ctx, doc, df)
            if value not in (None, ""):
                doc.set(df.fieldname, value)
    return doc


_SITE_REQD = {}


def _site_required(doctype):
    """Fields mandatory on this site but not in the standard DocType (site customisations)."""
    if doctype not in _SITE_REQD:
        std = set(frappe.db.sql_list("select fieldname from `tabDocField` where parent=%s and reqd=1", doctype))
        _SITE_REQD[doctype] = [df for df in frappe.get_meta(doctype).fields
                               if df.reqd and df.fieldname not in std and df.fieldtype not in ("Table", "Table MultiSelect")]
    return _SITE_REQD[doctype]


def _site_default(ctx, doc, df):
    if df.fieldtype == "Link":
        if df.options == "Department" and doc.get("department"):
            return doc.department
        return _default_link(ctx, df.options, doc)
    if df.fieldtype == "Select":
        opts = [o for o in (df.options or "").split("\n") if o]
        # a flag-like select ("Yes/No", "out of order"...) must not default to its alarming option
        safe = next((o for o in opts if o.lower() in ("no", "none", "normal", "active", "working", "available")), None)
        return safe or (opts[0] if opts else None)
    if df.fieldtype == "Check":
        return 0
    if df.fieldtype in ("Int", "Float", "Currency", "Percent"):
        if "capacity" in df.fieldname:              # e.g. Workstation.capacity_per_day on some sites
            return 1000
        return 0
    if df.fieldtype == "Datetime":
        return get_datetime(f"{ctx._current_day or nowdate()} 09:00:00")
    if df.fieldtype == "Time":
        return "09:00:00"
    if df.fieldtype in ("Data", "Small Text", "Text", "Read Only"):
        if df.fieldname == "department_name" and doc.get("department"):
            return frappe.db.get_value("Department", doc.department, "department_name")
        if df.fieldname == "father_name":
            return ctx.rnd.choice((ctx.t.get("hr") or {}).get("male_names") or ["Muhammad Akram"])
        return "N/A"
    if df.fieldtype in ("Date",):
        return ctx._current_day or nowdate()
    return None


def _default_link(ctx, doctype, doc=None):
    """A sensible existing record to link to: this company's (and currency's) first, submitted if submittable."""
    cache = ctx.__dict__.setdefault("_link_defaults", {})
    if doctype in cache:
        return cache[doctype]
    meta = frappe.get_meta(doctype)
    filters = {}
    if meta.has_field("company"):
        filters["company"] = ctx.company
    if meta.has_field("currency"):
        filters["currency"] = ctx.currency
    if meta.is_submittable:
        filters["docstatus"] = 1
    if meta.has_field("disabled"):
        filters["disabled"] = 0
    name = frappe.db.get_value(doctype, filters, "name", order_by="creation desc")
    if not name and "company" in filters:
        filters.pop("company")
        name = frappe.db.get_value(doctype, filters, "name", order_by="creation desc")
    if not name and doctype == "Branch":
        name = frappe.get_doc({"doctype": "Branch", "branch": "Head Office"}).insert(ignore_permissions=True).name
    cache[doctype] = name
    return name


def _insert_submit(ctx, doc):
    _fill(ctx, doc)
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()
    ctx.bump(doc.doctype)
    return doc


def _submit(ctx, doc, posting_date, remarks=True):
    if doc.meta.has_field("posting_date"):
        doc.posting_date = posting_date
    if doc.meta.has_field("set_posting_time"):
        doc.set_posting_time = 1
    if doc.meta.has_field("posting_time"):
        doc.posting_time = ctx.clock(posting_date)
    if remarks and doc.meta.has_field("remarks"):
        doc.remarks = f"{DEMO_TAG}" + (f" · {doc.remarks}" if doc.get("remarks") else "")
    return _insert_submit(ctx, doc)


def _opening_stock(ctx, day):
    se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": ctx.company,
                         "items": [{"item_code": f["code"], "qty": f.get("opening", 20000), "t_warehouse": ctx.wh["fibre"],
                                    "basic_rate": f["rate"],
                                    # opening stock belongs on the balance sheet, not in this year's P&L
                                    **({"expense_account": ctx.opening_account} if ctx.opening_account else {})}
                                   for f in ctx.t.fibres]})
    _submit(ctx, se, day)


def _balance(ctx, code, warehouse):
    """Stock as of the day being generated (not today's Bin, which can include later entries)."""
    from erpnext.stock.utils import get_stock_balance
    return flt(get_stock_balance(code, warehouse, ctx._current_day or nowdate(), "23:59:59"))


def _replenish_fibre(ctx, day):
    """Weekly: request what keeps each fibre at its reorder level, order it, receive, bill and pay later."""
    from erpnext.stock.doctype.material_request.material_request import make_purchase_order
    rnd = ctx.rnd
    lines = []
    for f in ctx.t.fibres:
        need = flt(f.get("reorder_to", 20000)) - _balance(ctx, f["code"], ctx.wh["fibre"])
        if need > 2000:
            lines.append((f["code"], round(need / 500 + 0.5) * 500, f["rate"]))
    if not lines:
        return
    mr = frappe.get_doc({"doctype": "Material Request", "material_request_type": "Purchase", "company": ctx.company,
                         "transaction_date": day, "schedule_date": add_days(day, 7),
                         "items": [{"item_code": code, "qty": qty, "schedule_date": add_days(day, 7),
                                    "warehouse": ctx.wh["fibre"]} for code, qty, _r in lines]})
    _insert_submit(ctx, mr)
    for code, qty, rate in lines:            # one PO per line, 1-3 days after the request
        po = make_purchase_order(mr.name)
        po.items = [i for i in po.items if i.item_code == code]
        imp = demo_modules.import_supplier_for(ctx, code)      # some lots are imported (longer lead time)
        po.supplier = imp["name"] if imp else rnd.choice(ctx.t.suppliers)
        po.transaction_date = add_days(day, rnd.randint(1, 3))
        po.schedule_date = add_days(po.transaction_date, imp["transit"] + 12 if imp else rnd.randint(4, 12))
        for i in po.items:
            i.schedule_date = po.schedule_date
            i.rate = ctx.price(rate, po.transaction_date) * (flt(ctx.t["import"].get("price_factor", 0.93)) if imp else 1)
        if not imp:                          # imports: sales tax is paid at customs, on the cost sheet
            _apply_tax(ctx, po, "Purchase")
        _insert_submit(ctx, po)
        buy = ctx.t.get("buying") or {}
        if imp:
            ctx.at(add_days(po.transaction_date, rnd.randint(3, 10)), 0, demo_modules.import_departure, po.name, imp["name"])
        elif rnd.random() < flt(buy.get("quick_share")):     # local supplier delivers the same / next day
            ctx.at(add_days(po.transaction_date, rnd.randint(0, 1)), 0, _receive_and_bill, po.name)
        else:
            ctx.at(add_days(po.transaction_date, rnd.randint(2, 9)), 0, _receive_and_bill, po.name)
    buy = ctx.t.get("buying") or {}
    if rnd.random() < flt(buy.get("suspect_share")) * 4:     # ~ suspect_share of PO lines: a sample lot keyed per bale
        _suspect_purchase(ctx, day)


def _suspect_purchase(ctx, day):
    """A small sample lot whose rate was keyed per bale instead of per kg — the 'suspect rate' the buying dashboard flags."""
    rnd, buy = ctx.rnd, ctx.t.get("buying") or {}
    f = rnd.choice(ctx.t.fibres)
    lo, hi = buy.get("suspect_factor") or [3.2, 4.5]
    po = frappe.get_doc({"doctype": "Purchase Order", "supplier": rnd.choice(ctx.t.suppliers), "company": ctx.company,
                         "transaction_date": day, "schedule_date": add_days(day, 2),
                         "items": [{"item_code": f["code"], "qty": rnd.randrange(100, 400, 50), "schedule_date": add_days(day, 2),
                                    "rate": round(ctx.price(f["rate"], day) * rnd.uniform(lo, hi), 2), "warehouse": ctx.wh["fibre"]}]})
    _apply_tax(ctx, po, "Purchase")
    _insert_submit(ctx, po)
    ctx.bump("Suspect-rate line")
    ctx.at(add_days(day, rnd.randint(0, 2)), 0, _receive_and_bill, po.name)


def _apply_tax(ctx, doc, kind):
    template = ctx.purchase_tax if kind == "Purchase" else ctx.sales_tax
    if template:
        from erpnext.controllers.accounts_controller import get_taxes_and_charges
        doc.taxes_and_charges = template
        doc.set("taxes", get_taxes_and_charges(f"{kind} Taxes and Charges Template", template))


def _receive_and_bill(ctx, po_name):
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
    rnd, day = ctx.rnd, ctx._current_day
    pr = make_purchase_receipt(po_name)
    for i in pr.items:  # occasionally a short delivery
        if rnd.random() < 0.15:
            i.qty = i.received_qty = round(i.qty * rnd.uniform(0.9, 0.98))
    _submit(ctx, pr, day)
    demo_modules.incoming_qi(ctx, pr)
    pi = make_purchase_invoice(pr.name)
    pi.bill_no = f"INV-{rnd.randint(10000, 99999)}"
    pi.bill_date = day
    pi.due_date = add_days(day, 30)
    _submit(ctx, pi, add_days(day, rnd.randint(0, 2)))
    if rnd.random() < 0.8:
        ctx.at(add_days(pi.posting_date, rnd.randint(7, 35)), 3, _pay, "Purchase Invoice", pi.name)


def _month_end_overheads(ctx, day):
    """One Journal Entry for the month's running costs, sized on the yarn made and sold that month."""
    oh = ctx.t.get("overheads") or {}
    if not oh or not ctx.overhead_accounts:
        return
    d = getdate(day)
    first = d.replace(day=1)
    yarns = tuple(list(ctx.yarn) + list(ctx.conv_yarn))
    made = flt(frappe.db.sql("""select sum(actual_qty) from `tabStock Ledger Entry` where company=%s and is_cancelled=0
        and voucher_type='Stock Entry' and actual_qty>0 and item_code in %s and posting_date between %s and %s""",
                              (ctx.company, yarns, first, d))[0][0])
    sold = -flt(frappe.db.sql("""select sum(actual_qty) from `tabStock Ledger Entry` where company=%s and is_cancelled=0
        and voucher_type='Delivery Note' and item_code in %s and posting_date between %s and %s""",
                               (ctx.company, yarns, first, d))[0][0])
    summer = d.month in ctx.t.load_shedding
    cc = getattr(ctx, "cc", {}) or {}
    share = {k: v for k, v in ((ctx.t.get("planning") or {}).get("mill_share") or {}).items() if cc.get(k)}
    cash = getattr(ctx, "cash", {}) or {}
    lines = []            # (account, amount, cost centre, paid from)
    for row in oh.get("factory", []):
        amt = made * flt(row.get("per_kg")) * ((flt(row.get("summer_factor")) or 1) if summer else 1) * ctx.rnd.uniform(0.95, 1.08)
        # factory running costs split over the mills by their share of output; repairs partly paid in cash at the mill
        for key, w in (share.items() or [(None, 1)]):
            part = amt * w * ctx.rnd.uniform(0.9, 1.1)
            petty = part * 0.15 if cash.get("mills") and "repair" in row["account"].lower() else 0
            lines.append((ctx.overhead_accounts[row["account"]], part - petty, cc.get(key, ctx.cost_center), None))
            if petty:
                lines.append((ctx.overhead_accounts[row["account"]], petty, cc.get(key, ctx.cost_center), cash["mills"]))
    for row in oh.get("other", []):
        amt = (sold * flt(row.get("per_kg_sold")) + flt(row.get("per_month")) * (1 + 0.01 * ctx.months_in(day))) * ctx.rnd.uniform(0.97, 1.05)
        freight = "freight" in row["account"].lower()
        # freight is paid to transporters from the mills' cash; office costs partly from head-office cash
        paid = cash.get("mills") if freight else None
        if not freight and cash.get("head_office"):
            lines.append((ctx.overhead_accounts[row["account"]], amt * 0.2, ctx.cost_center, cash["head_office"]))
            amt *= 0.8
        lines.append((ctx.overhead_accounts[row["account"]], amt, ctx.cost_center, paid))
    lines = [(a, round(v, 2), c_, p_) for a, v, c_, p_ in lines if v > 0]
    if not lines:
        return
    bank = demo_setup.bank(ctx)
    credit = {}
    for _a, v, _c, p_ in lines:
        credit[p_ or bank] = credit.get(p_ or bank, 0) + v
    je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": ctx.company,
                         "posting_date": day, "user_remark": f"{DEMO_TAG} · mill overheads for {d.strftime('%B %Y')}",
                         "accounts": [{"account": a, "debit_in_account_currency": v, "cost_center": c_} for a, v, c_, _p in lines]
                         + [{"account": acc, "credit_in_account_currency": round(v, 2), "cost_center": ctx.cost_center}
                            for acc, v in credit.items()]})
    _insert_submit(ctx, je)
    _top_up_cash(ctx, day, {acc: v for acc, v in credit.items() if acc in cash.values()})


def _top_up_cash(ctx, day, spent):
    """Cash in hand is drawn from the bank: what the month's cash payments used, plus the opening float the first time."""
    cash = getattr(ctx, "cash", {}) or {}
    if not cash:
        return
    rows = []
    for key, acc in cash.items():
        amt = flt(spent.get(acc))
        if not getattr(ctx, "_cash_funded", None):
            amt += flt(getattr(ctx, "cash_float", {}).get(key))
        if amt > 0:
            rows.append((acc, round(amt, 2)))
    ctx._cash_funded = True
    if not rows:
        return
    je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Contra Entry", "company": ctx.company,
                         "posting_date": day, "cheque_no": f"CSH-{ctx.rnd.randint(10000, 99999)}", "cheque_date": day,
                         "user_remark": f"{DEMO_TAG} · cash withdrawn for petty expenses",
                         "accounts": [{"account": a, "debit_in_account_currency": v, "cost_center": ctx.cost_center} for a, v in rows]
                         + [{"account": demo_setup.bank(ctx), "credit_in_account_currency": sum(v for _a, v in rows),
                             "cost_center": ctx.cost_center}]})
    _insert_submit(ctx, je)


def _sell_waste(ctx, day):
    """Month end: most of the waste on hand is sold to waste buyers (Waste Sales income, invoice with stock update)."""
    ws = (ctx.t.get("company_setup") or {}).get("waste_sales")
    if not ws or not getattr(ctx, "waste_income", None):
        return
    lo, hi = ws.get("sell_share") or [0.7, 0.95]
    items = []
    for w in ctx.t.wastes:
        on_hand = _balance(ctx, w["code"], ctx.wh["waste"])
        qty = round(on_hand * ctx.rnd.uniform(lo, hi))
        if qty >= 50:
            items.append({"item_code": w["code"], "qty": qty, "warehouse": ctx.wh["waste"], "income_account": ctx.waste_income,
                          "rate": round(ctx.price(w["rate"], day) * flt(ws.get("rate_factor") or 1), 2),
                          "cost_center": demo_setup.mill_cc(ctx)})
    if not items:
        return
    si = frappe.get_doc({"doctype": "Sales Invoice", "customer": ctx.rnd.choice(ws["customers"]), "company": ctx.company,
                         "posting_date": day, "set_posting_time": 1, "update_stock": 1, "due_date": add_days(day, 15),
                         "selling_price_list": "Standard Selling", "items": items})
    _apply_tax(ctx, si, "Sales")
    _submit(ctx, si, day)
    ctx.bump("Waste sale")
    ctx.at(add_days(day, ctx.rnd.randint(3, 15)), 5, _pay, "Sales Invoice", si.name)


def _pay(ctx, dt, dn):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    pe = get_payment_entry(dt, dn)
    bank = demo_setup.bank(ctx)
    if pe.payment_type == "Receive":
        pe.paid_to = bank
    else:
        pe.paid_from = bank
    pe.reference_no = f"CHQ-{ctx.rnd.randint(100000, 999999)}"
    pe.reference_date = ctx._current_day
    col = ctx.t.get("collections") or {}
    if dt == "Sales Invoice" and ctx.rnd.random() < flt(col.get("part_payment_share", 0.25)):   # part payment
        part = round(pe.paid_amount * ctx.rnd.uniform(0.4, 0.8), -3)
        pe.paid_amount = pe.received_amount = part
        pe.references[0].allocated_amount = part
        if ctx.rnd.random() < flt(col.get("remainder_probability", 0.7)):                      # rest later
            ctx.at(add_days(ctx._current_day, ctx.rnd.randint(20, 60)), 5, _pay, dt, dn)
    _submit(ctx, pe, ctx._current_day)
    if dt == "Sales Invoice" and ctx.t.get("export") and not flt(frappe.db.get_value(dt, dn, "outstanding_amount")):
        so = frappe.db.get_value("Sales Invoice Item", {"parent": dn}, "sales_order")
        if so and frappe.db.get_value("Sales Order", so, "export_order_flag") == "1":
            demo_modules.close_lc(ctx, so)


def _sales_order(ctx, day):
    """A customer order for one yarn (own yarn, or a conversion order at the conversion charge); it waits for
    the next production plan."""
    rnd, conv = ctx.rnd, ctx.t.conversion
    qty = int(rnd.randrange(1500, 6000, 100) * ctx.t.season[getdate(day).month] / 100) * 100
    due = add_days(day, rnd.randint(7, 14))
    ex, export = ctx.t.get("export"), {}
    if conv and rnd.random() < flt(conv.share):
        y = rnd.choices(conv.yarns, weights=[c.get("weight", 1) for c in conv.yarns])[0]
        customer, rate, wh = rnd.choice(conv.customers), ctx.price(y["charge"], day), ctx.wh["tp_yarn"]
    else:
        y = rnd.choices(ctx.t.yarns, weights=[c.get("weight", 1) for c in ctx.t.yarns])[0]
        customer, rate, wh = rnd.choice(ctx.t.customers), ctx.price(y["rate"], day), ctx.wh["yarn"]
        if ex and rnd.random() < flt(ex.get("share")):          # export order under LC, a longer lead time
            due = add_days(day, rnd.randint(14, 24))
            cu, export = demo_modules.export_order_fields(ctx, day, due)
            customer, rate = cu["name"], round(rate * flt(ex.get("premium", 1)), 2)
    pl = ctx.t.get("planning") or {}
    rush = not export and rnd.random() < flt(pl.get("rush_share"))
    if rush:                                   # customer wants it sooner than the mill's usual lead time
        due = add_days(day, rnd.randint(*(pl.get("rush_days") or [3, 6])))
    so = frappe.get_doc({
        "doctype": "Sales Order", "customer": customer, "company": ctx.company,
        "transaction_date": day, "delivery_date": due, "selling_price_list": "Standard Selling",
        "items": [{"item_code": y["code"], "qty": qty, "rate": rate, "delivery_date": due, "warehouse": wh}],
        **{k: v for k, v in export.items() if k != "customer"},
    })
    if not export:                            # exports are zero-rated
        _apply_tax(ctx, so, "Sales")
    _insert_submit(ctx, so)
    if export:
        demo_modules.lc_for_order(ctx, so, cu, day)
    if ctx.is_conversion(y["code"]):
        _receive_customer_fibre(ctx, so, y, qty, day)
    if rnd.random() < flt(pl.get("hold_share")):
        # on hold (credit check / customer's confirmation pending): planned weeks later, or never — the ageing tail
        ctx.bump("Order on hold")
        if rnd.random() < 0.5:
            ctx.at(add_days(day, rnd.randint(20, 75)), 0.5, _release_hold, so.name)
        return
    ctx.unplanned.append(so.name)


def _release_hold(ctx, so_name):
    if frappe.db.get_value("Sales Order", so_name, "status") not in ("Closed", "Completed"):
        ctx.unplanned.append(so_name)

def _receive_customer_fibre(ctx, so, y, qty, day):
    """Conversion: the customer's own fibre arrives for the order — received into Third Party Fibre at zero value
    (it is not the mill's stock), grossed up for yield plus the contract's extra allowance."""
    extra = 1 + flt(ctx.t.conversion.get("extra_fibre_pct")) / 100
    se = frappe.get_doc({
        "doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": ctx.company,
        "remarks": f"Fibre received from {so.customer} for conversion order {so.name}",
        "items": [{"item_code": f, "qty": round(qty * pct / 100 / (y["yield"] / 100) * extra, 2),
                   "t_warehouse": ctx.wh["tp_fibre"], "basic_rate": 0, "allow_zero_valuation_rate": 1}
                  for f, pct in y["blend"].items()],
    })
    _submit(ctx, se, day)


def _production_plan(ctx, day):
    """Twice a week the planner takes every open order into a Production Plan, fills its raw-material tab and
    makes the Work Orders from it (Sales Order -> Production Plan -> Work Order)."""
    if not ctx.unplanned:
        return
    sos, ctx.unplanned = ctx.unplanned, []
    pp = frappe.new_doc("Production Plan")
    pp.update({"company": ctx.company, "posting_date": day, "get_items_from": "Sales Order"})
    for so in sos:
        d = frappe.db.get_value("Sales Order", so, ["transaction_date", "customer", "grand_total"], as_dict=True)
        pp.append("sales_orders", {"sales_order": so, "sales_order_date": d.transaction_date,
                                   "customer": d.customer, "grand_total": d.grand_total})
    pp.get_items()
    for row in pp.po_items:
        row.bom_no = ctx.boms.get(row.item_code) or row.bom_no      # this company's BOM, not another's default
        row.planned_start_date = get_datetime(f"{day} 06:00:00")
        row.warehouse = ctx.wh["tp_yarn"] if ctx.is_conversion(row.item_code) else ctx.wh["yarn"]
    _insert_submit(ctx, pp)
    planner = demo_setup.planner(ctx)
    if planner:
        frappe.db.set_value("Production Plan", pp.name, "owner", planner, update_modified=False)
    try:                                    # Raw Materials tab (micromax), informational
        from micromax.production_plan import fill_raw_materials
        fill_raw_materials(pp.name)
    except Exception:
        pass
    pp.reload()
    pp.make_work_order()
    for wo_name in frappe.get_all("Work Order", {"production_plan": pp.name, "docstatus": 0}, pluck="name"):
        wo = frappe.get_doc("Work Order", wo_name)
        _prepare_work_order(ctx, wo, day)
        wo.flags.ignore_permissions = True
        wo.save()
        wo.submit()
        ctx.bump("Work Order")
        if planner:
            frappe.db.set_value("Work Order", wo.name, "owner", planner, update_modified=False)
        # which mill runs the lot: its cost centre goes on the lot's stock entries, delivery and invoice
        ctx.wo_mill[wo.name] = demo_setup.mill_cc(ctx)
        if wo.sales_order:
            ctx.so_mill[wo.sales_order] = ctx.wo_mill[wo.name]
        ctx.at(add_days(day, 1), 2, _transfer, wo.name)


def _prepare_work_order(ctx, wo, day):
    """What the planner fills on the Work Order: warehouses, ring frames, spindles, dates."""
    rnd = ctx.rnd
    conv = ctx.is_conversion(wo.production_item)
    wo.update({"wip_warehouse": ctx.wh["wip"], "fg_warehouse": ctx.wh["tp_yarn" if conv else "yarn"],
               "source_warehouse": ctx.wh["tp_fibre" if conv else "fibre"], "scrap_warehouse": ctx.wh["waste"],
               "transfer_material_against": "Work Order", "planned_start_date": get_datetime(f"{day} 06:00:00")})
    if wo.sales_order:
        wo.expected_delivery_date = frappe.db.get_value("Sales Order", wo.sales_order, "delivery_date")
    if wo.meta.has_field("work_order_date"):
        wo.work_order_date = day
    if wo.meta.has_field("spindle_allocated"):
        ops = flt(frappe.db.get_value("BOM", wo.bom_no, "target_ops")) if frappe.get_meta("BOM").has_field("target_ops") else 0
        required = 16 * flt(wo.qty) / ops if ops else 0
        wo.spindle_allocated = round(required * rnd.uniform(0.95, 1.0)) if required else 1
    if not wo.operations:
        wo.get_items_and_operations_from_bom()
    for op in wo.operations:                     # spread the lot over the ring frames / cards
        op.workstation = rnd.choice(ctx.ops[op.operation]["stations"])
    for row in wo.required_items:
        row.source_warehouse = wo.source_warehouse
    _fill(ctx, wo)


def _gross_qty(wo, fg_qty):
    """Fibre needed for `fg_qty` of yarn: blend share grossed up for the lot's target yield."""
    y = flt(wo.get("target_yield")) or 95
    return {i.item_code: fg_qty * (flt(i.get("blend_ratio")) or 100 * flt(i.required_qty) / flt(wo.qty)) / 100 / (y / 100)
            for i in wo.required_items}


def _transfer(ctx, wo_name):
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    wo = frappe.get_doc("Work Order", wo_name)
    need = _gross_qty(wo, wo.qty)
    conv = ctx.is_conversion(wo.production_item)
    for code, q in need.items():
        short = q - _balance(ctx, code, wo.source_warehouse)
        if short > 0 and not conv:                   # own fibre: emergency purchase
            _quick_buy(ctx, code, short + 2000)
        elif short > 0:                              # customer fibre: ask the customer for the balance
            need[code] = max(0.0, q - short)
    se = frappe.get_doc(make_stock_entry(wo_name, "Material Transfer for Manufacture", wo.qty))
    for row in se.items:
        if row.item_code in need:
            row.qty = row.transfer_qty = round(need[row.item_code], 2)
    se.items = [r for r in se.items if flt(r.qty) > 0]
    for r in se.items:
        r.cost_center = ctx.wo_mill.get(wo_name) or r.cost_center
    _submit(ctx, se, ctx._current_day)
    ctx.at(add_days(ctx._current_day, ctx.rnd.randint(1, 3)), 2, _run_and_finish, wo_name)


def _quick_buy(ctx, code, qty):
    f = ctx.fibre[code]
    po = frappe.get_doc({"doctype": "Purchase Order", "supplier": ctx.rnd.choice(ctx.t.suppliers), "company": ctx.company,
                         "transaction_date": ctx._current_day, "schedule_date": ctx._current_day,
                         "items": [{"item_code": code, "qty": round(qty / 500 + 0.5) * 500, "rate": f["rate"],
                                    "schedule_date": ctx._current_day, "warehouse": ctx.wh["fibre"]}]})
    _insert_submit(ctx, po)
    _receive_and_bill(ctx, po.name)


def _issued_to_wip(wo_name):
    """Fibre actually transferred to WIP for this work order, per item (from its transfer stock entries)."""
    return {r.item_code: flt(r.qty) for r in frappe.db.sql("""select d.item_code, sum(d.transfer_qty) qty
        from `tabStock Entry Detail` d join `tabStock Entry` e on e.name = d.parent
        where e.work_order = %s and e.docstatus = 1 and e.purpose = 'Material Transfer for Manufacture'
        group by d.item_code""", wo_name, as_dict=True)}


def _run_and_finish(ctx, wo_name):
    """Complete every job card, post the Manufacture entry with waste, store spinning actuals, maybe downtime."""
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    rnd, t = ctx.rnd, ctx.t
    wo = frappe.get_doc("Work Order", wo_name)
    day = ctx._current_day
    start = get_datetime(f"{add_days(day, -1)} 06:00:00")

    y_target = flt(wo.get("target_yield")) or 95
    learn = min(1.0, ctx.months_in(day) / 12)          # the mill gets a little better through the year
    actual_yield = min(99.5, y_target + rnd.uniform(-1.8, 0.8) + 0.8 * learn)
    transferred = _issued_to_wip(wo_name)
    issued = sum(transferred.values())
    # Most lots reach plan or run slightly over (5% overproduction is allowed); some come in short.
    produced = min(round(wo.qty * rnd.uniform(0.95, 1.03)), int(issued * actual_yield / 100 * 0.995))
    pl = t.get("planning") or {}
    # The last few days of the run are still on the floor: some operations done, one in progress, the rest open.
    in_progress = getdate(day) > add_days(getdate(ctx.end), -cint(pl.get("open_tail_days") or 0))
    cards = frappe.get_all("Job Card", {"work_order": wo_name, "docstatus": 0}, pluck="name", order_by="sequence_id, creation")
    stop_at = rnd.randint(0, max(0, len(cards) - 1)) if in_progress else len(cards)
    q_lo, q_hi = pl.get("queue_hours") or [0.5, 10]
    for i, jc_name in enumerate(cards):
        jc = frappe.get_doc("Job Card", jc_name)
        std = flt(frappe.db.get_value("Work Order Operation", jc.operation_id, "time_in_mins")) or 60
        mins = max(15, std * rnd.uniform(0.95, 1.25))          # most steps run a little over standard
        # planned slot vs actual: the lot waits in the queue before each machine is free
        queue = rnd.uniform(q_lo, q_hi) if rnd.random() < 0.8 else rnd.uniform(q_hi, q_hi * 2.5)
        jc.expected_start_date = frappe.utils.add_to_date(start, hours=-queue)
        jc.expected_end_date = frappe.utils.add_to_date(jc.expected_start_date, minutes=std)
        end = frappe.utils.add_to_date(start, minutes=mins)
        operator = demo_modules.operator_for(ctx, jc.operation, day)
        jc.posting_date = getdate(start)              # auto-created job cards are otherwise dated today
        jc.flags.ignore_permissions = True
        if i > stop_at:                               # not started yet: only its planned slot (a save would fail
            frappe.db.set_value("Job Card", jc.name, {     # the operation-sequence check while earlier steps are open)
                "expected_start_date": jc.expected_start_date, "expected_end_date": jc.expected_end_date,
                "posting_date": jc.posting_date}, update_modified=False)
            continue
        if i == stop_at and in_progress:              # running now (or held for a machine / material)
            jc.set("time_logs", [{"from_time": start, "employee": operator, "completed_qty": 0}])
            jc.save()
            if rnd.random() < flt(pl.get("on_hold_share")):
                frappe.db.set_value("Job Card", jc.name, "status", "On Hold", update_modified=False)
            continue
        # A job card completes its own quantity; running over plan is booked on the Manufacture entry
        # (within Manufacturing Settings' overproduction allowance).
        jc.set("time_logs", [{"from_time": start, "to_time": end, "time_in_mins": mins, "employee": operator,
                              "completed_qty": min(produced, flt(jc.for_quantity))}])
        jc.save()
        jc.submit()
        ctx.bump("Job Card")
        if rnd.random() < flt(pl.get("rework_share")):
            end = _rework(ctx, jc, end, operator) or end
        start = end
    if in_progress:
        ctx.bump("Work order still running")
        return

    wo.reload()
    consumed = produced / (actual_yield / 100)
    waste = max(0.0, consumed - produced - consumed * 0.02)      # 2% invisible loss (moisture, fly)
    se = frappe.get_doc(make_stock_entry(wo_name, "Manufacture", produced))
    share = {k: v / sum(_gross_qty(wo, 1).values()) for k, v in _gross_qty(wo, 1).items()}
    scrap_rows = []
    for row in se.items:
        if row.item_code in share:                     # never consume more than was issued to WIP
            row.qty = row.transfer_qty = round(min(consumed * share[row.item_code], transferred.get(row.item_code) or 1e12), 2)
        elif row.get("is_scrap_item") or row.get("secondary_item_type") == "Scrap":
            scrap_rows.append(row)
    per = {w["code"]: w["per_100kg"] for w in t.wastes}
    for row in scrap_rows:
        row.qty = row.transfer_qty = round(waste * per.get(row.item_code, 1) / (sum(per.values()) or 1), 2)
    for row in se.items:
        row.cost_center = ctx.wo_mill.get(wo_name) or row.cost_center
    _submit(ctx, se, day)
    demo_modules.production_qi(ctx, wo, se)

    # Spinning actuals on the Work Order (read-only fields the mill records after the lot).
    target_ops = flt(wo.get("target_ops")) or 8
    actual_ops = round(target_ops * (rnd.uniform(0.88, 1.0) + 0.04 * learn), 2)
    month = getdate(day).month
    if rnd.random() < t.load_shedding.get(month, 0.2):
        stop = rnd.choice([60, 90, 120, 180, 240, 360])
        reasons = t.downtime_reasons
        reason = reasons[0] if month in t.load_shedding and rnd.random() < 0.7 else rnd.choice(reasons)
    else:
        stop, reason = 0, None
    actuals = {"actual_ops": actual_ops, "actual_yield": round(actual_yield, 2), "actual_waste": round(waste, 2),
               "actual_waste_percentage": round(waste / consumed * 100, 2),
               "spindle_worked": round(16 * produced / actual_ops, 2) if actual_ops else 0,
               "stopage_in_minutes": stop, "total_downtime": stop}
    actuals["actual_frame_required"] = actuals["spindle_worked"] / 480
    actuals["actual_per_shift_frame_required"] = actuals["actual_frame_required"] / 3
    frappe.db.set_value("Work Order", wo_name, {k: v for k, v in actuals.items() if wo.meta.has_field(k)},
                        update_modified=False)

    if stop:
        ring = next((o.workstation for o in wo.operations if o.operation == "Ring Spinning"), None)
        dt_from = get_datetime(f"{day} {rnd.randint(7, 20):02d}:00:00")
        de = frappe.get_doc({"doctype": "Downtime Entry", "workstation": ring, "from_time": dt_from,
                             "to_time": frappe.utils.add_to_date(dt_from, minutes=stop),
                             "stop_reason": reason, "remarks": DEMO_TAG,
                             # the ring-frame operator on shift (mandatory on some sites)
                             "operator": demo_modules.operator_for(ctx, "Ring Spinning", day)})
        if de.meta.has_field("work_order"):
            de.work_order = wo_name
        _fill(ctx, de)
        de.flags.ignore_permissions = True
        de.insert()
        ctx.bump("Downtime Entry")

    # A lot that came in short of plan is closed, as the mill would (otherwise it stays "In Process").
    if produced < flt(wo.qty):
        from erpnext.manufacturing.doctype.work_order.work_order import close_work_order
        close_work_order(wo_name, "Closed")
    # Closing (or completing) stamps the run date as the actual end; the lot really ended on the manufacture day.
    frappe.db.set_value("Work Order", wo_name, "actual_end_date", get_datetime(f"{day} 18:00:00"), update_modified=False)
    # The date the lot was due off the floor: mostly met, some early, a good share late (feeds WO schedule adherence).
    due = pl.get("wo_due") or {}
    if due:
        band = rnd.choices(["early", "on_the_day", "late"], weights=[due.get("early", 0), due.get("on_the_day", 0), due.get("late", 0)])[0]
        offset = rnd.randint(*due["early_days"]) if band == "early" else -rnd.randint(*due["late_days"]) if band == "late" else 0
        frappe.db.set_value("Work Order", wo_name, "expected_delivery_date", add_days(day, offset), update_modified=False)

    ctx.at(add_days(day, rnd.randint(0, 2)), 4, _deliver_and_invoice, wo.sales_order, produced)


def _rework(ctx, jc, start, operator):
    """A lot that failed its check at this operation goes round again on a corrective job card."""
    from erpnext.manufacturing.doctype.job_card.job_card import make_corrective_job_card
    op = (ctx.t.get("planning") or {}).get("rework_operation")
    if not op:
        return None
    if not frappe.db.exists("Operation", op):
        frappe.get_doc({"doctype": "Operation", "name": op, "is_corrective_operation": 1,
                        "workstation": jc.workstation}).insert(ignore_permissions=True, set_name=op)
    mins = ctx.rnd.uniform(30, 150)
    end = frappe.utils.add_to_date(start, minutes=mins)
    rw = make_corrective_job_card(jc.name, operation=op, for_operation=jc.operation)
    rw.workstation = jc.workstation
    rw.posting_date = getdate(start)
    rw.expected_start_date, rw.expected_end_date = start, end
    rw.set("time_logs", [{"from_time": start, "to_time": end, "time_in_mins": mins, "employee": operator,
                          "completed_qty": flt(jc.for_quantity)}])
    _fill(ctx, rw)
    rw.flags.ignore_permissions = True
    rw.insert()
    rw.submit()
    ctx.bump("Rework job card")
    return end


def _deliver_and_invoice(ctx, so_name, qty):
    """Deliver what the lot made (as far as stock allows); invoice it the same day, a few days later or — now and
    then — not at all yet (the template's collections.invoice_lag bands feed the delivered-not-invoiced analysis)."""
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    rnd = ctx.rnd
    dn = make_delivery_note(so_name)
    for i in dn.items:
        i.qty = min(flt(i.qty), qty, _balance(ctx, i.item_code, i.warehouse))
        i.cost_center = ctx.so_mill.get(so_name) or i.cost_center
    dn.items = [i for i in dn.items if flt(i.qty) > 0]
    if not dn.items:
        return
    _submit(ctx, dn, ctx._current_day)
    demo_modules.outgoing_qi(ctx, dn)
    if dn.get("export_order_flag") == "1" or frappe.db.get_value("Sales Order", so_name, "export_order_flag") == "1":
        demo_modules.export_shipment(ctx, so_name, dn, ctx._current_day)
    lag = _invoice_lag(ctx)
    # Month-end billing cut-off: what was delivered in a month is invoiced by its last day, so revenue lands in the
    # month that carries the cost of goods. Only the last weeks of the run keep deliveries unbilled or billed later.
    recent = getdate(ctx._current_day) > add_days(getdate(ctx.end), -45)
    if not recent:
        to_month_end = (get_last_day(ctx._current_day) - getdate(ctx._current_day)).days
        lag = min(to_month_end, ctx.rnd.randint(1, 30)) if lag is None else min(lag, to_month_end)
    if lag is None:                               # delivered, not invoiced (yet)
        _close_if_short(ctx, so_name)
        ctx.bump("Delivered not invoiced")
        return
    if lag == 0:
        _invoice(ctx, dn.name, so_name)
    else:
        ctx.at(add_days(ctx._current_day, lag), 4.5, _invoice, dn.name, so_name)


def _invoice_lag(ctx):
    bands = {k: flt(v) for k, v in ((ctx.t.get("collections") or {}).get("invoice_lag") or {"same_day": 1}).items()
             if not k.startswith("_")}
    band = ctx.rnd.choices(list(bands), weights=list(bands.values()))[0]
    if band == "never":
        return None
    if band == "same_day":
        return 0
    lo, hi = (int(x) for x in band.split("-"))
    return ctx.rnd.randint(lo, hi)


def _invoice(ctx, dn_name, so_name):
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice
    rnd = ctx.rnd
    si = make_sales_invoice(dn_name)
    si.due_date = add_days(ctx._current_day, 30)
    _submit(ctx, si, ctx._current_day)
    col = ctx.t.get("collections") or {}
    if rnd.random() < flt(col.get("pay_probability", 0.9)):
        ctx.at(add_days(ctx._current_day, rnd.randint(5, 40)), 5, _pay, "Sales Invoice", si.name)
    _close_if_short(ctx, so_name)


def _close_if_short(ctx, so_name):
    """The balance of a short lot is not coming: the order is closed (most of the time — the rest show as overdue).
    Done after invoicing, as ERPNext won't invoice against a closed order."""
    col = ctx.t.get("collections") or {}
    if frappe.db.get_value("Sales Order", so_name, "status") != "Closed" and \
            flt(frappe.db.get_value("Sales Order", so_name, "per_delivered")) < 99.99 and \
            ctx.rnd.random() < flt(col.get("close_short_orders", 0.85)):
        frappe.get_doc("Sales Order", so_name).update_status("Closed")


# ============================================================================ undo
def purge(company=None, confirm=0, template="spinning_mill"):
    """Cancel and delete ONLY what generate() made for `company` (documents on the template's items, its
    suppliers / customers, their payments, conversion contracts), newest first so stock and ledgers unwind
    cleanly. Masters stay.

        bench --site <site> execute micromax.demo_data.purge                      # dry run: counts only
        bench --site <site> execute micromax.demo_data.purge --kwargs "{'confirm': 1}"
    """
    frappe.set_user("Administrator")
    t = load_template(template)
    company = _company(t, company)
    conv = t.conversion
    items = tuple([f["code"] for f in t.fibres] + [y["code"] for y in t.yarns] + [w["code"] for w in t.wastes]
                  + [f["code"] for f in conv.get("fibres", [])] + [y["code"] for y in conv.get("yarns", [])])
    parties = tuple(t.suppliers + t.customers + list(conv.get("customers", []))
                    + [c["name"] for c in (t.get("export") or {}).get("customers", [])]
                    + [s_["name"] for s_ in (t.get("import") or {}).get("suppliers", [])]
                    + list(((t.get("company_setup") or {}).get("waste_sales") or {}).get("customers", [])))

    def by_item(dt, child, date_col="posting_date"):
        return frappe.db.sql_list(f"""select distinct p.name from `tab{dt}` p join `tab{child}` i on i.parent = p.name
            where p.company = %s and p.docstatus < 2 and i.item_code in %s order by p.{date_col} desc, p.creation desc""",
                                  (company, items))

    plan = demo_modules.purge_plan(company, items, DEMO_TAG) + [
        ("Journal Entry", frappe.db.sql_list("""select name from `tabJournal Entry` where company=%s and docstatus<2
            and user_remark like %s order by posting_date desc""", (company, f"%{DEMO_TAG}%"))),
        ("Payment Entry", frappe.db.sql_list("""select name from `tabPayment Entry` where company=%s and docstatus<2
            and party in %s order by posting_date desc, creation desc""", (company, parties))),
        ("Sales Invoice", by_item("Sales Invoice", "Sales Invoice Item")),
        ("Delivery Note", by_item("Delivery Note", "Delivery Note Item")),
        ("Downtime Entry", frappe.db.sql_list("""select d.name from `tabDowntime Entry` d join `tabWork Order` w on w.name = d.work_order
            where w.company = %s and d.remarks like %s""", (company, f"%{DEMO_TAG}%")) if frappe.get_meta("Downtime Entry").has_field("work_order") else []),
        ("Stock Entry", by_item("Stock Entry", "Stock Entry Detail")),
        ("Job Card", frappe.db.sql_list("""select name from `tabJob Card` where company=%s and docstatus<2
            and production_item in %s order by posting_date desc, creation desc""", (company, items))),
        ("Work Order", frappe.db.sql_list("""select name from `tabWork Order` where company=%s and docstatus<2
            and production_item in %s order by creation desc""", (company, items))),
        ("Production Plan", frappe.db.sql_list("""select distinct p.name from `tabProduction Plan` p
            join `tabProduction Plan Item` i on i.parent=p.name where p.company=%s and p.docstatus<2
            and i.item_code in %s order by p.creation desc""", (company, items))),
        ("Sales Order", by_item("Sales Order", "Sales Order Item", "transaction_date")),
        ("Purchase Invoice", by_item("Purchase Invoice", "Purchase Invoice Item")),
        ("Purchase Receipt", by_item("Purchase Receipt", "Purchase Receipt Item")),
        ("Purchase Order", by_item("Purchase Order", "Purchase Order Item", "transaction_date")),
        ("Material Request", by_item("Material Request", "Material Request Item", "transaction_date")),
    ] + demo_modules.purge_plan_after(company)
    total = sum(len(names) for _dt, names in plan)
    print(f"{company}: {total} generated documents: " + ", ".join(f"{dt} {len(n)}" for dt, n in plan))
    if not cint(confirm):
        print("Nothing changed. Re-run with confirm=1 to cancel and delete them.")
        return {dt: len(n) for dt, n in plan}
    # Short orders were closed after their last delivery; ERPNext won't cancel an invoice or delivery against a
    # closed order, so reopen them first (they are deleted further down anyway).
    for so in next(n for dt, n in plan if dt == "Sales Order"):
        if frappe.db.get_value("Sales Order", so, "status") == "Closed":
            frappe.get_doc("Sales Order", so).update_status("Draft")
    frappe.db.commit()
    with _inline_background_jobs():
        return _purge_docs(plan)


def _cancel_and_delete(dt, name, orphan=False):
    """Cancel (reversing its stock / GL entries) and delete. `orphan`: the order / request / work order it would
    update on cancel no longer exists, so skip those back-updates; documents without ledgers are just removed."""
    if not frappe.db.exists(dt, name):             # already removed with an earlier document
        return
    if dt in demo_modules.RAW_DELETE:              # no ledgers: remove the rows directly
        demo_modules.raw_delete(dt, name)
        frappe.db.commit()
        return
    doc = frappe.get_doc(dt, name)
    if doc.docstatus == 1:
        if orphan and dt in ("Job Card", "Material Request", "Production Plan", "Work Order", "Sales Order",
                             "Purchase Order", "Downtime Entry", "Contract"):
            frappe.db.set_value(dt, name, "docstatus", 2, update_modified=False)   # no ledger to reverse
        else:
            doc.flags.ignore_links = True
            if orphan:
                doc.status_updater = []
                for method in ("update_prevdoc_status", "update_billing_status_in_pr", "update_billing_status_for_zero_amount_refdoc",
                               "update_ordered_and_reserved_qty", "update_requested_qty", "update_received_qty_if_from_pp",
                               "update_work_order", "update_status_in_purchase_order", "update_reserved_qty",
                               "update_so_in_serial_number", "update_work_order_status", "update_transferred_qty"):
                    if hasattr(doc, method):
                        setattr(doc, method, lambda *a, **k: None)
            doc.cancel()
    frappe.delete_doc(dt, name, force=1, ignore_permissions=True, ignore_on_trash=True)
    frappe.db.commit()


def _purge_docs(plan):
    done, failed = 0, 0
    for dt, names in plan:
        for name in names:
            try:
                _cancel_and_delete(dt, name)
                done += 1
            except Exception:
                frappe.db.rollback()
                try:                                   # an earlier, interrupted purge removed its source docs
                    _cancel_and_delete(dt, name, orphan=True)
                    done += 1
                except Exception as e:
                    frappe.db.rollback()
                    failed += 1
                    print(f"  ! {dt} {name}: {str(e)[:160]}")
        print(f"  {dt}: done")
    print(f"Purged {done}, failed {failed}")
    return {"purged": done, "failed": failed}


def restore_downtime(company=None, template="spinning_mill"):
    """Rebuild Downtime Entries from the stoppage minutes stored on the generated Work Orders (repair helper)."""
    frappe.set_user("Administrator")
    t = load_template(template)
    company = _company(t, company)
    rnd = random.Random(11)
    made = 0
    wos = frappe.db.sql("""select w.name, w.stopage_in_minutes stop, date(w.actual_end_date) d from `tabWork Order` w
        where w.company=%s and w.docstatus=1 and w.production_item in %s and ifnull(w.stopage_in_minutes,0) > 0
        and not exists (select 1 from `tabDowntime Entry` x where x.work_order = w.name)""",
                        (company, tuple([y["code"] for y in t.yarns] + [y["code"] for y in t.conversion.get("yarns", [])])), as_dict=True)
    for w in wos:
        ring = frappe.db.get_value("Work Order Operation", {"parent": w.name, "operation": "Ring Spinning"}, "workstation")
        month = getdate(w.d).month
        reason = t.downtime_reasons[0] if month in t.load_shedding and rnd.random() < 0.7 else rnd.choice(t.downtime_reasons)
        start = get_datetime(f"{w.d} {rnd.randint(7, 20):02d}:00:00")
        operator = frappe.db.get_value("Employee", {"company": company, "status": "Active"}, "name")
        frappe.get_doc({"doctype": "Downtime Entry", "workstation": ring, "work_order": w.name, "from_time": start,
                        "to_time": frappe.utils.add_to_date(start, minutes=w.stop), "stop_reason": reason,
                        "operator": operator, "remarks": DEMO_TAG}).insert(ignore_permissions=True)
        made += 1
    frappe.db.commit()
    print(f"Downtime entries restored: {made}")
    return made
