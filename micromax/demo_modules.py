"""Import, export, HR, payroll, quality and fixed-asset data for micromax.demo_data.

Same template (demo_templates/<name>.json, sections "import", "export", "hr", "payroll", "quality", "assets"),
same day-by-day event loop and posting clock as demo_data. Every entry point is a no-op when its template
section is missing, so a template can switch a module off by leaving it out.
"""

import random

import frappe
from frappe.utils import add_days, add_months, cint, flt, get_datetime, get_last_day, getdate


def _dd():
    from micromax import demo_data
    return demo_data


def _acc(ctx, name, parent_hints, root_type, account_type=None, is_group=0):
    """Find or create a leaf account `name` under the first group matching one of `parent_hints`."""
    c = ctx.company
    existing = frappe.db.get_value("Account", {"company": c, "account_name": name, "is_group": is_group}, "name")
    if existing:
        return existing
    parent = None
    for pattern in ("{}", "{}%", "%{}%"):        # exact group name first: "%Direct Expense%" also matches "Indirect Expenses"
        for h in parent_hints:
            parent = frappe.db.get_value("Account", {"company": c, "is_group": 1, "root_type": root_type,
                                                     "account_name": ["like", pattern.format(h)]}, "name")
            if parent:
                break
        if parent:
            break
    parent = parent or frappe.db.get_value("Account", {"company": c, "is_group": 1, "root_type": root_type,
                                                        "parent_account": ["in", ["", None]]}, "name")
    return frappe.get_doc({"doctype": "Account", "account_name": name, "parent_account": parent, "company": c,
                           "is_group": is_group, **({"account_type": account_type} if account_type else {})
                           }).insert(ignore_permissions=True).name


def _ensure(doctype, name, data):
    return _dd()._ensure(doctype, name, data)


def _setup():
    from micromax import demo_setup
    return demo_setup


# ============================================================================ masters
def masters(ctx):
    t = ctx.t
    if t.get("export"):
        _export_masters(ctx)
    if t.get("import"):
        _import_masters(ctx)
    if t.get("quality"):
        _quality_masters(ctx)
    if t.get("hr"):
        _hr_masters(ctx)
    if t.get("payroll") and t.get("hr"):
        _payroll_masters(ctx)
    if t.get("assets"):
        _asset_masters(ctx)


# ============================================================================ export
def _export_masters(ctx):
    ex = ctx.t.export
    parent_cg = frappe.db.get_value("Customer Group", {"is_group": 1}, "name")
    _ensure("Customer Group", ex["customer_group"], {"customer_group_name": ex["customer_group"], "parent_customer_group": parent_cg})
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or frappe.db.get_value("Territory", {}, "name")
    for cu in ex["customers"]:
        _ensure("Customer", cu["name"], {"customer_name": cu["name"], "customer_group": ex["customer_group"],
                                         "customer_type": "Company", "territory": territory})
    ctx.export_customers = {cu["name"]: cu for cu in ex["customers"]}


def usd_rate(ctx, day):
    ex = ctx.t.export
    return round(flt(ex.get("usd_rate", 280)) * (1 + flt(ex.get("usd_drift_per_month")) * ctx.months_in(day))
                 * ctx.rnd.uniform(0.995, 1.005), 2)


def export_order_fields(ctx, day, due):
    """Header fields for an export Sales Order (customer picked by weight)."""
    ex, rnd = ctx.t.export, ctx.rnd
    cu = rnd.choices(ex["customers"], weights=[c.get("weight", 1) for c in ex["customers"]])[0]
    fields = {"customer": cu["name"], "export_order_flag": "1", "country_of_destination": cu["country"],
              "incoterm": rnd.choice(ex["incoterms"]), "port_of_loading": ex["port_of_loading"],
              "port_of_discharge": cu["port"], "final_destination": cu["port"], "shipment_mode": "Sea",
              "latest_shipment_date": add_days(due, 10), "lc_expiry_date": add_days(due, 30),
              "export_status": "Planned", "buyer_po_no": f"PO-{rnd.randint(10000, 99999)}"}
    meta = frappe.get_meta("Sales Order")
    return cu, {k: v for k, v in fields.items() if k == "customer" or meta.has_field(k)}


def lc_for_order(ctx, so, cu, day):
    """LC Proforma for an export order, walked through its approval workflow to Confirmed."""
    ex, rnd = ctx.t.export, ctx.rnd
    if not frappe.db.exists("DocType", "LC Proforma"):
        return None
    fx = usd_rate(ctx, day)
    usd = round(flt(so.base_net_total) / fx, 2)
    item = so.items[0]
    lc = frappe.get_doc({
        "doctype": "LC Proforma", "proforma_no": f"PI-{so.name[-5:]}", "proforma_date": day, "company": ctx.company,
        "customer": so.customer, "buyer_po_no": so.get("buyer_po_no"), "export_order": so.name,
        "currency": ex.get("currency", "USD"), "exchange_rate": fx, "lc_required": 1,
        "lc_no": f"LC{rnd.randint(1000000, 9999999)}", "lc_date": add_days(day, rnd.randint(2, 6)),
        "lc_type": "Irrevocable", "lc_issuing_bank": cu["bank"], "lc_advising_bank": ex["advising_bank"],
        "lc_amount": usd, "lc_currency": ex.get("currency", "USD"),
        "lc_expiry_date": so.get("lc_expiry_date") or add_days(day, 45),
        "latest_shipment_date": so.get("latest_shipment_date") or add_days(day, 25),
        "port_of_loading": ex["port_of_loading"], "port_of_discharge": cu["port"], "final_destination": cu["port"],
        "country_of_destination": cu["country"], "shipment_mode": "Sea", "incoterm": so.get("incoterm") or "FOB",
        "lc_proforma_items": [{"item": item.item_code, "item_name": item.item_name, "quantity": item.qty, "uom": item.uom,
                               "rate": round(flt(item.base_net_rate) / fx, 4), "amount": usd,
                               "net_weight": item.qty, "gross_weight": round(item.qty * 1.03, 2),
                               "cartons": int(item.qty / 45) + 1}],
    })
    _dd()._fill(ctx, lc)
    lc.flags.ignore_permissions = True
    lc.insert()
    _walk_workflow(lc, ["Submit", "Send for Buyer Approval", "Approve", "Record LC", "Confirm"])
    ctx.bump("LC Proforma")
    for f, v in (("lc_proforma", lc.name), ("lc_no", lc.lc_no)):
        if frappe.get_meta("Sales Order").has_field(f):
            frappe.db.set_value("Sales Order", so.name, f, v, update_modified=False)
    return lc.name


def _walk_workflow(doc, actions):
    from frappe.model.workflow import apply_workflow
    if not frappe.db.exists("Workflow", {"document_type": doc.doctype, "is_active": 1}):
        if doc.docstatus == 0:
            doc.submit()
        return doc
    for action in actions:
        doc = apply_workflow(doc, action)
    return doc


