"""
Notification fan-out for inbound SMS replies.

Three channels, all opt-in via env:

  - FormSubmit.co (default ON if NOTIFY_EMAIL is set) — POST to
    https://formsubmit.co/ajax/<email> and FormSubmit forwards
    it to that email inbox. No SMTP, no API key, no signup.
    First-time use: FormSubmit sends a confirmation email to
    the target address — click it once to activate.

  - macOS native notification (default ON on darwin) — pops a banner
    in Notification Center so you see replies without watching the
    terminal. Uses `osascript` so no extra deps.

  - Local append-only log — always on. Writes one line per reply to
    .sms_inbox_log.txt. Good for grep / ack / building a digest.

Each call to `notify_reply(...)` is best-effort. Failures are
printed to stderr but never crash the inbox worker.
"""

from __future__ import annotations

import os
import smtplib
import subprocess
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / ".sms_inbox_log.txt"

CONTACT_PHONE_SMS = "+918790311010"


def _now_ist_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Local log (always on)
# ---------------------------------------------------------------------------

def _write_log(action: str, phone: str, name: str, text: str) -> None:
    try:
        line = f"[{_now_ist_str()}] {action:14s}  {phone}  {name[:30]:30s}  {text[:80]}\n"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[notify] log write failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# macOS native notification
# ---------------------------------------------------------------------------

def _notify_macos(title: str, body: str) -> bool:
    if sys.platform != "darwin":
        return False
    if os.getenv("NOTIFY_MACOS", "1") == "0":
        return False
    try:
        # Escape backslashes and double quotes for AppleScript.
        esc_title = title.replace("\\", "\\\\").replace('"', '\\"')
        esc_body = body.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{esc_body}" with title "{esc_title}"'
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=5,
            capture_output=True,
        )
        return True
    except Exception as e:
        print(f"[notify] macos notification failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Email via FormSubmit.co (no SMTP, no API key, no signup)
# ---------------------------------------------------------------------------

FORMSUBMIT_BASE = "https://formsubmit.co/ajax"


def _notify_formsubmit(action: str, phone: str, name: str, text: str, campaign: str) -> bool:
    """
    POST a form-encoded payload to https://formsubmit.co/ajax/<email>.
    FormSubmit then forwards it as an email to the target inbox.
    Set NOTIFY_EMAIL=notifications@docita.work (or your own) in .env.

    First-time use: FormSubmit sends a confirmation email to that
    address — click the link once to activate the forwarding. After
    that every POST is delivered as a regular email.

    Disable with NOTIFY_FORMSUBMIT=0 in .env.
    """
    if os.getenv("NOTIFY_FORMSUBMIT", "1") == "0":
        return False
    to_addr = os.getenv("NOTIFY_EMAIL", "").strip()
    if not to_addr:
        return False

    label = {
        "stop": "STOP",
        "partner_signup": "PARTNER SIGNUP",
        "reply": "REPLY",
    }.get(action, action.upper())

    subject = f"Docita SMS · {label} · {phone}"
    body = (
        f"Docita SMS reply received\n"
        f"\n"
        f"Action:  {label}\n"
        f"Name:    {name or '(unknown)'}\n"
        f"Phone:   {phone}\n"
        f"Campaign: {campaign or '(unknown)'}\n"
        f"Time:    {_now_ist_str()}\n"
        f"\n"
        f"Message:\n"
        f"{text}\n"
        f"\n"
        f"— hospital-scraper sms_inbox.py\n"
    )

    payload = {
        # FormSubmit special fields:
        "_subject": subject,
        "_template": "table",     # nice HTML table layout in the email
        "_captcha": "false",       # disable captcha (server-to-server)
        # Body fields:
        "Action": label,
        "Name": name or "(unknown)",
        "Phone": phone,
        "Campaign": campaign or "(unknown)",
        "Time": _now_ist_str(),
        "Message": text,
    }

    try:
        resp = requests.post(
            f"{FORMSUBMIT_BASE}/{to_addr}",
            data=payload,
            timeout=10,
        )
        ok = 200 <= resp.status_code < 300
        if not ok:
            print(
                f"[notify] formsubmit returned {resp.status_code}: {resp.text[:200]}",
                file=sys.stderr,
            )
        return ok
    except Exception as e:
        print(f"[notify] formsubmit request failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Email (SMTP) — kept as a fallback if you ever need it
# ---------------------------------------------------------------------------

def _notify_email_smtp(subject: str, body: str) -> bool:
    to_addr = os.getenv("NOTIFY_EMAIL", "").strip()
    if not to_addr:
        return False
    host = os.getenv("NOTIFY_SMTP_HOST", "").strip()
    port = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
    user = os.getenv("NOTIFY_SMTP_USER", "").strip()
    password = os.getenv("NOTIFY_SMTP_PASS", "")
    from_addr = os.getenv("NOTIFY_SMTP_FROM", user or f"docita-sms@localhost").strip()
    if not host:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[notify] smtp send failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def notify_reply(*, action: str, phone: str, name: str, text: str, campaign: str = "") -> None:
    """
    Called by scripts/sms_inbox.py for every classified reply.
    action: 'stop' | 'partner_signup' | 'reply'
    """
    # Always log locally.
    _write_log(action, phone, name, text)

    label = {
        "stop": "STOP",
        "partner_signup": "PARTNER SIGNUP",
        "reply": "REPLY",
    }.get(action, action.upper())

    title = f"Docita SMS · {label}"
    body = f"{name or '(unknown)'} · {phone}\n{text[:140]}"
    if campaign:
        body = f"[{campaign}] " + body

    _notify_macos(title, body)
    # FormSubmit is the primary email channel — no SMTP, no API key.
    _notify_formsubmit(action=action, phone=phone, name=name, text=text, campaign=campaign)
    # Optional SMTP fallback (kept for completeness, off by default).
    _notify_email_smtp(title, f"{body}\n\n— hospital-scraper sms_inbox.py")


# ---------------------------------------------------------------------------
# CLI for ad-hoc testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Test the notifier channels.")
    ap.add_argument("--action", default="reply")
    ap.add_argument("--phone", default="+919876543210")
    ap.add_argument("--name", default="Test Lead")
    ap.add_argument("--text", default="This is a test notification.")
    ap.add_argument("--campaign", default="clinic")
    args = ap.parse_args()
    notify_reply(
        action=args.action,
        phone=args.phone,
        name=args.name,
        text=args.text,
        campaign=args.campaign,
    )
    print(f"Notification fired for action={args.action}. Check:")
    print(f"  - log:        {LOG_PATH}")
    print(f"  - macOS:      Notification Center (if darwin)")
    print(f"  - FormSubmit: {os.getenv('NOTIFY_EMAIL', '(not set)')}")
