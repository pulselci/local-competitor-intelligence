"""
Patch routes.py: add is_test field to /followups/report-prospects response.

Run from backend/:
    python patch_followup_sandbox.py
"""
from pathlib import Path

ROUTES = Path(__file__).resolve().parent / "app" / "api" / "routes.py"
text = ROUTES.read_text(encoding="utf-8")

CHANGES = [
    # Add is_test to SELECT
    (
        "                SELECT\n"
        "                    b.id::text            AS business_id,\n"
        "                    b.name                AS business_name,\n"
        "                    b.notes,\n"
        "                    rdl.recipient_email   AS contact_email,\n",

        "                SELECT\n"
        "                    b.id::text            AS business_id,\n"
        "                    b.name                AS business_name,\n"
        "                    b.notes,\n"
        "                    COALESCE(b.is_test, false) AS is_test,\n"
        "                    rdl.recipient_email   AS contact_email,\n",
    ),

    # Add is_test to returned dict
    (
        '                    "subscribed":     bool(r["subscribed"]),\n'
        "                }\n"
        "                for r in rows\n"
        "            ]\n"
        "\n"
        "\n"
        "@router.get(\"/followups/cold-prospects\")",

        '                    "subscribed":     bool(r["subscribed"]),\n'
        '                    "is_test":        bool(r["is_test"]),\n'
        "                }\n"
        "                for r in rows\n"
        "            ]\n"
        "\n"
        "\n"
        "@router.get(\"/followups/cold-prospects\")",
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
