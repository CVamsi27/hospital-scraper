"""
Enrich a leads CSV with:
  - gmaps_link   — Google Maps directions URL (opens nav to that clinic)
  - reply        — empty, you fill: 'yes' / 'no' / 'no answer' / 'callback'
  - demo_date    — empty, you fill if reply == yes
  - no_reason    — empty, you fill if reply == no

Then reorders columns into a calling-workflow layout (action columns first).

Usage:
    python enrich.py --in leads.csv --out leads.csv
    python enrich.py --in leads_nearby.csv --out leads_nearby.csv
    python enrich.py --in leads_growth.csv --out leads_growth.csv

Idempotent — re-running on an already-enriched file preserves any
reply/demo_date/no_reason values you've filled in.
"""

import argparse
import csv
import sys
from urllib.parse import quote_plus

# Calling-workflow order: action columns first, then context, then noisy IDs.
DESIRED_ORDER = [
    "name",
    "phone",
    "gmaps_link",
    "reply",
    "demo_date",
    "no_reason",
    "whatsapp_status",
    "whatsapp_template_key",
    "whatsapp_sent_at",
    "whatsapp_last_message",
    "email",
    "demo_score",
    "demo_tier",
    "distance_km",
    "address",
    "area",
    "growth_zone",
    "category",
    "rating",
    "reviews",
    "website",
    "business_status",
    "notes",
    "place_id",
    "lat",
    "lng",
]
NEW_COLUMNS = [
    "gmaps_link",
    "reply",
    "demo_date",
    "no_reason",
    "whatsapp_status",
    "whatsapp_template_key",
    "whatsapp_sent_at",
    "whatsapp_last_message",
]


def gmaps_directions_url(row: dict) -> str:
    """
    Build a maps.google.com directions deep-link.

    Uses destination_place_id when available (most accurate — pins the exact
    business). Falls back to lat/lng or address.
    Opens in Google Maps app on mobile, web on desktop.
    """
    place_id = row.get("place_id", "").strip()
    lat = (row.get("lat") or "").strip()
    lng = (row.get("lng") or "").strip()
    name = row.get("name", "").strip()
    address = row.get("address", "").strip()

    base = "https://www.google.com/maps/dir/?api=1"

    if lat and lng and lat != "0" and lng != "0":
        dest = f"{lat},{lng}"
    elif address:
        dest = address
    else:
        dest = name
    parts = [f"destination={quote_plus(dest)}"]
    if place_id:
        parts.append(f"destination_place_id={quote_plus(place_id)}")
    parts.append("travelmode=driving")
    return base + "&" + "&".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Input is empty.", file=sys.stderr)
        return 1
    existing_fields = list(rows[0].keys())

    for r in rows:
        # Always (re)compute gmaps_link — it's deterministic from place data.
        r["gmaps_link"] = gmaps_directions_url(r)
        # Preserve any user-entered values; default to empty.
        for col in ("reply", "demo_date", "no_reason"):
            r.setdefault(col, "")
            if r[col] is None:
                r[col] = ""
        for col in (
            "whatsapp_status",
            "whatsapp_template_key",
            "whatsapp_sent_at",
            "whatsapp_last_message",
        ):
            r.setdefault(col, "")
            if r[col] is None:
                r[col] = ""

    # Build final column order: DESIRED_ORDER first (only columns that exist
    # or were just added), then any leftover original columns we didn't list.
    have = set(existing_fields) | set(NEW_COLUMNS)
    ordered = [c for c in DESIRED_ORDER if c in have]
    leftover = [c for c in existing_fields
                if c not in DESIRED_ORDER and c not in NEW_COLUMNS]
    fieldnames = ordered + leftover

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"Enriched {len(rows)} rows → {args.out}")
    print(f"Columns: {' | '.join(fieldnames)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
