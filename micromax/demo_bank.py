"""Bank statement → Bank Transactions → bank reconciliation, for the demo company.

    bench --site <site> execute micromax.demo_bank.run --kwargs "{'company': '...', 'bank_account': '...', 'from_date': '2026-09-01', 'to_date': '2026-10-07'}"

1. make_statement: a CSV statement the way the bank sends it — every voucher on the bank account that the bank has
   processed (clearance date in the period) on its value date, with the cheque / reference no, withdrawal, deposit and
   running balance; plus bank-side items the books don't have yet (service charges, cheque book fee, SMS alerts).
   Uncleared cheques / deposits are not on it (they haven't reached the bank). Attached as a private File.
2. import_statement: ERPNext Bank Statement Import of that CSV (Bank Transactions, submitted).
3. reconcile: ERPNext auto reconciliation (matches by reference no + amount); bank charges with no voucher are booked
   as a Bank Entry against Bank Charges and reconciled (what the Bank Reconciliation Tool's "Create Voucher" does).
"""

import csv
import io
import json
import random

import frappe
from frappe.utils import add_days, flt, get_last_day, getdate, nowdate

HEADERS = ["Date", "Description", "Reference Number", "Withdrawal", "Deposit", "Balance"]


def _bank_rows(company, account, from_date, to_date):
    """Vouchers on the account cleared within the period: (value date, description, reference, withdrawal, deposit)."""
    rows = []
    for p in frappe.db.sql("""select name, clearance_date, reference_no, party_name, paid_from, paid_to, paid_amount, received_amount, payment_type
            from `tabPayment Entry` where docstatus = 1 and company = %s and (paid_from = %s or paid_to = %s)
            and clearance_date between %s and %s""", (company, account, account, from_date, to_date), as_dict=True):
        out = p.paid_from == account
        what = ("CHQ PAID" if (p.reference_no or "").isdigit() else "TRANSFER OUT") if out else "DEPOSIT / CLEARING"
        rows.append((p.clearance_date, f"{what} {p.party_name or ''}".strip(), p.reference_no or p.name,
                     flt(p.paid_amount) if out else 0, 0 if out else flt(p.received_amount)))
    for j in frappe.db.sql("""select j.name, j.clearance_date, j.cheque_no, j.user_remark, sum(a.credit_in_account_currency) as cr,
            sum(a.debit_in_account_currency) as dr from `tabJournal Entry` j join `tabJournal Entry Account` a on a.parent = j.name
            where j.docstatus = 1 and j.company = %s and a.account = %s and j.clearance_date between %s and %s group by j.name""",
                           (company, account, from_date, to_date), as_dict=True):
        rows.append((j.clearance_date, ("SALARY / TRANSFER " if j.cr else "CREDIT ") + (j.user_remark or "")[:40], j.cheque_no or j.name,
                     flt(j.cr), flt(j.dr)))
    return rows


def _opening(company, account, from_date):
    """Bank's balance at the start: book balance before the period, less what the bank hadn't processed by then."""
    book = flt(frappe.db.sql("""select sum(debit - credit) from `tabGL Entry` where account = %s and company = %s and is_cancelled = 0
        and posting_date < %s""", (account, company, from_date))[0][0])
    pend_out = flt(frappe.db.sql("""select sum(paid_amount) from `tabPayment Entry` where docstatus = 1 and company = %s and paid_from = %s
        and posting_date < %s and (clearance_date is null or clearance_date >= %s)""", (company, account, from_date, from_date))[0][0])
    pend_in = flt(frappe.db.sql("""select sum(received_amount) from `tabPayment Entry` where docstatus = 1 and company = %s and paid_to = %s
        and posting_date < %s and (clearance_date is null or clearance_date >= %s)""", (company, account, from_date, from_date))[0][0])
    return flt(book + pend_out - pend_in, 2)


