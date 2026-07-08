from app.core.db import get_conn
import app.core.db as db

BUSINESS_ID = "29a52299-3d54-41a1-a5c5-dcf95d3efab7"
PREV_GR = "1b338265-eca7-4f0d-8fa8-f832e61f374f"
NEW_GR  = "c7cade38-f4fe-416a-aa48-92c80beccd55"

def gr_period(cur, gr_id):
    cur.execute("select period_start, period_end from generated_reports where id=%s", (gr_id,))
    r = cur.fetchone()
    return r["period_start"], r["period_end"]

try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            for label, gr_id in (("PREV", PREV_GR), ("NEW", NEW_GR)):
                ps, pe = gr_period(cur, gr_id)
                print("")
                print(f"{label} gr_id={gr_id}")
                print("period_start=", ps)
                print("period_end  =", pe)

                # A) competitors with ANY snapshot <= period_end (correct inclusion universe)
                cur.execute("""
                    select count(distinct competitor_id) as n
                    from snapshots
                    where business_id=%s and observed_at <= %s
                """, (BUSINESS_ID, pe))
                print("distinct competitors with snapshot <= period_end:", cur.fetchone())

                # B) competitors with snapshot within the window [period_start, period_end]
                cur.execute("""
                    select count(distinct competitor_id) as n
                    from snapshots
                    where business_id=%s and observed_at >= %s and observed_at <= %s
                """, (BUSINESS_ID, ps, pe))
                print("distinct competitors with snapshot within window:", cur.fetchone())

                # show who exists in-window (so we can see if it's only Prewitt)
                cur.execute("""
                    select competitor_id, max(observed_at) as max_observed_at
                    from snapshots
                    where business_id=%s and observed_at >= %s and observed_at <= %s
                    group by competitor_id
                    order by max_observed_at desc
                    limit 20
                """, (BUSINESS_ID, ps, pe))
                print("in-window competitors (up to 20):")
                for r in cur.fetchall():
                    print(r)

finally:
    if hasattr(db, "close_pool"):
        db.close_pool()
        print("[pool] closed")
