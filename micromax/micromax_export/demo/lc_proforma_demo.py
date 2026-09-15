"""
LC Proforma Demo Data Generator
Run via: bench --site <site-name> execute micromax.micromax_export.demo.lc_proforma_demo.create_demo_data
"""

import random
import frappe
from frappe.utils import today, add_days


@frappe.whitelist()
def create_demo_data():
	"""Create multiple LC Proforma demo records."""

	# Get or create company
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		company = frappe.get_all("Company", limit=1, pluck="name")[0]

	# Create demo customers if not exist
	customers = _get_or_create_customers()

	# Create demo items if not exist
	items = _get_or_create_items()

	# Demo data for LC Proformas
	demo_records = [
		{
			"customer": customers[0],
			"buyer_po_no": "PO-2026-001",
			"lc_no": "LC-1234567890",
			"lc_type": "Irrevocable",
			"lc_amount": 50000,
			"port_of_loading": "Chattogram",
			"port_of_discharge": "Rotterdam",
			"country_of_destination": "Netherlands",
			"shipment_mode": "Sea",
			"incoterm": "CIF",
			"proforma_date": add_days(today(), -10),
			"lc_expiry_date": add_days(today(), 50),
			"items": [
				{"item": items[0], "quantity": 1000, "rate": 12.50, "cartons": 50, "color": "Navy", "size": "L"},
				{"item": items[1], "quantity": 500, "rate": 15.00, "cartons": 25, "color": "White", "size": "M"},
			]
		},
		{
			"customer": customers[1],
			"buyer_po_no": "PO-2026-002",
			"lc_no": "LC-0987654321",
			"lc_type": "Irrevocable",
			"lc_amount": 75000,
			"port_of_loading": "Chattogram",
			"port_of_discharge": "Hamburg",
			"country_of_destination": "Germany",
			"shipment_mode": "Sea",
			"incoterm": "FOB",
			"proforma_date": add_days(today(), -5),
			"lc_expiry_date": add_days(today(), 55),
			"items": [
				{"item": items[2], "quantity": 800, "rate": 18.00, "cartons": 40, "color": "Black", "size": "XL"},
				{"item": items[3], "quantity": 1200, "rate": 22.00, "cartons": 60, "color": "Grey", "size": "M"},
			]
		},
		{
			"customer": customers[2],
			"buyer_po_no": "PO-2026-003",
			"lc_no": "LC-1122334455",
			"lc_type": "Irrevocable",
			"lc_amount": 100000,
			"port_of_loading": "Dhaka ICD",
			"port_of_discharge": "New York",
			"country_of_destination": "United States",
			"shipment_mode": "Sea",
			"incoterm": "CFR",
			"proforma_date": add_days(today(), -3),
			"lc_expiry_date": add_days(today(), 45),
			"items": [
				{"item": items[4], "quantity": 2000, "rate": 8.50, "cartons": 80, "color": "Olive", "size": "L"},
				{"item": items[0], "quantity": 1500, "rate": 12.50, "cartons": 75, "color": "Indigo", "size": "32"},
			]
		},
		{
			"customer": customers[3],
			"buyer_po_no": "PO-2026-004",
			"lc_no": "LC-5566778899",
			"lc_type": "Irrevocable",
			"lc_amount": 125000,
			"port_of_loading": "Chattogram",
			"port_of_discharge": "Valencia",
			"country_of_destination": "Spain",
			"shipment_mode": "Sea",
			"incoterm": "CIF",
			"proforma_date": add_days(today(), -1),
			"lc_expiry_date": add_days(today(), 60),
			"items": [
				{"item": items[1], "quantity": 3000, "rate": 15.00, "cartons": 150, "color": "White", "size": "S"},
				{"item": items[2], "quantity": 1000, "rate": 18.00, "cartons": 50, "color": "Black", "size": "L"},
			]
		},
		{
			"customer": customers[4],
			"buyer_po_no": "PO-2026-005",
			"lc_no": "",
			"lc_type": "",
			"lc_amount": 35000,
			"port_of_loading": "Chattogram",
			"port_of_discharge": "Jebel Ali",
			"country_of_destination": "United Arab Emirates",
			"shipment_mode": "Air",
			"incoterm": "CPT",
			"proforma_date": today(),
			"lc_expiry_date": add_days(today(), 30),
			"items": [
				{"item": items[3], "quantity": 600, "rate": 22.00, "cartons": 30, "color": "Charcoal", "size": "M"},
				{"item": items[4], "quantity": 400, "rate": 8.50, "cartons": 20, "color": "Beige", "size": "L"},
			]
		},
		{
			"customer": customers[0],
			"buyer_po_no": "PO-2026-006",
			"lc_no": "LC-9988776655",
			"lc_type": "Irrevocable",
			"lc_amount": 200000,
			"port_of_loading": "Chattogram",
			"port_of_discharge": "Rotterdam",
			"country_of_destination": "Netherlands",
			"shipment_mode": "Sea",
			"incoterm": "FOB",
			"proforma_date": add_days(today(), -7),
			"lc_expiry_date": add_days(today(), 70),
			"items": [
				{"item": items[0], "quantity": 5000, "rate": 12.50, "cartons": 250, "color": "Navy", "size": "34"},
				{"item": items[1], "quantity": 4000, "rate": 15.00, "cartons": 200, "color": "White", "size": "M"},
				{"item": items[2], "quantity": 2000, "rate": 18.00, "cartons": 100, "color": "Black", "size": "XL"},
			]
		},
		{
			"customer": customers[1],
			"buyer_po_no": "PO-2026-007",
			"lc_no": "LC-3344556677",
			"lc_type": "Irrevocable",
			"lc_amount": 80000,
			"port_of_loading": "Dhaka ICD",
			"port_of_discharge": "Hamburg",
			"country_of_destination": "Germany",
			"shipment_mode": "Sea",
			"incoterm": "CIF",
			"proforma_date": add_days(today(), -12),
			"lc_expiry_date": add_days(today(), 40),
			"items": [
				{"item": items[3], "quantity": 1500, "rate": 22.00, "cartons": 75, "color": "Dark Grey", "size": "L"},
				{"item": items[4], "quantity": 2500, "rate": 8.50, "cartons": 100, "color": "Sky Blue", "size": "S"},
			]
		},
	]

	created = []
	for data in demo_records:
		doc = _create_proforma(data, company)
		created.append(doc.name)

	frappe.db.commit()
	frappe.msgprint(f"Created {len(created)} LC Proforma records: {', '.join(created)}")
	return created


