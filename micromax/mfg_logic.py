"""Spinning-mill calculations for BOM and Work Order: blend ratio, yield, waste, spindles and frames.

Background
----------
The old ERPNext (hik) ran these calculations inside the ``lucrum_textile_changes`` app, which added
read-only custom fields to BOM, BOM Item and Work Order (``blend_ratio``, ``item_yield``,
``gross_up_qty``, ``target_yield``, ``material_issued``, ``spindle_required`` …). That app's source is
no longer available, so every rule below was RECOVERED FROM THE DATA: it was tested against the
992 Spinning BOMs (1,438 items) and 9,985 Work Orders transferred from hik, and the share of rows it
reproduces is written next to it. ``verify_against_data()`` re-runs that comparison on the live site.

Rules that only matched some of the old rows (the old app changed over the years) are documented as
such rather than forced. Nothing here touches the transferred values: they are stored exactly as hik
had them, and these rules apply when a BOM / Work Order is created or edited from now on.

Constants (spinning-frame geometry, inferred from the data — change here if the mill's setup differs):
    SPINDLE_FACTOR      = 16    spindle_required = SPINDLE_FACTOR * qty / target_ops
    SPINDLES_PER_FRAME  = 480   spindles on one ring frame
    SHIFTS_PER_DAY      = 3
"""

import frappe
from frappe.utils import flt

SPINDLE_FACTOR = 16
SPINDLES_PER_FRAME = 480
SHIFTS_PER_DAY = 3

SPINNING = "Spinning"


def _div(a, b):
    return a / b if b else 0.0


def _set(doc, fieldname, value):
    """Set a field on a Document or on a plain frappe._dict copy (which also answers hasattr('set'))."""
    if isinstance(doc, dict):
        doc[fieldname] = value
    else:
        doc.set(fieldname, value)


# ----------------------------------------------------------------------------- BOM
def apply_bom(doc):
    """Recompute the BOM's calculated fields in place. Works on a Document or a frappe._dict copy.

    Only Spinning BOMs are touched — the custom fields exist for every BOM but were only ever filled
    for that BOM type (all 992 hik BOMs are Spinning).
    """
    if doc.get("bom_type") != SPINNING:
        return doc

    quantity = flt(doc.get("quantity"))
    items = doc.get("items") or []

    for it in items:
        # Item quantity from its blend ratio (395/400 = 98.8% of hik rows).
        if flt(it.get("blend_ratio")) > 0:
            it.qty = quantity * flt(it.get("blend_ratio")) / 100
        # Gross-up quantity: what must be issued to get `qty` after the item's own yield loss
        # (exact for the rows that kept full precision; ~21% of old rows have it rounded to 2 decimals).
        y = flt(it.get("item_yield"))
        it.gross_up_qty = _div(flt(it.get("qty")), y / 100) if y > 0 else flt(it.get("qty"))

    # Blend-weighted yield of the mix; an item with no yield entered counts as 100 % (986/992 = 99.4%).
    total_blend = sum(flt(i.get("blend_ratio")) for i in items)
    if total_blend > 0:
        doc.target_yield = sum(flt(i.get("blend_ratio")) * (flt(i.get("item_yield")) or 100) for i in items) / total_blend

    target_yield = flt(doc.get("target_yield"))
    doc.material_required = quantity                                              # 979/992 = 98.7%
    doc.material_issued = _div(quantity, target_yield / 100) if target_yield > 0 else quantity   # 98.6%

    invisible_pct = flt(doc.get("invisible_lose_percentage"))
    # Waste % is what is left of the 100 % once yield and invisible loss are taken out
    # (73 % of the old rows follow this; the rest were set by an earlier version of the calculation).
    doc.target_waste_percentage = max(0.0, 100 - target_yield - invisible_pct) if target_yield > 0 else 0.0
    doc.target_waste = flt(doc.material_issued) * flt(doc.target_waste_percentage) / 100        # 99.8%
    doc.invisible_lose_qty = flt(doc.material_issued) * invisible_pct / 100                     # 99.7%

    # Machine requirement: 100 % of the rows that have target_ops (653/653); rows without it are 0.
    ops = flt(doc.get("target_ops"))
    doc.spindle_required = _div(SPINDLE_FACTOR * quantity, ops) if ops > 0 else 0.0
    doc.frame_required = doc.spindle_required / SPINDLES_PER_FRAME                              # 100%
    doc.per_shift_frame_required = doc.frame_required / SHIFTS_PER_DAY                          # 100%
    return doc


def bom_before_validate(doc, method=None):
    """doc_event: BOM.before_validate — runs before ERPNext's own BOM validation so the costs and
    stock_qty it derives from each item's qty see the blend-ratio quantities."""
    apply_bom(doc)
    items = doc.get("items") or []
    blends = [flt(i.get("blend_ratio")) for i in items]
    if doc.get("bom_type") == SPINNING and any(b > 0 for b in blends) and abs(sum(blends) - 100) > 0.01:
        frappe.msgprint(
            f"Blend ratios add up to {sum(blends):g} %, not 100 %. "
            "The item quantities were calculated from them as entered.",
            title="Blend ratio",
            indicator="orange",
        )


