// Copyright (c) 2026, MicroMax and contributors
// For license information, please see license.txt

frappe.query_reports["LC Proforma Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "buyer", label: __("Buyer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "lc_status",
			label: __("LC Status"),
			fieldtype: "Select",
			options: [
				"",
				"Draft",
				"Submitted",
				"Buyer Approval",
				"LC Requested",
				"LC Received",
				"Confirmed",
				"Closed",
			],
		},
		{ fieldname: "lc_expiry_date", label: __("LC Expiry Date"), fieldtype: "Date" },
		{ fieldname: "export_order", label: __("Export Order"), fieldtype: "Link", options: "Sales Order" },
	],
};