def export_shipment(ctx, so_name, dn, day):
    """Export Shipment for a delivered export order: sailing, transit and arrival from the buyer's route."""
    if not frappe.db.exists("DocType", "Export Shipment"):
        return
    rnd, ex = ctx.rnd, ctx.t.export
    so = frappe.db.get_value("Sales Order", so_name, ["customer", "latest_shipment_date"], as_dict=True)
    cu = ctx.export_customers.get(so.customer)
    if not cu:
        return
    lc = frappe.db.get_value("LC Proforma", {"export_order": so_name, "docstatus": 1}, ["name", "lc_no", "latest_shipment_date"], as_dict=True) \
        if frappe.db.exists("DocType", "LC Proforma") else None
    latest = (lc and lc.latest_shipment_date) or so.latest_shipment_date
    etd = add_days(day, rnd.randint(1, 3))
    if latest and rnd.random() < flt(ex.get("late_ship_chance")):
        etd = add_days(latest, rnd.randint(1, 8))           # missed the LC's latest shipment date
    eta = add_days(etd, cu["transit"] + rnd.randint(-2, 4))
    today = getdate(ctx.end)
    status = "Delivered" if getdate(eta) <= add_days(today, -5) else ("Arrived" if getdate(eta) <= today else
                                                                       ("In Transit" if getdate(etd) <= today else "Booking"))
    doc = frappe.get_doc({
        "doctype": "Export Shipment", "shipment_no": f"EXP-{dn.name[-5:]}", "shipment_date": etd, "customer": so.customer,
        "sales_order": so_name, "lc_proforma": lc and lc.name, "lc_no": lc and lc.lc_no,
        "commercial_invoice_no": f"CI-{dn.name[-5:]}", "packing_list_no": f"PL-{dn.name[-5:]}",
        "bill_of_lading_no": f"BL{rnd.randint(10000000, 99999999)}", "container_no": f"MSKU{rnd.randint(1000000, 9999999)}",
        "shipping_line": rnd.choice(ex["shipping_lines"]), "vessel": f"MV {rnd.choice(['Aurora', 'Pacific Star', 'Indus Pearl', 'Nordic Wave', 'Sea Falcon'])}",
        "port_of_loading": ex["port_of_loading"], "port_of_discharge": cu["port"], "final_destination": cu["port"],
        "etd": get_datetime(f"{etd} 18:00:00"), "eta": get_datetime(f"{eta} 08:00:00"),
        "actual_shipment_date": etd if getdate(etd) <= today else None, "shipment_status": status,
    })
    doc.insert(ignore_permissions=True)
    ctx.bump("Export Shipment")
    if frappe.get_meta("Sales Order").has_field("export_status"):
        frappe.db.set_value("Sales Order", so_name, "export_status", "Shipped", update_modified=False)


def close_lc(ctx, so_name):
    """When the export invoice is paid the LC is closed — except a few left open (the 'LCs expiring' use case)."""
    if not frappe.db.exists("DocType", "LC Proforma") or ctx.rnd.random() < flt(ctx.t.export.get("lc_left_open")):
        return
    name = frappe.db.get_value("LC Proforma", {"export_order": so_name, "docstatus": 1}, "name")
    if name:
        _walk_workflow(frappe.get_doc("LC Proforma", name), ["Close"])


# ============================================================================ import
def _import_masters(ctx):
    im = ctx.t["import"]
    parent_sg = frappe.db.get_value("Supplier Group", {"is_group": 1}, "name")
    _ensure("Supplier Group", im["supplier_group"], {"supplier_group_name": im["supplier_group"], "parent_supplier_group": parent_sg})
    for s in im["suppliers"]:
        _ensure("Supplier", s["name"], {"supplier_name": s["name"], "supplier_group": im["supplier_group"],
                                        "supplier_type": "Company", "country": s["country"]})
    _ensure("Supplier", im["clearing_agent"], {"supplier_name": im["clearing_agent"], "supplier_group": im["supplier_group"],
                                               "supplier_type": "Company"})
    ctx.import_charges_account = _acc(ctx, im["charges_account"], ["Current Liabilit", "Duties and Taxes"], "Liability")
    ctx.import_by_fibre = {}
    for s in im["suppliers"]:
        for f in s["fibres"]:
            ctx.import_by_fibre.setdefault(f, []).append(s)


def import_supplier_for(ctx, fibre_code):
    im = ctx.t.get("import")
    if not im or fibre_code not in getattr(ctx, "import_by_fibre", {}) or ctx.rnd.random() >= flt(im.get("share")):
        return None
    return ctx.rnd.choice(ctx.import_by_fibre[fibre_code])


def import_departure(ctx, po_name, sup):
    """Supplier ships: Import Shipment booked with ETD/ETA; arrival and clearance follow as events."""
    rnd, im = ctx.rnd, ctx.t["import"]
    day = ctx._current_day
    sup = next((s for s in im["suppliers"] if s["name"] == sup), None) if isinstance(sup, str) else sup
    eta = add_days(day, sup["transit"])
    sh = frappe.get_doc({
        "doctype": "Import Shipment", "shipment_no": f"IMP-{po_name[-5:]}", "shipment_date": day, "supplier": sup["name"],
        "purchase_order": po_name, "bill_of_lading": f"BL{rnd.randint(10000000, 99999999)}",
        "container_no": f"TGHU{rnd.randint(1000000, 9999999)}", "shipping_line": sup.get("shipping_line"),
        "vessel": f"MV {rnd.choice(['Hanjin Glory', 'Java Spirit', 'Gulf Trader', 'Ocean Grace'])}",
        "port_of_loading": sup["port"], "port_of_discharge": im["port_of_discharge"],
        "etd": get_datetime(f"{day} 20:00:00"), "eta": get_datetime(f"{eta} 06:00:00"),
        "clearing_agent": im["clearing_agent"], "shipment_status": "Shipped",
    })
    sh.insert(ignore_permissions=True)
    ctx.bump("Import Shipment")
    late = rnd.randint(3, 9) if rnd.random() < flt(im.get("late_chance")) else rnd.randint(-1, 2)
    arrival = add_days(eta, late)
    dwell = rnd.randint(*im["dwell_days"])
    ctx.at(arrival, 0, _import_arrive, sh.name, arrival)
    ctx.at(add_days(arrival, dwell), 0, _import_clear, sh.name, arrival, add_days(arrival, dwell))


def _import_arrive(ctx, sh_name, arrival):
    frappe.db.set_value("Import Shipment", sh_name, {"actual_arrival": arrival, "shipment_status": "Arrived"},
                        update_modified=False)


def _import_clear(ctx, sh_name, arrival, clearance):
    """Customs clearance: Purchase Receipt + Invoice, Import Cost Sheet and a Landed Cost Voucher."""
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
    dd, rnd, im = _dd(), ctx.rnd, ctx.t["import"]
    sh = frappe.get_doc("Import Shipment", sh_name)
    pr = make_purchase_receipt(sh.purchase_order)
    dd._submit(ctx, pr, clearance)
    incoming_qi(ctx, pr)
    pi = make_purchase_invoice(pr.name)
    pi.bill_no, pi.bill_date, pi.due_date = f"CI-{rnd.randint(10000, 99999)}", clearance, add_days(clearance, 60)
    dd._submit(ctx, pi, clearance)
    ctx.at(add_days(clearance, rnd.randint(20, 60)), 3, dd._pay, "Purchase Invoice", pi.name)

    r = im["rates"]
    rows, charges = [], {}
    for it in pr.items:
        pv = flt(it.base_net_amount)
        freight, ins = pv * r["freight"], pv * r["insurance"]
        av = pv + freight + ins
        row = {"item": it.item_code, "item_name": it.item_name, "quantity": it.qty, "uom": it.uom, "purchase_value": pv,
               "freight": freight, "insurance": ins, "assessable_value": av, "customs_duty": av * r["customs_duty"],
               "additional_duty": av * r["additional_duty"], "regulatory_duty": av * r["regulatory_duty"],
               "sales_tax": av * r["sales_tax"], "income_tax_148": av * r["income_tax_148"],
               "clearing_charges": pv * r["clearing_charges"], "port_charges": pv * r["port_charges"], "other_charges": pv * r["other_charges"]}
        stock_cost = sum(row[k] for k in ("freight", "insurance", "customs_duty", "additional_duty", "regulatory_duty",
                                          "clearing_charges", "port_charges", "other_charges"))
        row["total_landed_cost"] = pv + stock_cost
        row["landed_cost_per_unit"] = row["total_landed_cost"] / flt(it.qty) if it.qty else 0
        rows.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()})
        for k in ("freight", "insurance", "customs_duty", "additional_duty", "regulatory_duty", "clearing_charges", "port_charges", "other_charges"):
            charges[k] = charges.get(k, 0) + row[k]
    cs = frappe.get_doc({"doctype": "Import Cost Sheet", "cost_sheet_date": clearance, "company": ctx.company,
                         "supplier": sh.supplier, "purchase_order": sh.purchase_order, "import_shipment": sh.name,
                         "purchase_receipt": pr.name, "currency": ctx.currency, "expense_account": ctx.import_charges_account,
                         "import_cost_sheet_items": rows})
    for f, v in (("total_purchase_value", sum(x["purchase_value"] for x in rows)),
                 ("total_landed_cost", sum(x["total_landed_cost"] for x in rows)),
                 ("total_duties", sum(x["customs_duty"] + x["additional_duty"] + x["regulatory_duty"] for x in rows)),
                 ("total_input_sales_tax", sum(x["sales_tax"] for x in rows)),
                 ("total_income_tax_148", sum(x["income_tax_148"] for x in rows))):
        if cs.meta.has_field(f):
            cs.set(f, round(v, 2))
    cs.insert(ignore_permissions=True)
    ctx.bump("Import Cost Sheet")

    lcv = frappe.get_doc({"doctype": "Landed Cost Voucher", "company": ctx.company, "posting_date": clearance,
                          "distribute_charges_based_on": "Amount",
                          "purchase_receipts": [{"receipt_document_type": "Purchase Receipt", "receipt_document": pr.name,
                                                 "supplier": pr.supplier, "posting_date": pr.posting_date,
                                                 "grand_total": pr.grand_total}],
                          "taxes": [{"expense_account": ctx.import_charges_account, "description": k.replace("_", " ").title(),
                                     "amount": round(v, 2)} for k, v in charges.items() if v > 0]})
    lcv.get_items_from_purchase_receipts()
    dd._submit(ctx, lcv, clearance)
    frappe.db.set_value("Import Shipment", sh.name, {
        "actual_arrival": arrival, "clearance_date": clearance, "purchase_receipt": pr.name, "import_cost_sheet": cs.name,
        "customs_declaration_no": f"GD-KAPE-{rnd.randint(100000, 999999)}",
        "duty_amount": round(sum(x["customs_duty"] + x["additional_duty"] + x["regulatory_duty"] for x in rows), 2),
        "tax_amount": round(sum(x["sales_tax"] + x["income_tax_148"] for x in rows), 2),
        "shipment_status": "Delivered"}, update_modified=False)


