"""
Filter leads.csv → leads_growth.csv, keeping only clinics in
new/upcoming residential corridors of Hyderabad.

The `area` column in leads.csv is just the nearest grid-cell label,
not the clinic's actual neighborhood. So we match against the full
address string instead — much more accurate.

Usage:
    python filter_growth.py --in leads.csv --out leads_growth.csv
"""

import argparse
import csv
import sys

# New / upcoming residential corridors in Hyderabad (May 2026).
# Match is case-insensitive substring on the address field.
GROWTH_KEYWORDS = [
    # West / Outer Ring
    "tellapur", "narsingi", "kokapet", "manchirevula", "puppalaguda",
    "nallagandla", "gopanpally", "tellapuram",
    # Northwest
    "bachupally", "nizampet", "pragathi nagar", "miyapur",
    "chandanagar", "beeramguda", "ameenpur",
    # North
    "kompally", "shamirpet", "medchal", "alwal", "suchitra",
    # South
    "adibatla", "tukkuguda", "shamshabad", "maheshwaram",
    "shadnagar", "kandukur",
    # Hitec corridor
    "financial district", "raidurg", "khajaguda",
    # East new growth
    "pocharam", "ghatkesar", "narapally", "uppal bhagayath",
    "boduppal", "peerzadiguda",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input", default="leads.csv")
    ap.add_argument("--out", default="leads_growth.csv")
    ap.add_argument("--min-score", type=int, default=60,
                    help="minimum demo_score to include (default 60 = T1+upper T2)")
    ap.add_argument("--require-phone", action="store_true", default=True,
                    help="exclude rows with no phone number")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Input is empty.", file=sys.stderr)
        return 1
    print(f"Loaded {len(rows)} rows")

    kept = []
    for r in rows:
        addr = (r.get("address") or "").lower()
        name = (r.get("name") or "").lower()
        haystack = addr + " " + name

        if not any(kw in haystack for kw in GROWTH_KEYWORDS):
            continue
        try:
            score = int(r.get("demo_score") or 0)
        except ValueError:
            score = 0
        if score < args.min_score:
            continue
        if args.require_phone and not r.get("phone"):
            continue
        if r.get("business_status") and r["business_status"] != "OPERATIONAL":
            continue

        # Annotate which growth zone matched (first keyword hit)
        zone = next(kw for kw in GROWTH_KEYWORDS if kw in haystack)
        r["growth_zone"] = zone
        kept.append(r)

    kept.sort(key=lambda r: (-int(r.get("demo_score") or 0),
                             r.get("growth_zone", "")))

    fieldnames = list(rows[0].keys())
    if "growth_zone" not in fieldnames:
        fieldnames.append("growth_zone")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(kept)

    print(f"Kept {len(kept)} growth-zone leads → {args.out}")

    # Summary by zone
    from collections import Counter
    zones = Counter(r["growth_zone"] for r in kept)
    print("\nBy growth zone:")
    for z, n in zones.most_common():
        print(f"  {z:25s} {n}")

    # Tier breakdown
    tiers = Counter(r.get("demo_tier", "") for r in kept)
    print("\nBy tier:")
    for t in sorted(tiers):
        print(f"  {t:20s} {tiers[t]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
