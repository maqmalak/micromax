import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import frappe
import requests
from bs4 import BeautifulSoup

# On-demand donor-research scraper for the fundraising CRM: given a company/
# NGO's public website, best-effort extract identity + CSR contact info into
# a `CRM Prospect Scrape` review-queue row. Nothing here ever writes a
# `CRM Lead` directly — that only happens when a human approves a row and
# calls `CRMProspectScrape.convert_to_lead` by hand.

USER_AGENT = "Mozilla/5.0 (compatible; ApparelCRMResearchBot/1.0)"
# Wikimedia's API etiquette explicitly asks bots to self-identify (not
# masquerade as a browser) and grants friendlier rate-limit treatment for
# doing so — a bulk run of dozens of company-name lookups started getting
# 429s from en.wikipedia.org with the browser-style UA above; this is used
# only for the Wikipedia/Wikidata discovery calls, not for fetching the
# donor's own site (where blending in as a normal browser matters more).
WIKIMEDIA_USER_AGENT = "apparel-crm-donor-scraper/1.0 (internal fundraising CRM research tool)"
REQUEST_TIMEOUT = 7
MAX_RESPONSE_BYTES = 2_000_000
# A batch this size, all worst-case-slow entries (main fetch + CSR-link
# hop, each up to REQUEST_TIMEOUT), must still finish comfortably inside
# the reverse proxy's read timeout — a 25-URL batch of real (occasionally
# bot-protected/slow) corporate sites hit exactly that wall in production
# (confirmed live: "timeout of 60000ms exceeded" on the client, which would
# have become a proxy 504 regardless once that client-side number was
# raised). 12 * (7s * 2) = 168s worst case, leaving real margin under the
# also-raised 240s proxy timeout — the part of this fix that survives even
# if that infra-level timeout doesn't (it isn't tracked in this repo, so a
# rebuild resets it).
MAX_URLS_PER_CALL = 12
# Pacing between company-name lookups in a bulk run — Wikipedia's API starts
# 429-ing well before this if hit with no gap at all across dozens of names.
DISCOVERY_PACING_SECONDS = 2.0

# Links worth following one hop deeper for better contact info, matched
# against the anchor text or href of same-domain links on the landing page.
CSR_LINK_KEYWORDS = [
	"csr", "sustainability", "corporate affairs", "corporate-affairs",
	"contact", "about-us", "about",
]
DEPARTMENT_PATTERNS = [
	r"Head of[^.,\n]{0,40}(?:Corporate Affairs|CSR|Sustainability)[^.,\n]{0,40}",
	r"(?:Corporate Affairs|CSR|Sustainability)[^.,\n]{0,40}(?:Department|Team|Division)",
]
FOCUS_KEYWORDS = [
	"education", "health", "climate", "environment", "women empowerment",
	"poverty", "nutrition", "livelihood", "water", "sanitation",
	"disaster relief", "youth", "skills development",
]
# Social platform domains worth surfacing, and the friendly label each maps
# to in the stored "Platform: url" list — checked against each anchor's
# href, in this order, so a page linking multiple platforms keeps them
# grouped predictably.
SOCIAL_DOMAINS = [
	("facebook.com", "Facebook"),
	("linkedin.com", "LinkedIn"),
	("twitter.com", "Twitter/X"),
	("x.com", "Twitter/X"),
	("instagram.com", "Instagram"),
	("youtube.com", "YouTube"),
]
# The doctype's "Data" fieldtype fields (140-char DB column) that this
# module ever writes — everything else it sets (address, proposed_ask,
# social_media, raw_extract) is "Small Text"/no practical length limit.
SHORT_TEXT_FIELDS = {
	"donor_name", "segment", "website", "donor_profile_url", "country", "city",
	"csr_department", "focus_area", "focal_person", "designation", "email",
	"phone", "contact_source", "research_source",
}
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
# The middle group was unbounded ({7,}) — since it allows whitespace, a
# "History" timeline paragraph like "1965 1966 1968 ... 2026" satisfies the
# whole pattern as ONE match spanning the entire list of years (all digits/
# spaces), producing a "phone number" hundreds of characters long that then
# blew past the field's 140-char limit and crashed the insert entirely
# (confirmed live — SCRAPE-00367 never got created). Bounded to a real
# phone-number length (comfortably covers "+92 (42) 111-673-853" and the
# like) so a single match can't run away like that again.
PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{7,18}\d")
# PHONE_RE's dash/digit shape also matches plain ISO dates (e.g. "1919-04-15",
# common in article/bio text) — filter those out rather than tighten the
# regex itself, since real-world phone formatting is too varied to pin down
# more precisely without also losing genuine matches.
ISO_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")

