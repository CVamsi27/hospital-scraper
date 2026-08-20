"""
Hyderabad clinic & hospital scraper.

Pulls public listings from Google Places API, scrapes emails from clinic
websites, and ranks each lead by demo-acceptance probability — biased toward
new and small clinics (highest conversion) and against large hospital chains.

Usage:
    export GOOGLE_MAPS_API_KEY=AIza...
    python scraper.py --out leads.csv

Cost note: Places API charges ~$17 per 1k Place Details calls. Google gives
$200/month free credit which covers ~10k–11k detail lookups.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; env vars still work without it

PLACES_NEARBY = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACES_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
OBFUSCATED_EMAIL_RE = re.compile(
    r"([a-zA-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*"
    r"([a-zA-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\s+dot\s+)\s*([a-zA-Z]{2,})",
    re.IGNORECASE,
)

# Hyderabad coverage grid. Each tuple is (lat, lng, label). 3km radius per
# search; Places caps results at ~60 per query, so denser coverage in dense
# zones beats one big radius.
HYDERABAD_GRID: list[tuple[float, float, str]] = [
    # Core
    (17.4239, 78.4738, "Banjara Hills"),
    (17.4319, 78.4099, "Jubilee Hills"),
    (17.4474, 78.3914, "Madhapur/Hitec City"),
    (17.4504, 78.3808, "Kondapur"),
    (17.4849, 78.4138, "Kukatpally"),
    (17.4933, 78.3915, "Miyapur"),
    (17.5040, 78.3870, "Bachupally"),
    (17.4950, 78.4000, "Nizampet"),
    (17.5450, 78.4960, "Kompally"),
    (17.4400, 78.4480, "Begumpet"),
    (17.4399, 78.4983, "Secunderabad"),
    (17.4000, 78.5400, "Uppal"),
    (17.3500, 78.5500, "LB Nagar"),
    (17.3680, 78.5350, "Dilsukhnagar"),
    (17.3850, 78.4867, "Koti/Abids"),
    (17.3950, 78.4400, "Mehdipatnam"),
    (17.3300, 78.4400, "Rajendranagar"),
    (17.4200, 78.5500, "Malkajgiri"),
    (17.4600, 78.5400, "ECIL"),
    (17.4250, 78.4030, "Gachibowli"),
    (17.4127, 78.3460, "Tellapur"),
    (17.4000, 78.3500, "Narsingi"),
    (17.4080, 78.3270, "Kokapet"),
    (17.2990, 78.5570, "Adibatla"),
    (17.2470, 78.6020, "Tukkuguda"),
    (17.4480, 78.3760, "Financial District"),
    (17.5240, 78.5110, "Shamirpet"),
    (17.3700, 78.4800, "Mehdipatnam South"),
    (17.4520, 78.4670, "Ameerpet"),
    (17.4170, 78.4500, "SR Nagar"),
]

SEARCH_TYPES = ["hospital", "doctor"]
SEARCH_KEYWORDS = ["clinic", "polyclinic", "nursing home", "diagnostic"]

BIG_CHAINS = [
    "apollo", "yashoda", "kims", "care hospital", "continental", "rainbow",
    "sunshine", "asian institute", "medicover", "global hospital",
    "olive hospital", "star hospital", "aig", "citizen", "image hospital",
    "fernandez", "deccan", "preeti", "max cure",
]


@dataclass
class Lead:
    name: str
    place_id: str
    phone: str = ""
    email: str = ""
    website: str = ""
    address: str = ""
    area: str = ""
    category: str = ""
    rating: float = 0.0
    reviews: int = 0
    lat: float = 0.0
    lng: float = 0.0
    business_status: str = ""
    demo_score: int = 0
    demo_tier: str = ""
    notes: str = ""

    def csv_row(self) -> dict:
        d = asdict(self)
        return d


def _get_with_retry(url: str, params: dict, attempts: int = 5,
                    timeout: int = 20) -> dict | None:
    """
    GET with exponential backoff for transient failures.

    Retries on:
      - 5xx HTTP responses (Google occasionally returns 500 mid-pagination)
      - connection / read timeouts
      - JSON decode failures
    Returns None if all attempts fail. The caller decides what to do next.
    """
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if 500 <= r.status_code < 600:
                raise requests.HTTPError(f"server {r.status_code}", response=r)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt >= attempts:
                print(f"  ! giving up after {attempts} attempts: {e}",
                      file=sys.stderr)
                return None
            print(f"  ! attempt {attempt}/{attempts} failed ({e}); "
                  f"sleeping {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    return None


def nearby_search(api_key: str, lat: float, lng: float, radius: int,
                  type_: str | None = None, keyword: str | None = None
                  ) -> Iterable[dict]:
    """Yield raw place results, paginating through next_page_token."""
    params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "key": api_key,
    }
    if type_:
        params["type"] = type_
    if keyword:
        params["keyword"] = keyword

    while True:
        data = _get_with_retry(PLACES_NEARBY, params)
        if data is None:
            return
        status = data.get("status", "")
        if status not in ("OK", "ZERO_RESULTS"):
            print(f"  ! Places API status={status} {data.get('error_message','')}",
                  file=sys.stderr)
            return
        for item in data.get("results", []):
            yield item
        token = data.get("next_page_token")
        if not token:
            return
        # next_page_token needs a short delay before it's valid
        time.sleep(2)
        params = {"pagetoken": token, "key": api_key}


def place_details(api_key: str, place_id: str) -> dict:
    fields = ",".join([
        "name", "formatted_phone_number", "international_phone_number",
        "website", "formatted_address", "rating", "user_ratings_total",
        "business_status", "types", "geometry/location",
    ])
    data = _get_with_retry(
        PLACES_DETAILS,
        {"place_id": place_id, "fields": fields, "key": api_key},
    )
    if not data or data.get("status") != "OK":
        return {}
    return data.get("result", {})


def fetch_email_from_site(url: str, timeout: int = 8) -> str:
    """Best-effort email scrape from clinic homepage + /contact."""
    if not url:
        return ""
    candidates = [url.rstrip("/")]
    candidates.append(candidates[0] + "/contact")
    candidates.append(candidates[0] + "/contact-us")

    headers = {"User-Agent": "Mozilla/5.0 (clinic-lead-tool)"}
    found: set[str] = set()
    for u in candidates:
        try:
            r = requests.get(u, headers=headers, timeout=timeout,
                             allow_redirects=True)
            if r.status_code != 200 or not r.text:
                continue
            text = r.text
            for m in EMAIL_RE.findall(text):
                if any(bad in m.lower() for bad in
                       ("sentry.io", "wixpress.com", "example.com",
                        "yourdomain", "godaddy", "@2x", "png@", "jpg@")):
                    continue
                found.add(m.lower())
            for u_, d, t in OBFUSCATED_EMAIL_RE.findall(text):
                found.add(f"{u_}@{d}.{t}".lower())
            if found:
                break
        except requests.RequestException:
            continue
    if not found:
        return ""
    # prefer @clinic-domain emails over gmail/yahoo when both exist
    domain_emails = [e for e in found
                     if not any(p in e for p in ("gmail.", "yahoo.",
                                                 "hotmail.", "outlook."))]
    pick = sorted(domain_emails or list(found))
    return pick[0]


def score_demo_probability(lead: Lead) -> tuple[int, str, str]:
    """
    Heuristic 0–100 score.

    Higher = more likely to accept a demo. Weighted toward signals of new
    or small clinics (Docita's best ICP) and against signals of large
    hospital chains (long sales cycles, committee decisions).
    """
    score = 50
    notes: list[str] = []

    name_l = lead.name.lower()

    # Big chain detector: kills the score
    if any(c in name_l for c in BIG_CHAINS):
        score -= 35
        notes.append("big-chain")
    if "hospital" in name_l and lead.reviews > 1500:
        score -= 25
        notes.append("large-hospital")
    if "government" in name_l or "govt" in name_l or "g.h." in name_l:
        score -= 30
        notes.append("government")

    # Review-count signal: fewer reviews → newer/smaller → better lead
    r = lead.reviews
    if r == 0:
        score += 25
        notes.append("zero-reviews(new)")
    elif r <= 10:
        score += 30
        notes.append("very-new")
    elif r <= 50:
        score += 20
        notes.append("new-or-small")
    elif r <= 200:
        score += 8
        notes.append("established-small")
    elif r <= 1000:
        score -= 5
        notes.append("mid-size")
    else:
        score -= 15
        notes.append("large")

    # Name signals — single-doctor / specialty clinics convert best
    good_terms = ["clinic", "polyclinic", "dental", "skin", "child",
                  "pediatric", "derma", "ent", "physio", "homeo",
                  "ayurved", "eye care", "mother", "women",
                  "dr.", "dr ", "consulting"]
    if any(t in name_l for t in good_terms):
        score += 8
        notes.append("clinic-type")

    # Reachability: phone present is gold
    if lead.phone:
        score += 6
    else:
        score -= 10
        notes.append("no-phone")

    if lead.email:
        score += 4

    # No website but has phone → manual operation, ripe for digital
    if not lead.website and lead.phone and r < 200:
        score += 6
        notes.append("no-website")

    # Closed / temporarily closed
    if lead.business_status and lead.business_status != "OPERATIONAL":
        score -= 50
        notes.append(f"status={lead.business_status}")

    score = max(0, min(100, score))

    if score >= 75:
        tier = "T1-call-first"
    elif score >= 55:
        tier = "T2-call-soon"
    elif score >= 35:
        tier = "T3-later"
    else:
        tier = "T4-skip"

    return score, tier, ",".join(notes)


CHECKPOINT_FILE = "checkpoint.json"


def save_checkpoint(seen: dict, completed_cells: list[str]) -> None:
    try:
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "completed_cells": completed_cells,
                "seen": {k: asdict(v) for k, v in seen.items()},
            }, f)
    except OSError as e:
        print(f"  ! checkpoint write failed: {e}", file=sys.stderr)


def load_checkpoint() -> tuple[dict, list[str]]:
    if not os.path.exists(CHECKPOINT_FILE):
        return {}, []
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        seen = {k: Lead(**v) for k, v in data.get("seen", {}).items()}
        completed = data.get("completed_cells", [])
        print(f"Resuming from checkpoint: {len(seen)} places, "
              f"{len(completed)} cells already done")
        return seen, completed
    except (OSError, ValueError, TypeError) as e:
        print(f"  ! checkpoint load failed ({e}); starting fresh",
              file=sys.stderr)
        return {}, []


def harvest(api_key: str, radius: int, max_per_cell: int,
            scrape_emails: bool, resume: bool = True) -> list[Lead]:
    seen: dict[str, Lead] = {}
    completed_cells: list[str] = []
    if resume:
        seen, completed_cells = load_checkpoint()
    cells = HYDERABAD_GRID

    for i, (lat, lng, area) in enumerate(cells, 1):
        if area in completed_cells:
            print(f"[{i}/{len(cells)}] {area} — skipped (checkpoint)")
            continue
        print(f"[{i}/{len(cells)}] {area} ({lat},{lng})")
        cell_count = 0

        for type_ in SEARCH_TYPES:
            for raw in nearby_search(api_key, lat, lng, radius, type_=type_):
                pid = raw.get("place_id")
                if not pid or pid in seen:
                    continue
                if cell_count >= max_per_cell:
                    break
                seen[pid] = Lead(
                    name=raw.get("name", ""),
                    place_id=pid,
                    address=raw.get("vicinity", ""),
                    area=area,
                    category=type_,
                    rating=float(raw.get("rating", 0) or 0),
                    reviews=int(raw.get("user_ratings_total", 0) or 0),
                    lat=raw.get("geometry", {}).get("location", {}).get("lat", 0),
                    lng=raw.get("geometry", {}).get("location", {}).get("lng", 0),
                    business_status=raw.get("business_status", ""),
                )
                cell_count += 1

        for kw in SEARCH_KEYWORDS:
            for raw in nearby_search(api_key, lat, lng, radius, keyword=kw):
                pid = raw.get("place_id")
                if not pid or pid in seen:
                    continue
                seen[pid] = Lead(
                    name=raw.get("name", ""),
                    place_id=pid,
                    address=raw.get("vicinity", ""),
                    area=area,
                    category=kw,
                    rating=float(raw.get("rating", 0) or 0),
                    reviews=int(raw.get("user_ratings_total", 0) or 0),
                    lat=raw.get("geometry", {}).get("location", {}).get("lat", 0),
                    lng=raw.get("geometry", {}).get("location", {}).get("lng", 0),
                    business_status=raw.get("business_status", ""),
                )

        completed_cells.append(area)
        save_checkpoint(seen, completed_cells)
        print(f"   running total: {len(seen)} unique places "
              f"(checkpoint saved)")

    print(f"\nFetching details for {len(seen)} places...")
    leads = list(seen.values())
    for i, lead in enumerate(leads, 1):
        if i % 50 == 0:
            print(f"  details {i}/{len(leads)}")
        d = place_details(api_key, lead.place_id)
        if not d:
            continue
        lead.phone = d.get("formatted_phone_number") or d.get(
            "international_phone_number", "") or lead.phone
        lead.website = d.get("website", "") or lead.website
        lead.address = d.get("formatted_address", "") or lead.address
        lead.rating = float(d.get("rating", lead.rating) or lead.rating)
        lead.reviews = int(d.get("user_ratings_total", lead.reviews)
                           or lead.reviews)
        lead.business_status = d.get("business_status",
                                     lead.business_status)

    if scrape_emails:
        sites = [l for l in leads if l.website]
        print(f"\nScraping emails from {len(sites)} websites...")
        for i, lead in enumerate(sites, 1):
            if i % 25 == 0:
                print(f"  emails {i}/{len(sites)}")
            lead.email = fetch_email_from_site(lead.website)

    for lead in leads:
        score, tier, notes = score_demo_probability(lead)
        lead.demo_score = score
        lead.demo_tier = tier
        lead.notes = notes

    leads.sort(key=lambda l: (-l.demo_score, -l.reviews))
    return leads


def write_csv(leads: list[Lead], path: str) -> None:
    if not leads:
        print("No leads to write.")
        return
    fieldnames = list(leads[0].csv_row().keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for l in leads:
            w.writerow(l.csv_row())
    print(f"Wrote {len(leads)} leads → {path}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="leads.csv", help="output CSV path")
    p.add_argument("--radius", type=int, default=3000,
                   help="search radius per grid cell, meters")
    p.add_argument("--max-per-cell", type=int, default=200,
                   help="cap places taken per grid cell to control cost")
    p.add_argument("--no-emails", action="store_true",
                   help="skip website-email scraping (faster, no outbound)")
    p.add_argument("--no-resume", action="store_true",
                   help="ignore any existing checkpoint.json and start fresh")
    p.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY"),
                   help="Google Maps API key (or set GOOGLE_MAPS_API_KEY env)")
    args = p.parse_args()

    if not args.api_key:
        print("ERROR: set GOOGLE_MAPS_API_KEY or pass --api-key", file=sys.stderr)
        return 2

    leads = harvest(
        api_key=args.api_key,
        radius=args.radius,
        max_per_cell=args.max_per_cell,
        scrape_emails=not args.no_emails,
        resume=not args.no_resume,
    )
    write_csv(leads, args.out)

    tiers: dict[str, int] = {}
    for l in leads:
        tiers[l.demo_tier] = tiers.get(l.demo_tier, 0) + 1
    print("\nTier breakdown:")
    for t in sorted(tiers):
        print(f"  {t}: {tiers[t]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