# ============================================================================ quality
def _quality_masters(ctx):
    q = ctx.t.quality
    # Inspections are recorded on the receipt / lot / delivery after it is posted, as the mill's lab works.
    frappe.db.set_single_value("Stock Settings", "allow_to_make_quality_inspection_after_purchase_or_delivery", 1)
    for tpl in q["templates"]:
        for p in tpl["parameters"]:
            if not frappe.db.exists("Quality Inspection Parameter", p["specification"]):
                frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": p["specification"]}).insert(ignore_permissions=True)
        if not frappe.db.exists("Quality Inspection Template", tpl["name"]):
            frappe.get_doc({"doctype": "Quality Inspection Template", "quality_inspection_template_name": tpl["name"],
                            "item_quality_inspection_parameter": [
                                {"specification": p["specification"], "numeric": cint(p.get("numeric", 1)),
                                 "min_value": p["min"], "max_value": p["max"], "value": p.get("value")}
                                for p in tpl["parameters"]]}).insert(ignore_permissions=True)
    for proc in q["procedures"]:
        if not frappe.db.exists("Quality Procedure", proc):
            frappe.get_doc({"doctype": "Quality Procedure", "quality_procedure_name": proc}).insert(ignore_permissions=True)
    for g in q["goals"]:
        if not frappe.db.exists("Quality Goal", g["goal"]):
            frappe.get_doc({"doctype": "Quality Goal", "goal": g["goal"], "procedure": g.get("procedure"),
                            "frequency": g.get("frequency") or "Monthly",
                            "objectives": [{"objective": o["objective"], "target": o["target"], "uom": o.get("uom")}
                                           for o in g["objectives"]]}).insert(ignore_permissions=True)
    ctx.qparams = {tpl["name"]: tpl["parameters"] for tpl in q["templates"]}
    ctx.qi_log = []          # (month, stage, accepted, u_ok)


def _inspect(ctx, template, itype, ref_type, ref_name, item_code, stage, reject_factor=1.0):
    """One Quality Inspection with three readings per parameter; now and then one parameter out of limits."""
    q, rnd = ctx.t.quality, ctx.rnd
    params = ctx.qparams.get(template)
    if not params:
        return None
    reject = rnd.random() < flt(q["reject_rate"].get(itype, 0.03)) * reject_factor
    bad = -1
    readings, u_ok = [], True
    numeric_idx = [i for i, p in enumerate(params) if cint(p.get("numeric", 1))]
    if reject and numeric_idx:
        bad = rnd.choice(numeric_idx)
    for i, p in enumerate(params):
        if not cint(p.get("numeric", 1)):            # pass / fail checks (shade, labels)
            ok = p.get("value") or "OK"
            readings.append({"specification": p["specification"], "numeric": 0, "value": ok, "reading_value": ok})
            continue
        lo, hi = flt(p["min"]), flt(p["max"])
        min_only = hi > 1e6                          # "minimum only" limits (e.g. CSP >= 2200)
        if min_only:
            hi = lo * 1.25
        span = (hi - lo) or max(abs(lo), 1) * 0.1
        if i == bad:
            vals = [(lo - span * rnd.uniform(0.05, 0.3)) if min_only else (hi + span * rnd.uniform(0.05, 0.3))] * 3
        else:
            mid = lo + span * rnd.uniform(0.3, 0.7)
            vals = [min(hi, max(lo, mid + span * rnd.uniform(-0.12, 0.12))) for _ in range(3)]
        if p["specification"].strip().upper().startswith("U %") and i == bad:
            u_ok = False
        readings.append({"specification": p["specification"], "numeric": 1, "min_value": p["min"], "max_value": p["max"],
                         **{f"reading_{k + 1}": str(round(v, 3)) for k, v in enumerate(vals)}})
    qi = frappe.get_doc({"doctype": "Quality Inspection", "report_date": ctx._current_day, "inspection_type": itype,
                         "reference_type": ref_type, "reference_name": ref_name, "item_code": item_code, "sample_size": 3,
                         "inspected_by": "Administrator", "quality_inspection_template": template, "readings": readings,
                         "remarks": _dd().DEMO_TAG})
    _dd()._fill(ctx, qi)
    qi.flags.ignore_permissions = True
    qi.insert()
    qi.submit()
    ctx.bump("Quality Inspection")
    ctx.qi_log.append((getdate(ctx._current_day).strftime("%Y-%m"), stage, qi.status == "Accepted", u_ok))
    if qi.status != "Accepted":
        proc = q["nc_procedure"].get(stage)
        nc = frappe.get_doc({"doctype": "Non Conformance", "subject": f"{ref_name}: {params[bad]['specification'] if bad >= 0 else 'reading'} out of limit ({qi.name})",
                             "procedure": proc, "status": "Open",
                             "details": f"{_dd().DEMO_TAG} · {template} rejected on {ctx._current_day}."})
        nc.insert(ignore_permissions=True)
        # raised on the inspection day (Non Conformance has no date field; the dashboards read its creation)
        stamp = get_datetime(f"{ctx._current_day} {rnd.randint(9, 17):02d}:{rnd.randint(0, 59):02d}:00")
        frappe.db.set_value("Non Conformance", nc.name, {"creation": stamp, "modified": stamp}, update_modified=False)
        ctx.bump("Non Conformance")
        if rnd.random() < 0.88:                                  # most get closed out within weeks
            ctx.at(add_days(ctx._current_day, rnd.randint(4, 25)), 7, _resolve_nc, nc.name)
    return qi


def _resolve_nc(ctx, nc_name):
    frappe.db.set_value("Non Conformance", nc_name, "status", "Resolved", update_modified=False)


