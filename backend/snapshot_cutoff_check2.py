from app.core.db import get_conn
import app.core.db as db

PREV_ID = "341158a0-8649-4898-a5ef-7e6b2cdadeda"
NEW_ID  = "1b338265-eca7-4f0d-8fa8-f832e61f374f"

PREWITT_ID = "77fd95bc-b36c-40e6-a5ed-e6e39ee11ba4"
OWNER_ID   = "fc5e3513-8be1-4b58-a9c6-874553060bd4"

def latest_snapshot(cur, competitor_id, cutoff):
    cur.execute("""
        select competitor_id, observed_at, google_review_count
        from snapshots
        where competitor_id=%s and observed_at <= %s
        order by observed_at desc
        limit 1
    """, (competitor_id, cutoff))
    return cur.fetchone()

try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id, period_end from generated_reports where id in (%s,%s)", (PREV_ID, NEW_ID))
            rows = cur.fetchall()
            end_map = {}
            for r in rows:
                rid = str(r["id"])
                end_map[rid] = r["period_end"]

            for label, rid in (("PREV", PREV_ID), ("NEW", NEW_ID)):
                pend = end_map[rid]
                print("")
                print(f"{label}_period_end = {pend}")

                print("Prewitt latest snapshot <=", latest_snapshot(cur, PREWITT_ID, pend))
                print("Owner   latest snapshot <=", latest_snapshot(cur, OWNER_ID, pend))

finally:
    if hasattr(db, "close_pool"):
        db.close_pool()
        print("[pool] closed")