# ----------------------------------------------------------------------------- Work Order
def apply_work_order(doc):
    """Recompute the Work Order's calculated fields in place (Document or frappe._dict)."""
    qty = flt(doc.get("qty"))

    # A new Work Order starts from its BOM's targets (9,323 of 9,985 hik orders matched their BOM).
    bom_no = doc.get("bom_no")
    if bom_no and (not flt(doc.get("target_yield")) or not flt(doc.get("target_ops"))):
        bom = frappe.db.get_value(
            "BOM", bom_no, ["bom_type", "target_yield", "target_waste_percentage", "target_ops"], as_dict=True
        )
        if bom and bom.bom_type == SPINNING:
            for f in ("target_yield", "target_waste_percentage", "target_ops"):
                if not flt(doc.get(f)):
                    _set(doc, f, bom.get(f))

    target_yield = flt(doc.get("target_yield"))
    if target_yield > 0:
        doc.material_issued = _div(qty, target_yield / 100)                                  # 98.6%
    ops = flt(doc.get("target_ops"))
    if ops > 0:
        doc.spindle_required = _div(SPINDLE_FACTOR * qty, ops)                               # 99.9%
        # Planned frames: later hik orders (2022+, 84 %) use spindles / (480 x 3 shifts).
        doc.frame_required = doc.spindle_required / (SPINDLES_PER_FRAME * SHIFTS_PER_DAY)

    # What actually ran: 100 % agreement on the frame figures, 98.4 % on spindle_worked.
    actual_ops = flt(doc.get("actual_ops"))
    if actual_ops > 0:
        doc.spindle_worked = _div(SPINDLE_FACTOR * flt(doc.get("produced_qty")), actual_ops)
    if flt(doc.get("spindle_worked")) > 0:
        doc.actual_frame_required = doc.spindle_worked / SPINDLES_PER_FRAME
        doc.actual_per_shift_frame_required = doc.actual_frame_required / SHIFTS_PER_DAY
    return doc


def work_order_validate(doc, method=None):
    """doc_event: Work Order.validate."""
    if doc.meta.has_field("target_yield"):       # the custom fields exist only where hik's were added
        apply_work_order(doc)


# ----------------------------------------------------------------------------- self-test
def _close(a, b):
    return abs(flt(a) - flt(b)) <= max(0.0101, 1e-3 * abs(flt(b)))


def verify_against_data(limit=None):
    """Recompute every BOM and Work Order on this site with the rules above and report how many of
    the STORED (transferred) values each rule reproduces. Read-only.

        bench --site <site> execute micromax.mfg_logic.verify_against_data
    """
    out = {}

    def report(title, fields, rows, run):
        counts = {f: [0, 0] for f in fields}
        for original, recomputed in rows:
            run(recomputed)
            for f in fields:
                counts[f][1] += 1
                counts[f][0] += 1 if _close(recomputed.get(f), original.get(f)) else 0
        print(f"\n{title}  ({len(rows)} records)")
        for f, (ok, n) in counts.items():
            print(f"  {f:34} {ok:6}/{n:<6} {100 * ok / max(1, n):5.1f}%")
            out[f"{title}.{f}"] = (ok, n)

    bom_fields = ["target_yield", "material_required", "material_issued", "target_waste_percentage", "target_waste",
                  "invisible_lose_qty", "spindle_required", "frame_required", "per_shift_frame_required"]
    boms = frappe.get_all("BOM", filters={"bom_type": SPINNING}, fields=["name", "bom_type", "quantity", "target_ops",
                          "invisible_lose_percentage", *bom_fields], limit_page_length=limit or 0)
    item_rows = frappe.get_all("BOM Item", filters={"parenttype": "BOM", "parent": ["in", [b.name for b in boms]]},
                               fields=["parent", "qty", "blend_ratio", "item_yield", "gross_up_qty", "idx"],
                               order_by="parent, idx", limit_page_length=0)
    by_parent = {}
    for r in item_rows:
        by_parent.setdefault(r.parent, []).append(r)
    pairs = []
    for b in boms:
        its = [frappe._dict(i) for i in by_parent.get(b.name, [])]
        pairs.append((b, frappe._dict(b, items=its)))
    report("BOM header", bom_fields, pairs, apply_bom)

    item_pairs = []
    for b, recomputed in pairs:
        for orig, new in zip(by_parent.get(b.name, []), recomputed["items"]):
            item_pairs.append((orig, new))
    report("BOM Item", ["qty", "gross_up_qty"], item_pairs, lambda _new: None)

    wo_fields = ["material_issued", "spindle_required", "frame_required", "spindle_worked",
                 "actual_frame_required", "actual_per_shift_frame_required"]
    wos = frappe.get_all("Work Order", fields=["name", "qty", "produced_qty", "bom_no", "target_yield", "target_ops",
                         "target_waste_percentage", "actual_ops", "spindle_worked", *wo_fields], limit_page_length=limit or 0)
    report("Work Order", wo_fields, [(w, frappe._dict(w)) for w in wos], apply_work_order)
    return out
