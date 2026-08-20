"""
Prepare WhatsApp outreach messages for T1 leads and (optionally) mark them as sent in-place.

This script intentionally does NOT integrate with WhatsApp APIs. It generates:
  - message text (based on template key)
  - wa.me deep-links (for manual sending)
and only updates the CSV when you pass --confirm.

Typical usage:
  python whatsapp_outreach.py --in leads_hospitals_clinics.csv --out leads_hospitals_clinics.csv --limit 50
  python whatsapp_outreach.py --in leads_hospitals_clinics.csv --out leads_hospitals_clinics.csv --limit 50 --confirm

Conventions:
  - T1 is detected via demo_tier starting with "T1".
  - A row is considered already sent when whatsapp_status == "sent".
  - The script adds missing whatsapp_* columns in a backwards-compatible way.
"""

import argparse
import csv
import datetime as dt
import sys
from urllib.parse import quote

CONTACT_PHONE = "+91 8790311010"
CONTACT_EMAIL = "support@docita.work"
CONTACT_WEB = "docita.work"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _normalize_phone_to_e164_indiaish(raw: str) -> str | None:
    """
    Best-effort normalization for Indian numbers in these CSVs.
    Accepts:
      - "090599 30965"
      - "040 2707 5355" (landline; we skip by returning None)
      - "+91 87903 11010"
    Returns E.164-ish: +91XXXXXXXXXX.
    """
    s = (raw or "").strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None

    # Likely landline (Hyderabad/India) if starts with 0 and length <= 11 and not a mobile pattern.
    # We only want mobile WhatsApp targets.
    if len(digits) in (9, 10):
        # Could be mobile without country code.
        if len(digits) == 10:
            return "+91" + digits
        return None

    # 11 digits starting with 0 and then 10-digit mobile.
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]

    # Country code included.
    if digits.startswith("91") and len(digits) == 12:
        return "+91" + digits[2:]

    # Fallback: if longer, last 10 might be mobile.
    if len(digits) > 12:
        tail = digits[-10:]
        if len(tail) == 10:
            return "+91" + tail

    return None


TEMPLATES: dict[str, str] = {
    # Primary pitch (clinics/hospitals)
    "docita_hms_intro_v1": (
        "Hi {{name}}, quick question — are you looking for a simple hospital/clinic management software?\n\n"
        "Docita can help you:\n"
        "• Manage appointments + follow-ups\n"
        "• Billing & daily reports\n"
        "• Invoices\n"
        "• Patient records (EMR)\n"
        "• Send prescriptions on WhatsApp\n"
        "• WhatsApp reminders\n\n"
        "Would you like a quick 5-minute demo?\n\n"
        f"{CONTACT_PHONE} | {CONTACT_EMAIL} | {CONTACT_WEB}"
    ),
    # Shorter variant (higher reply rate)
    "docita_hms_question_v1": (
        "Hi {{name}}, are you currently using any hospital/clinic management software?\n\n"
        "If not, Docita helps with appointments, billing, EMR, and WhatsApp reminders.\n\n"
        f"If you want details: {CONTACT_PHONE} | {CONTACT_EMAIL} | {CONTACT_WEB}"
    ),
    # Diagnostics referral pitch
    "docita_diagnostics_referral_v1": (
        "Hi {{name}}, Docita works with clinics/hospitals across Hyderabad.\n\n"
        "If you know a clinic/hospital that needs management software (appointments, billing, EMR), "
        "please connect us — we handle onboarding end-to-end.\n\n"
        "Reply REFER with the name + contact number.\n\n"
        f"{CONTACT_PHONE} | {CONTACT_EMAIL} | {CONTACT_WEB}"
    ),
    # Pharmacy scout/referral pitch
    "docita_pharmacy_referral_v1": (
        "Hi {{name}}, we’re onboarding clinics/hospitals on Docita.\n\n"
        "If you know a nearby clinic/hospital that needs software for appointments, billing, and EMR, "
        "please connect us.\n\n"
        "Reply REFER with the name + contact number.\n\n"
        f"{CONTACT_PHONE} | {CONTACT_EMAIL} | {CONTACT_WEB}"
    ),
}


def _render(template: str, *, name: str) -> str:
    safe_name = (name or "").strip() or "there"
    return template.replace("{{name}}", safe_name)


def _wa_me_link(phone_e164: str, message: str) -> str:
    # wa.me expects phone digits without +.
    phone_digits = "".join(ch for ch in phone_e164 if ch.isdigit())
    return f"https://wa.me/{phone_digits}?text={quote(message)}"


def _is_t1(row: dict) -> bool:
    return (row.get("demo_tier") or "").strip().upper().startswith("T1")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input", required=True)
    ap.add_argument("--out", required=True, help="Write updated CSV here (can be same as --in).")
    ap.add_argument("--template", default="docita_hms_intro_v1", choices=sorted(TEMPLATES.keys()))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--include-already-sent", action="store_true")
    ap.add_argument("--confirm", action="store_true", help="Actually mark rows as sent in the output CSV.")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Input is empty.", file=sys.stderr)
        return 1

    # Ensure whatsapp columns exist.
    required_cols = [
        "whatsapp_status",
        "whatsapp_template_key",
        "whatsapp_sent_at",
        "whatsapp_last_message",
    ]
    fieldnames = list(rows[0].keys())
    for c in required_cols:
        if c not in fieldnames:
            fieldnames.append(c)

    eligible: list[tuple[int, dict, str, str]] = []
    for idx, r in enumerate(rows):
        for c in required_cols:
            r.setdefault(c, "")
            if r[c] is None:
                r[c] = ""

        if not _is_t1(r):
            continue
        if not args.include_already_sent and (r.get("whatsapp_status") or "").strip().lower() == "sent":
            continue

        phone_e164 = _normalize_phone_to_e164_indiaish(r.get("phone", ""))
        if not phone_e164:
            continue

        msg = _render(TEMPLATES[args.template], name=r.get("name", ""))
        link = _wa_me_link(phone_e164, msg)
        eligible.append((idx, r, msg, link))

    eligible = eligible[: max(args.limit, 0)]

    print(f"Found {len(eligible)} eligible T1 leads (template={args.template}).")
    if not eligible:
        return 0

    print("\nPreview (first 10):")
    for i, (_, r, msg, link) in enumerate(eligible[:10], start=1):
        print(f"\n{i}. {r.get('name','').strip()} — {r.get('phone','').strip()} — {r.get('area','').strip()} — {r.get('category','').strip()}")
        print(f"   wa.me: {link}")
        print("   message:")
        for line in msg.splitlines():
            print(f"   {line}")

    if not args.confirm:
        print("\nNOT marking as sent. Re-run with --confirm after you’re ready to send.")
    else:
        sent_at = _now_iso()
        for _, r, msg, _ in eligible:
            r["whatsapp_status"] = "sent"
            r["whatsapp_template_key"] = args.template
            r["whatsapp_sent_at"] = sent_at
            r["whatsapp_last_message"] = msg

        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

        print(f"\nMarked {len(eligible)} rows as sent → {args.out}")
        print("Important: this only updates the CSV; it does not send WhatsApp messages automatically.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

