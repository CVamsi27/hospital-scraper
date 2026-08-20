"""
Free Hyderabad clinic & hospital scraper — OpenStreetMap via Overpass API.

No API key. No credit card. No signup. OSM data is community-maintained and
licensed ODbL. Coverage in Hyderabad is thinner than Google Maps (expect
~1,500–3,500 listings vs ~5,000+ from Google) but it is genuinely free.

Usage:
    pip install requests
    python scraper_osm.py --out leads_osm.csv

Tip: you can run BOTH scrapers and merge — OSM finds places Google Places
ranks low, and vice versa.
"""

import argparse
import csv
import math
import re
import sys
import time
from dataclasses import dataclass, asdict

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

# Hyderabad bounding box (south, west, north, east)
HYD_BBOX = (17.20, 78.20, 17.65, 78.70)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
OBFUSCATED_EMAIL_RE = re.compile(
    r"([a-zA-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*"
    r"([a-zA-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\s+dot\s+)\s*([a-zA-Z]{2,})",
    re.IGNORECASE,
)

AREA_GRID: list[tuple[float, float, str]] = [
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
    (17.4520, 78.4670, "Ameerpet"),
    (17.4170, 78.4500, "SR Nagar"),
]

BIG_CHAINS = [
    "apollo", "yashoda", "kims", "care hospital", "continental", "rainbow",
    "sunshine", "asian institute", "medicover", "global hospital",
    "olive hospital", "star hospital", "aig", "citizen", "image hospital",
    "fernandez", "deccan", "preeti", "max cure",
]

OVERPASS_QUERY = f"""
[out:json][timeout:120];
(
  node["amenity"~"^(clinic|hospital|doctors|dentist)$"]
      ({HYD_BBOX[0]},{HYD_BBOX[1]},{HYD_BBOX[2]},{HYD_BBOX[3]});
  way ["amenity"~"^(clinic|hospital|doctors|dentist)$"]
      ({HYD_BBOX[0]},{HYD_BBOX[1]},{HYD_BBOX[2]},{HYD_BBOX[3]});
  node["healthcare"]
      ({HYD_BBOX[0]},{HYD_BBOX[1]},{HYD_BBOX[2]},{HYD_BBOX[3]});
  way ["healthcare"]
      ({HYD_BBOX[0]},{HYD_BBOX[1]},{HYD_BBOX[2]},{HYD_BBOX[3]});
);
out center tags;
"""


@dataclass
class Lead:
    name: str
    osm_id: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    address: str = ""
    area: str = ""
    category: str = ""
    speciality: str = ""
    operator: str = ""
    lat: float = 0.0
    lng: float = 0.0
    demo_score: int = 0
    demo_tier: str = ""
    notes: str = ""


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def nearest_area(lat: float, lng: float) -> str:
    best = min(AREA_GRID, key=lambda g: haversine_km(lat, lng, g[0], g[1]))
    return best[2]


def fetch_overpass() -> list[dict]:
    last_err = None
    for url in OVERPASS_ENDPOINTS:
        try:
            print(f"Querying Overpass: {url}")
            r = requests.post(url, data={"data": OVERPASS_QUERY}, timeout=180)
            if r.status_code == 200:
                return r.json().get("elements", [])
            print(f"  HTTP {r.status_code} — trying next mirror")
            last_err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            print(f"  {e} — trying next mirror")
            last_err = str(e)
            time.sleep(2)
    raise RuntimeError(f"All Overpass mirrors failed: {last_err}")


def normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    parts = [p.strip() for p in re.split(r"[;,]", raw) if p.strip()]
    return parts[0] if parts else ""


def build_address(tags: dict) -> str:
    bits = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    return ", ".join([b for b in bits if b])


def osm_to_lead(el: dict) -> Lead | None:
    tags = el.get("tags", {}) or {}
    name = tags.get("name") or tags.get("name:en") or ""
    if not name:
        return None

    if el.get("type") == "node":
        lat, lng = el.get("lat", 0.0), el.get("lon", 0.0)
    else:
        c = el.get("center", {})
        lat, lng = c.get("lat", 0.0), c.get("lon", 0.0)

    category = tags.get("amenity") or tags.get("healthcare") or ""

    return Lead(
        name=name,
        osm_id=f"{el.get('type','')}/{el.get('id','')}",
        phone=normalize_phone(tags.get("phone") or tags.get("contact:phone", "")),
        email=normalize_phone(tags.get("email") or tags.get("contact:email", "")),
        website=tags.get("website") or tags.get("contact:website", ""),
        address=build_address(tags),
        area=tags.get("addr:suburb") or nearest_area(lat, lng),
        category=category,
        speciality=tags.get("healthcare:speciality", ""),
        operator=tags.get("operator", ""),
        lat=lat,
        lng=lng,
    )


