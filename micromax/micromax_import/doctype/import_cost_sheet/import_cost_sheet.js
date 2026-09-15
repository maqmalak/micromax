const COST_FIELDS = [
	"purchase_value",
	"freight",
	"insurance",
	"customs_duty",
	"additional_duty",
	"sales_tax",
	"regulatory_duty",
	"clearing_charges",
	"port_charges",
	"other_charges",
];

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
		frappe.model.set_value(cdt, cdn, "total_landed_cost", total);
		const qty = flt(row.quantity);
		frappe.model.set_value(cdt, cdn, "landed_cost_per_unit", qty ? total / qty : 0);
		frm.trigger("calc_totals");
	},
});

COST_FIELDS.forEach((f) => {
	frappe.ui.form.on("Import Cost Sheet Item", {
		[f]: function (frm, cdt, cdn) {
			frm.script_manager.trigger("calc", cdt, cdn);
		},
	});
});

frappe.ui.form.on("Import Cost Sheet", {
	calc_totals: function (frm) {
		let purchase = 0;
		let landed = 0;
		(frm.doc.import_cost_sheet_items || []).forEach((row) => {
			purchase += flt(row.purchase_value);
			landed += flt(row.total_landed_cost);
		});
		frm.set_value("total_purchase_value", purchase);
		frm.set_value("total_landed_cost", landed);
		frm.refresh_field(["total_purchase_value", "total_landed_cost"]);
	},
});