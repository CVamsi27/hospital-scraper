"""Send one explicit gateway test SMS to OWNER_PHONE_E164."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outreach_common import normalize_phone_to_e164_india  # noqa: E402
from sms_gateway import LocalSmsGateway  # noqa: E402


def main() -> int:
    recipient = normalize_phone_to_e164_india(os.getenv("OWNER_PHONE_E164", ""))
    if not recipient:
        print("OWNER_PHONE_E164 must be a valid Indian mobile number.", file=sys.stderr)
        return 2
    try:
        gateway = LocalSmsGateway.from_env()
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    health = gateway.health_check()
    if not health.ok:
        print(f"[error] gateway unreachable: {health.error or health.body}", file=sys.stderr)
        return 3
    result = gateway.send_sms(
        recipient,
        "Docita SMS gateway test. If you received this, the local phone gateway accepted it. Reply STOP to opt out.",
    )
    if result.ok:
        print(f"OK — gateway accepted test SMS for {recipient}; carrier delivery is not confirmed.")
        return 0
    print(f"FAIL — status={result.status_code} error={result.error or result.body}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