def make_statement(company, bank_account, from_date, to_date):
    account = frappe.db.get_value("Bank Account", bank_account, "account")
    from_date, to_date = getdate(from_date), getdate(to_date)
    rnd = random.Random(f"stmt|{bank_account}|{from_date}")
    rows = _bank_rows(company, account, from_date, to_date)
    # bank-side items not in the books yet
    d = from_date
    while d <= to_date:
        end = min(get_last_day(d), to_date)
        rows.append((end, "SERVICE CHARGES — MONTHLY MAINTENANCE", f"SC{end:%y%m}", flt(rnd.choice([350, 500, 750])), 0))
        rows.append((end, "SMS ALERT CHARGES", f"SMS{end:%y%m}", 150.0, 0))
        d = add_days(end, 1)
    rows.append((add_days(from_date, rnd.randint(3, 10)), "CHEQUE BOOK ISSUANCE FEE (25 LEAVES)", "CBF001", 1500.0, 0))
    rows.sort(key=lambda r: (r[0], -r[4]))
    bal = _opening(company, account, from_date)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADERS)
    for date, desc, ref, wd, dep in rows:
        bal = flt(bal - wd + dep, 2)
        w.writerow([date.strftime("%Y-%m-%d"), desc, ref, f"{wd:.2f}" if wd else "", f"{dep:.2f}" if dep else "", f"{bal:.2f}"])
    name = f"statement-{frappe.scrub(bank_account)}-{from_date:%Y%m%d}-{to_date:%Y%m%d}.csv"
    f = frappe.get_doc({"doctype": "File", "file_name": name, "is_private": 1, "content": buf.getvalue().encode(),
                        "attached_to_doctype": "Bank Account", "attached_to_name": bank_account})
    f.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Statement {f.file_url}: {len(rows)} lines, closing balance {bal:,.2f}")
    return f.file_url, len(rows), bal


def import_statement(company, bank_account, file_url):
    from erpnext.accounts.doctype.bank_statement_import.bank_statement_import import start_import

    bank = frappe.db.get_value("Bank Account", bank_account, "bank")
    bsi = frappe.get_doc({"doctype": "Bank Statement Import", "company": company, "bank_account": bank_account, "bank": bank,
                          "reference_doctype": "Bank Transaction", "import_type": "Insert New Records", "submit_after_import": 1,
                          "mute_emails": 1, "import_file": file_url,
                          "template_options": json.dumps({"column_to_field_map": {"5": "Don't Import"}})})
    bsi.insert(ignore_permissions=True)
    frappe.db.commit()
    before = frappe.db.count("Bank Transaction", {"bank_account": bank_account})
    # the same function ERPNext's background job runs, called directly
    start_import(bsi.name, bank_account, file_url, None, bank, bsi.template_options)
    frappe.db.commit()
    created = frappe.db.count("Bank Transaction", {"bank_account": bank_account}) - before
    print(f"Bank Statement Import {bsi.name}: {frappe.db.get_value('Bank Statement Import', bsi.name, 'status')}, {created} bank transactions")
    return bsi.name, created


def reconcile(company, bank_account, from_date, to_date):
    from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import (
        create_journal_entry_bts, get_bank_transactions, start_auto_reconcile)

    txns = get_bank_transactions(bank_account)
    # look back a month: cheques written before the statement period are presented during it
    start_auto_reconcile(txns, add_days(getdate(from_date), -30), to_date, None, None, None)
    frappe.db.commit()
    # bank charges: no voucher in the books → book a Bank Entry against Bank Charges and reconcile it
    charges = frappe.db.get_value("Account", {"company": company, "is_group": 0, "account_name": ["like", "%Bank Charges%"]}, "name") \
        or frappe.db.get_value("Company", company, "round_off_account")
    booked = 0
    for t in frappe.get_all("Bank Transaction", {"bank_account": bank_account, "docstatus": 1, "status": "Unreconciled",
                                                  "withdrawal": [">", 0], "description": ["like", "%CHARGE%"]}, ["name", "date", "reference_number"]):
        create_journal_entry_bts(t.name, t.reference_number, t.date, t.date, "Bank Entry", charges, allow_edit=False)
        booked += 1
    for t in frappe.get_all("Bank Transaction", {"bank_account": bank_account, "docstatus": 1, "status": "Unreconciled",
                                                  "withdrawal": [">", 0], "description": ["like", "%FEE%"]}, ["name", "date", "reference_number"]):
        create_journal_entry_bts(t.name, t.reference_number, t.date, t.date, "Bank Entry", charges, allow_edit=False)
        booked += 1
    frappe.db.commit()
    stats = dict(frappe.db.sql("select status, count(*) from `tabBank Transaction` where bank_account = %s and docstatus = 1 group by status",
                               bank_account))
    print(f"Reconciliation: {stats}; bank charges booked and reconciled: {booked}")
    return stats


