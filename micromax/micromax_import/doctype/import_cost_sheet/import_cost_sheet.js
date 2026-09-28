// Pakistan import costing: landed cost = purchase value + capitalised charges. Sales tax at import (s.7 STA)
// and income tax u/s 148 ITO are adjustable taxes, so they are shown but NOT added to landed cost.
const COST_FIELDS = [
	"purchase_value",
	"freight",
	"insurance",
	"customs_duty",
	"additional_duty",
	"regulatory_duty",
	"clearing_charges",
	"port_charges",
	"other_charges",
];
const TRIGGER_FIELDS = COST_FIELDS.concat(["sales_tax", "income_tax_148"]);
const LANDING_CHARGE_RATE = 0.01;

frappe.ui.form.on("Import Cost Sheet Item", {
	setup: function (frm) {},
	refresh: function (frm) {},

	item: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item) return;
		frappe.db.get_value("Item", row.item, ["item_name", "stock_uom"], (r) => {
			if (!r) return;
			frappe.model.set_value(cdt, cdn, "item_name", r.item_name);
			if (r.stock_uom && !row.uom) frappe.model.set_value(cdt, cdn, "uom", r.stock_uom);
		});
	},

	quantity: function (frm, cdt, cdn) {
		frm.script_manager.trigger("calc", cdt, cdn);
	},

	calc: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		let total = 0;
		COST_FIELDS.forEach((c) => (total += flt(row[c])));
		frappe.model.set_value(cdt, cdn, "assessable_value",
			(flt(row.purchase_value) + flt(row.freight) + flt(row.insurance)) * (1 + LANDING_CHARGE_RATE));
		frappe.model.set_value(cdt, cdn, "total_landed_cost", total);
		const qty = flt(row.quantity);
		frappe.model.set_value(cdt, cdn, "landed_cost_per_unit", qty ? total / qty : 0);
		frm.trigger("calc_totals");
	},
});

TRIGGER_FIELDS.forEach((f) => {
	frappe.ui.form.on("Import Cost Sheet Item", {
		[f]: function (frm, cdt, cdn) {
			frm.script_manager.trigger("calc", cdt, cdn);
		},
	});
});

frappe.ui.form.on("Import Cost Sheet", {
	calc_totals: function (frm) {
		let purchase = 0, landed = 0, duties = 0, salesTax = 0, incomeTax = 0;
		(frm.doc.import_cost_sheet_items || []).forEach((row) => {
			purchase += flt(row.purchase_value);
			landed += flt(row.total_landed_cost);
			duties += flt(row.customs_duty) + flt(row.additional_duty) + flt(row.regulatory_duty);
			salesTax += flt(row.sales_tax);
			incomeTax += flt(row.income_tax_148);
		});
		frm.set_value("total_purchase_value", purchase);
		frm.set_value("total_landed_cost", landed);
		frm.set_value("total_duties", duties);
		frm.set_value("total_input_sales_tax", salesTax);
		frm.set_value("total_income_tax_148", incomeTax);
	},
});