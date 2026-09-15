// LC Proforma Item child table client logic.
frappe.ui.form.on("LC Proforma Item", {
	setup: function (frm) {},
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
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "amount", flt(row.quantity) * flt(row.rate));
	},
	rate: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "amount", flt(row.quantity) * flt(row.rate));
	},
});