"""
Patch sweep — fill gaps in an existing leads.csv by re-scanning Hyderabad
with smaller cells and specialty keywords, then APPEND only new finds.

The original scraper.py hits the Google Places 60-result-per-query cap in
dense areas. Pulse Heart Center (3,459 reviews) was missed in Kukatpally
because >60 hospitals are within 3km of that grid point.

Strategy:
  - Tighter radius (1.5 km) so each query has fewer competitors → fewer truncations
  - Denser grid (~70 points) covering dense medical corridors
  - Specialty keywords (each returns its own top-60: heart, dental, eye, etc.)
  - Read existing leads.csv → skip every place_id already present
  - Append new finds, then run enrichment + filters

Usage:
    python patch_sweep.py
    python patch_sweep.py --radius 1500 --max-per-cell 60
"""

import argparse
import csv
import json
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

# Reuse pieces from scraper.py
from scraper import (  # type: ignore
    Lead, score_demo_probability, _get_with_retry,
    PLACES_NEARBY, PLACES_DETAILS,
)

# Denser grid: smaller radius means we need more cells. Below is ~70 points
# laid out across Hyderabad with extra density in known-saturated zones
# (Kukatpally/KPHB, Madhapur, LB Nagar, Secunderabad).
DENSE_GRID: list[tuple[float, float, str]] = [
    # Madinaguda / Miyapur cluster (user's home base — extra dense)
    (17.4924, 78.3677, "Madinaguda"),
    (17.4933, 78.3915, "Miyapur"),
    (17.4980, 78.3780, "Miyapur-W"),
    (17.4860, 78.3580, "Chandanagar"),
    (17.5040, 78.3870, "Bachupally"),
    (17.5100, 78.3700, "Pragathi Nagar"),
    (17.4950, 78.4000, "Nizampet"),
    # Kukatpally / KPHB (where Pulse Heart was missed)
    (17.4849, 78.4138, "Kukatpally"),
    (17.4924, 78.3905, "KPHB-1"),
    (17.4800, 78.4050, "KPHB-2"),
    (17.4750, 78.4180, "Kukatpally-S"),
    (17.4960, 78.4080, "Allwyn Colony"),
    # Madhapur / Hitec / Kondapur
    (17.4474, 78.3914, "Madhapur"),
    (17.4504, 78.3808, "Kondapur"),
    (17.4400, 78.3850, "Hitec City"),
    (17.4380, 78.3760, "Botanical Garden"),
    (17.4520, 78.4040, "Yousufguda"),
    (17.4380, 78.4040, "Jubilee Hills-W"),
    (17.4319, 78.4099, "Jubilee Hills"),
    (17.4250, 78.4030, "Gachibowli"),
    (17.4180, 78.3850, "Nallagandla"),
    (17.4127, 78.3460, "Tellapur"),
    (17.4000, 78.3500, "Narsingi"),
    (17.4080, 78.3270, "Kokapet"),
    (17.4180, 78.3680, "Manchirevula"),
    (17.4480, 78.3760, "Financial District"),
    # Central
    (17.4239, 78.4738, "Banjara Hills"),
    (17.4170, 78.4500, "SR Nagar"),
    (17.4520, 78.4670, "Ameerpet"),
    (17.4400, 78.4480, "Begumpet"),
    (17.4399, 78.4983, "Secunderabad"),
    (17.4450, 78.5050, "Marredpally"),
    (17.4280, 78.5000, "Padmarao Nagar"),
    # South
    (17.3850, 78.4867, "Koti/Abids"),
    (17.3950, 78.4400, "Mehdipatnam"),
    (17.3700, 78.4400, "Tolichowki"),
    (17.3800, 78.4570, "Masab Tank"),
    (17.3680, 78.4720, "Charminar"),
    (17.3300, 78.4400, "Rajendranagar"),
    (17.3170, 78.4830, "Falaknuma"),
    # East
    (17.4000, 78.5400, "Uppal"),
    (17.3950, 78.5550, "Boduppal"),
    (17.3850, 78.5450, "Nagole"),
    (17.4200, 78.5500, "Malkajgiri"),
    (17.4600, 78.5400, "ECIL"),
    (17.4470, 78.5500, "Sainikpuri"),
    (17.4700, 78.5550, "Kapra"),
    # LB Nagar corridor
    (17.3500, 78.5500, "LB Nagar"),
    (17.3680, 78.5350, "Dilsukhnagar"),
    (17.3400, 78.5650, "Vanasthalipuram"),
    (17.3320, 78.5400, "Hayathnagar"),
    # North
    (17.5450, 78.4960, "Kompally"),
    (17.5350, 78.5050, "Suchitra"),
    (17.5240, 78.5110, "Shamirpet"),
    (17.5180, 78.4970, "Medchal-S"),
    (17.4750, 78.5050, "Bowenpally"),
    (17.4900, 78.5100, "Alwal"),
    # Far south growth
    (17.2990, 78.5570, "Adibatla"),
    (17.2470, 78.6020, "Tukkuguda"),
    (17.2400, 78.4300, "Shamshabad"),
    (17.2800, 78.4900, "Maheshwaram"),
]

