"""Demo point of sale: a factory outlet selling yarn cones and waste over the counter.

    bench --site <site> execute micromax.demo_pos.setup --kwargs "{'company': '...'}"
    bench --site <site> execute micromax.demo_pos.sell --kwargs "{'company': '...', 'sales': 12}"   # one shift of sales

setup: Bank mode of payment account, a "Factory Outlet" warehouse stocked from the yarn / waste stores, the POS
Profile (credit sales and a small write-off allowed), and POS-Awesome-style offers: a bulk-yarn discount, a coupon
(OUTLET10) and an "Outlet Rewards" loyalty program. sell: opens a shift, rings up sales (cash / card, some with discounts, one held, one returned) through
mm_core.pos — exactly what the React POS terminal does — and closes the shift.
"""

import json
import random

import frappe
from frappe.utils import flt

PROFILE = "{abbr} Factory Outlet"


def _leaf(doctype, *preferred):
    """A non-group record of a tree doctype (Customer Group / Territory): the first preferred one that is a leaf, else any leaf;
    creates one under the root if the tree has none."""
    for name in preferred:
        if name and frappe.db.get_value(doctype, name, "is_group") == 0:
            return name
    leaf = frappe.db.get_value(doctype, {"is_group": 0}, "name", order_by="lft")
    if leaf:
        return leaf
    field = "customer_group_name" if doctype == "Customer Group" else "territory_name"
    parent_field = "parent_customer_group" if doctype == "Customer Group" else "parent_territory"
    root = frappe.db.get_value(doctype, {"is_group": 1, parent_field: ["in", ["", None]]}, "name")
    new = "Individual" if doctype == "Customer Group" else "Pakistan"
    frappe.get_doc({"doctype": doctype, field: new, parent_field: root, "is_group": 0}).insert(ignore_permissions=True)
    return new


def _cost_center(company):
    return (frappe.db.get_value("Company", company, "cost_center")
            or frappe.db.get_value("Cost Center", {"company": company, "is_group": 0, "disabled": 0}, "name", order_by="lft"))


def _company_account(company, kind):
    """The company's default Cash / Bank account, else its first ledger of that account type."""
    field = "default_cash_account" if kind == "Cash" else "default_bank_account"
    return (frappe.db.get_value("Company", company, field)
            or frappe.db.get_value("Account", {"company": company, "account_type": kind, "is_group": 0, "disabled": 0}, "name"))


def _ensure_mode(name, kind, company):
    """Mode of Payment `name` (type Cash / Bank) exists and points at a company account. Returns True when usable."""
    account = _company_account(company, "Cash" if kind == "Cash" else "Bank")
    if not frappe.db.exists("Mode of Payment", name):
        frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": name, "type": kind, "enabled": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("Mode of Payment Account", {"parent": name, "company": company}):
        if not account:
            return False
        mop = frappe.get_doc("Mode of Payment", name)
        mop.append("accounts", {"company": company, "default_account": account})
        mop.save(ignore_permissions=True)
    return True