def incoming_qi(ctx, pr):
    if not ctx.t.get("quality") or not hasattr(ctx, "qparams"):
        return
    worst = ctx.t.quality.get("worst_supplier")
    for it in pr.items:
        f = ctx.fibre.get(it.item_code) or ctx.conv_fibre.get(it.item_code)
        if f and f.get("qi_template"):
            factor = flt(ctx.t.quality.get("worst_supplier_factor", 1)) if pr.supplier == worst else 1
            _inspect(ctx, f["qi_template"], "Incoming", "Purchase Receipt", pr.name, it.item_code, "Incoming", factor)


def production_qi(ctx, wo, se):
    q = ctx.t.get("quality")
    if not q or not hasattr(ctx, "qparams"):
        return
    _inspect(ctx, ctx.rnd.choice(q["in_process_templates"]), "In Process", "Stock Entry", se.name, wo.production_item, "In Process")
    _inspect(ctx, q["lab_template"], "In Process", "Stock Entry", se.name, wo.production_item, "Lab")


def outgoing_qi(ctx, dn):
    q = ctx.t.get("quality")
    if not q or not hasattr(ctx, "qparams") or ctx.rnd.random() > flt(q.get("outgoing_share", 0.5)):
        return
    _inspect(ctx, q["outgoing_template"], "Outgoing", "Delivery Note", dn.name, dn.items[0].item_code, "Outgoing")


def _quality_reviews(ctx, day):
    """Month-end review per goal from the month's inspections; a failed goal raises a corrective action."""
    q = ctx.t.get("quality")
    if not q or not hasattr(ctx, "qi_log"):
        return
    m = getdate(day).strftime("%Y-%m")
    rows = [r for r in ctx.qi_log if r[0] == m]
    for g in q["goals"]:
        metric = q["goal_metric"].get(g["goal"])
        if metric == "Evenness":
            pool = [r for r in rows if r[1] == "Lab"]
            achieved = 100 * sum(1 for r in pool if r[3]) / len(pool) if pool else None
        else:
            pool = [r for r in rows if r[1] == metric]
            achieved = 100 * sum(1 for r in pool if r[2]) / len(pool) if pool else None
        if achieved is None:
            continue
        obj = g["objectives"][0]
        passed = achieved >= flt(obj["target"])
        rv = frappe.get_doc({"doctype": "Quality Review", "goal": g["goal"], "date": day,
                             "status": "Passed" if passed else "Failed",
                             "reviews": [{"objective": obj["objective"], "target": obj["target"], "uom": obj.get("uom"),
                                          "status": "Passed" if passed else "Failed",
                                          "review": f"{round(achieved, 1)}% achieved ({len(pool)} inspections)"}],
                             "additional_information": f"{_dd().DEMO_TAG} · {ctx.company}"})
        rv.insert(ignore_permissions=True)
        ctx.bump("Quality Review")
        if not passed:
            qa = frappe.get_doc({"doctype": "Quality Action", "corrective_preventive": "Corrective", "goal": g["goal"],
                                 "review": rv.name, "date": day, "status": "Open",
                                 "resolutions": [{"problem": f"{g['goal']}: {round(achieved, 1)}% vs target {obj['target']}%",
                                                  "resolution": "Root-cause review with supplier / shift in-charge; recheck next lots.",
                                                  "status": "Open"}]})
            qa.insert(ignore_permissions=True)
            ctx.bump("Quality Action")
            if ctx.rnd.random() < 0.85:
                ctx.at(add_days(day, ctx.rnd.randint(10, 35)), 7, _close_action, qa.name)


def _close_action(ctx, name):
    frappe.db.set_value("Quality Action", name, "status", "Completed", update_modified=False)


# ============================================================================ HR
def _hr_masters(ctx):
    hr, c, abbr = ctx.t.hr, ctx.company, ctx.abbr
    for d in hr["departments"]:
        dep = f"{d['name']} - {abbr}"
        if not frappe.db.exists("Department", dep):
            doc = frappe.get_doc({"doctype": "Department", "department_name": d["name"], "company": c,
                                  "parent_department": "All Departments"})
            _dd()._fill(ctx, doc)
            doc.insert(ignore_permissions=True)
        d["_name"] = frappe.db.get_value("Department", {"department_name": d["name"], "company": c}, "name") or dep
        for des in d["designations"]:
            _ensure("Designation", des, {"designation_name": des})
    ctx.holiday_list = _holiday_list(ctx)
    if frappe.db.exists("DocType", "Holiday List Assignment") and not frappe.db.exists(
            "Holiday List Assignment", {"applicable_for": "Company", "assigned_to": c, "docstatus": 1}):
        hla = frappe.get_doc({"doctype": "Holiday List Assignment", "applicable_for": "Company", "assigned_to": c,
                              "holiday_list": ctx.holiday_list,
                              "from_date": frappe.db.get_value("Holiday List", ctx.holiday_list, "from_date")})
        hla.insert(ignore_permissions=True)
        hla.submit()
    for s in hr["shifts"]:
        _ensure("Shift Type", s["name"], {"__newname": s["name"], "start_time": s["start"], "end_time": s["end"],
                                          "holiday_list": ctx.holiday_list})
    if not frappe.db.exists("Leave Type", "Casual Leave"):
        frappe.get_doc({"doctype": "Leave Type", "leave_type_name": "Casual Leave", "max_leaves_allowed": 10}).insert(ignore_permissions=True)
    ctx.employees = {}
    for e in frappe.get_all("Employee", {"company": c, "employee_number": ["like", "MMD-%"]},
                            ["name", "department", "status", "date_of_joining", "relieving_date"]):
        ctx.employees[e.name] = e
    if not ctx.employees:                      # the opening workforce, joined before the run
        for _i in range(cint(hr["headcount"])):
            _hire(ctx, add_days(ctx.start, -ctx.rnd.randint(60, 2200)))


def _holiday_list(ctx):
    hr = ctx.t.hr
    name = f"MicroMax Mill Holidays - {ctx.abbr}"
    start = getdate(f"{ctx.start.year - (1 if ctx.start.month < 7 else 0)}-07-01")
    end = getdate(f"{getdate(ctx.end).year + 1}-06-30")
    if frappe.db.exists("Holiday List", name):
        return name
    hl = frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": name, "from_date": start,
                         "to_date": end, "weekly_off": "Sunday"})
    hl.get_weekly_off_dates()
    for y in range(start.year, end.year + 1):
        for md in hr.get("holidays", []):
            d = getdate(f"{y}-{md}")
            if getdate(hl.from_date) <= d <= end and d.weekday() != 6:
                hl.append("holidays", {"holiday_date": d, "description": "Public holiday"})
    hl.insert(ignore_permissions=True)
    return name


def _pick_department(ctx):
    deps = ctx.t.hr["departments"]
    return ctx.rnd.choices(deps, weights=[d["share"] for d in deps])[0]