SPECIALTY_KEYWORDS = [
    # generic
    "clinic", "polyclinic", "nursing home", "hospital", "diagnostic",
    # specialties — each returns its own ranked top-60 of medical results
    "heart", "cardiology", "cardiac",
    "dental", "dentist", "orthodontist",
    "eye care", "ophthalmology", "lasik",
    "ENT",
    "children hospital", "pediatric", "child specialist",
    "gynecology", "maternity", "fertility", "women hospital",
    "ortho", "orthopedic", "spine",
    "skin", "dermatology", "cosmetic clinic",
    "physio", "physiotherapy",
    "ayurveda", "homeopathy", "homeo",
    "neuro", "neurology",
    "kidney", "nephrology", "dialysis",
    "cancer", "oncology",
    "pathology lab", "scan centre",
    "wellness clinic", "multispeciality",
]

# Search types (legacy Places "types" enum) — used in addition to keywords.
SEARCH_TYPES = ["hospital", "doctor", "dentist", "physiotherapist"]


def nearby_search(api_key: str, lat: float, lng: float, radius: int,
                  type_: str | None = None, keyword: str | None = None
                  ) -> Iterable[dict]:
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
            print(f"  ! status={data.get('status')} {data.get('error_message','')}",
                  file=sys.stderr)
            return
        for item in data.get("results", []):
            yield item
        token = data.get("next_page_token")
        if not token:
            return
        time.sleep(2)
        params = {"pagetoken": token, "key": api_key}


def place_details(api_key: str, place_id: str) -> dict:
    fields = ",".join([
        "name", "formatted_phone_number", "international_phone_number",
        "website", "formatted_address", "rating", "user_ratings_total",
        "business_status", "types", "geometry/location",
    ])
    data = _get_with_retry(PLACES_DETAILS,
                           {"place_id": place_id, "fields": fields,
                            "key": api_key})
    if not data or data.get("status") != "OK":
        return {}
    return data.get("result", {})


def load_existing(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {row["place_id"] for row in csv.DictReader(f)
                if row.get("place_id")}


def append_new(path: str, new_leads: list[Lead]) -> None:
    """Append rows to leads.csv keeping the existing column order."""
    if not new_leads:
        return
    with open(path, encoding="utf-8") as f:
        existing_fields = list(csv.DictReader(f).fieldnames or [])

    # Map Lead → row using existing column names (extras blank, missing fine)
    def lead_to_row(l: Lead) -> dict:
        d = asdict(l)
        return {c: d.get(c, "") for c in existing_fields}

    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=existing_fields,
                           extrasaction="ignore")
        for l in new_leads:
            w.writerow(lead_to_row(l))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="leads.csv",
                    help="leads CSV to read existing place_ids from and "
                         "append new finds to")
    ap.add_argument("--radius", type=int, default=1500)
    ap.add_argument("--max-per-cell", type=int, default=300)
    ap.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY"))
    args = ap.parse_args()

    if not args.api_key:
        print("ERROR: GOOGLE_MAPS_API_KEY missing", file=sys.stderr)
        return 2

    existing_pids = load_existing(args.out)
    print(f"Existing leads.csv has {len(existing_pids)} place_ids")
    print(f"Patch grid: {len(DENSE_GRID)} cells × radius {args.radius}m")
    print(f"Keywords: {len(SPECIALTY_KEYWORDS)}, types: {len(SEARCH_TYPES)}")

    new_seen: dict[str, Lead] = {}

    for i, (lat, lng, area) in enumerate(DENSE_GRID, 1):
        print(f"[{i}/{len(DENSE_GRID)}] {area} ({lat},{lng})")
        cell_new = 0

        def consume(raw: dict, source: str) -> None:
            nonlocal cell_new
            pid = raw.get("place_id")
            if not pid or pid in existing_pids or pid in new_seen:
                return
            if cell_new >= args.max_per_cell:
                return
            new_seen[pid] = Lead(
                name=raw.get("name", ""),
                place_id=pid,
                address=raw.get("vicinity", ""),
                area=area,
                category=source,
                rating=float(raw.get("rating", 0) or 0),
                reviews=int(raw.get("user_ratings_total", 0) or 0),
                lat=raw.get("geometry", {}).get("location", {}).get("lat", 0),
                lng=raw.get("geometry", {}).get("location", {}).get("lng", 0),
                business_status=raw.get("business_status", ""),
            )
            cell_new += 1

        for t in SEARCH_TYPES:
            for raw in nearby_search(args.api_key, lat, lng, args.radius,
                                     type_=t):
                consume(raw, t)
        for kw in SPECIALTY_KEYWORDS:
            for raw in nearby_search(args.api_key, lat, lng, args.radius,
                                     keyword=kw):
                consume(raw, kw)

        print(f"   new this cell: {cell_new} | running new total: "
              f"{len(new_seen)}")

    print(f"\n{len(new_seen)} new places (not in existing CSV)")
    if not new_seen:
        print("No new finds. Existing CSV is already saturated.")
        return 0

    print(f"Fetching details for {len(new_seen)} new places...")
    new_leads = list(new_seen.values())
    for i, lead in enumerate(new_leads, 1):
        if i % 50 == 0:
            print(f"  details {i}/{len(new_leads)}")
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

    for lead in new_leads:
        s, t, n = score_demo_probability(lead)
        lead.demo_score, lead.demo_tier, lead.notes = s, t, n

    append_new(args.out, new_leads)
    print(f"\nAppended {len(new_leads)} new leads to {args.out}")

    # Quick check: confirm Pulse Heart specifically landed
    pulse_hits = [l for l in new_leads if "pulse heart" in l.name.lower()]
    if pulse_hits:
        print(f"\n✓ Pulse Heart found: {pulse_hits[0].name} ({pulse_hits[0].phone})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
