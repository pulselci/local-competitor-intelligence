"""
Prospect discovery script for Pulse LCI cold outreach.

Usage:
    python -m outreach.discover --city "Dallas" --state "TX"
    python -m outreach.discover --city "Phoenix" --state "AZ" --categories "auto_repair,dentist"

What it does:
1. Searches Google Places for review-heavy local businesses in the given city
2. Filters out chains and low-review-count businesses
3. Finds their top nearby competitor (for personalization)
4. Scrapes their website for a contact email
5. Generates a personalized draft cold email
6. Inserts into outreach_prospects table with status "draft_ready"

Skips businesses already in the DB.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests

# Allow running as `python -m outreach.discover` from /backend
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.db import get_conn
from outreach.draft_email import generate_draft

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES = [
    "auto repair shop",
    "medical spa",
    "dental office",
    "hair salon",
    "gym",
    "chiropractor",
    "physical therapy",
]

# Expanded aliases — use these when running a specific vertical to maximize results.
# Pass multiple comma-separated values via --categories in the CLI or the UI.
# Examples:
#   dental:     "dental office,dentist,dental clinic,family dentistry"
#   auto:       "auto repair shop,auto mechanic,car repair,automotive service"
#   med spa:    "medical spa,med spa,medspa,laser spa,aesthetic clinic"
#   hvac:       "hvac contractor,air conditioning contractor,heating contractor"
#   home svcs:  "plumber,plumbing service,roofing contractor,electrician"

# Chains to skip (partial match, lowercase)
CHAIN_BLOCKLIST = [
    "jiffy lube", "midas", "firestone", "pep boys", "meineke",
    "aspen dental", "heartland dental", "pacific dental",
    "great clips", "supercuts", "sport clips",
    "planet fitness", "anytime fitness", "la fitness",
    "massage envy",
]

MIN_REVIEWS = 15       # must have enough reviews to be worth targeting
MAX_REVIEWS = 2000     # avoid truly dominant chains (raised from 800 for large markets)
MIN_RATING = 3.2       # too low = dying business
MAX_RATING = 4.95      # near-perfect = low urgency (raised slightly)

GOOGLE_PLACES_TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_PLACES_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
GOOGLE_PLACES_NEARBY = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


# ---------------------------------------------------------------------------
# Google Places helpers
# ---------------------------------------------------------------------------

def _api_key() -> str:
    key = getattr(settings, "GOOGLE_PLACES_API_KEY", None)
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set in .env")
    return key


def search_places(query: str, city: str, state: str) -> list[dict]:
    """Text search for businesses matching query in city, state."""
    location_query = f"{query} in {city}, {state}"
    results = []
    next_page_token = None

    for _ in range(3):  # max 3 pages = 60 results
        params: dict = {"query": location_query, "key": _api_key()}
        if next_page_token:
            params = {"pagetoken": next_page_token, "key": _api_key()}
            time.sleep(2)  # Google requires delay for page tokens

        try:
            r = requests.get(GOOGLE_PLACES_TEXT_SEARCH, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [WARN] Places search failed: {e}")
            break

        results.extend(data.get("results", []))
        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break

    return results


def get_place_details(place_id: str) -> dict:
    """Fetch website and phone for a place."""
    try:
        r = requests.get(
            GOOGLE_PLACES_DETAILS,
            params={
                "place_id": place_id,
                "fields": "website,formatted_phone_number,name",
                "key": _api_key(),
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("result", {})
    except Exception as e:
        print(f"  [WARN] Place details failed for {place_id}: {e}")
        return {}


# Maps human-readable category strings to strict Google Places types.
# When a type is set, Google only returns businesses of that exact place type,
# preventing cross-category matches (e.g. plumber vs HVAC company).
_CATEGORY_TO_PLACE_TYPE: dict[str, str] = {
    "plumber": "plumber",
    "plumbing": "plumber",
    "plumbing service": "plumber",
    "electrician": "electrician",
    "roofing contractor": "roofing_contractor",
    "roofer": "roofing_contractor",
    "hvac contractor": "hvac_contractor",
    "air conditioning contractor": "hvac_contractor",
    "heating contractor": "hvac_contractor",
    "auto repair shop": "car_repair",
    "auto mechanic": "car_repair",
    "car repair": "car_repair",
    "automotive service": "car_repair",
    "dentist": "dentist",
    "dental office": "dentist",
    "dental clinic": "dentist",
    "gym": "gym",
    "hair salon": "hair_care",
    "beauty salon": "beauty_salon",
    "spa": "spa",
    "medical spa": "spa",
    "chiropractor": "physiotherapist",
    "physical therapy": "physiotherapist",
    "restaurant": "restaurant",
    "painter": "painter",
    "landscaping": "landscaping",
    "locksmith": "locksmith",
    "moving company": "moving_company",
}


def _place_type_for_category(category: str) -> str | None:
    """Return the strict Google Places type for a category string, or None."""
    normalized = (category or "").lower().strip()
    # Exact match first
    if normalized in _CATEGORY_TO_PLACE_TYPE:
        return _CATEGORY_TO_PLACE_TYPE[normalized]
    # Partial match
    for key, place_type in _CATEGORY_TO_PLACE_TYPE.items():
        if key in normalized or normalized in key:
            return place_type
    return None


def find_top_competitor(lat: float, lng: float, category: str, own_place_id: str) -> dict | None:
    """Find the highest-reviewed nearby business in the same category."""
    params: dict = {
        "location": f"{lat},{lng}",
        "radius": 8000,  # 5 miles
        "keyword": category,
        "key": _api_key(),
    }
    # Add strict type filter when we have a known mapping — prevents
    # cross-category matches (e.g. plumber vs HVAC company).
    place_type = _place_type_for_category(category)
    if place_type:
        params["type"] = place_type

    try:
        r = requests.get(
            GOOGLE_PLACES_NEARBY,
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        nearby = r.json().get("results", [])
    except Exception as e:
        print(f"  [WARN] Nearby search failed: {e}")
        return None

    candidates = [
        p for p in nearby
        if p.get("place_id") != own_place_id
        and p.get("user_ratings_total", 0) >= MIN_REVIEWS
    ]
    if not candidates:
        return None

    # Return the one with most reviews (the dominant competitor)
    return max(candidates, key=lambda p: p.get("user_ratings_total", 0))


# ---------------------------------------------------------------------------
# Email scraping
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

SKIP_EMAIL_DOMAINS = {
    "sentry.io", "example.com", "wixpress.com", "squarespace.com",
    "wordpress.com", "shopify.com", "adobe.com", "google.com",
    "schema.org", "w3.org", "gravatar.com", "jsdelivr.net",
    "cloudflare.com", "amazonaws.com", "fontawesome.com",
}

# File extensions that appear in image/asset filenames scraped as fake emails
SKIP_LOCAL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".pdf", ".js", ".css"}

SKIP_LOCAL_PREFIXES = {"noreply", "no-reply", "donotreply", "bounce", "mailer-daemon", "postmaster"}

PREFERRED_PREFIXES = {"contact", "info", "hello", "office", "admin", "appointments", "booking", "front", "reception"}


def _is_junk_email(email: str) -> bool:
    """Return True if this looks like a scraped image filename or other junk."""
    local, _, domain = email.partition("@")
    local_lower = local.lower()
    domain_lower = domain.lower()
    email_lower = email.lower()

    # Reject if the full email string ends with a file extension (e.g. phone@2x.png, ico-arrow@2x.png)
    if any(email_lower.endswith(ext) for ext in SKIP_LOCAL_EXTENSIONS):
        return True
    # Reject if local part ends with an image/file extension
    if any(local_lower.endswith(ext) for ext in SKIP_LOCAL_EXTENSIONS):
        return True
    # Reject known junk prefixes
    if any(local_lower.startswith(p) for p in SKIP_LOCAL_PREFIXES):
        return True
    # Reject known junk domains
    if any(skip in domain_lower for skip in SKIP_EMAIL_DOMAINS):
        return True
    # Reject obviously invalid: no dot in domain
    if "." not in domain_lower:
        return True
    return False


def _fetch_page_text(url: str) -> str | None:
    """Fetch a single URL and return decoded text, or None on failure."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PulseLCI/1.0)"}
    r = requests.get(url, headers=headers, timeout=(3, 5), allow_redirects=True, stream=True)
    content = b""
    for chunk in r.iter_content(chunk_size=8192):
        content += chunk
        if len(content) > 81920:  # cap at 80 KB
            break
    return content.decode("utf-8", errors="ignore")


