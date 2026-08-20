"""Shared helpers for WhatsApp and SMS outreach scripts."""

from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path
from typing import Any

CONTACT_PHONE = "+91 8790311010"
CONTACT_PHONE_SMS = "+918790311010"
CONTACT_EMAIL = "support@docita.work"
CONTACT_WEB = "docita.work"

SMS_TRACKING_COLUMNS = [
    "sms_status",
    "sms_campaign",
    "sms_template_key",
    "sms_sent_at",
    "sms_last_attempt_at",
    "sms_attempts",
    "sms_gateway_status",
    "sms_delivery_status",
    "sms_last_message",
    "sms_error",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_phone_to_e164_india(raw: str) -> str | None:
    """
    Best-effort normalization for Indian numbers in lead CSVs.
    Returns E.164 +91XXXXXXXXXX for mobile numbers; None for landlines/invalid.
    """
    s = (raw or "").strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None

    if len(digits) in (9, 10):
        if len(digits) == 10 and digits[0] in "6789":
            return "+91" + digits
        return None

    if len(digits) == 11 and digits.startswith("0"):
        return normalize_phone_to_e164_india(digits[1:])

    if digits.startswith("91") and len(digits) == 12:
        return normalize_phone_to_e164_india(digits[2:])

    if len(digits) > 12:
        tail = digits[-10:]
        if len(tail) == 10 and tail[0] in "6789":
            return "+91" + tail

    return None


def first_valid_phone(row: dict[str, Any]) -> str | None:
    """Return the primary mobile, falling back to enriched public numbers."""
    candidates = [row.get("phone", "")]
    candidates.extend((row.get("phone_numbers", "") or "").replace(",", "|").split("|"))
    for candidate in candidates:
        normalized = normalize_phone_to_e164_india(candidate)
        if normalized:
            return normalized
    return None


def render_template(template: str, *, name: str, area: str = "") -> str:
    safe_name = (name or "").strip() or "there"
    safe_area = (area or "").strip() or "Hyderabad"
    return (
        template.replace("{{name}}", safe_name)
        .replace("{{area}}", safe_area)
        .replace("{{phone}}", CONTACT_PHONE_SMS)
        .replace("{{email}}", CONTACT_EMAIL)
        .replace("{{web}}", CONTACT_WEB)
        .replace("{{partner_join_url}}", os.getenv("DOCITA_PARTNER_JOIN_URL", "https://app.docita.work/partner"))
    )


def is_t1(row: dict[str, Any]) -> bool:
    return (row.get("demo_tier") or "").strip().upper().startswith("T1")


def is_t1_or_t2(row: dict[str, Any]) -> bool:
    tier = (row.get("demo_tier") or "").strip().upper()
    return tier.startswith("T1") or tier.startswith("T2")


def load_csv(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return [], []
    fieldnames = list(rows[0].keys())
    return fieldnames, rows


def ensure_columns(fieldnames: list[str], columns: list[str]) -> list[str]:
    out = list(fieldnames)
    for col in columns:
        if col not in out:
            out.append(col)
    return out


def write_csv(path: str | Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