def fetch_email_from_site(url: str, timeout: int = 8) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = "http://" + url
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
            for m in EMAIL_RE.findall(r.text):
                if any(bad in m.lower() for bad in
                       ("sentry.io", "wixpress.com", "example.com",
                        "yourdomain", "godaddy", "@2x", "png@", "jpg@")):
                    continue
                found.add(m.lower())
            for u_, d, t in OBFUSCATED_EMAIL_RE.findall(r.text):
                found.add(f"{u_}@{d}.{t}".lower())
            if found:
                break
        except requests.RequestException:
            continue
    if not found:
        return ""
    domain_emails = [e for e in found
                     if not any(p in e for p in
                                ("gmail.", "yahoo.", "hotmail.", "outlook."))]
    return sorted(domain_emails or list(found))[0]


def score_demo_probability(lead: Lead) -> tuple[int, str, str]:
    """
    OSM doesn't give review counts, so we rely on richness-of-tags as a
    proxy for clinic size: small clinics have sparse tags, big chains
    have full address/website/operator/etc.
    """
    score = 55
    notes: list[str] = []

    name_l = lead.name.lower()
    op_l = (lead.operator or "").lower()

    if any(c in name_l or c in op_l for c in BIG_CHAINS):
        score -= 35
        notes.append("big-chain")
    if lead.category == "hospital":
        score -= 10
        notes.append("hospital")
    if "government" in name_l or "govt" in name_l:
        score -= 30
        notes.append("government")

    good_terms = ["clinic", "polyclinic", "dental", "skin", "child",
                  "pediatric", "derma", "ent", "physio", "homeo",
                  "ayurved", "eye care", "mother", "women",
                  "dr.", "dr ", "consulting"]
    if any(t in name_l for t in good_terms):
        score += 10
        notes.append("clinic-type")

    if lead.category in ("clinic", "doctors", "dentist"):
        score += 8
    if lead.speciality:
        score += 4

    # Reachability
    if lead.phone:
        score += 8
    else:
        score -= 12
        notes.append("no-phone")
    if lead.email:
        score += 5
    if not lead.website and lead.phone:
        score += 5
        notes.append("no-website")

    # Tag-richness proxy: many tags → established/large; few → small/new
    tag_signal = sum(bool(x) for x in
                     [lead.address, lead.website, lead.operator, lead.email])
    if tag_signal == 0:
        score += 8
        notes.append("sparse-tags(small)")
    elif tag_signal >= 3:
        score -= 4
        notes.append("rich-tags")

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


def harvest(scrape_emails: bool) -> list[Lead]:
    elements = fetch_overpass()
    print(f"Overpass returned {len(elements)} elements")

    leads: list[Lead] = []
    seen: set[str] = set()
    for el in elements:
        lead = osm_to_lead(el)
        if not lead:
            continue
        key = lead.name.lower().strip() + f"|{round(lead.lat,4)},{round(lead.lng,4)}"
        if key in seen:
            continue
        seen.add(key)
        leads.append(lead)
    print(f"Built {len(leads)} unique named leads")

    if scrape_emails:
        sites = [l for l in leads if l.website and not l.email]
        print(f"\nScraping emails from {len(sites)} websites...")
        for i, lead in enumerate(sites, 1):
            if i % 25 == 0:
                print(f"  emails {i}/{len(sites)}")
            lead.email = fetch_email_from_site(lead.website)

    for lead in leads:
        s, t, n = score_demo_probability(lead)
        lead.demo_score, lead.demo_tier, lead.notes = s, t, n

    leads.sort(key=lambda l: (-l.demo_score, l.name))
    return leads


def write_csv(leads: list[Lead], path: str) -> None:
    if not leads:
        print("No leads to write.")
        return
    fieldnames = list(asdict(leads[0]).keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for l in leads:
            w.writerow(asdict(l))
    print(f"Wrote {len(leads)} leads → {path}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="leads_osm.csv")
    p.add_argument("--no-emails", action="store_true",
                   help="skip website-email scraping (faster)")
    args = p.parse_args()

    leads = harvest(scrape_emails=not args.no_emails)
    write_csv(leads, args.out)

    tiers: dict[str, int] = {}
    for l in leads:
        tiers[l.demo_tier] = tiers.get(l.demo_tier, 0) + 1
    print("\nTier breakdown:")
    for t in sorted(tiers):
        print(f"  {t}: {tiers[t]}")

    have_phone = sum(1 for l in leads if l.phone)
    have_email = sum(1 for l in leads if l.email)
    print(f"\nReachability: {have_phone} with phone, "
          f"{have_email} with email out of {len(leads)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