def _collect_emails_from_text(text: str) -> list[str]:
    """Return all valid email candidates from a page (mailto: hrefs first, then regex)."""
    mailto_pattern = re.compile(r'href=["\']mailto:([^"\'?\s]+)', re.IGNORECASE)
    seen = set()
    results = []

    for e in mailto_pattern.findall(text) + EMAIL_PATTERN.findall(text):
        e = e.lower()
        if e not in seen and not _is_junk_email(e) and len(e) < 80:
            seen.add(e)
            results.append(e)

    return results


def _do_scrape_multipages(base_url: str) -> str | None:
    """Scrape homepage + common contact/about paths, collect ALL emails,
    then pick the best one: own domain > preferred prefix > first valid."""
    from urllib.parse import urljoin, urlparse

    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    own_domain = parsed.netloc.lower().lstrip("www.")

    paths_to_try = [
        base_url,
        urljoin(root, "/contact"),
        urljoin(root, "/contact-us"),
        urljoin(root, "/about"),
        urljoin(root, "/about-us"),
    ]

    all_emails: list[str] = []
    seen: set[str] = set()

    for url in paths_to_try:
        try:
            text = _fetch_page_text(url)
            if text:
                for e in _collect_emails_from_text(text):
                    if e not in seen:
                        seen.add(e)
                        all_emails.append(e)
        except Exception:
            continue

    if not all_emails:
        return None

    # 1. Own domain match — always wins
    for e in all_emails:
        if e.split("@")[-1].lstrip("www.") == own_domain:
            return e

    # No own-domain email found — don't fall back to third-party emails
    # (booking widgets, analytics platforms, etc. show up on every page and
    # will bounce or go to the wrong person). Mark as no_email instead.
    return None