def setup(company):
    frappe.set_user("Administrator")
    abbr = frappe.db.get_value("Company", company, "abbr")
    usable = {m: _ensure_mode(m, k, company) for m, k in (("Cash", "Cash"), ("Bank", "Bank"), ("Credit Card", "Bank"))}
    if not usable["Cash"]:
        frappe.throw(f"{company} has no Cash account — set Company → Default Cash Account first.")
    if not frappe.db.exists("Customer", "Walk-in Customer"):
        frappe.get_doc({"doctype": "Customer", "customer_name": "Walk-in Customer", "customer_type": "Individual",
                        "customer_group": _leaf("Customer Group", frappe.db.get_single_value("Selling Settings", "customer_group"), "Individual", "Commercial"),
                        "territory": _leaf("Territory", frappe.db.get_single_value("Selling Settings", "territory"), "Pakistan", "Rest Of The World")}
                       ).insert(ignore_permissions=True)
    wh = f"Factory Outlet - {abbr}"
    if not frappe.db.exists("Warehouse", wh):
        parent = frappe.db.get_value("Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["in", ["", None]]}, "name")
        frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "Factory Outlet", "company": company,
                        "parent_warehouse": parent}).insert(ignore_permissions=True)
    _stock_outlet(company, abbr, wh)
    name = PROFILE.format(abbr=abbr)
    if not frappe.db.exists("POS Profile", name):
        cash = frappe.db.get_value("Mode of Payment Account", {"parent": "Cash", "company": company}, "default_account")
        tenders = [{"mode_of_payment": "Cash", "default": 1}] + [{"mode_of_payment": m, "default": 0} for m in ("Bank",) if usable[m]]
        cc = _cost_center(company)
        p = frappe.get_doc({
            "doctype": "POS Profile", "__newname": name, "company": company, "warehouse": wh, "currency": "PKR",
            "selling_price_list": "Standard Selling", "customer": "Walk-in Customer", "update_stock": 1,
            "write_off_account": frappe.db.get_value("Company", company, "write_off_account") or frappe.db.get_value("Company", company, "round_off_account"),
            "write_off_cost_center": cc,
            "cost_center": cc, "account_for_change_amount": cash, "allow_discount_change": 1, "allow_rate_change": 1,
            "payments": tenders,
            "item_groups": [{"item_group": g} for g in ("Spun Yarn", "Spinning Waste") if frappe.db.exists("Item Group", g)],
            "applicable_for_users": [{"user": u, "default": 1} for u in ("Administrator", "sales@micromaxonline.uk")
                                     if frappe.db.exists("User", u)],
        })
        p.insert(ignore_permissions=True, set_name=name)
    frappe.db.set_value("POS Profile", name, {"allow_partial_payment": 1, "write_off_limit": 10})
    _tax_and_tenders(company, name)
    _offers(company, abbr)
    frappe.db.commit()
    print(f"POS profile {name} on {wh}")
    return name


def _sales_tax_template(company, abbr):
    """The company's 18% sales tax template (On Net Total), created on the output sales tax account if missing."""
    for t in frappe.get_all("Sales Taxes and Charges Template", {"company": company, "disabled": 0}, pluck="name", order_by="is_default desc, name"):
        rows = frappe.get_all("Sales Taxes and Charges", {"parent": t, "parenttype": "Sales Taxes and Charges Template"}, ["charge_type", "rate"])
        if any(r.charge_type == "On Net Total" and abs(r.rate - 18) < 0.01 for r in rows):
            return t
    account = (frappe.db.get_value("Account", {"company": company, "is_group": 0, "account_type": "Tax", "account_name": ["like", "%Output%"]}, "name")
               or frappe.db.get_value("Account", {"company": company, "is_group": 0, "account_type": "Tax"}, "name"))
    if not account:
        return None
    return frappe.get_doc({"doctype": "Sales Taxes and Charges Template", "title": "Sales Tax 18%", "company": company,
                           "taxes": [{"charge_type": "On Net Total", "account_head": account, "rate": 18,
                                      "description": "Sales tax 18%"}]}).insert(ignore_permissions=True).name


def _tax_and_tenders(company, profile):
    """18% sales tax on the counter's sales, and Cash / Bank / Credit Card at the till."""
    abbr = frappe.db.get_value("Company", company, "abbr")
    doc = frappe.get_doc("POS Profile", profile)
    tax = _sales_tax_template(company, abbr)
    if tax and not doc.taxes_and_charges:
        doc.taxes_and_charges = tax
    _ensure_mode("Credit Card", "Bank", company)
    have = {p.mode_of_payment for p in doc.payments}
    if "Credit Card" not in have and frappe.db.exists("Mode of Payment Account", {"parent": "Credit Card", "company": company}):
        doc.append("payments", {"mode_of_payment": "Credit Card", "default": 0})
    doc.save(ignore_permissions=True)


