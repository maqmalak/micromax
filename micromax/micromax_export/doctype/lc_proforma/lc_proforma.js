frappe.ui.form.on("LC Proforma", {
	refresh: function (frm) {
		frm.set_df_property("export_order", "read_only", 0);
		if (!frm.is_new()) {
			frm.set_value("proforma_no", frm.doc.name);
		}
	},

	// Create Sales Order dashboard action -> calls the whitelisted method on the
	// server and refreshes, then opens / links the new Sales Order.
	"Create Sales Order": function (frm) {
		if (!frm.doc.customer) {
			frappe.msgprint(__("Select Customer / Buyer first."));
			return;
		}
		frm.call({
			method: "make_sales_order",
			doc: frm.doc,
			callback: function (r) {
				if (r.message) {
					frm.reload_doc();
					frappe.msgprint(__("Sales Order created: {0}", [r.message]));
				}
			},
		});
	},

	calculate_totals: function (frm) {
		let qty = 0,
			cartons = 0,
			net = 0,
			gross = 0,
			value = 0;
		(frm.doc.lc_proforma_items || []).forEach((row) => {
			qty += flt(row.quantity);
			cartons += flt(row.cartons);
			net += flt(row.net_weight);
			gross += flt(row.gross_weight);
			value += flt(row.amount);
		});
		frm.set_value("total_quantity", qty);
		frm.set_value("total_cartons", cartons);
		frm.set_value("total_net_weight", net);
		frm.set_value("total_gross_weight", gross);
		frm.set_value("total_proforma_value", value);
		frm.refresh_field([
			"total_quantity",
			"total_cartons",
			"total_net_weight",
			"total_gross_weight",
			"total_proforma_value",
		]);
	},
});

frappe.ui.form.on("LC Proforma Item", {
	amount: function (frm) {
		frm.trigger("calculate_totals");
	},
	quantity: function (frm) {
		frm.trigger("calculate_totals");
	},
	rate: function (frm) {
		frm.trigger("calculate_totals");
	},
	cartons: function (frm) {
		frm.trigger("calculate_totals");
	},
	net_weight: function (frm) {
		frm.trigger("calculate_totals");
	},
	gross_weight: function (frm) {
		frm.trigger("calculate_totals");
	},
});