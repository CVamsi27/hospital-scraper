"""
SMS outreach runner.

Reads a lead CSV, renders a template per row, and sends through the local
Android SMS gateway (hospital-scraper/sms_gateway.py). A successful HTTP
response means the phone accepted the request; it does not prove carrier
delivery.

Single command. No interactive prompts unless --confirm is omitted.

Usage (via Makefile):
    make sms-preview-clinics         # first 10, no send
    make sms-clinics                 # up to 500 T1, with confirmation
    make sms-clinics-force           # no prompt

Direct:
    python scripts/sms_send.py \
        --csv leads_hospitals_clinics.csv \
        --template docita_hms_sms_v1 \
        --limit 500 --confirm
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import yaml

# Make sibling modules importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outreach_common import (  # noqa: E402
    CONTACT_PHONE_SMS,
    CONTACT_WEB,
    SMS_TRACKING_COLUMNS,
    ensure_columns,
    is_t1,
    first_valid_phone,
    load_csv,
    normalize_phone_to_e164_india,
    now_iso,
    render_template,
    write_csv,
)
from sms_gateway import LocalSmsGateway  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_PATH = ROOT / "templates.yaml"
REFERRAL_CODES_PATH = ROOT / "referral_codes.csv"
DO_NOT_SMS_PATH = ROOT / "do_not_sms.csv"
PARTNER_SIGNUPS_PATH = ROOT / "partner_signups.csv"
SMS_DELIVERY_LOG_PATH = ROOT / "sms_delivery_log.csv"
APP_BASE_URL = "https://app.docita.work"

# Reply keywords that mean "I want to onboard as a referral partner".
# Matched case-insensitively against the FIRST WORD of the incoming SMS body.
# Keep this list in sync with templates.yaml — partner-facing templates ask the
# recipient to reply with one of these.
PARTNER_REPLY_KEYWORDS = {"join", "refer", "partner", "signup", "sign", "register", "yes"}


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

def load_templates() -> dict:
    with open(TEMPLATES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_template(campaign: str, override: str | None, *, allow_clinic_referral: bool = False) -> str:
    data = load_templates()
    if override:
        if override not in data.get("templates", {}):
            raise SystemExit(f"Template '{override}' not found in {TEMPLATES_PATH}")
        allowed_prefixes = {
            "clinic": ("docita_hms_",),
            "pharmacy": ("docita_pharmacy_",),
            "diagnostic": ("docita_diagnostics_",),
            "partner": ("docita_partner_",),
            "marketing": ("docita_marketing_",),
        }
        generic_templates = {"docita_intro_sms_v1", "docita_test_sms_v1"}
        if override == "docita_clinic_referral_v1":
            if campaign != "clinic" or not allow_clinic_referral:
                raise SystemExit(
                    "Refusing clinic peer-referral template. Use an onboarded-clinics CSV "
                    "and pass --allow-clinic-referral explicitly."
                )
        elif override not in generic_templates and not any(
            override.startswith(prefix) for prefix in allowed_prefixes.get(campaign, ())
        ):
            raise SystemExit(
                f"Refusing template '{override}' for campaign '{campaign}'. "
                "Use the campaign's approved template family or a generic template."
            )
        return override
    camp = data.get("campaigns", {}).get(campaign)
    if not camp:
        raise SystemExit(f"Unknown campaign '{campaign}'")
    return camp["default_template"]


# ---------------------------------------------------------------------------
# Referral codes
# ---------------------------------------------------------------------------

def _slugify(s: str, max_len: int = 16) -> str:
    out = "".join(ch if ch.isalnum() else "" for ch in (s or "").upper())
    return out[:max_len] or "PARTNER"


def build_referral_code(row: dict, campaign: str) -> str:
    """Per-row referral code. Stable on phone, used to build the ref link."""
    name = (row.get("name") or "").strip()
    area = (row.get("area") or "").strip()
    if campaign in ("pharmacy", "diagnostic", "diagnostics"):
        prefix = "PH" if campaign == "pharmacy" else "DG"
        return f"{prefix}-{_slugify(name)}-{_slugify(area, 6)}"
    # Clinic: prefer the existing PRO-XXXXXXXX if the scraper put it there.
    existing = (row.get("referral_code") or "").strip()
    if existing.startswith("PRO-"):
        return existing
    return f"CL-{_slugify(name)}"


def ref_link_for(code: str) -> str:
    return f"{APP_BASE_URL}/book/{code}"


def partner_join_url() -> str:
    return os.getenv("DOCITA_PARTNER_JOIN_URL", f"{APP_BASE_URL}/partner")


def link_for_row(row: dict, campaign: str, phone: str, ref_map: dict[str, str], template_body: str) -> str | None:
    """Return only links that are valid for the current audience."""
    if "{{partner_join_url}}" in template_body or campaign in {"pharmacy", "diagnostic", "partner", "marketing"}:
        return partner_join_url()
    if "{{ref_link}}" not in template_body:
        return None
    return ref_map.get(phone) or ref_link_for(build_referral_code(row, campaign))


def load_referral_map() -> dict[str, str]:
    """phone_e164 -> ref_link, from referral_codes.csv if it exists."""
    if not REFERRAL_CODES_PATH.exists():
        return {}
    out = {}
    with open(REFERRAL_CODES_PATH, encoding="utf-8") as f:
        for row in __import__("csv").DictReader(f):
            if row.get("phone") and row.get("ref_link"):
                out[row["phone"]] = row["ref_link"]
    return out


def write_referral_map(rows: list[dict], campaign: str) -> None:
    """Build referral_codes.csv (phone -> referralCode, ref_link)."""
    import csv
    with open(REFERRAL_CODES_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["phone", "name", "campaign", "referralCode", "ref_link"])
        w.writeheader()
        for r in rows:
            phone = normalize_phone_to_e164_india(r.get("phone", ""))
            if not phone:
                continue
            code = build_referral_code(r, campaign)
            w.writerow({
                "phone": phone,
                "name": (r.get("name") or "").strip(),
                "campaign": campaign,
                "referralCode": code,
                "ref_link": ref_link_for(code),
            })


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def load_dnc() -> set[str]:
    if not DO_NOT_SMS_PATH.exists():
        return set()
    out = set()
    with open(DO_NOT_SMS_PATH, encoding="utf-8") as f:
        for row in __import__("csv").DictReader(f):
            p = normalize_phone_to_e164_india(row.get("phone", ""))
            if p:
                out.add(p)
    return out


# ---------------------------------------------------------------------------
# Time-window guard
# ---------------------------------------------------------------------------

def in_send_window(now_ist: __import__("datetime").datetime, allow_offhours: bool) -> bool:
    """Indian-carrier-friendly hours. Allow override via env var."""
    if allow_offhours or os.getenv("ALLOW_OFFHOURS") == "1":
        return True
    wd = now_ist.weekday()  # 0=Mon
    h, m = now_ist.hour, now_ist.minute
    minutes = h * 60 + m
    if wd <= 4:  # Mon–Fri
        return (10 * 60 <= minutes <= 12 * 60 + 30) or (15 * 60 <= minutes <= 18 * 60)
    if wd == 5:  # Sat
        return 10 * 60 <= minutes <= 13 * 60
    return False  # Sun: never


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def select_rows(
    rows: list[dict],
    campaign: str,
    *,
    skip_already_sent: bool,
    limit: int,
    include_all: bool,
) -> list[tuple[int, dict, str]]:
    """
    Returns (idx_in_csv, row, phone_e164) for every eligible recipient.
    """
    dnc = load_dnc()
    out: list[tuple[int, dict, str]] = []
    for idx, r in enumerate(rows):
        # ensure tracking cols exist
        for c in SMS_TRACKING_COLUMNS:
            r.setdefault(c, "")

        if not include_all and not is_t1(r):
            continue
        if skip_already_sent and (r.get("sms_campaign") or "").strip() == campaign and (r.get("sms_status") or "").strip().lower() in {"accepted", "sent"}:
            continue

        phone = first_valid_phone(r)
        if not phone:
            continue
        if phone in dnc:
            continue
        out.append((idx, r, phone))

        if limit and len(out) >= limit:
            break
    return out


def render_for(template: str, row: dict, *, ref_link: str | None) -> str:
    body = render_template(
        template,
        name=row.get("name", ""),
        area=row.get("area", ""),
    )
    # Extra placeholders not handled by render_template.
    if ref_link:
        body = body.replace("{{ref_link}}", ref_link)
    else:
        body = body.replace("{{ref_link}}", f"{APP_BASE_URL}").replace(" | ref:", "")
    return body


def append_delivery_log(*, row: dict, phone: str, campaign: str, template_key: str, result, message: str) -> None:
    fieldnames = [
        "lead_key",
        "phone",
        "campaign",
        "template_key",
        "attempted_at",
        "gateway_status",
        "delivery_status",
        "status_code",
        "message_chars",
        "error",
    ]
    new_file = not SMS_DELIVERY_LOG_PATH.exists()
    with SMS_DELIVERY_LOG_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow({
            "lead_key": row.get("place_id") or row.get("osm_id") or row.get("name", ""),
            "phone": phone,
            "campaign": campaign,
            "template_key": template_key,
            "attempted_at": now_iso(),
            "gateway_status": "accepted" if result.ok else "rejected",
            "delivery_status": "gateway_accepted" if result.ok else "failed",
            "status_code": result.status_code,
            "message_chars": len(message),
            "error": (result.error or result.body or "")[:200],
        })


def run(
    *,
    csv_path: Path,
    campaign: str,
    template_key: str,
    limit: int,
    confirm: bool,
    force: bool,
    allow_offhours: bool,
    delay_sec: float,
    include_all: bool,
    output_csv: Path | None = None,
) -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    templates = load_templates()
    if template_key not in templates.get("templates", {}):
        raise SystemExit(f"Template '{template_key}' not in {TEMPLATES_PATH}")
    template_body = templates["templates"][template_key]["body"]

    now_ist = datetime.now(tz=ZoneInfo("Asia/Kolkata"))
    if not in_send_window(now_ist, allow_offhours):
        print(
            f"[refuse] current time {now_ist.strftime('%Y-%m-%d %H:%M IST')} is outside "
            f"the carrier-safe send window. Re-run with ALLOW_OFFHOURS=1 to override.",
            file=sys.stderr,
        )
        return 2

    fieldnames, rows = load_csv(csv_path)
    fieldnames = ensure_columns(fieldnames, SMS_TRACKING_COLUMNS)
    for r in rows:
        for c in SMS_TRACKING_COLUMNS:
            r.setdefault(c, "")

    ref_map = load_referral_map()
    eligible = select_rows(rows, campaign, skip_already_sent=not force, limit=limit, include_all=include_all)
    print(
        f"Found {len(eligible)} eligible leads in {csv_path.name} "
        f"(campaign={campaign}, template={template_key}, limit={limit})."
    )
    if not eligible:
        return 0

    # Preview
    print("\nPreview (first 5):")
    for i, (_, r, phone) in enumerate(eligible[:5], start=1):
        ref_link = link_for_row(r, campaign, phone, ref_map, template_body)
        msg = render_for(template_body, r, ref_link=ref_link)
        print(f"\n{i}. {r.get('name','').strip()} — {phone} — {r.get('area','').strip()}")
        for line in msg.splitlines():
            print(f"   {line}")

    if not confirm and not force:
        print(
            "\nNOT sending. Re-run with --confirm (or use the *-force make target).",
            file=sys.stderr,
        )
        return 0

    # Persist peer-referral codes only for clinic referral templates. Partner
    # campaigns use the configured partner join URL; PH-/DG- booking links are
    # not valid Docita partner credentials.
    if "{{ref_link}}" in template_body:
        write_referral_map([r for _, r, _ in eligible], campaign)

    # Build the send list.
    items: list[tuple[str, str]] = []
    for _, r, phone in eligible:
        ref_link = link_for_row(r, campaign, phone, ref_map, template_body)
        msg = render_for(template_body, r, ref_link=ref_link)
        items.append((phone, msg))

    # Build the gateway from env.
    try:
        gateway = LocalSmsGateway.from_env()
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3

    health = gateway.health_check()
    if not health.ok:
        print(
            f"[error] gateway unreachable: status={health.status_code} body={health.body}\n"
            f"        Is the Android app's Local Server toggle ON and showing 'Online'?\n"
            f"        Can this Mac reach {gateway.host_label} on the same WiFi?",
            file=sys.stderr,
        )
        return 4
    print(f"[ok] gateway reachable at {gateway.host_label}.")

    # Send sequentially, with per-row status update.
    sent_at = now_iso()
    failed = 0
    for i, ((_, r, phone), (to, msg)) in enumerate(zip(eligible, items), start=1):
        if i > 1 and delay_sec > 0:
            time.sleep(delay_sec)
        result = gateway.send_sms(to, msg)
        attempts = int(r.get("sms_attempts") or 0) + 1
        if result.ok:
            r["sms_status"] = "accepted"
            r["sms_gateway_status"] = "accepted"
            r["sms_delivery_status"] = "gateway_accepted"
            r["sms_error"] = ""
            print(f"  [{i}/{len(items)}] OK   {to}  ({len(msg)} chars)")
        else:
            r["sms_status"] = "failed"
            r["sms_gateway_status"] = "rejected"
            r["sms_delivery_status"] = "failed"
            r["sms_error"] = (result.error or result.body or "")[:200]
            failed += 1
            print(f"  [{i}/{len(items)}] FAIL {to}  {r['sms_error']}")
        r["sms_campaign"] = campaign
        r["sms_template_key"] = template_key
        r["sms_sent_at"] = sent_at if result.ok else r.get("sms_sent_at", "")
        r["sms_last_attempt_at"] = now_iso()
        r["sms_attempts"] = str(attempts)
        r["sms_last_message"] = msg
        append_delivery_log(row=r, phone=phone, campaign=campaign, template_key=template_key, result=result, message=msg)
        # Durable checkpoint: a process or phone failure must not erase the
        # attempts already made in this run.
        write_csv(output_csv or csv_path, fieldnames, rows)

    # Write back to CSV (in place unless caller overrides).
    out_path = output_csv or csv_path
    write_csv(out_path, fieldnames, rows)
    print(f"\nDone. {len(items) - failed} gateway-accepted, {failed} failed. CSV updated: {out_path}")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True, help="Path to lead CSV.")
    ap.add_argument("--campaign", required=True, choices=["clinic", "pharmacy", "diagnostic", "partner", "marketing"],
                    help="Which audience this is for (drives default template + referral prefix).")
    ap.add_argument("--template", default=None, help="Override template key (default: campaign default).")
    ap.add_argument("--limit", type=int, default=500, help="Max messages in this run (default 500).")
    ap.add_argument("--delay", type=float, default=4.0, help="Seconds between messages (default 4).")
    ap.add_argument("--confirm", action="store_true", help="Actually send (default: dry-run).")
    ap.add_argument("--force", action="store_true", help="Skip window check + re-send already-sent rows.")
    ap.add_argument("--allow-offhours", action="store_true", help="Send outside the carrier-safe window.")
    ap.add_argument("--all-leads", action="store_true", help="Include non-T1 rows; required for partner/marketing lists unless overridden.")
    ap.add_argument("--allow-clinic-referral", action="store_true", help="Allow the post-onboarding clinic peer-referral template.")
    ap.add_argument("--output", default=None, help="Output CSV path (default: overwrite --csv in place).")
    args = ap.parse_args()

    template_key = resolve_template(
        args.campaign,
        args.template,
        allow_clinic_referral=args.allow_clinic_referral,
    )
    return run(
        csv_path=Path(args.csv),
        campaign=args.campaign,
        template_key=template_key,
        limit=args.limit,
        confirm=args.confirm,
        force=args.force,
        allow_offhours=args.allow_offhours or args.force or os.getenv("ALLOW_OFFHOURS") == "1",
        delay_sec=args.delay,
        include_all=args.all_leads or args.campaign in {"partner", "marketing"},
        output_csv=Path(args.output) if args.output else None,
    )

    # (Note: from_env / health check happens inside run() so dry-runs don't
    # need gateway creds at all.)


if __name__ == "__main__":
    raise SystemExit(main())