# A bare company/NGO name (no scheme, no dot-tld shape) triggers discovery
# instead of being fetched directly.
DOMAIN_LIKE_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}(?:/.*)?$")

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


def _looks_like_url_or_domain(s: str) -> bool:
	if s.startswith(("http://", "https://")):
		return True
	return bool(DOMAIN_LIKE_RE.match(s))


def _get_json(url: str, params: dict | None = None) -> dict:
	"""GET + parse JSON with one retry against Wikipedia/Wikidata. A 429
	(confirmed live during a 74-company bulk run) gets a real backoff —
	honoring `Retry-After` when the server sends one, else 8s, since a short
	retry just gets 429'd again; anything else (a transient empty/non-JSON
	body) only needs a brief pause before the retry.
	"""
	last_exc: Exception | None = None
	for attempt in range(2):
		try:
			resp = requests.get(url, params=params, headers={"User-Agent": WIKIMEDIA_USER_AGENT}, timeout=REQUEST_TIMEOUT)
			resp.raise_for_status()
			return resp.json()
		except Exception as e:
			last_exc = e
			if attempt == 0:
				retry_after = None
				resp = getattr(e, "response", None)
				if resp is not None and resp.status_code == 429:
					retry_after = resp.headers.get("Retry-After")
				try:
					wait = float(retry_after) if retry_after else (8.0 if getattr(resp, "status_code", None) == 429 else 1.5)
				except ValueError:
					wait = 8.0
				time.sleep(wait)
	raise last_exc  # type: ignore[misc]


def _discover_official_website(company_name: str) -> tuple[str | None, str | None]:
	"""Resolve a bare company/NGO name to its official website via Wikipedia
	search -> Wikidata's "official website" (P856) claim — both free, public,
	documented APIs (no key, generous read-only rate limits for this kind of
	one-off lookup), unlike a general web-search API which needs a paid key
	we don't have configured. Returns (url, source_label) or (None, None).
	"""
	search = _get_json(
		WIKIPEDIA_API,
		params={"action": "query", "list": "search", "srsearch": company_name, "format": "json", "srlimit": 1},
	)
	hits = search.get("query", {}).get("search", [])
	if not hits:
		return None, None
	title = hits[0]["title"]

	pageprops = _get_json(
		WIKIPEDIA_API,
		params={"action": "query", "titles": title, "prop": "pageprops", "format": "json"},
	)
	pages = pageprops.get("query", {}).get("pages", {})
	qid = next(iter(pages.values()), {}).get("pageprops", {}).get("wikibase_item")
	if not qid:
		return None, None

	entity = _get_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
	claims = entity.get("entities", {}).get(qid, {}).get("claims", {})
	p856 = claims.get("P856")
	if not p856:
		return None, None
	try:
		url = p856[0]["mainsnak"]["datavalue"]["value"]
	except (KeyError, IndexError):
		return None, None
	return url, f"Wikidata (via Wikipedia article \"{title}\")"