def _hire(ctx, doj):
    hr, rnd = ctx.t.hr, ctx.rnd
    female = rnd.random() < flt(hr.get("female_share"))
    full = rnd.choice(hr["female_names"] if female else hr["male_names"]).split(" ", 1)
    d = _pick_department(ctx)
    shift = ctx.t.hr["shifts"][-1]["name"] if d["kind"] == "staff" else rnd.choice(ctx.t.hr["shifts"][:3])["name"]
    n = frappe.db.count("Employee", {"employee_number": ["like", "MMD-%"]}) + 1
    etypes = hr.get("employment_types") or []
    etype = rnd.choices([x["name"] for x in etypes], weights=[x["share"] for x in etypes])[0] if etypes else None
    if etype and getdate(doj) > add_months(getdate(ctx.end), -cint(hr.get("probation_months") or 3)) and rnd.random() < 0.7:
        etype = "Probation"                        # recent joiners are still on probation
    if etype and not frappe.db.exists("Employment Type", etype):
        frappe.get_doc({"doctype": "Employment Type", "employee_type_name": etype}).insert(ignore_permissions=True)
    emp = frappe.get_doc({
        "doctype": "Employee", "first_name": full[0], "last_name": full[1] if len(full) > 1 else "",
        "gender": "Female" if female else "Male", "salutation": "Ms" if female else "Mr",
        "date_of_birth": add_days(doj, -rnd.randint(19 * 365, 48 * 365)), "date_of_joining": doj,
        "company": ctx.company, "department": d["_name"], "designation": rnd.choice(d["designations"]),
        "employment_type": etype,
        "holiday_list": ctx.holiday_list, "default_shift": shift, "status": "Active", "employee_number": f"MMD-{n:04d}",
        "ctc": 0,
    })
    _dd()._fill(ctx, emp)
    emp.flags.ignore_permissions = True
    emp.insert()
    ctx.bump("Employee")
    base = ctx.t.get("payroll", {}).get("base", {}).get(d["kind"], [35000, 45000])
    emp_row = frappe._dict(name=emp.name, department=emp.department, status="Active", date_of_joining=getdate(doj),
                           relieving_date=None, kind=d["kind"], base=round(rnd.uniform(*base), -2))
    ctx.employees[emp.name] = emp_row
    if hasattr(ctx, "salary_structure"):
        _assign_structure(ctx, emp_row, max(getdate(doj), getdate(ctx.start)))
    return emp_row


def _month_start_hr(ctx, day):
    """Joiners and leavers for the month (attrition), before attendance is marked."""
    hr, rnd = ctx.t.hr, ctx.rnd
    last = min(get_last_day(day), getdate(ctx.end))
    for _i in range(rnd.randint(*hr["joiners_per_month"])):
        _hire(ctx, add_days(day, rnd.randint(0, max(0, (last - getdate(day)).days))))
    active = [e for e in ctx.employees.values() if e.status == "Active" and e.date_of_joining < getdate(day)]
    summer = getdate(day).month in ctx.t.load_shedding
    for e in rnd.sample(active, min(len(active), rnd.randint(*hr["leavers_per_month"]) + (1 if summer else 0))):
        rel = add_days(day, rnd.randint(0, max(0, (last - getdate(day)).days)))
        frappe.db.set_value("Employee", e.name, {"relieving_date": rel, "status": "Left"}, update_modified=False)
        e.status, e.relieving_date = "Left", getdate(rel)


def _attendance(ctx, day):
    """The month's attendance (bulk, submitted): present / absent / on leave per working day and employee."""
    hr, rnd = ctx.t.hr, ctx.rnd
    first, last = getdate(day).replace(day=1), min(get_last_day(day), getdate(ctx.end))
    holidays = set(getdate(h) for h in frappe.get_all("Holiday", {"parent": ctx.holiday_list,
                                                                   "holiday_date": ["between", [first, last]]}, pluck="holiday_date"))
    absent_rate = flt(hr["absence_rate"]) + (flt(hr.get("summer_absence_extra")) if first.month in ctx.t.load_shedding else 0)
    existing = set((r.employee, getdate(r.attendance_date)) for r in frappe.get_all(
        "Attendance", {"company": ctx.company, "attendance_date": ["between", [first, last]], "docstatus": ["<", 2]},
        ["employee", "attendance_date"]))
    names = {e: frappe.db.get_value("Employee", e, "employee_name") for e in ctx.employees}
    rows, now = [], frappe.utils.now()
    fields = ["name", "naming_series", "employee", "employee_name", "status", "attendance_date", "company", "department",
              "docstatus", "creation", "modified", "owner", "modified_by", "leave_type"]
    for e in ctx.employees.values():
        d = max(first, e.date_of_joining)
        end = min(last, e.relieving_date or last)
        while d <= end:
            if d not in holidays and (e.name, d) not in existing:
                r = rnd.random()
                status, lt = ("Absent", None) if r < absent_rate else (("On Leave", "Casual Leave") if r < absent_rate + flt(hr["leave_rate"]) else ("Present", None))
                rows.append((f"HR-ATT-MMD-{frappe.generate_hash(length=10)}", "HR-ATT-.YYYY.-", e.name, names.get(e.name), status, d,
                             ctx.company, e.department, 1, now, now, "Administrator", "Administrator", lt))
            d = add_days(d, 1)
    if rows:
        frappe.db.bulk_insert("Attendance", fields, rows)
        ctx.bump("Attendance", len(rows))
    # The leave days as approved Leave Applications (written directly: the site's approval workflow and leave
    # allocations are not what the demo is about).
    branch = _dd()._default_link(ctx, "Branch") if frappe.get_meta("Leave Application").has_field("branch") else None
    la_rows = [(f"HR-LAP-MMD-{frappe.generate_hash(length=10)}", "HR-LAP-.YYYY.-", emp, en, "Casual Leave", ctx.company,
                dep, d, d, d, 1, "Approved", 1, _dd().DEMO_TAG, now, now, "Administrator", "Administrator", branch)
               for (_n, _s, emp, en, status, d, _c, dep, *_rest) in rows if status == "On Leave"]
    if la_rows:
        cols = ["name", "naming_series", "employee", "employee_name", "leave_type", "company", "department", "from_date",
                "to_date", "posting_date", "total_leave_days", "status", "docstatus", "description", "creation", "modified",
                "owner", "modified_by", "branch"]
        if not branch:
            cols, la_rows = cols[:-1], [r[:-1] for r in la_rows]
        frappe.db.bulk_insert("Leave Application", cols, la_rows)
        ctx.bump("Leave Application", len(la_rows))