def _offers(company, abbr):
    """Bulk-yarn discount (automatic), a coupon-based 10% off, and a loyalty program the outlet's customers join."""
    today = frappe.utils.nowdate()
    if not frappe.db.exists("Item Group", "Spun Yarn"):
        print("No 'Spun Yarn' item group — skipping the demo offers and coupon")
    elif not frappe.db.exists("Pricing Rule", {"title": "Outlet — 5% off 50 kg+ yarn"}):
        frappe.get_doc({"doctype": "Pricing Rule", "title": "Outlet — 5% off 50 kg+ yarn", "apply_on": "Item Group",
                        "item_groups": [{"item_group": "Spun Yarn"}], "selling": 1, "company": company, "min_qty": 50,
                        "price_or_product_discount": "Price", "rate_or_discount": "Discount Percentage", "discount_percentage": 5,
                        "valid_from": today, "priority": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("Pricing Rule", {"title": "Outlet coupon — 10% off"}):
        # bill-level (Transaction) so it never competes with the item rules on priority
        rule = frappe.get_doc({"doctype": "Pricing Rule", "title": "Outlet coupon — 10% off", "apply_on": "Transaction", "selling": 1,
                               "company": company, "coupon_code_based": 1, "price_or_product_discount": "Price",
                               "rate_or_discount": "Discount Percentage", "discount_percentage": 10, "valid_from": today,
                               "priority": 2}).insert(ignore_permissions=True)
        if not frappe.db.exists("Coupon Code", {"coupon_code": "OUTLET10"}):
            frappe.get_doc({"doctype": "Coupon Code", "coupon_name": "Outlet 10", "coupon_code": "OUTLET10", "coupon_type": "Promotional",
                            "pricing_rule": rule.name, "valid_from": today, "valid_upto": frappe.utils.add_days(today, 90),
                            "maximum_use": 500}).insert(ignore_permissions=True)
    for name in frappe.get_all("Pricing Rule", {"title": "Outlet coupon — 10% off", "apply_on": ["!=", "Transaction"]}, pluck="name"):
        rule = frappe.get_doc("Pricing Rule", name)          # first version was item-group based
        rule.apply_on = "Transaction"
        rule.set("item_groups", [])
        rule.save(ignore_permissions=True)
    lp = "Outlet Rewards"
    if not frappe.db.exists("Loyalty Program", lp):
        expense = (frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Expense",
                                                   "account_name": ["like", "%Marketing%"]}, "name")
                   or frappe.db.get_value("Account", {"company": company, "is_group": 0, "root_type": "Expense",
                                                      "account_type": ["in", ["", None]]}, "name"))
        if not expense or not _cost_center(company):
            print("No expense account / cost center — skipping the loyalty program")
            return
        frappe.get_doc({"doctype": "Loyalty Program", "loyalty_program_name": lp, "loyalty_program_type": "Single Tier Program",
                        "company": company, "from_date": today, "auto_opt_in": 1, "conversion_factor": 1, "expiry_duration": 365,
                        "expense_account": expense, "cost_center": _cost_center(company),
                        "collection_rules": [{"tier_name": "Outlet", "min_spent": 0, "collection_factor": 100}]}
                       ).insert(ignore_permissions=True)
    # the outlet's regulars (not the walk-in customer) collect points
    for c in frappe.get_all("Customer", {"disabled": 0, "name": ["!=", "Walk-in Customer"], "loyalty_program": ["in", ["", None]]},
                            pluck="name", limit=5):
        frappe.db.set_value("Customer", c, "loyalty_program", lp)


