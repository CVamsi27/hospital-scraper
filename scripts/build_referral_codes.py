"""
Build referral_codes.csv from the three lead CSVs.

Each row gets a stable per-row referral code + a tracking link that
points at the existing /book/{referralCode} route in the Docita app.

Output schema (referral_codes.csv):
    phone, name, campaign, referralCode, ref_link
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.sms_send import build_referral_code, ref_link_for  # noqa: E402
from outreach_common import normalize_phone_to_e164_india  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clinic-csv", required=True)
    ap.add_argument("--pharmacy-csv", required=True)
    ap.add_argument("--diagnostic-csv", required=True)
    ap.add_argument("--out", default="referral_codes.csv")
    args = ap.parse_args()

    targets = [
        ("clinic", args.clinic_csv),
        ("pharmacy", args.pharmacy_csv),
        ("diagnostic", args.diagnostic_csv),
    ]

    rows_out: list[dict] = []
    for campaign, path in targets:
        if not Path(path).exists():
            print(f"  skip: {path} not found", file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                phone = normalize_phone_to_e164_india(r.get("phone", ""))
                if not phone:
                    continue
                code = build_referral_code(r, campaign)
                rows_out.append({
                    "phone": phone,
                    "name": (r.get("name") or "").strip(),
                    "campaign": campaign,
                    "referralCode": code,
                    "ref_link": ref_link_for(code),
                })

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["phone", "name", "campaign", "referralCode", "ref_link"]
        )
        w.writeheader()
        w.writerows(rows_out)

    print(f"wrote {args.out} — {len(rows_out)} referral codes")
    by_campaign: dict[str, int] = {}
    for r in rows_out:
        by_campaign[r["campaign"]] = by_campaign.get(r["campaign"], 0) + 1
    for k, v in by_campaign.items():
        print(f"  {k:10s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
