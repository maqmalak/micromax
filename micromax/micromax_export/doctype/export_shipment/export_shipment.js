frappe.ui.form.on("Export Shipment", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			frm.set_value("shipment_no", frm.doc.name);
		}
	},
});