frappe.ui.form.on("Export Packing Details", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			frm.set_value("packing_no", frm.doc.name);
		}
	},
	calc_totals: function (frm) {
		let cartons = 0,
			pieces = 0,
			net = 0,
			gross = 0,
			cbm = 0;
		(frm.doc.export_packing_details || []).forEach((row) => {
			if (row.carton_no) cartons += 1;
			pieces += flt(row.quantity);
			net += flt(row.net_weight);
			gross += flt(row.gross_weight);
			cbm += flt(row.volume_cbm);
		});
		frm.set_value("total_cartons", cartons);
		frm.set_value("total_pieces", pieces);
		frm.set_value("total_net_weight", net);
		frm.set_value("total_gross_weight", gross);
		frm.set_value("total_cbm", cbm);
		frm.refresh_field([
			"total_cartons",
			"total_pieces",
			"total_net_weight",
			"total_gross_weight",
			"total_cbm",
		]);
	},
});

frappe.ui.form.on("Export Packing Details Item", {
	quantity: function (frm) {
		frm.trigger("calc_totals");
	},
	net_weight: function (frm) {
		frm.trigger("calc_totals");
	},
	gross_weight: function (frm) {
		frm.trigger("calc_totals");
	},
	volume_cbm: function (frm) {
		frm.trigger("calc_totals");
	},
	carton_no: function (frm) {
		frm.trigger("calc_totals");
	},
});