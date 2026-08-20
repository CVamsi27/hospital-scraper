"""
Pharmacy-only patch sweep. Pharmacies are useful for TWO outreach plays:
  1. Referral channel — pharmacists know every new clinic in their area
  2. Distribution — they can hand your card to doctors who walk in

Runs against the same dense grid as patch_sweep.py but only with
type=pharmacy and a few pharmacy-keyword variants. Appends to leads.csv,
deduped by place_id.

Usage:
    python patch_pharmacy.py
"""

import argparse
import csv
import os
import sys
import time
from dataclasses import asdict
from typing import Iterable

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from scraper import (  # type: ignore
    Lead, score_demo_probability, _get_with_retry,
    PLACES_NEARBY, PLACES_DETAILS,
)
from patch_sweep import DENSE_GRID  # type: ignore

PHARMACY_KEYWORDS = [
    "pharmacy", "medical store", "medical hall", "chemist",
    "drug store", "medical shop",
]


def nearby_search(api_key, lat, lng, radius, type_=None, keyword=None):
    params = {"location": f"{lat},{lng}", "radius": radius, "key": api_key}
    if type_:
        params["type"] = type_
    if keyword:
        params["keyword"] = keyword
    while True:
        data = _get_with_retry(PLACES_NEARBY, params)
        if data is None:
            return
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            print(f"  ! {data.get('status')} {data.get('error_message','')}",
                  file=sys.stderr)
            return
        for item in data.get("results", []):
            yield item
        token = data.get("next_page_token")
        if not token:
            return
        time.sleep(2)
        params = {"pagetoken": token, "key": api_key}


def place_details(api_key, place_id):
    fields = ",".join([
        "name", "formatted_phone_number", "international_phone_number",
        "website", "formatted_address", "rating", "user_ratings_total",
        "business_status", "geometry/location",
    ])
    data = _get_with_retry(PLACES_DETAILS,
                           {"place_id": place_id, "fields": fields,
                            "key": api_key})
    if not data or data.get("status") != "OK":
        return {}
    return data.get("result", {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="leads.csv")
    ap.add_argument("--radius", type=int, default=1500)
    ap.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY"))
    args = ap.parse_args()

    if not args.api_key:
        print("ERROR: GOOGLE_MAPS_API_KEY missing", file=sys.stderr)
        return 2

    # Load existing place_ids to skip
    existing = set()
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            existing = {r["place_id"] for r in csv.DictReader(f)
                        if r.get("place_id")}
    print(f"Existing leads: {len(existing)} place_ids")

    new_seen: dict[str, Lead] = {}

    for i, (lat, lng, area) in enumerate(DENSE_GRID, 1):
        print(f"[{i}/{len(DENSE_GRID)}] {area}")
        before = len(new_seen)

        for raw in nearby_search(args.api_key, lat, lng, args.radius,
                                 type_="pharmacy"):
            pid = raw.get("place_id")
            if not pid or pid in existing or pid in new_seen:
                continue
            new_seen[pid] = Lead(
                name=raw.get("name", ""),
                place_id=pid,
                address=raw.get("vicinity", ""),
                area=area,
                category="pharmacy",
                rating=float(raw.get("rating", 0) or 0),
                reviews=int(raw.get("user_ratings_total", 0) or 0),
                lat=raw.get("geometry", {}).get("location", {}).get("lat", 0),
                lng=raw.get("geometry", {}).get("location", {}).get("lng", 0),
                business_status=raw.get("business_status", ""),
            )

        for kw in PHARMACY_KEYWORDS:
            for raw in nearby_search(args.api_key, lat, lng, args.radius,
                                     keyword=kw):
                pid = raw.get("place_id")
                if not pid or pid in existing or pid in new_seen:
                    continue
                new_seen[pid] = Lead(
                    name=raw.get("name", ""),
                    place_id=pid,
                    address=raw.get("vicinity", ""),
                    area=area,
                    category="pharmacy",
                    rating=float(raw.get("rating", 0) or 0),
                    reviews=int(raw.get("user_ratings_total", 0) or 0),
                    lat=raw.get("geometry", {}).get("location", {}).get("lat", 0),
                    lng=raw.get("geometry", {}).get("location", {}).get("lng", 0),
                    business_status=raw.get("business_status", ""),
                )

        print(f"   +{len(new_seen) - before} new (total new: {len(new_seen)})")

    if not new_seen:
        print("No new pharmacies found.")
        return 0

    print(f"\nFetching details for {len(new_seen)} pharmacies...")
    leads = list(new_seen.values())
    for i, lead in enumerate(leads, 1):
        if i % 100 == 0:
            print(f"  details {i}/{len(leads)}")
        d = place_details(args.api_key, lead.place_id)
        if not d:
            continue
        lead.phone = d.get("formatted_phone_number") or d.get(
            "international_phone_number", "") or lead.phone
        lead.website = d.get("website", "") or lead.website
        lead.address = d.get("formatted_address", "") or lead.address
        lead.rating = float(d.get("rating", lead.rating) or lead.rating)
        lead.reviews = int(d.get("user_ratings_total", lead.reviews)
                           or lead.reviews)
        lead.business_status = d.get("business_status", lead.business_status)

    for lead in leads:
        s, t, n = score_demo_probability(lead)
        lead.demo_score, lead.demo_tier, lead.notes = s, t, n

    # Append to leads.csv with the existing column layout
    with open(args.out, encoding="utf-8") as f:
        fields = list(csv.DictReader(f).fieldnames or [])
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        for l in leads:
            d = asdict(l)
            w.writerow({c: d.get(c, "") for c in fields})

    print(f"\nAppended {len(leads)} pharmacies to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
