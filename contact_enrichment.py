"""Conservative public-phone enrichment for lead websites.

Only pages linked from a lead's official website are fetched. This module does
not scrape Google Maps HTML, search-engine result pages, private directories,
or guessed personal numbers. Callers should use :func:`enrich_website` from a
bounded worker pool and preserve the returned source URL for auditability.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from outreach_common import normalize_phone_to_e164_india

USER_AGENT = "DocitaLeadEnrichment/1.0 (+https://docita.work)"
CONTACT_PATHS = (
    "",
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/reach-us",
    "/appointment",
    "/book",
)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?91[\s().-]*)?0?[6-9](?:[\s().-]*\d){9}(?!\d)"
)
HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(?:script|style|noscript)\b.*?</(?:script|style|noscript)>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class EnrichmentResult:
    phone_numbers: tuple[str, ...]
    phone_sources: tuple[str, ...]
    contact_page: str
    status: str
    error: str = ""


def _base_url(raw_url: str) -> str | None:
    value = (raw_url or "").strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _robots_allows(session: requests.Session, url: str, timeout_sec: float) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=timeout_sec, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        return False
    if response.status_code in {401, 403}:
        return False
    if response.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _candidate_urls(base: str) -> list[str]:
    return [urljoin(base + "/", path.lstrip("/")) for path in CONTACT_PATHS]


def _extract_candidates(body: str) -> list[str]:
    cleaned = HTML_TAG_RE.sub(" ", SCRIPT_STYLE_RE.sub(" ", html.unescape(body)))
    values = PHONE_RE.findall(body) + PHONE_RE.findall(cleaned)
    numbers: list[str] = []
    for value in values:
        normalized = normalize_phone_to_e164_india(value)
        if normalized and normalized not in numbers:
            numbers.append(normalized)
    return numbers


def enrich_website(
    website: str,
    *,
    timeout_sec: float = 8.0,
    session: requests.Session | None = None,
) -> EnrichmentResult:
    """Fetch a small set of public official-site pages and extract phones."""

    base = _base_url(website)
    if not base:
        return EnrichmentResult((), (), "", "invalid_website")

    client = session or requests.Session()
    client.headers.update({"User-Agent": USER_AGENT})
    numbers: list[str] = []
    sources: list[str] = []
    fetched_pages: list[str] = []

    for url in _candidate_urls(base):
        if not _robots_allows(client, url, timeout_sec):
            continue
        try:
            response = client.get(url, timeout=timeout_sec, allow_redirects=True)
            response.raise_for_status()
        except requests.RequestException:
            continue
        if "text/html" not in response.headers.get("content-type", "text/html").lower():
            continue
        fetched_pages.append(response.url)
        page_numbers = _extract_candidates(response.text)
        for number in page_numbers:
            if number not in numbers:
                numbers.append(number)
                sources.append(response.url)

        # Follow only same-site contact-like links from the homepage.
        if url == base or url == base + "/":
            for href in HREF_RE.findall(response.text):
                absolute = urljoin(response.url, html.unescape(href))
                parsed = urlparse(absolute)
                if parsed.netloc != urlparse(base).netloc:
                    continue
                if any(token in (parsed.path + parsed.query).lower() for token in ("contact", "reach", "about", "appointment")):
                    if absolute not in fetched_pages and _robots_allows(client, absolute, timeout_sec):
                        try:
                            linked = client.get(absolute, timeout=timeout_sec, allow_redirects=True)
                            linked.raise_for_status()
                        except requests.RequestException:
                            continue
                        if "text/html" not in linked.headers.get("content-type", "text/html").lower():
                            continue
                        linked_numbers = _extract_candidates(linked.text)
                        for number in linked_numbers:
                            if number not in numbers:
                                numbers.append(number)
                                sources.append(linked.url)

    return EnrichmentResult(
        tuple(numbers),
        tuple(sources),
        sources[0] if sources else "",
        "found" if numbers else ("not_found" if fetched_pages else "unreachable"),
    )