def _create_proforma(data, company):
	"""Create a single LC Proforma."""
	doc = frappe.new_doc("LC Proforma")
	doc.proforma_date = data["proforma_date"]
	doc.company = company
	doc.customer = data["customer"]
	doc.buyer_po_no = data["buyer_po_no"]
	doc.currency = "USD"
	doc.exchange_rate = 117.50

	# LC Details
	doc.lc_required = 1 if data["lc_no"] else 0
	doc.lc_no = data["lc_no"]
	doc.lc_date = add_days(data["proforma_date"], 5) if data["lc_no"] else None
	doc.lc_type = data["lc_type"]
	doc.lc_issuing_bank = _get_bank(data["customer"]) if data["lc_no"] else None
	doc.lc_advising_bank = "Eastern Bank Limited" if data["lc_no"] else None
	doc.lc_confirming_bank = "HSBC Bank" if data["lc_no"] and data["lc_amount"] > 100000 else None
	doc.lc_amount = data["lc_amount"]
	doc.lc_currency = "USD"
	doc.lc_expiry_date = data["lc_expiry_date"]
	doc.lc_expiry_place = "Beneficiary's Country"
	doc.latest_shipment_date = add_days(data["proforma_date"], 30)
	doc.partial_shipment_allowed = 1
	doc.transshipment_allowed = 0 if data["shipment_mode"] == "Air" else 1

	# Shipment
	doc.port_of_loading = data["port_of_loading"]
	doc.port_of_discharge = data["port_of_discharge"]
	doc.final_destination = data["port_of_discharge"]
	doc.country_of_destination = data["country_of_destination"]
	doc.shipment_mode = data["shipment_mode"]
	doc.incoterm = data["incoterm"]

	# Check if payment terms exists, otherwise leave blank
	if frappe.db.exists("Payment Terms Template", "LC at Sight"):
		doc.payment_terms = "LC at Sight"

	# Banking
	doc.beneficiary_bank = "Eastern Bank Limited"
	doc.swift_code = "EBLDBDDH"
	doc.bank_branch = "Principal Branch, Dhaka"
	doc.correspondent_bank = "Citibank N.A., New York"

	# Items
	for it in data["items"]:
		item_doc = frappe.get_doc("Item", it["item"])
		doc.append("lc_proforma_items", {
			"item": it["item"],
			"item_name": item_doc.item_name,
			"description": item_doc.description or f"Export quality {item_doc.item_name}",
			"buyer_style_no": f"BS-{random.randint(1000, 9999)}",
			"style_no": f"ST-{random.randint(10000, 99999)}",
			"color": it["color"],
			"size": it["size"],
			"hs_code": _get_hs_code(it["item"]),
			"country_of_origin": "Bangladesh",
			"quantity": it["quantity"],
			"uom": "Nos",
			"rate": it["rate"],
			"net_weight": round(random.uniform(0.2, 0.8), 2),
			"gross_weight": round(random.uniform(0.25, 0.9), 2),
			"cartons": it["cartons"],
		})

	doc.insert(ignore_permissions=True)
	return doc