def _is_safe_url(url: str) -> bool:
	"""Basic SSRF guard: only plain http(s) to a publicly routable host.
	Blocks loopback/private/link-local/reserved ranges (this also covers the
	169.254.169.254 cloud metadata address). Does not pin the resolved IP
	for the actual request, so a DNS-rebinding attacker could in principle
	still slip past the check-then-fetch gap — acceptable residual risk for
	a tool gated to authenticated System/Sales Manager/User roles, not
	something to over-engineer here.
	"""
	parsed = urlparse(url)
	if parsed.scheme not in ("http", "https"):
		return False
	host = parsed.hostname
	if not host:
		return False
	try:
		infos = socket.getaddrinfo(host, None)
	except socket.gaierror:
		return False
	for info in infos:
		ip = ipaddress.ip_address(info[4][0])
		if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
			return False
	return True


def _fetch(url: str) -> str:
	resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT, stream=True)
	resp.raise_for_status()
	content = b""
	for chunk in resp.iter_content(8192):
		content += chunk
		if len(content) > MAX_RESPONSE_BYTES:
			break
	return content.decode(resp.encoding or "utf-8", errors="ignore")


def _find_csr_link(url: str, html: str) -> str | None:
	soup = BeautifulSoup(html, "lxml")
	for a in soup.find_all("a", href=True):
		label = (a.get_text() or "").strip().lower()
		href = a["href"]
		if any(k in label or k in href.lower() for k in CSR_LINK_KEYWORDS):
			candidate = urljoin(url, href)
			if urlparse(candidate).netloc == urlparse(url).netloc and candidate != url:
				return candidate
	return None


def _extract_social_links(soup: BeautifulSoup) -> str | None:
	"""First matching profile URL per platform (site headers/footers often
	repeat the same social icon several times) as `"Platform: url"` lines."""
	found: dict[str, str] = {}
	for a in soup.find_all("a", href=True):
		href = a["href"].strip()
		host = urlparse(href).netloc.lower()
		if not host:
			continue
		for domain, label in SOCIAL_DOMAINS:
			if domain in host and label not in found:
				found[label] = href
				break
	if not found:
		return None
	return "\n".join(f"{label}: {href}" for label, href in found.items())


def _extract(url: str, html: str) -> dict:
	soup = BeautifulSoup(html, "lxml")
	title = soup.title.get_text().strip() if soup.title and soup.title.string else ""
	donor_name = re.split(r"[|\-–—:]", title)[0].strip() if title else ""

	text = soup.get_text(" ", strip=True)

	emails = set(EMAIL_RE.findall(text))
	for a in soup.find_all("a", href=True):
		if a["href"].lower().startswith("mailto:"):
			emails.add(a["href"][7:].split("?")[0])
	emails = sorted(e for e in emails if "example" not in e.lower() and "sentry" not in e.lower())

	phones = [p for p in PHONE_RE.findall(text) if not ISO_DATE_RE.match(p.strip())]

	department = None
	for pat in DEPARTMENT_PATTERNS:
		m = re.search(pat, text, re.IGNORECASE)
		if m:
			department = m.group(0).strip()
			break

	focus_hits = [k.title() for k in FOCUS_KEYWORDS if k in text.lower()]

	meta_desc_tag = soup.find("meta", attrs={"name": "description"})
	meta_desc = (meta_desc_tag.get("content") or "").strip() if meta_desc_tag else ""

	parsed = urlparse(url)
	return {
		"donor_name": donor_name or parsed.netloc,
		"website": f"{parsed.scheme}://{parsed.netloc}",
		"email": emails[0] if emails else None,
		"phone": phones[0].strip() if phones else None,
		"csr_department": department,
		"focus_area": ", ".join(focus_hits[:5]) or None,
		"social_media": _extract_social_links(soup),
		"raw_extract": meta_desc or text[:500],
	}


