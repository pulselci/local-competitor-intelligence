"""Quick diagnostic: print first 5 places returned for 'dental office in Chicago, IL'"""
import sys
sys.path.insert(0, ".")
from app.core.config import settings
import requests

key = settings.GOOGLE_PLACES_API_KEY
r = requests.get(
    "https://maps.googleapis.com/maps/api/place/textsearch/json",
    params={"query": "dental office in Chicago, IL", "key": key},
    timeout=10,
)
data = r.json()
print(f"Status: {data.get('status')}")
results = data.get("results", [])
print(f"Total results: {len(results)}\n")
for p in results[:8]:
    name = p.get("name")
    rating = p.get("rating")
    reviews = p.get("user_ratings_total")
    print(f"  {name}: rating={rating}, reviews={reviews}")
