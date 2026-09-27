"""Temporary diagnostic helpers for the POS Awesome installation.

Usage:
    bench --site micromaxerp execute micromax.posa_diag.verify
    bench --site micromaxerp execute micromax.posa_diag.scan_fixture_doctypes

Delete this file once the installation is verified.
"""

import json
from collections import Counter

import frappe


def verify():
	"""Report the state of the Sales Order fields and posawesome fixtures."""
	print("### posawesome on site:", "posawesome" in frappe.get_installed_apps())

	meta = frappe.get_meta("Sales Order", cached=False)
	counts = Counter(d.fieldname for d in meta.fields if d.fieldname)
	dups = {k: v for k, v in counts.items() if v > 1}
	print("### duplicate Sales Order fieldnames:", dups or "none")

	print(
		"### custom field 'Sales Order-incoterm' exists:",
		bool(frappe.db.exists("Custom Field", "Sales Order-incoterm")),
	)
	print("### incoterm meta rows:", [(d.idx, d.fieldtype) for d in meta.fields if d.fieldname == "incoterm"])
	print("### column present:", bool(frappe.db.sql("show columns from `tabSales Order` like 'incoterm'")))

	rows = frappe.get_all(
		"Custom Field",
		filters={"dt": ["in", ["Sales Order", "Sales Order Item", "POS Profile", "POS Invoice"]]},
		fields=["name", "dt", "fieldname"],
		limit_page_length=0,
	)
	print("### custom fields on SO/SO Item/POS Profile/POS Invoice:", len(rows))
	so_cf = sorted({r.fieldname for r in rows if r.dt == "Sales Order"})
	print("### Sales Order custom fieldnames:", so_cf)

	# duplicate custom fieldnames across the whole Custom Field table
	all_cf = frappe.get_all("Custom Field", fields=["dt", "fieldname"], limit_page_length=0)
	seen = Counter((r.dt, r.fieldname) for r in all_cf)
	dups_cf = {f"{k[0]} :: {k[1]}": v for k, v in seen.items() if v > 1}
	print("### duplicate Custom Field (dt, fieldname) rows:", dups_cf or "none")

	print("### patch log entries:", frappe.db.count("Patch Log", {"patch": ["like", "posawesome%"]}))
	print("### workspace 'POS Awesome':", bool(frappe.db.exists("Workspace", "POS Awesome")))
	print("### POS Profile count:", frappe.db.count("POS Profile"))
	print("### installed app version:", frappe.get_attr("posawesome.__version__") if hasattr(frappe.get_module("posawesome"), "__version__") else "n/a")


def check_page():
	"""Confirm the POS Awesome Desk Pages and workspace are present."""
	frappe.connect()
	for page in ("posapp", "pos"):
		print(f"### Page '{page}' exists:", bool(frappe.db.exists("Page", page)))
		if frappe.db.exists("Page", page):
			row = frappe.db.get_value("Page", page, ["title", "module", "standard"], as_dict=True)
			print("    ", row)
	print("### Workspace 'POS Awesome' exists:", bool(frappe.db.exists("Workspace", "POS Awesome")))
	print("### POS Awesome roles:", frappe.get_all("Role", filters={"name": ["like", "POS Awesome%"]}, pluck="name"))
	print(
		"### POS Awesome doctypes:",
		frappe.db.count("DocType", {"module": "POS Awesome"}),
	)
	print("### desktop icon:", bool(frappe.db.exists("Desktop Icon", {"label": "POS Awesome"})))


def compare_removed_with_standard():
	"""Compare the removed Custom Fields against the standard DocFields.

	The removed custom fields duplicated standard ERPNext v16 fields. Confirm the
	standard definition can carry the already-stored data (same fieldtype/options).
	"""
	frappe.connect()
	import json as _json

	backup = _json.load(open("/tmp/posa_backup_custom_fields.json"))
	for cf in backup:
		dt, fn = cf["dt"], cf["fieldname"]
		std = frappe.db.sql(
			"select fieldtype, options, label from `tabDocField` where parent=%s and fieldname=%s",
			(dt, fn),
			as_dict=True,
		)
		print(f"### {dt}.{fn}")
		print(f"    removed custom field : type={cf['fieldtype']} options={cf.get('options')!r} label={cf.get('label')!r}")
		print(f"    standard DocField    : {std}")


