"""
SMS inbox worker.

Pulls inbound SMS from the phone's local SMS Gateway, matches each
sender against the lead CSVs, and:

  - For STOP / unsubscribe / UNSUBSCRIBE   → adds the phone to
                                              do_not_sms.csv (suppression list).
  - For JOIN / REFER / PARTNER / SIGNUP / YES
                                            → marks the lead as
                                              partner_signup_intent = true and
                                              appends the message to
                                              partner_signups.csv for manual
                                              follow-up. (Real partner onboarding
                                              happens at https://app.docita.work/register
                                              — we just hand the lead to support.)
  - For everything else                     → marks the lead's
                                              `reply` column with the message
                                              text + timestamp so the team can
                                              triage by hand.

Run with `make sms-inbox-poll` (continuous, every 60s) or
`make sms-inbox-once` (single pass, good for cron).

Idempotent: processes each incoming message at most once. Already-seen
message ids are tracked in `.sms_inbox_seen.csv` at the repo root.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outreach_common import (  # noqa: E402
    normalize_phone_to_e164_india,
    now_iso,
)
from sms_gateway import LocalSmsGateway  # noqa: E402
from scripts.sms_send import (  # noqa: E402
    APP_BASE_URL,
    DO_NOT_SMS_PATH,
    PARTNER_REPLY_KEYWORDS,
    PARTNER_SIGNUPS_PATH,
    REFERRAL_CODES_PATH,
    ROOT,
)
from scripts.sms_notify import notify_reply  # noqa: E402

SEEN_PATH = ROOT / ".sms_inbox_seen.csv"
IST = ZoneInfo("Asia/Kolkata")

# Capcom6 returns messages with `id` field; if the gateway doesn't supply
# one we synthesise a stable hash from sender + text + received_at so we
# don't double-process.
STOP_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout", "opt-out"}


# ---------------------------------------------------------------------------
# Gateway call
# ---------------------------------------------------------------------------

def fetch_inbound(gateway: LocalSmsGateway) -> list[dict]:
    """
    Hit the phone's GET /message endpoint and return a list of inbound
    messages. The capcom6 gateway returns both sent + received messages
    in the log; we filter to only those with direction == 'incoming'
    (or no direction, depending on gateway version).
    """
    resp = gateway._request("GET", "/message")  # noqa: SLF001 — internal but stable
    resp.raise_for_status()
    data = resp.json()
    # Newer capcom6 API returns {"messages": [...]}; older just returns a list.
    if isinstance(data, dict) and "messages" in data:
        msgs = data["messages"]
    elif isinstance(data, list):
        msgs = data
    else:
        msgs = []
    # Only inbound. Different gateway versions use different field names.
    inbound = []
    for m in msgs:
        direction = (m.get("direction") or m.get("type") or "").lower()
        if direction in ("incoming", "in", "received", "rx"):
            inbound.append(m)
        elif not direction:
            # No direction field — best effort: if 'from' looks like a phone
            # and 'to' is a single recipient (our phone), it's inbound.
            frm = m.get("from") or m.get("phoneNumber") or ""
            if frm and not frm.startswith("+91"):  # heuristic
                continue
            if frm and normalize_phone_to_e164_india(frm):
                inbound.append(m)
    return inbound


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_seen_ids() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    with open(SEEN_PATH, encoding="utf-8") as f:
        return {row["id"] for row in csv.DictReader(f) if row.get("id")}


def save_seen_id(msg_id: str) -> None:
    new = not SEEN_PATH.exists()
    with open(SEEN_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "seen_at"])
        if new:
            w.writeheader()
        w.writerow({"id": msg_id, "seen_at": now_iso()})


def append_dnc(phone: str, reason: str) -> None:
    new = not DO_NOT_SMS_PATH.exists()
    with open(DO_NOT_SMS_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["phone", "reason", "added_at"])
        if new:
            w.writeheader()
        w.writerow({"phone": phone, "reason": reason, "added_at": now_iso()})


def append_partner_signup(phone: str, name: str, message: str, campaign: str | None) -> None:
    new = not PARTNER_SIGNUPS_PATH.exists()
    with open(PARTNER_SIGNUPS_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["phone", "name", "campaign", "message", "received_at", "follow_up"],
        )
        if new:
            w.writeheader()
        w.writerow({
            "phone": phone,
            "name": name,
            "campaign": campaign or "",
            "message": message,
            "received_at": now_iso(),
            "follow_up": "",  # team fills in after manual follow-up
        })


def find_lead(phone: str) -> tuple[Path | None, dict | None, str | None]:
    """
    Search the three lead CSVs for the given phone number.
    Returns (csv_path, row, campaign) or (None, None, None).
    """
    targets = [
        (ROOT / "leads_pharmacies.csv", "pharmacy"),
        (ROOT / "leads_diagnostics.csv", "diagnostic"),
        (ROOT / "leads_hospitals_clinics.csv", "clinic"),
    ]
    for path, campaign in targets:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for idx, r in enumerate(rows):
            e164 = normalize_phone_to_e164_india(r.get("phone", ""))
            if e164 == phone:
                return path, (idx, r), campaign
    return None, None, None


def update_lead_reply(path: Path, row_idx: int, row: dict, message: str, action: str) -> None:
    """Write the reply + action back to the lead CSV (additive columns)."""
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys())

    target = rows[row_idx]
    for col in ("reply", "reply_at", "reply_action", "sms_inbox_action"):
        if col not in fieldnames:
            fieldnames.append(col)
        target.setdefault(col, "")

    target["reply"] = message[:300]
    target["reply_at"] = now_iso()
    target["reply_action"] = action
    target["sms_inbox_action"] = action

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------

def classify(text: str) -> str:
    """Return one of: 'stop', 'partner_signup', 'reply'."""
    t = (text or "").strip().lower()
    if not t:
        return "reply"
    first = re.split(r"[\s,]+", t, maxsplit=1)[0]
    if first in STOP_KEYWORDS or t in STOP_KEYWORDS:
        return "stop"
    if first in PARTNER_REPLY_KEYWORDS:
        return "partner_signup"
    return "reply"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_once(verbose: bool = True) -> int:
    try:
        gateway = LocalSmsGateway.from_env()
    except ValueError as e:
        if verbose:
            print(f"SETUP NEEDED: {e}")
        return 1

    health = gateway.health_check()
    if not health.ok:
        if verbose:
            print(f"gateway unreachable: {health.status_code} {health.body}")
        return 2

    seen = load_seen_ids()
    msgs = fetch_inbound(gateway)
    new_msgs = []
    for m in msgs:
        mid = str(m.get("id") or m.get("messageId") or "")
        if not mid:
            frm = m.get("from") or m.get("phoneNumber") or ""
            txt = m.get("text") or m.get("message") or m.get("body") or ""
            ts = m.get("receivedAt") or m.get("date") or ""
            mid = f"noid:{frm}:{ts}:{hash(txt)}"
        if mid in seen:
            continue
        new_msgs.append((mid, m))

    if not new_msgs:
        if verbose:
            print("inbox: no new messages")
        return 0

    for mid, m in new_msgs:
        frm = m.get("from") or m.get("phoneNumber") or ""
        text = m.get("text") or m.get("message") or m.get("body") or ""
        phone = normalize_phone_to_e164_india(frm)
        if not phone:
            if verbose:
                print(f"  skip (could not normalise sender {frm!r})")
            save_seen_id(mid)
            continue

        action = classify(text)
        path, found, campaign = find_lead(phone)

        if action == "stop":
            append_dnc(phone, f"STOP reply: {text[:60]}")
            msg_action = "suppressed"
        elif action == "partner_signup":
            name = found[1].get("name", "") if found else ""
            append_partner_signup(phone, name, text, campaign)
            msg_action = "partner_signup"
        else:
            msg_action = "reply"

        if found:
            update_lead_reply(path, found[0], found[1], text, msg_action)
            lead_name = found[1].get("name", "") or ""
        else:
            lead_name = ""

        # Fan out notifications: macOS banner, email, and local log.
        # Errors are swallowed inside the notifier — never crashes the poll loop.
        notify_reply(
            action=action,
            phone=phone,
            name=lead_name,
            text=text,
            campaign=campaign or "",
        )

        if verbose:
            print(f"  [{action:>14s}] {phone}  →  {msg_action}")
            print(f"               text: {text[:120]}")

        save_seen_id(mid)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="Single pass, then exit.")
    ap.add_argument("--interval", type=int, default=60, help="Poll interval in seconds (default 60).")
    args = ap.parse_args()

    if args.once:
        return process_once(verbose=True)

    print(f"Polling inbox every {args.interval}s. Ctrl-C to stop.")
    while True:
        try:
            process_once(verbose=True)
        except Exception as e:
            print(f"[error] {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