# ============================================================================ payroll
def _payroll_masters(ctx):
    pr, c = ctx.t.payroll, ctx.company
    wages = _acc(ctx, pr["wages_account"], ["Direct Expenses", "Cost of Sales", "Direct Expense"], "Expense")
    direct = frappe.db.get_value("Account", {"company": c, "is_group": 1, "account_name": "Direct Expenses"}, "name")
    if direct and frappe.db.get_value("Account", wages, "parent_account") != direct:     # mill wages are manufacturing cost
        acc = frappe.get_doc("Account", wages)
        acc.parent_account = direct
        acc.save(ignore_permissions=True)
    if frappe.get_meta("Account").has_field("cps_applicable"):
        frappe.db.set_value("Account", wages, "cps_applicable", 1, update_modified=False)
    payable = _acc(ctx, pr["payable_account"], ["Current Liabilit"], "Liability", "Payable")
    if frappe.db.get_value("Account", payable, "account_type") != "Payable":       # HRMS needs a Payable account
        if frappe.db.exists("GL Entry", {"account": payable}):
            payable = _acc(ctx, f"{pr['payable_account']} (Mill)", ["Current Liabilit"], "Liability", "Payable")
        else:
            frappe.db.set_value("Account", payable, "account_type", "Payable")
    eobi = _acc(ctx, pr["eobi_account"], ["Duties and Taxes", "Current Liabilit"], "Liability")
    tax = _acc(ctx, pr["tax_account"], ["Duties and Taxes", "Current Liabilit"], "Liability")
    if frappe.db.get_value("Company", c, "default_payroll_payable_account") != payable:
        frappe.db.set_value("Company", c, "default_payroll_payable_account", payable)
    ctx.payroll_payable = payable
    pf = _acc(ctx, pr.get("pf_account") or "Provident Fund Payable", ["Current Liabilit"], "Liability")
    # Pakistan salaried pay: Basic = base / 1.65; HRA 45%, utilities 10%, medical 10% of basic (medical exempt from tax).
    # Own abbreviations (MM_*) so they never clash with components a site already has.
    comps = [
        # name, abbr, type, formula, fixed amount, account, taxable, tax component
        ("Basic Salary", "MM_BS", "Earning", "base / 1.65", None, wages, 1, 0),
        ("House Rent Allowance", "MM_HRA", "Earning", "MM_BS * 0.45", None, wages, 1, 0),
        ("Utilities Allowance", "MM_UTL", "Earning", "MM_BS * 0.10", None, wages, 1, 0),
        ("Medical Allowance (Exempt)", "MM_MED", "Earning", "MM_BS * 0.10", None, wages, 0, 0),
        ("EOBI Contribution", "MM_EOBI", "Deduction", None, pr["eobi"], eobi, 1, 0),
        ("Provident Fund", "MM_PF", "Deduction", f"MM_BS * {flt(pr.get('pf_rate') or 0.0833)} if base >= 60000 else 0", None, pf, 1, 0),
        ("Income Tax (Salaried)", "MM_IT", "Deduction", None, None, tax, 1, 1),
    ]
    for name, abbr_, typ, formula, amount, acc, taxable, is_tax in comps:
        if not frappe.db.exists("Salary Component", name):
            frappe.get_doc({"doctype": "Salary Component", "salary_component": name, "salary_component_abbr": abbr_,
                            # only Basic is prorated by payment days; allowances follow it through their formula
                            "type": typ, "depends_on_payment_days": 1 if abbr_ == "MM_BS" else 0,
                            "is_tax_applicable": taxable if typ == "Earning" else 0,
                            "variable_based_on_taxable_salary": is_tax, "is_income_tax_component": is_tax,
                            "round_to_the_nearest_integer": 1,
                            "accounts": [{"company": c, "account": acc}]}).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("Salary Component", name, {"is_tax_applicable": taxable if typ == "Earning" else 0,
                                                           "depends_on_payment_days": 1 if abbr_ == "MM_BS" else 0,
                                                           "variable_based_on_taxable_salary": is_tax, "is_income_tax_component": is_tax})
            if not frappe.db.exists("Salary Component Account", {"parent": name, "company": c}):
                sc = frappe.get_doc("Salary Component", name)
                sc.append("accounts", {"company": c, "account": acc})
                sc.save(ignore_permissions=True)
    ctx.tax_slab = _tax_slab(ctx)
    _payroll_periods(ctx)
    name = f"{pr['structure']} - {ctx.abbr}"
    if not frappe.db.exists("Salary Structure", name):
        ss = frappe.get_doc({"doctype": "Salary Structure", "company": c, "currency": ctx.currency,
                             "payroll_frequency": "Monthly", "is_active": "Yes",
                             "earnings": [{"salary_component": n, "abbr": a, "amount_based_on_formula": 1, "formula": f}
                                          for n, a, typ, f, _amt, _acc, _tx, _it in comps if typ == "Earning"],
                             "deductions": [{"salary_component": n, "abbr": a,
                                             **({"amount_based_on_formula": 1, "formula": f} if f else
                                                {"amount": amt} if amt else {"variable_based_on_taxable_salary": 1})}
                                            for n, a, typ, f, amt, _acc, _tx, _it in comps if typ == "Deduction"]})
        ss.name = name
        _dd()._fill(ctx, ss)
        ss.flags.ignore_permissions = True
        ss.insert(set_name=name)
        ss.submit()
    ctx.salary_structure = name
    for e in ctx.employees.values():
        if not frappe.db.exists("Salary Structure Assignment", {"employee": e.name, "docstatus": 1}):
            _assign_structure(ctx, e, max(getdate(e.date_of_joining), getdate(ctx.start)))


def _tax_slab(ctx):
    """FBR salaried income tax slabs as an HRMS Income Tax Slab (bands taxed progressively = FBR's fixed + % of excess)."""
    ts = ctx.t.payroll.get("tax_slab")
    if not ts:
        return None
    name = f"{ts['name']} - {ctx.abbr}"
    if not frappe.db.exists("Income Tax Slab", name):
        doc = frappe.get_doc({"doctype": "Income Tax Slab", "effective_from": ts["effective_from"], "currency": ctx.currency,
                              "company": ctx.company,
                              "slabs": [{"from_amount": lo, "to_amount": hi or 0, "percent_deduction": pct} for lo, hi, pct in ts["slabs"]]})
        doc.name = name
        doc.flags.ignore_permissions = True
        doc.insert(set_name=name)
        doc.submit()
    return name


def _payroll_periods(ctx):
    """Payroll Periods (Pakistan tax year, July-June) covering the run; HRMS projects annual tax within them."""
    y = getdate(ctx.start).year - (1 if getdate(ctx.start).month < 7 else 0)
    while getdate(f"{y}-07-01") <= getdate(ctx.end):
        start, end = getdate(f"{y}-07-01"), getdate(f"{y + 1}-06-30")
        if not frappe.db.exists("Payroll Period", {"company": ctx.company, "start_date": ["<=", start], "end_date": [">=", start]}):
            pp = frappe.get_doc({"doctype": "Payroll Period", "company": ctx.company, "start_date": start, "end_date": end})
            pp.name = f"Tax Year {y}-{str(y + 1)[2:]} - {ctx.abbr}"
            pp.insert(ignore_permissions=True, set_name=pp.name)
        y += 1


def _staff_departments(ctx):
    return {d.get("_name") for d in ctx.t.hr["departments"] if d.get("kind") == "staff"}


def _assign_structure(ctx, e, from_date):
    staff = e.get("kind") == "staff" or e.get("department") in _staff_departments(ctx)
    base = e.get("base") or round(ctx.rnd.uniform(*ctx.t.payroll["base"]["staff" if staff else "worker"]), -2)
    from micromax import demo_setup
    cc = ctx.cost_center if staff else demo_setup.mill_cc(ctx)    # mill workers are costed to their mill
    ssa = frappe.get_doc({"doctype": "Salary Structure Assignment", "employee": e.name, "salary_structure": ctx.salary_structure,
                          "company": ctx.company, "currency": ctx.currency, "from_date": from_date, "base": base,
                          "income_tax_slab": getattr(ctx, "tax_slab", None),
                          "payroll_payable_account": getattr(ctx, "payroll_payable", None),
                          "payroll_cost_centers": [{"cost_center": cc, "percentage": 100}]})
    _dd()._fill(ctx, ssa)
    ssa.flags.ignore_permissions = True
    ssa.insert()
    ssa.submit()


def _payroll(ctx, day):
    """Month-end Payroll Entry for the mill (attendance based) and the salary payment a few days later."""
    dd = _dd()
    first, last = getdate(day).replace(day=1), get_last_day(day)
    if getdate(last) > getdate(ctx.end):          # the current, unfinished month is paid next month
        return
    pe = frappe.get_doc({"doctype": "Payroll Entry", "company": ctx.company, "posting_date": last, "payroll_frequency": "Monthly",
                         "start_date": first, "end_date": last, "currency": ctx.currency, "exchange_rate": 1,
                         "payroll_payable_account": ctx.payroll_payable, "cost_center": ctx.cost_center,
                         "payment_account": _setup().bank(ctx, "payroll")})
    dd._fill(ctx, pe)
    pe.flags.ignore_permissions = True
    pe.insert()
    pe.fill_employee_details()
    pe.set("employees", [r for r in pe.employees if r.employee in ctx.employees])
    if not pe.employees:
        return
    pe.save()
    # HRMS creates / submits slips in helpers that swallow errors and roll the whole transaction back; do the same
    # steps directly so a problem surfaces as an error on this event only.
    pe.create_salary_slips = lambda *a, **k: None
    pe.submit()
    submitted = []
    frappe.flags.via_payroll_entry = True
    try:
        for row in pe.employees:
            slip = frappe.get_doc({"doctype": "Salary Slip", "employee": row.employee, "payroll_frequency": "Monthly",
                                   "start_date": first, "end_date": last, "company": ctx.company, "posting_date": last,
                                   "payroll_entry": pe.name, "exchange_rate": 1, "currency": ctx.currency})
            dd._fill(ctx, slip)
            slip.flags.ignore_permissions = True
            slip.insert()
            if flt(slip.net_pay) >= 0:
                slip.submit()
                submitted.append(slip)
    finally:
        frappe.flags.via_payroll_entry = False
    if submitted:
        pe.make_accrual_jv_entry(submitted)
    pe.db_set({"status": "Submitted", "salary_slips_created": 1, "salary_slips_submitted": 1})
    ctx.bump("Payroll Entry")
    ctx.bump("Salary Slip", len(submitted))
    ctx.at(add_days(last, cint(ctx.t.payroll.get("pay_after_days", 5))), 6, _pay_salaries, pe.name)