def deep_check():
	"""Print the exact rows behind the 'incoterm appears multiple times' error."""
	frappe.connect()
	print("### DocField rows (Sales Order / incoterm):")
	for r in frappe.db.sql(
		"""select name, idx, fieldtype, options, label from `tabDocField`
		where parent='Sales Order' and fieldname='incoterm' order by idx""",
		as_dict=True,
	):
		print("   ", r)
	print("### Custom Field rows (dt=Sales Order / fieldname=incoterm):")
	for r in frappe.db.sql(
		"""select name, idx, fieldtype, options, insert_after from `tabCustom Field`
		where dt='Sales Order' and fieldname='incoterm'""",
		as_dict=True,
	):
		print("   ", r)
	print("### tabSales Order incoterm values:")
	for r in frappe.db.sql(
		"""select ifnull(incoterm,'') as v, count(*) as c from `tabSales Order`
		group by ifnull(incoterm,'') order by c desc""",
		as_dict=True,
	):
		print("   ", r)
	has_inc = frappe.db.exists("DocType", "Incoterm")
	print("### Incoterm doctype:", has_inc, "records:", frappe.db.count("Incoterm") if has_inc else 0)
	if has_inc:
		print("   ", frappe.get_all("Incoterm", pluck="name", limit_page_length=30))
	print("### posawesome fixture SO custom fields:")
	with open("/home/maqmalak/erpnext-react/apps/posawesome/posawesome/fixtures/custom_field.json") as f:
		declared = json.load(f)
	so = [(r.get("fieldname"), r.get("dt"), r.get("fieldtype")) for r in declared if r.get("dt") == "Sales Order"]
	print("   ", so)


def scan_all_duplicates():
	"""Every Custom Field whose fieldname collides with a standard DocField.

	This is the exact class of problem that throws
	"Fieldname <x> appears multiple times in rows".
	"""
	frappe.connect()
	rows = frappe.db.sql(
		"""select cf.name, cf.dt, cf.fieldname, cf.fieldtype, df.fieldtype as std_fieldtype
		from `tabCustom Field` cf
		inner join `tabDocField` df on df.parent = cf.dt and df.fieldname = cf.fieldname
		order by cf.dt, cf.fieldname""",
		as_dict=True,
	)
	print("### custom fields colliding with standard fields:", len(rows))
	for r in rows:
		print("   ", r)


def fix_collisions():
	"""Delete Custom Fields that duplicate a standard DocField of the same name.

	ERPNext v16 promoted fields that older benches had as Custom Fields (e.g.
	Item.country_of_origin, Sales Order.incoterm). Custom Field validation then
	raises "Fieldname <x> appears multiple times in rows" and blocks fixtures
	from importing. The DB column is kept, so existing data is preserved.

	A JSON backup of every deleted Custom Field is written to /tmp first.
	"""
	import frappe as _frappe

	frappe.connect()
	rows = frappe.db.sql(
		"""select cf.name, cf.dt, cf.fieldname, df.fieldtype as std_fieldtype
		from `tabCustom Field` cf
		inner join `tabDocField` df on df.parent = cf.dt and df.fieldname = cf.fieldname
		order by cf.dt, cf.fieldname""",
		as_dict=True,
	)
	if not rows:
		print("### no colliding custom fields")
		return

	backup = []
	for r in rows:
		doc = frappe.get_doc("Custom Field", r.name)
		backup.append(doc.as_dict())
		print(f"### removing {r.name} (standard field {r.dt}.{r.fieldname} is {r.std_fieldtype})")
		frappe.delete_doc("Custom Field", r.name, force=True)

	with open("/tmp/posa_backup_custom_fields.json", "w") as f:
		f.write(_frappe.as_json(backup, indent=1))
	frappe.db.commit()
	print(f"### deleted {len(rows)} custom fields, backup -> /tmp/posa_backup_custom_fields.json")

	for r in rows:
		meta = frappe.get_meta(r.dt, cached=False)
		left = [d.idx for d in meta.fields if d.fieldname == r.fieldname]
		print(f"### {r.dt}.{r.fieldname} meta rows now: {left}")


def check_columns():
	"""Verify the DB columns still exist after the custom fields were removed."""
	frappe.connect()
	pairs = [
		("Employee Checkin", "latitude"),
		("Employee Checkin", "location_section"),
		("Employee Checkin", "longitude"),
		("Item", "country_of_origin"),
		("Payroll Entry", "grade"),
		("Sales Order", "incoterm"),
		("Stock Entry", "cost_center"),
		("Travel Request", "company"),
	]
	for dt, fn in pairs:
		table = f"tab{dt}"
		cols = frappe.db.sql(f"show columns from `{table}` like %s", fn, as_dict=True)
		non_null = None
		if cols:
			non_null = frappe.db.sql(f"select count(*) from `{table}` where `{fn}` is not null and `{fn}` != ''")[0][0]
		print(f"### {dt}.{fn}: column={'yes' if cols else 'NO'} type={cols[0]['Type'] if cols else '-'} rows_with_value={non_null}")


def check_posa_custom_fields_applied():
	"""Confirm the custom fields posawesome's fixture file declares are present."""
	path = "/home/maqmalak/erpnext-react/apps/posawesome/posawesome/fixtures"
	with open(f"{path}/custom_field.json") as f:
		declared = json.load(f)
	missing = []
	for row in declared:
		name = row.get("name") or f"{row['dt']}-{row['fieldname']}"
		if not frappe.db.exists("Custom Field", name):
			missing.append(name)
	print(f"### declared: {len(declared)}  missing: {len(missing)}")
	if missing:
		print("### missing:", missing[:40])
