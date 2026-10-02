"""Production cost simulator: the standard cost structure of a Production Plan, for the driver-tree simulator page
(/production/cost-simulator). The page recomputes everything client-side as drivers change; this module only
supplies the baseline:

- raw materials   BOM consumption scaled to the planned quantity, at current valuation rates
- operations      BOM routing hours per workstation x the workstation's cost components (wages = operators,
                  electricity = power, consumables, ...) or its hour rate when it has no breakdown
- waste credit    BOM secondary (scrap) items at their selling / valuation rate
- overheads       factory overhead the machine rates don't absorb and admin & selling overhead, per unit of
                  output, from the ledger of the 3 months before the plan (treated as fixed cost by the page)
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate

CATEGORY_RULES = (
    ("operators", ("wage", "labour", "labor", "operator", "salary", "staff")),
    ("power", ("electric", "power", "gas", "fuel", "energy", "diesel")),
    ("consumables", ("consumable", "spare", "repair", "maint", "store", "lubric")),
)


def _category(component):
    low = (component or "").lower()
    for cat, words in CATEGORY_RULES:
        if any(w in low for w in words):
            return cat
    return "machine"


def _valuation_rate(item_code, company, fallback=0.0):
    r = frappe.db.sql("""select sum(b.stock_value) / nullif(sum(b.actual_qty), 0) from `tabBin` b
        join `tabWarehouse` w on w.name = b.warehouse where b.item_code = %s and w.company = %s and b.actual_qty > 0""",
                      (item_code, company))
    rate = flt(r[0][0]) if r else 0
    return rate or flt(frappe.db.get_value("Item", item_code, "valuation_rate")) or flt(fallback)


def _is_conversion(warehouse):
    """Conversion (job work for a customer): the finished yarn goes to a third-party warehouse."""
    return "third party" in (warehouse or "").lower()


STREAM_SQL = "lower(ifnull({0}.warehouse, '')) like '%%third party%%'"


def _selling_rate(item_code, company, fallback=0.0):
    rate = frappe.db.get_value("Item Price", {"item_code": item_code, "selling": 1}, "price_list_rate", order_by="modified desc")
    return flt(rate) or _valuation_rate(item_code, company, fallback)


@frappe.whitelist()
def get_plan_baseline(production_plan: str, stream: str | None = None, item: str | None = None) -> dict:
    """`stream` ("Conversion" / "Own production") and `item` cost only the matching lines of the plan."""
    pp = frappe.get_doc("Production Plan", production_plan)
    pp.check_permission("read")
    company = pp.company
    currency = frappe.get_cached_value("Company", company, "default_currency")
    raw, ops, waste, products = {}, {}, {}, []
    output = yield_w = 0.0
    uoms = {}
    for row in pp.po_items:
        if stream and (_is_conversion(row.warehouse) != (stream == "Conversion")):
            continue
        if item and row.item_code != item:
            continue
        qty = flt(row.planned_qty)
        bom_no = row.bom_no or frappe.db.get_value("Item", row.item_code, "default_bom")
        if not qty or not bom_no:
            continue
        bom = frappe.get_doc("BOM", bom_no)
        scale = qty / (flt(bom.quantity) or 1)
        output += qty
        uoms[row.stock_uom or bom.uom] = uoms.get(row.stock_uom or bom.uom, 0) + qty
        y = flt(bom.get("target_yield")) or (100 - flt(bom.get("process_loss_percentage"))) or 100
        yield_w += y * qty
        routing = []
        products.append({"item": row.item_code, "item_name": row.item_name, "qty": qty, "bom": bom_no, "uom": row.stock_uom, "yield": y,
                         "stream": "Conversion" if _is_conversion(row.warehouse) else "Own production", "routing": routing})
        for it in bom.items:
            r = raw.setdefault(it.item_code, {"code": it.item_code, "name": it.item_name, "uom": it.stock_uom, "qty": 0.0,
                                              "rate": _valuation_rate(it.item_code, company, it.rate)})
            r["qty"] += flt(it.stock_qty or it.qty) * scale
        for op in bom.operations:
            # BOM operation time covers the BOM quantity (an explicit batch size > 1 overrides it), as ERPNext costs it
            per = flt(op.batch_size) if flt(op.batch_size) > 1 else (flt(bom.quantity) or 1)
            hours = flt(op.time_in_mins) / 60 * qty / per
            # the product's routing, in sequence, for the operations Gantt
            routing.append({"operation": op.operation, "workstation": op.workstation, "sequence": op.sequence_id or op.idx,
                            "hours": hours, "hour_rate": flt(op.hour_rate)})
            costs = frappe.get_all("Workstation Cost", {"parent": op.workstation, "parenttype": "Workstation"},
                                   ["operating_component", "operating_cost"]) if op.workstation else []
            if not costs:
                costs = [frappe._dict(operating_component=_("Machine hour rate"), operating_cost=flt(op.hour_rate))]
            for c in costs:
                if not flt(c.operating_cost):
                    continue
                key = c.operating_component
                o = ops.setdefault(key, {"component": key, "category": _category(key), "hours": 0.0, "amount": 0.0, "operations": set()})
                o["hours"] += hours
                o["amount"] += hours * flt(c.operating_cost)
                o["operations"].add(op.operation)
        for s in bom.get("secondary_items") or []:
            w = waste.setdefault(s.item_code, {"code": s.item_code, "name": s.item_name, "uom": s.stock_uom, "qty": 0.0,
                                               "rate": _selling_rate(s.item_code, company, flt(s.get("cost")) / (flt(s.stock_qty) or 1))})
            w["qty"] += flt(s.stock_qty or s.qty) * scale
    if not output:
        # nothing matches the stream / item filter (or no BOMs): an empty result the page explains, not an error
        lines = [{"item": r.item_code, "item_name": r.item_name,
                  "stream": "Conversion" if _is_conversion(r.warehouse) else "Own production"} for r in pp.po_items]
        return {"plan": pp.name, "company": company, "currency": currency, "posting_date": str(pp.posting_date),
                "stream": stream or "", "item": item or "", "empty": True, "lines": lines,
                "message": _("This plan has no {0}lines{1} with a BOM to cost.").format(
                    f"{stream.lower()} " if stream else "", f" for {item}" if item else "")}
    for o in ops.values():
        o["rate"] = o["amount"] / o["hours"] if o["hours"] else 0
        o["operations"] = sorted(o["operations"])
        o.pop("amount")
    return {
        "plan": pp.name, "company": company, "currency": currency, "posting_date": str(pp.posting_date), "stream": stream or "", "item": item or "",
        "uom": max(uoms, key=uoms.get) if uoms else "",
        "output": output, "yield": round(yield_w / output, 2) if output else 100,
        "products": products,
        "raw_materials": sorted(raw.values(), key=lambda r: -r["qty"] * r["rate"]),
        "operations": sorted(ops.values(), key=lambda o: -o["hours"] * o["rate"]),
        "waste": sorted(waste.values(), key=lambda w: -w["qty"] * w["rate"]),
        "overheads": _overheads(company, getdate(pp.posting_date)),
    }


def _group_accounts(company, *names):
    """Leaf accounts under the first expense group whose name (without number) matches one of `names`."""
    for n in names:
        g = frappe.db.get_value("Account", {"company": company, "is_group": 1, "account_name": n}, ["lft", "rgt"], as_dict=True)
        if g:
            return frappe.db.sql_list("""select name from `tabAccount` where company = %s and is_group = 0
                and lft > %s and rgt < %s""", (company, g.lft, g.rgt))
    return []


def _overheads(company, day):
    """Per-unit overheads from the 3 months before `day`: factory overhead the machine rates did not absorb (direct
    expenses outside stock, less what manufacture entries capitalised), and admin & selling overhead."""
    start, end = add_months(day, -3), day
    direct = set(_group_accounts(company, "Direct Expenses", "Manufacturing Overheads")) - set(_group_accounts(company, "Stock Expenses"))
    indirect = _group_accounts(company, "Indirect Expenses")
    absorbed_acc = frappe.db.get_value("Company", company, "default_operating_cost_account")

    def gl(accounts):
        if not accounts:
            return 0.0
        return flt(frappe.db.sql("""select sum(debit - credit) from `tabGL Entry` where company = %s and is_cancelled = 0
            and account in %s and posting_date between %s and %s""", (company, tuple(accounts), start, end))[0][0])

    factory, admin = gl(direct), gl(indirect)
    absorbed = -gl([absorbed_acc]) if absorbed_acc else 0
    produced = flt(frappe.db.sql("""select sum(sle.actual_qty) from `tabStock Ledger Entry` sle
        join `tabStock Entry` se on se.name = sle.voucher_no where sle.company = %s and sle.is_cancelled = 0
        and sle.voucher_type = 'Stock Entry' and se.purpose = 'Manufacture' and sle.actual_qty > 0
        and ifnull(sle.is_scrap_item, 0) = 0 and sle.posting_date between %s and %s""", (company, start, end))[0][0]) \
        if frappe.db.has_column("Stock Ledger Entry", "is_scrap_item") else 0
    if not produced:
        produced = flt(frappe.db.sql("""select sum(produced_qty) from `tabWork Order` where company = %s and docstatus = 1
            and date(actual_end_date) between %s and %s""", (company, start, end))[0][0])
    unabsorbed = max(0.0, factory - absorbed)
    return {
        "from": str(start), "to": str(end), "basis_qty": produced,
        "factory_per_unit": round(unabsorbed / produced, 4) if produced else 0,
        "admin_per_unit": round(admin / produced, 4) if produced else 0,
        "factory_total": round(unabsorbed, 2), "admin_total": round(admin, 2),
    }


@frappe.whitelist()
def list_plans(company: str | None = None, txt: str | None = None, limit: int = 30, stream: str | None = None,
               from_date: str | None = None, to_date: str | None = None, item: str | None = None) -> list:
    """Submitted Production Plans for the simulator's picker, newest first, matched on plan number or product, optionally
    only plans with conversion / own-production lines and posted within a date range."""
    frappe.has_permission("Production Plan", "read", throw=True)
    cond, params = ["p.docstatus = 1"], {"txt": f"%{(txt or '').strip()}%", "limit": min(int(limit or 30), 100)}
    if company:
        cond.append("p.company = %(company)s")
        params["company"] = company
    if from_date:
        cond.append("p.posting_date >= %(from_date)s")
        params["from_date"] = from_date
    if to_date:
        cond.append("p.posting_date <= %(to_date)s")
        params["to_date"] = to_date
    if item:
        cond.append("exists (select 1 from `tabProduction Plan Item` it where it.parent = p.name and it.item_code = %(item)s)")
        params["item"] = item
    if stream in ("Conversion", "Own production"):
        match = STREAM_SQL.format("x") if stream == "Conversion" else f"not ({STREAM_SQL.format('x')})"
        cond.append(f"exists (select 1 from `tabProduction Plan Item` x where x.parent = p.name and {match})")
    if (txt or "").strip():
        cond.append("(p.name like %(txt)s or exists (select 1 from `tabProduction Plan Item` s where s.parent = p.name "
                    "and (s.item_code like %(txt)s or s.item_name like %(txt)s)))")
    return frappe.db.sql(f"""select p.name, p.posting_date, p.status, p.company,
            sum(i.planned_qty) qty, count(i.name) line_count, max(i.stock_uom) uom,
            sum(case when {STREAM_SQL.format("i")} then 1 else 0 end) conversion_lines,
            substring_index(group_concat(distinct ifnull(i.item_name, i.item_code) order by i.idx separator ', '), ', ', 3) products
        from `tabProduction Plan` p left join `tabProduction Plan Item` i on i.parent = p.name
        where {' and '.join(cond)} group by p.name order by p.posting_date desc, p.creation desc limit %(limit)s""",
                         params, as_dict=True)
