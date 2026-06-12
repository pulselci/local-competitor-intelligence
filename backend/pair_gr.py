from app.core.db import get_conn
import app.core.db as db

BUSINESS_ID = "29a52299-3d54-41a1-a5c5-dcf95d3efab7"

try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select id, period_start, period_end, generated_at, status, title
                from generated_reports
                where business_id=%s
                order by period_end desc, generated_at desc
                limit 2
            """, (BUSINESS_ID,))
            print("--- Step 5/6 comparison pair (newest, previous) ---")
            for r in cur.fetchall():
                print(r)
finally:
    if hasattr(db, "close_pool"):
        db.close_pool()
        print("[pool] closed")