def _scrape_one(raw_input: str) -> "frappe.model.document.Document":
	raw_input = raw_input.strip()
	doc = frappe.new_doc("CRM Prospect Scrape")
	doc.last_research_date = frappe.utils.today()

	if _looks_like_url_or_domain(raw_input):
		url = raw_input if raw_input.startswith(("http://", "https://")) else "https://" + raw_input
		doc.research_source = url
	else:
		# Bare company/NGO name — find its official site first (Wikipedia/
		# Wikidata, not a URL the caller gave us) before trying to scrape it.
		doc.donor_name = raw_input
		try:
			website, source_label = _discover_official_website(raw_input)
		except Exception as e:
			website, source_label = None, None
			doc.scrape_error = f"Website lookup failed: {str(e)[:100]}"
		if not website:
			if not doc.scrape_error:
				doc.scrape_error = f'Could not find an official website for "{raw_input}" — paste its URL directly instead.'
			doc.source_url = raw_input
			doc.research_source = "Wikidata lookup (no match)"
			doc.insert()
			return doc
		url = website
		doc.research_source = source_label

	doc.source_url = url

	try:
		if not _is_safe_url(url):
			raise ValueError("URL not allowed (must be a public http/https address)")
		html = _fetch(url)
		data = _extract(url, html)

		csr_link = _find_csr_link(url, html)
		if csr_link:
			try:
				if _is_safe_url(csr_link):
					sub_html = _fetch(csr_link)
					sub_data = _extract(csr_link, sub_html)
					for k in ("email", "phone", "csr_department", "social_media"):
						if not data.get(k) and sub_data.get(k):
							data[k] = sub_data[k]
					doc.donor_profile_url = csr_link
			except Exception:
				pass  # landing-page data still stands even if the deeper fetch fails

		for k, v in data.items():
			if v:
				# Defensive cap on the Data-typed (140-char) fields — the
				# PHONE_RE fix above addresses the one confirmed cause of an
				# oversized value, but scraped web text is inherently messy,
				# so this stays as a second line of defense against whatever
				# the next one turns out to be.
				if k in SHORT_TEXT_FIELDS and isinstance(v, str) and len(v) > 140:
					v = v[:137] + "..."
				doc.set(k, v)
		if data.get("email"):
			doc.contact_source = doc.donor_profile_url or url
	except Exception as e:
		doc.scrape_error = str(e)[:140]

	try:
		doc.insert()
	except Exception as e:
		# A bad value should never lose the whole row silently — fall back to
		# just the identifying fields plus what broke, instead of crashing
		# the rest of a bulk run (confirmed live: an unhandled insert error
		# here previously did exactly that).
		frappe.db.rollback()
		failed = doc
		doc = frappe.new_doc("CRM Prospect Scrape")
		doc.source_url = failed.source_url or url
		doc.research_source = failed.research_source
		doc.last_research_date = frappe.utils.today()
		if failed.donor_name:
			doc.donor_name = failed.donor_name
		doc.scrape_error = f"Saved with errors — {str(e)[:100]}"
		doc.insert()
	return doc


@frappe.whitelist()
def scrape_urls(urls):
	"""Entry point for the scraper page: takes a list of company/NGO URLs
	*or bare names* (e.g. "Unilever Pakistan" — matching the donor
	spreadsheet's Company column directly), fetches/discovers each, and
	inserts one `CRM Prospect Scrape` row per entry (successful or not —
	failures land in the queue with `scrape_error` set so they're visible
	and retryable rather than silently dropped).
	"""
	if isinstance(urls, str):
		urls = frappe.parse_json(urls)
	urls = [u for u in (urls or []) if isinstance(u, str) and u.strip()][:MAX_URLS_PER_CALL]
	if not urls:
		frappe.throw("Provide at least one URL or company name to scrape.")

	names = []
	prev_needed_discovery = False
	for i, u in enumerate(urls):
		# Pacing only matters for the Wikipedia/Wikidata discovery step (bare
		# company names) — a batch of plain URLs never touches that API at
		# all, so pacing every entry unconditionally (the original version of
		# this loop) just added dead time for exactly that case and helped
		# push a 25-URL batch over the frontend's 60s request timeout
		# (confirmed live). Only wait when the *previous* entry actually did
		# a discovery lookup.
		if i > 0 and prev_needed_discovery:
			time.sleep(DISCOVERY_PACING_SECONDS)
		prev_needed_discovery = not _looks_like_url_or_domain(u.strip())
		names.append(_scrape_one(u).name)
	frappe.db.commit()
	return names
