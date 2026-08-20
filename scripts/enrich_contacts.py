"""Enrich lead CSVs with additional public phone numbers from official sites."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contact_enrichment import EnrichmentResult, enrich_website  # noqa: E402
from outreach_common import normalize_phone_to_e164_india, now_iso  # noqa: E402

ENRICHMENT_COLUMNS = [
    "phone_numbers",
    "phone_sources",
    "contact_page",
    "contact_enrichment_status",
    "contact_enriched_at",
]


def _load(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _enrich(index: int, website: str, timeout_sec: float) -> tuple[int, EnrichmentResult]:
    return index, enrich_website(website, timeout_sec=timeout_sec)


def enrich_csv(
    path: Path,
    *,
    workers: int,
    timeout_sec: float,
    delay_ms: int,
    limit: int,
    overwrite: bool,
) -> tuple[int, int, int]:
    fieldnames, rows = _load(path)
    for column in ENRICHMENT_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)

    candidates = [
        (index, row.get("website", "").strip())
        for index, row in enumerate(rows)
        if row.get("website", "").strip()
        and (overwrite or not row.get("contact_enrichment_status", "").strip())
    ]
    if limit:
        candidates = candidates[:limit]
    if not candidates:
        return 0, 0, 0

    found = 0
    failed = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_enrich, index, website, timeout_sec): index
            for index, website in candidates
        }
        for future in as_completed(futures):
            index, result = future.result()
            row = rows[index]
            row["contact_enrichment_status"] = result.status
            row["contact_enriched_at"] = now_iso()
            row["contact_page"] = result.contact_page
            existing_phone = normalize_phone_to_e164_india(row.get("phone", ""))
            all_numbers: list[str] = []
            all_sources: list[str] = []
            if existing_phone:
                all_numbers.append(existing_phone)
                all_sources.append("existing_csv")
            for number, source in zip(result.phone_numbers, result.phone_sources):
                if number not in all_numbers:
                    all_numbers.append(number)
                    all_sources.append(source)
            row["phone_numbers"] = "|".join(all_numbers)
            row["phone_sources"] = "|".join(all_sources)
            if result.phone_numbers:
                found += 1
                if overwrite or not normalize_phone_to_e164_india(row.get("phone", "")):
                    row["phone"] = result.phone_numbers[0]
            elif result.status in {"unreachable", "invalid_website"}:
                failed += 1
            completed += 1
            if delay_ms:
                time.sleep(delay_ms / 1000)

    _write(path, fieldnames, rows)
    return completed, found, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", action="append", required=True, type=Path, help="Lead CSV to enrich; repeatable.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent sites (default: 4).")
    parser.add_argument("--timeout", type=float, default=8.0, help="Per-request timeout in seconds.")
    parser.add_argument("--delay-ms", type=int, default=100, help="Delay after each completed site (default: 100).")
    parser.add_argument("--limit", type=int, default=0, help="Maximum websites per CSV; 0 means all.")
    parser.add_argument("--overwrite", action="store_true", help="Replace the primary phone with the first public number found.")
    args = parser.parse_args()

    for path in args.csv:
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            print(f"[skip] {path} does not exist", file=sys.stderr)
            continue
        completed, found, failed = enrich_csv(
            path,
            workers=args.workers,
            timeout_sec=args.timeout,
            delay_ms=args.delay_ms,
            limit=args.limit,
            overwrite=args.overwrite,
        )
        print(f"{path.name}: checked={completed} found={found} unreachable_or_invalid={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
