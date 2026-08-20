"""
Read leads.csv, scrape email addresses from each clinic's website
(in parallel), write the result back. Run after scraper.py.

Usage:
    python add_emails.py --in leads.csv --out leads.csv --workers 20
"""

import argparse
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
OBFUSCATED_EMAIL_RE = re.compile(
    r"([a-zA-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*"
    r"([a-zA-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\s+dot\s+)\s*([a-zA-Z]{2,})",
    re.IGNORECASE,
)
SKIP_SUBSTRINGS = (
    "sentry.io", "wixpress.com", "example.com", "yourdomain", "godaddy",
    "@2x", "png@", "jpg@", "svg@", "webp@", "noreply@google",
    "u003e", "u003c",
)
HEADERS = {"User-Agent": "Mozilla/5.0 (clinic-lead-tool)"}


def scrape_email(url: str, timeout: int = 6) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = "http://" + url
    base = url.rstrip("/")
    candidates = [base, base + "/contact", base + "/contact-us"]
    found: set[str] = set()
    for u in candidates:
        try:
            r = requests.get(u, headers=HEADERS, timeout=timeout,
                             allow_redirects=True)
            if r.status_code != 200 or not r.text:
                continue
            for m in EMAIL_RE.findall(r.text):
                ml = m.lower()
                if any(b in ml for b in SKIP_SUBSTRINGS):
                    continue
                if len(ml) > 80:
                    continue
                found.add(ml)
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
                                ("gmail.", "yahoo.", "hotmail.",
                                 "outlook.", "rediffmail."))]
    return sorted(domain_emails or list(found))[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input", default="leads.csv")
    ap.add_argument("--out", default="leads.csv")
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Input is empty.", file=sys.stderr)
        return 1
    fieldnames = list(rows[0].keys())
    print(f"Loaded {len(rows)} rows")

    targets = [r for r in rows if r.get("website") and not r.get("email")]
    print(f"Scraping emails for {len(targets)} sites with "
          f"{args.workers} workers...")

    done = 0
    found_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_email, r["website"]): r for r in targets}
        for fut in as_completed(futures):
            r = futures[fut]
            try:
                email = fut.result()
            except Exception:
                email = ""
            r["email"] = email
            if email:
                found_count += 1
            done += 1
            if done % 50 == 0 or done == len(targets):
                print(f"  {done}/{len(targets)} done, "
                      f"{found_count} emails found")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    have_phone = sum(1 for r in rows if r.get("phone"))
    have_email = sum(1 for r in rows if r.get("email"))
    print(f"\nWrote {len(rows)} rows → {args.out}")
    print(f"  with phone: {have_phone}")
    print(f"  with email: {have_email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