def unclear_statement_vouchers(company, bank_account, from_date, to_date):
    """Before reconciling, the vouchers on the statement are uncleared (as they are until the bank confirms them);
    reconciliation then sets their clearance date from the bank transaction."""
    account = frappe.db.get_value("Bank Account", bank_account, "account")
    pes = frappe.db.sql("""select name from `tabPayment Entry` where docstatus = 1 and company = %s and (paid_from = %s or paid_to = %s)
        and clearance_date between %s and %s""", (company, account, account, from_date, to_date), pluck=True)
    jes = frappe.db.sql("""select distinct j.name from `tabJournal Entry` j join `tabJournal Entry Account` a on a.parent = j.name
        where j.docstatus = 1 and j.company = %s and a.account = %s and j.clearance_date between %s and %s""",
                        (company, account, from_date, to_date), pluck=True)
    for n in pes:
        frappe.db.set_value("Payment Entry", n, "clearance_date", None, update_modified=False)
    for n in jes:
        frappe.db.set_value("Journal Entry", n, "clearance_date", None, update_modified=False)
    frappe.db.commit()
    return len(pes) + len(jes)


def reset(company, bank_account):
    """Remove imported bank transactions of the account (and the bank-charge entries created from them) to start over."""
    for t in frappe.get_all("Bank Transaction", {"bank_account": bank_account, "docstatus": ["<", 2]}, ["name", "docstatus"]):
        jes = frappe.get_all("Bank Transaction Payments", {"parent": t.name, "payment_document": "Journal Entry"}, pluck="payment_entry")
        doc = frappe.get_doc("Bank Transaction", t.name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc("Bank Transaction", t.name, force=True, ignore_permissions=True)
        for je in jes:                                  # only the bank-charge entries created from the statement
            jd = frappe.get_doc("Journal Entry", je)
            if (jd.cheque_no or "").startswith(("SC", "SMS", "CBF")):
                if jd.docstatus == 1:
                    jd.cancel()
                frappe.delete_doc("Journal Entry", je, force=True, ignore_permissions=True)
    for b in frappe.get_all("Bank Statement Import", {"bank_account": bank_account}, pluck="name"):
        frappe.delete_doc("Bank Statement Import", b, force=True, ignore_permissions=True)
    frappe.db.commit()
    print(f"Reset bank transactions for {bank_account}")


def run(company, bank_account, from_date, to_date=None):
    frappe.set_user("Administrator")
    frappe.flags.mute_emails = True
    if not frappe.db.exists("Bank Account", {"name": bank_account, "company": company}):
        names = frappe.get_all("Bank Account", {"company": company, "is_company_account": 1}, pluck="name")
        frappe.throw(f"Bank Account {bank_account!r} not found for {company}. Use one of: {', '.join(names) or 'none'}")
    to_date = to_date or nowdate()
    file_url, lines, closing = make_statement(company, bank_account, from_date, to_date)
    bsi, created = import_statement(company, bank_account, file_url)
    print(f"Vouchers on the statement waiting for reconciliation: {unclear_statement_vouchers(company, bank_account, from_date, to_date)}")
    stats = reconcile(company, bank_account, from_date, to_date)
    from mm_core.cheques import sync_cleared_status

    sync_cleared_status()
    return {"file": file_url, "lines": lines, "closing": closing, "import": bsi, "created": created, "status": stats}