def _get_or_create_customers():
	"""Get or create demo customers."""
	names = [
		"Demo Buyer - Europe Fashion BV",
		"Demo Buyer - H&M Bangladesh",
		"Demo Buyer - PVH Corp",
		"Demo Buyer - Inditex Spain",
		"Demo Buyer - Sharaf DG",
	]
	# Get a valid non-group customer group
	customer_group = frappe.get_all("Customer Group", filters={"is_group": 0}, limit=1, pluck="name")
	customer_group = customer_group[0] if customer_group else None

	result = []
	for name in names:
		if not frappe.db.exists("Customer", name):
			c = frappe.new_doc("Customer")
			c.customer_name = name  # Use full name as display name
			c.name = name  # Set the ID
			c.customer_type = "Company"
			if customer_group:
				c.customer_group = customer_group
			c.territory = "All Territories"
			c.insert(ignore_permissions=True, ignore_if_duplicate=True)
		result.append(name)
	return result


def _get_or_create_items():
	"""Get or create demo items."""
	items_data = [
		("Demo Item - Men's Denim Jeans", "Men's Denim Jeans", "62034200"),
		("Demo Item - Women's Chino Pants", "Women's Chino Pants", "62046200"),
		("Demo Item - Men's Bomber Jacket", "Men's Bomber Jacket", "62014300"),
		("Demo Item - Cargo Pants", "Cargo Pants", "62029300"),
		("Demo Item - Basic T-Shirt", "Basic T-Shirt", "61091000"),
	]
	# Get a valid non-group item group
	item_group = frappe.get_all("Item Group", filters={"is_group": 0}, limit=1, pluck="name")
	item_group = item_group[0] if item_group else None

	result = []
	for name, item_name, hs in items_data:
		if not frappe.db.exists("Item", name):
			item = frappe.new_doc("Item")
			item.item_name = name  # Use full name as display name
			item.name = name  # Set the ID
			item.item_code = name
			if item_group:
				item.item_group = item_group
			item.stock_uom = "Nos"
			item.is_stock_item = 0
			item.description = f"Export quality {item_name}"
			item.insert(ignore_permissions=True, ignore_if_duplicate=True)
		result.append(name)
	return result


def _get_bank(customer):
	"""Get bank name based on customer."""
	banks = {
		"Demo Buyer - Europe Fashion BV": "ING Bank N.V.",
		"Demo Buyer - H&M Bangladesh": "Commerzbank AG",
		"Demo Buyer - PVH Corp": "JPMorgan Chase Bank",
		"Demo Buyer - Inditex Spain": "Banco Santander",
		"Demo Buyer - Sharaf DG": "Emirates NBD",
	}
	return banks.get(customer, "Standard Chartered Bank")


def _get_hs_code(item_name):
	"""Get HS code for item."""
	hs_codes = {
		"Demo Item - Men's Denim Jeans": "62034200",
		"Demo Item - Women's Chino Pants": "62046200",
		"Demo Item - Men's Bomber Jacket": "62014300",
		"Demo Item - Cargo Pants": "62029300",
		"Demo Item - Basic T-Shirt": "61091000",
	}
	return hs_codes.get(item_name, "62000000")


@frappe.whitelist()
def clear_demo_data():
	"""Remove all demo LC Proforma records."""
	# Find demo items
	demo_items = frappe.get_all("Item", filters={"name": ("like", "Demo Item -%")}, pluck="name")
	if demo_items:
		proformas = frappe.get_all("LC Proforma Item", filters={"item": ("in", demo_items)}, pluck="parent")
		proformas = list(set(proformas))
		for name in proformas:
			try:
				frappe.delete_doc("LC Proforma", name, ignore_permissions=True)
			except:
				pass
		frappe.msgprint(f"Deleted {len(proformas)} LC Proforma records")

	# Delete demo customers
	demo_customers = frappe.get_all("Customer", filters={"name": ("like", "Demo Buyer -%")}, pluck="name")
	for name in demo_customers:
		try:
			frappe.delete_doc("Customer", name, ignore_permissions=True)
		except:
			pass

	# Delete demo items
	for name in demo_items:
		try:
			frappe.delete_doc("Item", name, ignore_permissions=True)
		except:
			pass

	frappe.msgprint("Demo data cleared")
