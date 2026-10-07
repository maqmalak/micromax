"""Demo cheque books for each company bank account, linked to the real bank payments already in the books.

    bench --site <site> execute micromax.demo_cheques.generate --kwargs "{'company': '...'}"
    bench --site <site> execute micromax.demo_cheques.purge --kwargs "{'company': '...', 'confirm': 1}"

Per bank account: books of 25 leaves are filled in date order with the account's paid-out Payment Entries (each
payment's Cheque/Reference No becomes the leaf's number; Cleared when the bank cleared it, else Issued), with now and
then a spoiled leaf (Void) or a cheque returned unpaid (Dishonoured). The last book stays In Use, a fresh book is added
Unused, and one account gets a lost book (stop-payment on every leaf: Stopped, book Cancelled). The daily job (micromax.demo_daily) issues the next leaf for new payments.
"""

import random

import frappe
from frappe.utils import add_days, cint, getdate, now

SPOILED = ["Spoiled — wrong amount written", "Spoiled — overwriting", "Torn while printing", "Date mistake — reissued"]
REJECTED = ["Returned by bank — signature mismatch", "Returned by bank — stale cheque", "Returned — amount in words differs"]
LEAVES = 25


def _book(company, bank_account, posting_date, first_serial, n, leaves):
    doc = frappe.get_doc({"doctype": "Cheque Book", "company": company, "bank_account": bank_account, "posting_date": posting_date,
                          "issue_date": posting_date, "no_of_leaves": n, "from_serial": first_serial, "leaves": leaves})
    doc.flags.ignore_permissions = True
    doc.insert()
    return doc


def _clear_older(pe, rnd):
    """Cheques older than a week have been paid by the bank: clearance date 2–6 days after the cheque (what the Bank
    Clearance tool records) → leaf Cleared; recent ones stay Issued (outstanding cheques on the reconciliation)."""
    if not pe.clearance_date and getdate(pe.posting_date) < add_days(getdate(), -7):
        pe.clearance_date = min(add_days(pe.posting_date, rnd.randint(2, 6)), getdate())
        frappe.db.set_value("Payment Entry", pe.name, "clearance_date", pe.clearance_date, update_modified=False)
    return pe


def _issued_leaf(sno, cheque_no, pe):
    return {"sno": sno, "cheque_no": cheque_no, "status": "Cleared" if pe.clearance_date else "Issued", "issue_date": pe.posting_date, "party_type": pe.party_type,
            "party": pe.party, "party_name": pe.party_name, "amount": pe.paid_amount, "voucher_type": "Payment Entry",
            "voucher_no": pe.name, "clearance_date": pe.clearance_date}