def scrape_email_from_website(base_url: str, hard_timeout: int = 20) -> str | None:
    """Scrape homepage + contact/about pages with a hard wall-clock timeout."""
    if not base_url:
        return None

    import threading
    result: list[str | None] = [None]

    def _run():
        try:
            result[0] = _do_scrape_multipages(base_url)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=hard_timeout)
    return result[0]


# ---------------------------------------------------------------------------
# Hunter.io fallback
# ---------------------------------------------------------------------------

HUNTER_API = "https://api.hunter.io/v2/domain-search"


def lookup_email_hunter(domain: str) -> str | None:
    """
    Use Hunter.io to find a contact email for a domain.
    Only runs if HUNTER_API_KEY is set in .env — silently skips otherwise.
    Free tier: 25 searches/month. Paid: ~$49/mo for 500.
    """
    api_key = getattr(settings, "HUNTER_API_KEY", None) or ""
    if not api_key:
        return None

    try:
        r = requests.get(
            HUNTER_API,
            params={"domain": domain, "api_key": api_key, "limit": 5},
            timeout=(4, 8),
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        emails = data.get("emails", [])
        if not emails:
            return None

        # Prefer generic/department addresses over personal ones
        for entry in emails:
            email = (entry.get("value") or "").lower()
            etype = (entry.get("type") or "").lower()
            if etype == "generic" and not _is_junk_email(email):
                return email

        # Fall back to first valid email
        for entry in emails:
            email = (entry.get("value") or "").lower()
            if email and not _is_junk_email(email):
                return email

    except Exception as e:
        print(f"  [WARN] Hunter.io lookup failed for {domain}: {e}")

    return None


OUTSCRAPER_CONTACTS_API = "https://api.app.outscraper.com/maps/emails-and-contacts"

APOLLO_PEOPLE_SEARCH = "https://api.apollo.io/api/v1/mixed_people/api_search"

# Job titles likely to be the decision-maker at a small local business
APOLLO_TARGET_TITLES = ["owner", "founder", "president", "ceo", "manager", "general manager", "director"]


def lookup_email_outscraper(place_id: str) -> str | None:
    """
    Use Outscraper's Emails & Contacts Scraper to find a contact email for a
    Google Place ID.  Uses the same OUTSCRAPER_API_KEY already configured for
    review ingestion.  Silently skips if the key is not set.

    Outscraper scrapes the business website + Google Maps profile to surface
    contact emails — complementary to our own scraper and Hunter/Apollo because
    it has broader coverage of non-indexed pages and GMB data.

    Pricing: counts against the Outscraper contacts-scraper quota (separate
    from the reviews quota).  Check your plan at app.outscraper.com.
    """
    api_key = getattr(settings, "OUTSCRAPER_API_KEY", None) or ""
    if not api_key:
        return None

    try:
        r = requests.get(
            OUTSCRAPER_CONTACTS_API,
            params={"query": f"https://www.google.com/maps/place/?q=place_id:{place_id}", "async": False},
            headers={"X-API-KEY": api_key},
            timeout=(5, 45),
        )
        r.raise_for_status()
        payload = r.json()

        # Outscraper wraps results in data[0] (list of tasks) or data directly
        data = payload.get("data", [])
        if not data:
            return None
        # data is typically [[{result}, ...]] or [{result}, ...]
        inner = data[0] if isinstance(data[0], list) else data
        if not inner:
            return None
        result = inner[0] if isinstance(inner[0], dict) else {}

        # Primary: emails array (list of dicts with "value" key, or plain strings)
        for raw_email in result.get("emails", []):
            email = (raw_email.get("value") if isinstance(raw_email, dict) else raw_email) or ""
            email = email.strip().lower()
            if email and not _is_junk_email(email):
                return email

        # Fallback: site_email field (sometimes populated separately)
        site_email = (result.get("site_email") or "").strip().lower()
        if site_email and not _is_junk_email(site_email):
            return site_email

        # Last resort: email_1 / email_2 flat fields
        for field in ("email_1", "email_2", "email"):
            flat = (result.get(field) or "").strip().lower()
            if flat and not _is_junk_email(flat):
                return flat

    except Exception as e:
        print(f"  [WARN] Outscraper contacts lookup failed for {place_id}: {e}")

    return None


def lookup_email_apollo(domain: str, business_name: str | None = None) -> str | None:
    """
    Use Apollo.io People Search to find a contact email for a domain.
    Only runs if APOLLO_API_KEY is set in .env — silently skips otherwise.
    Targets owner/founder/manager titles first, then any verified email.
    """
    api_key = getattr(settings, "APOLLO_API_KEY", None) or ""
    print(f"  [DEBUG] Apollo key present: {bool(api_key)} len={len(api_key)}")
    if not api_key:
        return None

    try:
        payload = {
            "api_key": api_key,
            "q_organization_domain_name": domain,
            "person_titles": APOLLO_TARGET_TITLES,
            "per_page": 10,
        }
        r = requests.post(
            APOLLO_PEOPLE_SEARCH,
            json=payload,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=(4, 15),
        )
        r.raise_for_status()
        people = r.json().get("people", [])

        if not people:
            return None

        # Prefer decision-maker titles
        for person in people:
            title = (person.get("title") or "").lower()
            email = (person.get("email") or "").lower()
            if not email or _is_junk_email(email):
                continue
            if any(t in title for t in APOLLO_TARGET_TITLES):
                return email

        # Fall back to first person with a valid email
        for person in people:
            email = (person.get("email") or "").lower()
            if email and not _is_junk_email(email):
                return email

    except Exception as e:
        print(f"  [WARN] Apollo lookup failed for {domain}: {e}")

    return None


# ---------------------------------------------------------------------------
# Chain detection
# ---------------------------------------------------------------------------

def _is_chain(name: str) -> bool:
    name_lower = name.lower()
    return any(chain in name_lower for chain in CHAIN_BLOCKLIST)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _already_exists(place_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM outreach_prospects WHERE place_id = %s LIMIT 1",
                (place_id,),
            )
            return cur.fetchone() is not None


def _insert_prospect(
    place_id: str,
    business_name: str,
    category: str,
    address: str,
    city: str,
    state: str,
    website: str | None,
    phone: str | None,
    contact_email: str | None,
    reviews_count: int,
    rating: float,
    top_competitor_name: str | None,
    top_competitor_reviews: int | None,
    draft_subject: str,
    draft_body: str,
    prospect_type: str = "local_business",
    partnership_type: str | None = None,
) -> None:
    status = "draft_ready" if contact_email else "no_email"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO outreach_prospects (
                    place_id, business_name, category, address, city, state,
                    website, phone, contact_email, reviews_count, rating,
                    top_competitor_name, top_competitor_reviews,
                    draft_subject, draft_body, status,
                    prospect_type, partnership_type
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (place_id) DO NOTHING
                """,
                (
                    place_id, business_name, category, address, city, state,
                    website, phone, contact_email, reviews_count, rating,
                    top_competitor_name, top_competitor_reviews,
                    draft_subject, draft_body, status,
                    prospect_type, partnership_type,
                ),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Agency email draft
# ---------------------------------------------------------------------------

def generate_agency_draft(
    business_name: str,
    city: str,
    category: str | None,
    partnership_type: str = "both",
) -> tuple[str, str]:
    """Generate a short cold email for an agency prospect."""
    subject = "Quick question about your local business clients"

    body = f"""Hi,

Do you currently give your local business clients any insight into how they're performing against local competitors on reviews?

I ask because I built something for exactly that. Monthly reports showing review momentum, rating gaps, and competitor positioning in their local market. Takes 60 seconds to set up per client.

Happy to pull a free sample report for any of your clients' markets. Just reply with a city and business type and I'll send it over.

Craig
"""
    return subject, body


# ---------------------------------------------------------------------------
# Main discovery loop
# ---------------------------------------------------------------------------

def discover(
    city: str,
    state: str,
    categories: list[str],
    prospect_type: str = "local_business",
    partnership_type: str = "both",
) -> None:
    print(f"\n=== Pulse LCI Prospect Discovery ===")
    print(f"City: {city}, {state} | Type: {prospect_type}")
    print(f"Categories: {', '.join(categories)}\n")

    is_agency = prospect_type == "agency"
    total_found = 0
    total_inserted = 0

    for category in categories:
        print(f"\n--- Searching: {category} ---")
        places = search_places(category, city, state)
        print(f"  Found {len(places)} raw results")

        for place in places:
            name = place.get("name", "")
            place_id = place.get("place_id", "")
            rating = place.get("rating")
            reviews = place.get("user_ratings_total", 0)
            address = place.get("formatted_address", "")
            geometry = place.get("geometry", {}).get("location", {})
            lat = geometry.get("lat")
            lng = geometry.get("lng")

            # Filter
            if not place_id or not name:
                continue
            if _is_chain(name):
                print(f"  SKIP (chain): {name}")
                continue
            if not is_agency:
                # Review/rating filters only apply to local business outreach
                if not rating or not (MIN_RATING <= rating <= MAX_RATING):
                    continue
                if not reviews or not (MIN_REVIEWS <= reviews <= MAX_REVIEWS):
                    continue
            if _already_exists(place_id):
                print(f"  SKIP (exists): {name}")
                continue

            total_found += 1
            print(f"\n  Processing: {name}")

            # Get website + phone
            details = get_place_details(place_id)
            website = details.get("website")
            phone = details.get("formatted_phone_number")

            # Competitor lookup — skip for agencies
            top_competitor_name = None
            top_competitor_reviews = None
            if not is_agency and lat and lng:
                competitor = find_top_competitor(lat, lng, category, place_id)
                if competitor:
                    top_competitor_name = competitor.get("name")
                    top_competitor_reviews = competitor.get("user_ratings_total")
                    print(f"  Top competitor: {top_competitor_name} ({top_competitor_reviews} reviews)")

            # Scrape email
            contact_email = scrape_email_from_website(website) if website else None
            email_source = "scrape"

            if not contact_email and website:
                from urllib.parse import urlparse
                domain = urlparse(website).netloc.lstrip("www.")
                if domain:
                    contact_email = lookup_email_hunter(domain)
                    email_source = "hunter"

            if not contact_email and website:
                from urllib.parse import urlparse
                domain = urlparse(website).netloc.lstrip("www.")
                if domain:
                    contact_email = lookup_email_apollo(domain, business_name=name)
                    email_source = "apollo"

            # Outscraper contacts scraper disabled — maps/emails-and-contacts
            # endpoint returns 404 (URL not found on server); revisit if they
            # publish a working contacts API endpoint.
            # if not contact_email:
            #     contact_email = lookup_email_outscraper(place_id)
            #     if contact_email:
            #         email_source = "outscraper"

            if contact_email:
                print(f"  Email found ({email_source}): {contact_email}")
            else:
                print(f"  No email found (website: {website or 'none'})")

            # Generate draft
            if is_agency:
                subject, body = generate_agency_draft(
                    business_name=name,
                    city=city,
                    category=category,
                    partnership_type=partnership_type,
                )
            else:
                subject, body = generate_draft(
                    business_name=name,
                    city=city,
                    reviews_count=reviews,
                    rating=rating,
                    top_competitor_name=top_competitor_name,
                    top_competitor_reviews=top_competitor_reviews,
                    category=category,
                )

            # Insert
            _insert_prospect(
                place_id=place_id,
                business_name=name,
                category=category,
                address=address,
                city=city,
                state=state,
                website=website,
                phone=phone,
                contact_email=contact_email,
                reviews_count=reviews,
                rating=rating,
                top_competitor_name=top_competitor_name,
                top_competitor_reviews=top_competitor_reviews,
                draft_subject=subject,
                draft_body=body,
                prospect_type=prospect_type,
                partnership_type=partnership_type if is_agency else None,
            )
            total_inserted += 1
            time.sleep(0.3)

    print(f"\n=== Done ===")
    print(f"Processed: {total_found} prospects")
    print(f"Inserted:  {total_inserted} new records")
    print(f"Run 'python -m outreach.queue' or open the approval UI to review drafts.\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Discover cold outreach prospects via Google Places")
    parser.add_argument("--city", required=True, help="City to search (e.g. 'Dallas')")
    parser.add_argument("--state", required=True, help="State abbreviation (e.g. 'TX')")
    parser.add_argument(
        "--categories",
        default=",".join(DEFAULT_CATEGORIES),
        help="Comma-separated list of business categories to search",
    )
    args = parser.parse_args()
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    discover(city=args.city, state=args.state, categories=categories)


if __name__ == "__main__":
    main()
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        