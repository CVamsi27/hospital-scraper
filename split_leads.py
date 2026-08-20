"""
Split leads.csv into three campaign-specific calling lists:

  leads_hospitals_clinics.csv  — your PRIMARY pitch (Docita demo)
  leads_diagnostics.csv        — referral pitch ("send hospitals our way, get ₹500/clinic")
  leads_pharmacies.csv         — referral pitch + "tell us when a new clinic opens nearby"

Classification uses BOTH the recorded `category` (search keyword that found it)
and name-based heuristics — names like "Apollo Pharmacy" or "Vijaya
Diagnostic Centre" are reliably typeable even if the search category was wrong.

Usage:
    python split_leads.py
    python split_leads.py --in leads.csv
"""

import argparse
import csv
import re
import sys
from collections import Counter

PHARMACY_NAME_HINTS = re.compile(
    r"\b(pharmacy|medical store|medical hall|medical shop|chemist|"
    r"drug store|drugstore|generics|medplus|apollo pharmacy|"
    r"wellness forever|netmeds|tata 1mg|pharmeasy|frank ross|"
    r"medicines|aushadh|davai)\b",
    re.IGNORECASE,
)

DIAGNOSTIC_NAME_HINTS = re.compile(
    r"\b(diagnostic|diagnostics|scan|imaging|radiology|pathology|"
    r"lab\b|labs\b|laboratory|x.?ray|ultrasound|mri|"
    r"sample collection|blood test|blood bank|"
    r"vijaya diagnostic|lucid|tapadia|elbit|metropolis|thyrocare|"
    r"lal path|dr lal|srl diagnostic|spectra|aarthi|pulse diagnostic)\b",
    re.IGNORECASE,
)

# Strong signal: the recorded category column
PHARMACY_CATEGORIES = {"pharmacy"}
DIAGNOSTIC_CATEGORIES = {"diagnostic", "scan centre", "pathology lab"}


def classify(row: dict) -> str:
    """Return one of: 'pharmacy', 'diagnostic', 'clinic'."""
    name = (row.get("name") or "").lower()
    cat = (row.get("category") or "").lower()

    # Category column is the strongest signal (we labeled by what found it).
    if cat in PHARMACY_CATEGORIES:
        return "pharmacy"
    if cat in DIAGNOSTIC_CATEGORIES:
        return "diagnostic"

    # Name overrides — sometimes a pharmacy got picked up under "clinic"
    # because it had "clinic" in its name. Treat name as more authoritative.
    if PHARMACY_NAME_HINTS.search(name):
        return "pharmacy"
    if DIAGNOSTIC_NAME_HINTS.search(name):
        return "diagnostic"

    return "clinic"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input", default="leads.csv")
    args = ap.parse_args()

    try:
        with open(args.input, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            fieldnames = list(rows[0].keys()) if rows else []
    except FileNotFoundError:
        print(f"Not found: {args.input}", file=sys.stderr)
        return 2

    if not rows:
        print("Input is empty.", file=sys.stderr)
        return 1
    print(f"Loaded {len(rows)} rows")

    buckets: dict[str, list[dict]] = {
        "clinic": [],
        "diagnostic": [],
        "pharmacy": [],
    }
    for r in rows:
        buckets[classify(r)].append(r)

    outputs = {
        "clinic":     "leads_hospitals_clinics.csv",
        "diagnostic": "leads_diagnostics.csv",
        "pharmacy":   "leads_pharmacies.csv",
    }

    for kind, items in buckets.items():
        # Sort by demo_score desc — already pre-sorted in source, but
        # re-sort defensively in case appends were unordered.
        items.sort(key=lambda r: -int(r.get("demo_score") or 0))
        path = outputs[kind]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames,
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(items)
        with_phone = sum(1 for r in items if r.get("phone"))
        with_email = sum(1 for r in items if r.get("email"))
        print(f"  {kind:11s} → {path:32s} "
              f"{len(items):>5} rows  ({with_phone} phone, {with_email} email)")

    # Sanity sample of the clinic bucket — make sure we didn't accidentally
    # bucket pharmacies as clinics.
    print("\nTop 10 in leads_hospitals_clinics.csv:")
    for r in buckets["clinic"][:10]:
        print(f"  s{r.get('demo_score','?'):>3}  "
              f"{(r.get('name') or '')[:50]:50s}  "
              f"{r.get('category','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