def generate(company):
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    if not frappe.db.exists("DocType", "Cheque Book"):
        print("Cheque Book doctype missing — migrate mm_core first.")
        return
    rnd = random.Random(f"cheques|{company}")
    since = now()
    made = []
    accounts = frappe.get_all("Bank Account", {"company": company, "is_company_account": 1, "disabled": 0},
                              ["name", "account"], order_by="name")
    for idx, ba in enumerate(accounts):
        if frappe.db.exists("Cheque Book", {"bank_account": ba.name}):
            continue
        linked = set(frappe.get_all("Cheque Book Leaf", {"voucher_type": "Payment Entry", "voucher_no": ["is", "set"]}, pluck="voucher_no"))
        pes = [p for p in frappe.get_all("Payment Entry", {"company": company, "docstatus": 1, "payment_type": "Pay", "paid_from": ba.account},
                                         ["name", "posting_date", "party_type", "party", "party_name", "paid_amount", "clearance_date"],
                                         order_by="posting_date asc, name asc") if p.name not in linked]
        serial = 100001 + rnd.randint(1, 80) * 1000 + idx * 200000
        width = 6
        while pes:
            first = serial
            leaves, sno = [], 0
            book_date = add_days(pes[0].posting_date, -rnd.randint(2, 10))
            while sno < LEAVES:
                sno += 1
                no = str(serial).zfill(width)
                serial += 1
                r = rnd.random()
                if not pes:
                    leaves.append({"sno": sno, "cheque_no": no, "status": "Unused"})
                elif r < 0.06:
                    leaves.append({"sno": sno, "cheque_no": no, "status": "Void", "cancel_reason": rnd.choice(SPOILED)})
                elif r < 0.09:
                    leaves.append({"sno": sno, "cheque_no": no, "status": "Dishonoured", "cancel_reason": rnd.choice(REJECTED),
                                   "issue_date": pes[0].posting_date, "party_type": pes[0].party_type, "party": pes[0].party,
                                   "party_name": pes[0].party_name, "amount": pes[0].paid_amount})
                else:
                    pe = _clear_older(pes.pop(0), rnd)
                    leaves.append(_issued_leaf(sno, no, pe))
                    frappe.db.set_value("Payment Entry", pe.name, {"reference_no": no}, update_modified=False)
            b = _book(company, ba.name, book_date, str(first).zfill(width), LEAVES, leaves)
            made.append((b.name, ba.name, b.from_serial, b.to_serial, b.status, b.leaves_issued, b.leaves_in_hand, b.leaves_cancelled))
            frappe.db.commit()
        # a fresh book in the drawer
        b = _book(company, ba.name, add_days(getdate(), -rnd.randint(1, 20)), str(serial).zfill(width), LEAVES, [])
        made.append((b.name, ba.name, b.from_serial, b.to_serial, b.status, 0, b.leaves_in_hand, 0))
        serial += LEAVES
        if idx == 1:                                        # one lost book, cancelled leaf by leaf
            lost = [{"sno": i + 1, "cheque_no": str(serial + i).zfill(width), "status": "Stopped",
                     "cancel_reason": "Book lost — reported to the bank, leaves stopped"} for i in range(10)]
            b = _book(company, ba.name, add_days(getdate(), -rnd.randint(30, 60)), str(serial).zfill(width), 10, lost)
            made.append((b.name, ba.name, b.from_serial, b.to_serial, b.status, 0, 0, 10))
        frappe.db.commit()

    from micromax import demo_daily
    demo_daily._stamp_users(frappe._dict(rnd=rnd), since)
    for m in made:
        print(f"{m[0]}  {m[1]:<40} {m[2]}–{m[3]}  {m[4]:<9} issued {m[5]:>2}  in hand {m[6]:>2}  cancelled/rejected {m[7]:>2}")
    return made


def issue_for_new_payments(ctx, since):
    """Daily: bank payments made since `since` get the next in-hand leaf of their account's cheque book."""
    if not frappe.db.exists("DocType", "Cheque Book"):
        return
    for pe in frappe.get_all("Payment Entry", {"company": ctx.company, "docstatus": 1, "payment_type": "Pay", "creation": [">=", since]},
                             ["name", "posting_date", "party_type", "party", "party_name", "paid_amount", "paid_from", "clearance_date"]):
        if frappe.db.exists("Cheque Book Leaf", {"voucher_type": "Payment Entry", "voucher_no": pe.name}):
            continue
        book = frappe.db.get_value("Cheque Book", {"company": ctx.company, "account": pe.paid_from, "status": ["in", ["Unused", "In Use"]]}, "name",
                                   order_by="posting_date asc")
        if not book:
            continue
        leaf = frappe.db.get_value("Cheque Book Leaf", {"parent": book, "status": "Unused"}, ["name", "cheque_no"], as_dict=True,
                                   order_by="idx asc")
        if not leaf:
            continue
        frappe.db.set_value("Cheque Book Leaf", leaf.name, {"status": "Issued", "issue_date": pe.posting_date, "party_type": pe.party_type,
                                                           "party": pe.party, "party_name": pe.party_name, "amount": pe.paid_amount,
                                                           "voucher_type": "Payment Entry", "voucher_no": pe.name})
        frappe.db.set_value("Payment Entry", pe.name, {"reference_no": leaf.cheque_no}, update_modified=False)
        doc = frappe.get_doc("Cheque Book", book)
        doc.flags.ignore_permissions = True
        doc.save()
        ctx.bump("Cheque issued")