def _pay_salaries(ctx, pe_name):
    """Salaries paid from the bank a few days after month end (HRMS's own bank entry for the payroll)."""
    pe = frappe.get_doc("Payroll Entry", pe_name)
    je = pe.make_bank_entry()
    if not je:
        return
    je = frappe.get_doc("Journal Entry", je.name if hasattr(je, "name") else je)
    je.posting_date = ctx._current_day
    je.cheque_no, je.cheque_date = f"SAL-{pe_name[-5:]}", ctx._current_day
    je.user_remark = f"{_dd().DEMO_TAG} · {je.user_remark or 'salaries paid'}"
    _dd()._fill(ctx, je)
    je.flags.ignore_permissions = True
    je.save()
    je.submit()
    ctx.bump("Salary payment")


# ============================================================================ assets
def _asset_masters(ctx):
    a, c = ctx.t.assets, ctx.company
    for loc in a["locations"]:
        _ensure("Location", loc, {"location_name": loc})
    fixed = lambda n: _acc(ctx, n, ["Fixed Asset"], "Asset", "Fixed Asset")                         # noqa: E731
    acc_dep = _acc(ctx, "Accumulated Depreciation (Demo Assets)", ["Fixed Asset"], "Asset", "Accumulated Depreciation")
    dep_exp = _acc(ctx, "Depreciation (Demo Assets)", ["Indirect Expense", "Administrative"], "Expense", "Depreciation")
    for f, v in (("accumulated_depreciation_account", acc_dep), ("depreciation_expense_account", dep_exp),
                 ("depreciation_cost_center", ctx.cost_center)):
        if not frappe.db.get_value("Company", c, f):
            frappe.db.set_value("Company", c, f, v)
    if not frappe.db.get_value("Company", c, "asset_received_but_not_billed"):
        frappe.db.set_value("Company", c, "asset_received_but_not_billed",
                            _acc(ctx, "Asset Received But Not Billed", ["Current Liabilit"], "Liability", "Asset Received But Not Billed"))
    ctx.asset_life = {}
    for cat in a["categories"]:
        ctx.asset_life[cat["name"]] = cint(cat["life_months"])
        if not frappe.db.exists("Asset Category", cat["name"]):
            frappe.get_doc({"doctype": "Asset Category", "asset_category_name": cat["name"],
                            "accounts": [{"company_name": c, "fixed_asset_account": fixed(cat["name"]),
                                          "accumulated_depreciation_account": acc_dep, "depreciation_expense_account": dep_exp}],
                            "finance_books": [{"depreciation_method": "Straight Line", "frequency_of_depreciation": 1,
                                               "total_number_of_depreciations": cint(cat["life_months"])}]}).insert(ignore_permissions=True)
        elif not frappe.db.exists("Asset Category Account", {"parent": cat["name"], "company_name": c}):
            ac = frappe.get_doc("Asset Category", cat["name"])
            ac.append("accounts", {"company_name": c, "fixed_asset_account": fixed(cat["name"]),
                                   "accumulated_depreciation_account": acc_dep, "depreciation_expense_account": dep_exp})
            ac.save(ignore_permissions=True)
    ctx.asset_items = {}
    for it in a["items"]:
        ctx.asset_items[it["code"]] = it
        if not frappe.db.exists("Item", it["code"]):
            frappe.get_doc({"doctype": "Item", "item_code": it["code"], "item_name": it["name"], "item_group": "All Item Groups",
                            "stock_uom": "Nos", "is_stock_item": 0, "is_fixed_asset": 1, "asset_category": it["category"],
                            "auto_create_assets": 0}).insert(ignore_permissions=True)
    if not frappe.db.exists("Asset", {"company": c, "item_code": ["like", "MM-A-%"]}):
        for o in a["opening"]:
            for _i in range(cint(o["count"])):
                age = ctx.rnd.randint(*o["age_months"])
                _asset(ctx, o["item"], add_months(ctx.start, -age), existing=True)


def _asset(ctx, item_code, purchase_date, existing=False, draft=False):
    it = ctx.asset_items[item_code]
    life = ctx.asset_life[it["category"]]
    cost = round(flt(it["cost"]) * ctx.rnd.uniform(0.95, 1.08), -3)
    booked = 0
    if existing:                                   # month ends already depreciated before the run starts
        booked = max(0, min(life - 1, (getdate(ctx.start).year - getdate(purchase_date).year) * 12
                            + getdate(ctx.start).month - getdate(purchase_date).month))
    n = frappe.db.count("Asset", {"item_code": item_code, "company": ctx.company}) + 1
    asset = frappe.get_doc({
        "doctype": "Asset", "item_code": item_code, "asset_name": f"{it['name']} #{n:02d}", "company": ctx.company,
        "location": it["location"], "asset_category": it["category"], "purchase_date": purchase_date,
        "available_for_use_date": purchase_date, "is_existing_asset": 1, "net_purchase_amount": cost,
        "purchase_amount": cost, "calculate_depreciation": 1,
        "opening_accumulated_depreciation": round(cost / life * booked, 2) if booked else 0,
        "opening_number_of_booked_depreciations": booked,
        # machinery and utilities belong to a mill; vehicles and office kit to head office
        "cost_center": _setup().mill_cc(ctx) if it["location"] in ("Mill Floor", "Utility Area") else ctx.cost_center,
        "finance_books": [{"depreciation_method": "Straight Line", "frequency_of_depreciation": 1,
                           "total_number_of_depreciations": life,
                           "depreciation_start_date": get_last_day(add_months(purchase_date, booked))}],
    })
    _dd()._fill(ctx, asset)
    asset.flags.ignore_permissions = True
    asset.insert()
    if not draft:
        asset.submit()
    ctx.bump("Asset")
    return asset


def _asset_month(ctx, day):
    """Month end: additions due this month, quarterly movements, and depreciation for this company's assets."""
    a, rnd = ctx.t.assets, ctx.rnd
    m = ctx.months_in(day) + 1
    for add in a["additions"]:
        if cint(add["month"]) == m:
            for _i in range(cint(add["count"])):
                pd_ = add_days(getdate(day).replace(day=1), rnd.randint(0, 20))
                _asset(ctx, add["item"], pd_, draft=rnd.random() < flt(a.get("draft_share")))
    if getdate(day).month in (9, 12, 3, 6):
        movable = frappe.get_all("Asset", {"company": ctx.company, "docstatus": 1, "item_code": ["like", "MM-A-%"]},
                                 ["name", "location"])
        for asset in rnd.sample(movable, min(len(movable), cint(a.get("movements_per_quarter", 2)))):
            to = rnd.choice([x for x in a["locations"] if x != asset.location])
            mv = frappe.get_doc({"doctype": "Asset Movement", "company": ctx.company, "purpose": "Transfer",
                                 "transaction_date": get_datetime(f"{day} 11:00:00"),
                                 "assets": [{"asset": asset.name, "source_location": asset.location, "target_location": to}]})
            _dd()._fill(ctx, mv)
            frappe.db.savepoint("asset_move")
            try:
                mv.flags.ignore_permissions = True
                mv.insert()
                mv.submit()
                ctx.bump("Asset Movement")
            except Exception as e:
                frappe.db.rollback(save_point="asset_move")
                ctx.log(f"    asset movement {asset.name}: {str(e)[:120]}")
    from erpnext.assets.doctype.asset.depreciation import make_depreciation_entry
    for s in frappe.db.sql("""select distinct s.name from `tabAsset Depreciation Schedule` s
            join `tabAsset` a on a.name = s.asset join `tabDepreciation Schedule` d on d.parent = s.name
            where a.company = %s and a.docstatus = 1 and s.docstatus = 1 and a.item_code like 'MM-A-%%'
            and d.schedule_date <= %s and d.journal_entry is null""", (ctx.company, day), pluck=True):
        frappe.db.savepoint("depr")
        try:
            make_depreciation_entry(s, day)
            ctx.bump("Depreciation posting")
        except Exception as e:
            frappe.db.rollback(save_point="depr")
            ctx.log(f"    depreciation {s}: {str(e)[:120]}")


