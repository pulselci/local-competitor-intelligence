"""
Patch routes.py:
1. Add is_test to /followups/cold-prospects response
2. Exclude is_test prospects from /admin/stats cold outreach counts

Run from backend/:
    python patch_outreach_sandbox.py
"""
from pathlib import Path

ROUTES = Path(__file__).resolve().parent / "app" / "api" / "routes.py"
text = ROUTES.read_text(encoding="utf-8")

CHANGES = [

    # 1. Add is_test to cold-prospects SELECT
    (
        "                SELECT\n"
        "                    id::text, business_name, contact_email,\n"
        "                    city, state, sent_at,\n"
        "                    followup1_sent_at, followup2_sent_at, status\n"
        "                FROM outreach_prospects\n"
        "                WHERE status IN ('sent', 'converted')\n",

        "                SELECT\n"
        "                    id::text, business_name, contact_email,\n"
        "                    city, state, sent_at,\n"
        "                    followup1_sent_at, followup2_sent_at, status,\n"
        "                    COALESCE(is_test, false) AS is_test\n"
        "                FROM outreach_prospects\n"
        "                WHERE status IN ('sent', 'converted')\n",
    ),

    # 2. Add is_test to cold-prospects return dict
    (
        '                    "status":             r["status"],\n'
        "                }\n"
        "                for r in rows\n"
        "            ]\n"
        "\n"
        "\n"
        "\n"
        "\n"
        "@router.get(\"/admin/stats\")",

        '                    "status":             r["status"],\n'
        '                    "is_test":            bool(r["is_test"]),\n'
        "                }\n"
        "                for r in rows\n"
        "            ]\n"
        "\n"
        "\n"
        "\n"
        "\n"
        "@router.get(\"/admin/stats\")",
    ),

    # 3. Exclude is_test from cold_total in admin/stats
    (
        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM outreach_prospects WHERE status IN ('sent','converted','bounced','skipped')\")\n"
        "            cold_total = (cur.fetchone() or {}).get(\"cnt\", 0)\n",

        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM outreach_prospects WHERE status IN ('sent','converted','bounced','skipped') AND COALESCE(is_test, false) = false\")\n"
        "            cold_total = (cur.fetchone() or {}).get(\"cnt\", 0)\n",
    ),

    # 4. Exclude is_test from cold_converted in admin/stats
    (
        "                SELECT COUNT(DISTINCT op.id) AS cnt\n"
        "                FROM outreach_prospects op\n"
        "                WHERE op.contact_email IS NOT NULL\n"
        "                  AND op.status IN ('sent','converted','bounced','skipped')\n",

        "                SELECT COUNT(DISTINCT op.id) AS cnt\n"
        "                FROM outreach_prospects op\n"
        "                WHERE op.contact_email IS NOT NULL\n"
        "                  AND COALESCE(op.is_test, false) = false\n"
        "                  AND op.status IN ('sent','converted','bounced','skipped')\n",
    ),

    # 5. Exclude is_test from total_prospects_discovered
    (
        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM outreach_prospects\")\n"
        "            total_prospects_discovered = (cur.fetchone() or {}).get(\"cnt\", 0)\n",

        "            cur.execute(\"SELECT COUNT(*) AS cnt FROM outreach_prospects WHERE COALESCE(is_test, false) = false\")\n"
        "            total_prospects_discovered = (cur.fetchone() or {}).get(\"cnt\", 0)\n",
    ),

    # 6. Exclude is_test from cold follow-up stats
    (
        "                SELECT\n"
        "                    COUNT(*) FILTER (WHERE followup1_sent_at IS NOT NULL) AS fu1,\n"
        "                    COUNT(*) FILTER (WHERE followup2_sent_at IS NOT NULL) AS fu2\n"
        "                FROM outreach_prospects WHERE status IN ('sent','converted')\n",

        "                SELECT\n"
        "                    COUNT(*) FILTER (WHERE followup1_sent_at IS NOT NULL) AS fu1,\n"
        "                    COUNT(*) FILTER (WHERE followup2_sent_at IS NOT NULL) AS fu2\n"
        "                FROM outreach_prospects WHERE status IN ('sent','converted')\n"
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
