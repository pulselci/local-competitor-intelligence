"""Test Outscraper contacts API with different query formats."""
import sys
sys.path.insert(0, ".")
from app.core.config import settings
import requests

api_key = settings.OUTSCRAPER_API_KEY
place_id = "ChIJKVJwBIQsDogRdvb7lrMjmak"  # Dental Group of Chicago

print(f"API key set: {'yes' if api_key else 'NO'}\n")

formats = [
    ("Google Maps URL", f"https://www.google.com/maps/place/?q=place_id:{place_id}"),
    ("place_id prefix", f"place_id:{place_id}"),
    ("plain place_id", place_id),
    ("business name", "Dental Group of Chicago, Chicago, IL"),
]

for label, query in formats:
    r = requests.get(
        "https://api.app.outscraper.com/maps/emails-and-contacts",
        params={"query": query, "async": "false"},
        headers={"X-API-KEY": api_key},
        timeout=30,
    )
    print(f"[{label}] → HTTP {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"  Response: {str(data)[:300]}")
    else:
        print(f"  Body: {r.text[:200]}")
    print()
