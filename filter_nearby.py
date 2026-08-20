"""
Sort leads.csv by distance from a chosen point (default: Madinaguda,
Miyapur). Optionally filter by max radius, score, phone presence.

Usage:
    # Default: all 4,731 leads, sorted nearest-first from Madinaguda
    python filter_nearby.py

    # Within 5 km of home, T1+T2 only
    python filter_nearby.py --radius 5 --min-score 55

    # Custom center
    python filter_nearby.py --lat 17.49 --lng 78.37 --radius 3
"""

import argparse
import csv
import math
import sys
from collections import Counter

# Madinaguda, Miyapur — approx center of the locality.
DEFAULT_LAT = 17.4924
DEFAULT_LNG = 78.3677


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input", default="leads.csv")
    ap.add_argument("--out", default="leads_nearby.csv")
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lng", type=float, default=DEFAULT_LNG)
    ap.add_argument("--radius", type=float, default=0,
                    help="max distance in km (0 = no limit, just sort)")
    ap.add_argument("--min-score", type=int, default=0,
                    help="minimum demo_score (default 0 = include all)")
    ap.add_argument("--require-phone", action="store_true", default=True)
    ap.add_argument("--all-statuses", action="store_true",
                    help="include non-operational places")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Input is empty.", file=sys.stderr)
        return 1
    print(f"Loaded {len(rows)} rows; center = ({args.lat}, {args.lng})")

    enriched = []
    for r in rows:
        try:
            lat = float(r.get("lat") or 0)
            lng = float(r.get("lng") or 0)
        except ValueError:
            continue
        if lat == 0 or lng == 0:
            continue
        dist = haversine_km(args.lat, args.lng, lat, lng)
        r["distance_km"] = f"{dist:.2f}"
        enriched.append((dist, r))

    kept = []
    for dist, r in enriched:
        if args.radius and dist > args.radius:
            continue
        try:
            score = int(r.get("demo_score") or 0)
        except ValueError:
            score = 0
        if score < args.min_score:
            continue
        if args.require_phone and not r.get("phone"):
            continue
        if (not args.all_statuses
                and r.get("business_status")
                and r["business_status"] != "OPERATIONAL"):
            continue
        kept.append((dist, r))

    kept.sort(key=lambda x: (x[0], -int(x[1].get("demo_score") or 0)))

    fieldnames = list(rows[0].keys())
    if "distance_km" not in fieldnames:
        fieldnames.append("distance_km")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(r for _, r in kept)

    print(f"\nKept {len(kept)} leads → {args.out}")

    if not kept:
        return 0

    # Distance buckets
    buckets = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 20), (20, 999)]
    print("\nBy distance from home:")
    for lo, hi in buckets:
        n = sum(1 for d, _ in kept if lo <= d < hi)
        if n:
            label = f"{lo}–{hi} km" if hi < 999 else f"{lo}+ km"
            print(f"  {label:10s} {n}")

    # Tier breakdown of nearest 50
    near50 = kept[:50]
    tiers = Counter(r.get("demo_tier", "") for _, r in near50)
    print(f"\nNearest-50 tier mix:")
    for t in sorted(tiers):
        print(f"  {t:20s} {tiers[t]}")

    print("\nNearest 15 leads:")
    for d, r in kept[:15]:
        name = (r.get("name") or "")[:45]
        phone = r.get("phone") or "(no phone)"
        score = r.get("demo_score", "")
        print(f"  {d:5.2f} km  s{score:>3}  {name:45s}  {phone}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
