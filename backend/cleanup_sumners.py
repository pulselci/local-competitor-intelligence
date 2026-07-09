"""
One-time cleanup: wipe Sumner's Auto Care from targeted_prospects, competitors,
businesses, and generated_reports so it can be re-added from scratch.

Run from Render shell:
  cd /app && python cleanup_sumners.py
"""
import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]
conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()

BUSINESS_NAME_LIKE = "%sumner%"

# 1. Find matching businesses
cur.execute("SELECT id, name FROM businesses WHERE LOWER(name) LIKE %s", (BUSINESS_NAME_LIKE,))
businesses = cur.fetchall()
print(f"Found {len(businesses)} business record(s):")
for b in businesses:
    print(f"  {b['id']} — {b['name']}")

business_ids = [str(b["id"]) for b in businesses]

# 2. Find and delete targeted_prospects
cur.execute(
    "SELECT id, business_name, status FROM targeted_prospects WHERE LOWER(business_name) LIKE %s",
    (BUSINESS_NAME_LIKE,),
)
prospects = cur.fetchall()
print(f"\nFound {len(prospects)} targeted prospect(s):")
for p in prospects:
    print(f"  {p['id']} — {p['business_name']} [{p['status']}]")

prospect_ids = [str(p["id"]) for p in prospects]

# 3. Find generated_reports tied to these prospects or businesses
report_ids = []
if business_ids:
    cur.execute(
        "SELECT id FROM generated_reports WHERE business_id = ANY(%s::uuid[])",
        (business_ids,),
    )
    report_ids = [str(r["id"]) for r in cur.fetchall()]
    print(f"\nFound {len(report_ids)} generated report(s) for this business.")

# 4. Confirm before deleting
print("\n--- About to delete all of the above. Press Enter to confirm or Ctrl+C to abort. ---")
input()

if prospect_ids:
    cur.execute("DELETE FROM targeted_prospects WHERE id = ANY(%s::uuid[])", (prospect_ids,))
    print(f"Deleted {cur.rowcount} targeted prospect(s).")

if report_ids:
    cur.execute("DELETE FROM generated_reports WHERE id = ANY(%s::uuid[])", (report_ids,))
    print(f"Deleted {cur.rowcount} generated report(s).")

if business_ids:
    cur.execute("DELETE FROM competitors WHERE business_id = ANY(%s::uuid[])", (business_ids,))
    print(f"Deleted {cur.rowcount} competitor record(s).")

    cur.execute("DELETE FROM businesses WHERE id = ANY(%s::uuid[])", (business_ids,))
    print(f"Deleted {cur.rowcount} business record(s).")

conn.commit()
cur.close()
conn.close()
print("\nDone. Sumner's Auto Care has been fully cleared.")