def _stock_outlet(company, abbr, wh):
    """Move some yarn and waste into the outlet (once)."""
    if frappe.db.sql("select sum(actual_qty) from tabBin where warehouse = %s", wh)[0][0]:
        return
    rows = []
    for src in (f"Yarn Store - {abbr}", f"Waste Store - {abbr}"):
        for item, qty in frappe.db.sql("select item_code, actual_qty from tabBin where warehouse = %s and actual_qty > 20", src):
            rows.append({"item_code": item, "qty": min(flt(qty) * 0.25, 400), "s_warehouse": src, "t_warehouse": wh})
    if not rows:
        return
    se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Transfer", "purpose": "Material Transfer",
                         "company": company, "items": rows, "remarks": "Stock for the factory outlet (POS)"})
    se.insert(ignore_permissions=True)
    se.submit()


def sell(company, sales=12, seed=None):
    """One shift: open, ring up sales (some discounted, card, over-tendered cash, one held, one return), close."""
    from mm_core import pos

    frappe.set_user("Administrator")
    abbr = frappe.db.get_value("Company", company, "abbr")
    profile = PROFILE.format(abbr=abbr)
    rnd = random.Random(seed or frappe.utils.now())
    if not pos._open_shift():
        pos.open_shift(profile, json.dumps([{"mode_of_payment": "Cash", "opening_amount": 5000}, {"mode_of_payment": "Bank", "opening_amount": 0}]))
    from erpnext.selling.page.point_of_sale.point_of_sale import get_items

    items = [i for i in get_items(0, 50, "Standard Selling", "", profile) .get("items", []) if flt(i.get("actual_qty")) > 0 and flt(i.get("price_list_rate"))]
    customers = ["Walk-in Customer"] + frappe.get_all("Customer", {"disabled": 0}, pluck="name", limit=5)
    done = []
    for n in range(int(sales)):
        cart = []
        for it in rnd.sample(items, k=min(len(items), rnd.randint(1, 3))):
            disc = rnd.choice([None, None, None, 5, 10])        # None: leave ERPNext's pricing rules (bulk discount) in charge
            qty = min(rnd.choice([1, 2, 5, 10, 20, 60]), max(1, int(flt(it["actual_qty"]) / 6)))   # never more than the shelf holds
            cart.append({"item_code": it["item_code"], "qty": qty, "uom": it.get("uom"),
                         **({"discount_percentage": disc} if disc else {})})
        data = {"pos_profile": profile, "customer": rnd.choice(customers), "items": cart}
        if n == 1:
            data["coupon_code"] = frappe.db.get_value("Coupon Code", {"coupon_code": "OUTLET10"}, "name")
        total = flt(pos.preview(json.dumps(data))["rounded_total"])
        if n == 3:
            done.append(("held", pos.submit_invoice(json.dumps({**data, "hold": 1}))["name"]))
            continue
        regular = [c for c in customers if c != "Walk-in Customer"]
        if n == 5 and regular:                                   # a regular buys on credit, pays a third now
            data.update({"customer": regular[0], "credit": 1, "due_date": frappe.utils.add_days(frappe.utils.nowdate(), 15)})
            data["payments"] = [{"mode_of_payment": "Cash", "amount": round(total / 3)}]
        elif rnd.random() < 0.3:
            data["payments"] = [{"mode_of_payment": "Bank", "amount": total}]
        else:
            data["payments"] = [{"mode_of_payment": "Cash", "amount": round(total / 500 + 0.5) * 500}]   # customer hands notes
        r = pos.submit_invoice(json.dumps(data))
        done.append(("sale", r["name"], r["grand_total"], r.get("change_amount")))
    first_sale = next(d[1] for d in done if d[0] == "sale")
    done.append(("return", pos.return_invoice(first_sale)["name"]))
    summary = pos.shift_summary()
    closing = pos.close_shift(json.dumps({m["mode_of_payment"]: m["expected"] for m in summary["modes"]}))
    frappe.db.commit()
    for d in done:
        print(*d)
    print("SUMMARY", {k: summary[k] for k in ("invoices", "returns", "total")}, summary["modes"])
    print("CLOSING", closing)
    return done
