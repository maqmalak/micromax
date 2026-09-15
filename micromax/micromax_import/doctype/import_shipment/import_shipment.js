frappe.ui.form.on("Import Shipment", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			frm.set_value("shipment_no", frm.doc.name);
		}
	},
});