# ============================================================================ month events
def month_start(ctx, day):
    if ctx.t.get("hr") and hasattr(ctx, "employees"):
        _month_start_hr(ctx, day)


def month_end(ctx, day):
    if ctx.t.get("hr") and hasattr(ctx, "employees"):
        _attendance(ctx, day)
        if ctx.t.get("payroll") and hasattr(ctx, "salary_structure"):
            _payroll(ctx, day)
    if ctx.t.get("quality"):
        _quality_reviews(ctx, day)
    if ctx.t.get("assets") and hasattr(ctx, "asset_items"):
        _asset_month(ctx, day)


# ============================================================================ undo
RAW_DELETE = {"Quality Inspection", "Non Conformance", "Quality Review", "Quality Action", "Export Shipment", "LC Proforma",
              "Import Shipment", "Import Cost Sheet", "Attendance", "Leave Application", "Employee"}


def raw_delete(dt, name):
    for table in frappe.get_meta(dt).get_table_fields():
        frappe.db.delete(table.options, {"parent": name, "parenttype": dt})
    frappe.db.delete(dt, {"name": name})


def _exists(dt):
    return frappe.db.exists("DocType", dt)


def purge_plan(company, items, tag):
    """What demo_modules made for `company`, in the order it has to go before the core documents."""
    sql = frappe.db.sql_list
    emps = tuple(sql("select name from `tabEmployee` where company=%s and employee_number like 'MMD-%%'", company)) or ("",)
    qis = sql("""select q.name from `tabQuality Inspection` q where q.remarks like %(tag)s and (
            (q.reference_type='Purchase Receipt' and q.reference_name in (select name from `tabPurchase Receipt` where company=%(c)s))
         or (q.reference_type='Stock Entry' and q.reference_name in (select name from `tabStock Entry` where company=%(c)s))
         or (q.reference_type='Delivery Note' and q.reference_name in (select name from `tabDelivery Note` where company=%(c)s)))""",
              {"tag": f"%{tag}%", "c": company})
    ncs = [n for q in qis for n in sql("select name from `tabNon Conformance` where subject like %s", f"%({q})%")]
    reviews = sql("select name from `tabQuality Review` where additional_information like %s", f"%{tag} · {company}%")
    actions = sql("select name from `tabQuality Action` where review in %s", (tuple(reviews) or ("",),))
    assets = tuple(sql("select name from `tabAsset` where company=%s and item_code like 'MM-A-%%'", company)) or ("",)
    pes = tuple(sql("""select distinct p.name from `tabPayroll Entry` p join `tabPayroll Employee Detail` d on d.parent=p.name
        where p.company=%s and d.employee in %s""", (company, emps))) or ("",)
    plan = [
        ("Share Transfer", sql("""select name from `tabShare Transfer` where company=%s and docstatus<2 and remarks like %s
            order by date desc, creation desc""", (company, f"%{tag}%"))),
        ("Quality Action", actions), ("Quality Review", reviews), ("Non Conformance", ncs), ("Quality Inspection", qis),
        ("Export Shipment", sql("""select name from `tabExport Shipment` where sales_order in
            (select name from `tabSales Order` where company=%s)""", company) if _exists("Export Shipment") else []),
        ("LC Proforma", sql("select name from `tabLC Proforma` where company=%s and export_order is not null and export_order != ''",
                            company) if _exists("LC Proforma") else []),
        ("Import Cost Sheet", sql("select name from `tabImport Cost Sheet` where company=%s and import_shipment is not null",
                                  company) if _exists("Import Cost Sheet") else []),
        ("Import Shipment", sql("""select name from `tabImport Shipment` where purchase_order in
            (select name from `tabPurchase Order` where company=%s)""", company) if _exists("Import Shipment") else []),
        ("Landed Cost Voucher", sql("""select distinct l.name from `tabLanded Cost Voucher` l
            join `tabLanded Cost Purchase Receipt` r on r.parent=l.name join `tabPurchase Receipt Item` i on i.parent=r.receipt_document
            where l.company=%s and l.docstatus<2 and i.item_code in %s order by l.posting_date desc""", (company, items))),
        # payroll: salary payments and accruals, slips, runs, assignments; then attendance and leave
        ("Journal Entry", sql("""select distinct j.name from `tabJournal Entry` j join `tabJournal Entry Account` a on a.parent=j.name
            where j.company=%s and j.docstatus<2 and a.reference_type='Payroll Entry' and a.reference_name in %s
            order by j.posting_date desc, j.creation desc""", (company, pes))),
        ("Salary Slip", sql("select name from `tabSalary Slip` where company=%s and employee in %s and docstatus<2 order by start_date desc",
                            (company, emps))),
        ("Payroll Entry", sql("select name from `tabPayroll Entry` where name in %s and docstatus<2 order by posting_date desc", (pes,))),
        ("Salary Structure Assignment", sql("select name from `tabSalary Structure Assignment` where employee in %s and docstatus<2", (emps,))),
        ("Attendance", sql("select name from `tabAttendance` where employee in %s", (emps,))),
        ("Leave Application", sql("select name from `tabLeave Application` where employee in %s", (emps,))),
        # assets: depreciation entries, then the assets (cancelling one cancels its movements and schedules)
        ("Journal Entry", sql("""select distinct j.name from `tabJournal Entry` j join `tabJournal Entry Account` a on a.parent=j.name
            where j.company=%s and j.docstatus=1 and j.voucher_type='Depreciation Entry' and a.reference_type='Asset'
            and a.reference_name in %s order by j.posting_date desc""", (company, assets))),
        ("Asset Movement", sql("""select distinct m.name from `tabAsset Movement` m join `tabAsset Movement Item` i on i.parent=m.name
            where m.docstatus=1 and m.purpose='Transfer' and i.asset in %s order by m.transaction_date desc""", (assets,))),
        ("Asset", sql("select name from `tabAsset` where name in %s order by creation desc", (assets,))),
        ("Asset Movement", sql("""select distinct m.name from `tabAsset Movement` m join `tabAsset Movement Item` i on i.parent=m.name
            where i.asset in %s""", (assets,))),
        ("Asset Depreciation Schedule", sql("select name from `tabAsset Depreciation Schedule` where asset in %s", (assets,))),
    ]
    return plan


def purge_plan_after(company):
    return [("Employee", frappe.db.sql_list("select name from `tabEmployee` where company=%s and employee_number like 'MMD-%%'",
                                            company))]


# ============================================================================ shop floor
_OP_DEPT = (("card", "Blow Room"), ("blow", "Blow Room"), ("draw", "Drawing"), ("simplex", "Drawing"), ("roving", "Drawing"),
            ("ring", "Ring Spinning"), ("cone", "Auto Cone"), ("wind", "Auto Cone"), ("rework", "Auto Cone"))


def operator_for(ctx, operation, day):
    """An active mill worker from the department that runs `operation` (any active worker if none matches)."""
    emps = getattr(ctx, "employees", None)
    if not emps:
        return None
    d = getdate(day)
    low = (operation or "").lower()
    dept_hint = next((dep for key, dep in _OP_DEPT if key in low), None)
    pool = [e for e in emps.values() if e.date_of_joining <= d and (not e.relieving_date or e.relieving_date >= d)]
    matched = [e for e in pool if dept_hint and dept_hint.lower() in (e.department or "").lower()]
    pick = matched or pool
    return ctx.rnd.choice(pick).name if pick else None
