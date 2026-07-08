from app.core.db import get_conn
import app.core.db as db

CID = "77fd95bc-b36c-40e6-a5ed-e6e39ee11ba4"

try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select competitor_id, observed_at, google_review_count
                from snapshots
                where competitor_id=%s
                order by observed_at desc
                limit 10
            """, (CID,))
            print("--- latest 10 snapshots for Prewitt ---")
            for r in cur.fetchall():
                print(r)
finally:
    if hasattr(db, "close_pool"):
        db.close_pool()
        print("[pool] closed")
