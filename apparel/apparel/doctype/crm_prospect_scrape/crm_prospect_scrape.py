import frappe
from frappe.model.document import Document


def _existing_link(doctype: str, value: str | None) -> str | None:
	"""Only point a Link field at a value that's actually a valid master
	record — scraped Segment/City text rarely matches a CRM Industry /
	CRM Territory record verbatim, and setting a Link to a non-existent
	value throws on save."""
	if value and frappe.db.exists(doctype, value):
		return value
	return None


class CRMProspectScrape(Document):
	@frappe.whitelist()
	def convert_to_lead(self):
		"""Turn this reviewed staging row into a real CRM Lead. Only ever
		called by hand from the review queue — approving a row here never
		auto-creates a Lead, so nothing is duplicated behind the scenes."""
		if self.converted_lead and frappe.db.exists("CRM Lead", self.converted_lead):
			return self.converted_lead

		first_name, _, last_name = (self.focal_person or "").partition(" ")
		lead = frappe.new_doc("CRM Lead")
		lead.update(
			{
				"first_name": first_name or self.donor_name or "Unknown",
				"last_name": last_name or None,
				"organization": self.donor_name,
				"website": self.website,
				"industry": _existing_link("CRM Industry", self.segment),
				"territory": _existing_link("CRM Territory", self.city),
				"address": self.address,
				"csr_department": self.csr_department,
				"focus_area": self.focus_area,
				"proposed_ask": self.proposed_ask,
				"job_title": self.designation,
				"email": self.email,
				"phone": self.phone,
				"first_contact_date": self.last_research_date,
				"remarks": f"Sourced from {self.research_source or self.source_url}",
				"status": "New",
			}
		)
		lead.insert()

		self.converted_lead = lead.name
		self.status = "Converted"
		self.save()
		return lead.name
