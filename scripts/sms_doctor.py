"""Quick reachability check for the local SMS gateway (no SMS sent)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sms_gateway import LocalSmsGateway  # noqa: E402

try:
    g = LocalSmsGateway.from_env()
except ValueError as e:
    print(f"SETUP NEEDED: {e}")
    raise SystemExit(1)

r = g.health_check()
if r.ok:
    print(f"OK — gateway reachable at {g.host_label}")
    raise SystemExit(0)
print(f"FAIL: status={r.status_code} body={r.body}")
print()
print("Phone-side checklist (open the SMS Gateway app on the phone):")
print("  1. Local Server toggle is ON")
print("  2. Bottom button shows 'Online' (not 'Offline')")
print("  3. Settings → Local Server has a username (≥3 chars) and password (≥8 chars)")
print("  4. The phone's local IP (e.g. 192.168.1.50) is what you put in SMS_GATEWAY_URL")
print()
print("Mac-side checklist:")
print(f"  1. Mac is on the same WiFi as the phone")
print(f"  2. Quick test:  curl -u USER:PASS http://{g.host_label}/message")
print("     Expected: 200 (or 401/403 if creds are off) — anything else means unreachable")
raise SystemExit(1)
