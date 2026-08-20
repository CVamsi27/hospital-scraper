"""
Local-mode SMS gateway client for [SMS Gateway for Android](https://github.com/capcom6/android-sms-gateway).

Architecture: phone runs the SMS Gateway for Android app in **Local Server** mode
(a tiny HTTP server on the phone, port 8080 by default). This Mac script talks
to it over your local WiFi using HTTP Basic Auth. No cloud, no Firebase, no tunnel.

Setup (one-time, ~5 min):
    1. On the Android phone, install "SMS Gateway for Android" from the Play Store.
    2. Open the app. Go to Settings → Local Server:
         - Port: 8080 (default)
         - Username: anything ≥ 3 chars
         - Password: anything ≥ 8 chars
    3. Back on the Home tab, flip the "Local Server" toggle ON, then tap
       "Offline" so it becomes "Online". The phone will show its local IP
       (e.g. 192.168.1.50).
    4. Plug those three values into .env:
         SMS_GATEWAY_URL=http://192.168.1.50:8080
         SMS_GATEWAY_USER=...
         SMS_GATEWAY_PASS=...
    5. Make sure the phone and Mac are on the same WiFi, then `make doctor`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests


@dataclass
class SmsSendResult:
    ok: bool
    status_code: int
    body: str
    error: str = ""


class LocalSmsGateway:
    """
    Sends SMS through an Android phone running SMS Gateway for Android in local mode.
    See module docstring for setup.
    """

    DEFAULT_PORT = 8080

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_sec: int = 30,
    ) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.username = username.strip()
        self.password = password.strip()
        self.timeout_sec = timeout_sec

    # ---- factory -------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "LocalSmsGateway":
        base_url = os.getenv("SMS_GATEWAY_URL", "").strip()
        username = os.getenv("SMS_GATEWAY_USER", "").strip()
        password = os.getenv("SMS_GATEWAY_PASS", "").strip()
        if not base_url:
            raise ValueError(
                "SMS_GATEWAY_URL is not set. See docs/SMS_OUTREACH_PLAN.md §2 for the 5-min setup."
            )
        if not username or not password:
            raise ValueError(
                "SMS_GATEWAY_USER and SMS_GATEWAY_PASS must be set in .env "
                "(username ≥ 3 chars, password ≥ 8 chars — set inside the Android app)."
            )
        return cls(base_url=base_url, username=username, password=password)

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _normalize_base_url(raw: str) -> str:
        # Allow "192.168.1.50:8080" or "http://192.168.1.50:8080/" — normalise.
        s = (raw or "").strip().rstrip("/")
        if not s:
            raise ValueError("empty SMS_GATEWAY_URL")
        if not s.startswith("http://") and not s.startswith("https://"):
            s = "http://" + s
        return s

    @property
    def host_label(self) -> str:
        return urlparse(self.base_url).netloc or self.base_url

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        url = f"{self.base_url}{path}"
        return requests.request(
            method,
            url,
            auth=(self.username, self.password),
            timeout=self.timeout_sec,
            **kw,
        )

    # ---- public API ----------------------------------------------------------

    def health_check(self) -> SmsSendResult:
        """
        The local gateway doesn't expose a /health endpoint, so we hit a
        no-op-ish endpoint (the messages log) and treat 401/403/200 as reachable.
        """
        try:
            resp = self._request("GET", "/message")
        except requests.RequestException as exc:
            return SmsSendResult(ok=False, status_code=0, body="", error=str(exc))
        # 200: ok. 401/403: reachable but auth wrong — still "the phone is there".
        ok = resp.status_code in (200, 401, 403)
        return SmsSendResult(
            ok=ok,
            status_code=resp.status_code,
            body=resp.text[:500],
            error="" if ok else f"unreachable (status={resp.status_code})",
        )

    def send_sms(self, recipient: str, message: str) -> SmsSendResult:
        """Submit to the phone; ``ok`` means gateway acceptance only.

        The local Android gateway does not provide carrier delivery receipts,
        so callers must not label a successful HTTP response as delivered.
        """
        payload = {
            "textMessage": {"text": message},
            "phoneNumbers": [recipient],
        }
        try:
            resp = self._request("POST", "/message", json=payload)
        except requests.RequestException as exc:
            return SmsSendResult(ok=False, status_code=0, body="", error=str(exc))
        ok = 200 <= resp.status_code < 300
        return SmsSendResult(
            ok=ok,
            status_code=resp.status_code,
            body=resp.text[:500],
            error="" if ok else (resp.text[:500] or f"HTTP {resp.status_code}"),
        )

    def send_batch(
        self,
        items: list[tuple[str, str]],
        *,
        delay_sec: float = 4.0,
        on_progress=None,
    ) -> list[tuple[str, SmsSendResult]]:
        """
        Send messages sequentially with delay between them. The local gateway
        accepts multiple phone numbers per request, so we batch up to 50 per
        call to keep round-trips low. items: list of (recipient_e164, message).
        Note: a single /message call sends the same text to all phones in the
        batch — that's why we use it as a per-row fallback here.
        """
        results: list[tuple[str, SmsSendResult]] = []
        for i, (recipient, message) in enumerate(items):
            if i > 0 and delay_sec > 0:
                time.sleep(delay_sec)
            result = self.send_sms(recipient, message)
            results.append((recipient, result))
            if on_progress:
                on_progress(i + 1, len(items), recipient, result)
        return results


# Backwards-compat alias: the old Textbee client lived here. Anything that
# imports `TextbeeGateway` still gets a clear error if accidentally re-enabled.
def TextbeeGateway(*_args, **_kwargs):  # pragma: no cover
    raise RuntimeError(
        "TextbeeGateway was removed. This repo is now local-only — use "
        "LocalSmsGateway (set SMS_GATEWAY_URL/USER/PASS in .env). "
        "See docs/SMS_OUTREACH_PLAN.md §2."
    )
