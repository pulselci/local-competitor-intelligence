"""Test Apollo people/search with the configured API key."""
import sys
sys.path.insert(0, ".")
from app.core.config import settings
import requests

api_key = getattr(settings, "APOLLO_API_KEY", None) or ""
print(f"API key set: {'yes (ends in ...' + api_key[-4:] + ')' if api_key else 'NO'}\n")

if not api_key:
    print("Set APOLLO_API_KEY in .env and retry.")
    sys.exit(1)

payload = {
    "q_organization_domain_name": "strobeldentistry.com",
    "per_page": 5,
    "page": 1,
}

r = requests.post(
    "https://api.apollo.io/v1/people/search",
    json=payload,
    headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
    timeout=15,
)

print(f"HTTP {r.status_code}")
print(f"Response body:\n{r.text[:500]}")
