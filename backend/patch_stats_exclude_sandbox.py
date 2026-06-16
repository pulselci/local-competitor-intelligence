"""
Patch routes.py: exclude sandbox (is_test) businesses from /admin/stats.

Affected metrics:
- Active subscribers / churn (safety filter)
- reports_sent_to_businesses (conversion denominator)
- total_reports_delivered
- total_businesses
- total_reports_generated
- new_subs_30d / YTD revenue / all-time revenue (safety filter)

Run from backend/:
    python patch_stats_exclude_sandbox.py
"""
from pathlib import Path

ROUTES = Path(__file__).resolve().parent / "app" / "api" / "routes.py"
text = ROUTES.read_text(encoding="utf-8")

CHANGES = [

    # 1. Subscribers — add is_test filter
    (
        "                SELECT stripe_price_id, COUNT(*) AS cnt\n"
        "                FROM businesses\n"
        "                WHERE is_active = true AND stripe_subscription_id IS NOT NULL\n"
        "                GROUP BY stripe_price_id\n",

        "                SELECT stripe_price_id, COUNT(*) AS cnt\n"
        "                FROM businesses\n"
        "                WHERE is_active = true AND stripe_subscription_id IS NOT NULL\n"
        "                  AND COALESCE(is_test, false) = false\n"
        "                GROUP BY stripe_price_id\n",
    ),

    # 2. Churn — add is_test filter
    (
        "                SELECT COUNT(*) AS cnt FROM businesses\n"
        "                WHERE is_active = false AND stripe_subscription_id IS NOT NULL\n",

        "                SELECT COUNT(*) AS cnt FROM businesses\n"
        "                WHERE is_active = false AND stripe_subscription_id IS NOT NULL\n"
        "                  AND COALESCE(is_test, false) = false\n",
    ),

    # 3. reports_sent_to_businesses — join businesses to filter sandbox
    (
        "                SELECT COUNT(DISTINCT gr.business_id) AS cnt\n"
        "                FROM generated_reports gr\n"
        "                JOIN report_delivery_logs rdl ON rdl.report_id = gr.id\n"
        "                WHERE rdl.status = 'sent'\n",

        "                SELECT COUNT(DISTINCT gr.business_id) AS cnt\n"
        "                FROM generated_reports gr\n"
        "                JOIN report_delivery_logs rdl ON rdl.report_id = gr.id\n"
        "                JOIN businesses b ON b.id = gr.business_id\n"
        "                WHERE rdl.status = 'sent'\n"
        "                  AND COALESCE(b.is_test, false) = false\n",
    ),

    # 4. report_to_sub_converted — add is_test filter
    (
        "                SELECT COUNT(DISTINCT b.id) AS cnt\n"
        "                FROM businesses b\n"
        "                WHERE b.is_active = true AND b.stripe_subscription_id IS NOT NULL\n"
        "                  AND EXISTS (\n"
        "                      SELECT 1 FROM generated_reports gr\n"
        "                      JOIN report_delivery_logs rdl ON rdl.report_id = gr.id\n"
        "                      WHERE gr.business_id = b.id AND rdl.status = 'sent'\n"
        "                  )\n",

        "                SELECT COUNT(DISTINCT b.id) AS cnt\n"
        "                FROM businesses b\n"
        "                WHERE b.is_active = true AND b.stripe_subscription_id IS NOT NULL\n"
        "                  AND COALESCE(b.is_test, false) = false\n"
        "                  AND EXISTS (\n"
        "                      SELECT 1 FROM generated_reports gr\n"
        "                      JOIN report_delivery_logs rdl ON rdl.report_id = gr.id\n"
        "                      WHERE gr.business_id = b.id AND rdl.status = 'sent'\n"
        "                  )\n",
    ),

    # 5. total_reports_delivered — join through businesses to exclude sandbox
    (
        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM report_delivery_logs WHERE status = 'sent'\")\n"
        "            total_reports_delivered = (cur.fetchone() or {}).get(\"cnt\", 0)\n",

        "            cur.execute(\"\"\"\n"
        "                SELECT COUNT(*) AS cnt\n"
        "                FROM report_delivery_logs rdl\n"
        "                JOIN generated_reports gr ON gr.id = rdl.report_id\n"
        "                JOIN businesses b ON b.id = gr.business_id\n"
        "                WHERE rdl.status = 'sent'\n"
        "                  AND COALESCE(b.is_test, false) = false\n"
        "            \"\"\")\n"
        "            total_reports_delivered = (cur.fetchone() or {}).get(\"cnt\", 0)\n",
    ),

    # 6. total_businesses — exclude sandbox
    (
        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM businesses\")\n"
        "            total_businesses = (cur.fetchone() or {}).get(\"cnt\", 0)\n",

        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM businesses WHERE COALESCE(is_test, false) = false\")\n"
        "            total_businesses = (cur.fetchone() or {}).get(\"cnt\", 0)\n",
    ),

    # 7. total_reports_generated — exclude sandbox
    (
        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM generated_reports\")\n"
        "            total_reports_generated = (cur.fetchone() or {}).get(\"cnt\", 0)\n",

        "            cur.execute(\"\"\"\n"
        "                SELECT COUNT(*) AS cnt\n"
        "                FROM generated_reports gr\n"
        "                JOIN businesses b ON b.id = gr.business_id\n"
        "                WHERE COALESCE(b.is_test, false) = false\n"
        "            \"\"\")\n"
        "            total_reports_generated = (cur.fetchone() or {}).get(\"cnt\", 0)\n",
    ),

    # 8. new_subs_30d — add is_test filter
    (
        "                SELECT COUNT(*) AS cnt FROM businesses\n"
        "                WHERE is_active = true AND stripe_subscription_id IS NOT NULL\n"
        "                  AND created_at >= NOW() - INTERVAL '30 days'\n",

        "                SELECT COUNT(*) AS cnt FROM businesses\n"
        "                WHERE is_active = true AND stripe_subscription_id IS NOT NULL\n"
        "                  AND COALESCE(is_test, false) = false\n"
        "                  AND created_at >= NOW() - INTERVAL '30 days'\n",
    ),

    # 9. YTD revenue — add is_test filter
    (
        "                SELECT stripe_price_id, created_at FROM businesses\n"
        "                WHERE is_active = true AND stripe_subscription_id IS NOT NULL\n"
        "                  AND created_at >= DATE_TRUNC('year', NOW())\n",

        "                SELECT stripe_price_id, created_at FROM businesses\n"
        "                WHERE is_active = true AND stripe_subscription_id IS NOT NULL\n"
        "                  AND COALESCE(is_test, false) = false\n"
        "                  AND created_at >= DATE_TRUNC('year', NOW())\n",
    ),

    # 10. All-time revenue — add is_test filter
    (
        "                SELECT stripe_price_id, created_at FROM businesses\n"
        "                WHERE stripe_subscription_id IS NOT NULL\n",

        "                SELECT stripe_price_id, created_at FROM businesses\n"
        "                WHERE stripe_subscription_id IS NOT NULL\n"
        "                  AND COALESCE(is_test, false) = false\n",
    ),
]

patched = text
for old, new in CHANGES:
    if old not in patched:
        print(f"[WARN] Could not find:\n  {old[:80]!r}")
        continue
    patched = patched.replace(old, new, 1)
    print(f"[OK] {old[:70]!r}...")

ROUTES.write_text(patched, encoding="utf-8")
print(f"\nDone — wrote {ROUTES}")