def purge(company, confirm=0):
    if not cint(confirm):
        print("Pass confirm=1 to delete the cheque books of this company.")
        return
    for name in frappe.get_all("Cheque Book", {"company": company}, pluck="name"):
        frappe.delete_doc("Cheque Book", name, force=True, ignore_permissions=True)
    frappe.db.commit()
    print(f"Cheque books removed for {company}")


def clear_existing(company):
    """Apply the 'older than a week is cleared' rule to cheque books generated before it existed."""
    rnd = random.Random(f"clear|{company}")
    for leaf in frappe.get_all("Cheque Book Leaf", {"status": "Issued", "voucher_type": "Payment Entry"}, ["name", "parent", "voucher_no"]):
        if frappe.db.get_value("Cheque Book", leaf.parent, "company") != company:
            continue
        pe = frappe.db.get_value("Payment Entry", leaf.voucher_no, ["name", "posting_date", "clearance_date"], as_dict=True)
        if not pe:
            continue
        _clear_older(pe, rnd)
        if pe.clearance_date:
            frappe.db.set_value("Cheque Book Leaf", leaf.name, {"status": "Cleared", "clearance_date": pe.clearance_date})
    for b in frappe.get_all("Cheque Book", {"company": company}, pluck="name"):
        d = frappe.get_doc("Cheque Book", b)
        d.flags.ignore_permissions = True
        d.save()
    frappe.db.commit()
    print(frappe.db.sql("select status, count(*) from `tabCheque Book Leaf` group by status"))


def clear_bank_entries(company, older_than_days=7, quiet=False):
    """The bank has processed everything older than a week: payments, receipts and bank journals on the company's bank
    accounts get a clearance date 1–5 days after posting (what Bank Clearance records); the last week stays uncleared
    (outstanding cheques / deposits in transit on the reconciliation). Cheque leaves of cleared payments → Cleared."""
    from frappe.utils import add_days, getdate, nowdate

    rnd = random.Random(f"clear|{company}|{nowdate()}")
    cutoff = add_days(getdate(nowdate()), -int(older_than_days))
    accounts = frappe.get_all("Bank Account", {"company": company, "is_company_account": 1, "account": ["is", "set"]}, pluck="account")
    if not accounts:
        return 0
    n = 0
    pes = frappe.db.sql("""select name, posting_date from `tabPayment Entry` where docstatus = 1 and company = %s and clearance_date is null
        and posting_date < %s and (paid_from in %s or paid_to in %s)""", (company, cutoff, accounts, accounts), as_dict=True)
    for pe in pes:
        d = min(add_days(pe.posting_date, rnd.randint(1, 5)), getdate(nowdate()))
        frappe.db.set_value("Payment Entry", pe.name, "clearance_date", d, update_modified=False)
        for leaf in frappe.get_all("Cheque Book Leaf", {"voucher_type": "Payment Entry", "voucher_no": pe.name, "status": "Issued"}, ["name", "parent"]) \
                if frappe.db.exists("DocType", "Cheque Book Leaf") else []:
            frappe.db.set_value("Cheque Book Leaf", leaf.name, {"status": "Cleared", "clearance_date": d})
        n += 1
    jes = frappe.db.sql("""select distinct j.name, j.posting_date from `tabJournal Entry` j join `tabJournal Entry Account` a on a.parent = j.name
        where j.docstatus = 1 and j.company = %s and j.clearance_date is null and j.posting_date < %s and a.account in %s""",
                        (company, cutoff, accounts), as_dict=True)
    for je in jes:
        d = min(add_days(je.posting_date, rnd.randint(1, 5)), getdate(nowdate()))
        frappe.db.set_value("Journal Entry", je.name, "clearance_date", d, update_modified=False)
        n += 1
    if frappe.db.exists("DocType", "Cheque Book"):
        for b in frappe.get_all("Cheque Book", {"company": company, "status": ["!=", "Cancelled"]}, pluck="name"):
            doc = frappe.get_doc("Cheque Book", b)
            doc.flags.ignore_permissions = True
            doc.save()
    frappe.db.commit()
    if not quiet:
        print(f"Cleared {len(pes)} payment entries and {len(jes)} journal entries older than {older_than_days} days")
    return n
