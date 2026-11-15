from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from google.cloud import bigquery

from app.config import settings
from app.db import ensure_cdr_list_table, get_bq_client
from app.services.cdr_scraper import CdrRecord, ScrapeOutcome, scrape_all_cdrs

logger = logging.getLogger(__name__)

_COMPARE_FIELDS = (
    "name",
    "issuer",
    "country",
    "underlying_ticker",
    "underlying_symbol",
    "underlying_exchange",
    "underlying_currency",
    "cdr_exchange",
    "sector",
    "ratio",
    "listing_date",
    "is_active",
)


@dataclass(slots=True)
class CdrDiff:
    """Comparison summary between existing BigQuery `cdr_list` rows and scraped CDRs."""

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SyncReport:
    """Result returned by a scrape-compare-merge cycle."""

    status: str
    trigger: str
    started_at: str
    completed_at: str
    table: str
    total_scraped: int
    total_active: int
    added_count: int
    updated_count: int
    removed_count: int
    unchanged_count: int
    added_symbols: list[str]
    updated_symbols: list[str]
    removed_symbols: list[str]
    source_counts: dict[str, int]
    errors: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "table": self.table,
            "total_scraped": self.total_scraped,
            "total_active": self.total_active,
            "added_count": self.added_count,
            "updated_count": self.updated_count,
            "removed_count": self.removed_count,
            "unchanged_count": self.unchanged_count,
            "added_symbols": self.added_symbols,
            "updated_symbols": self.updated_symbols,
            "removed_symbols": self.removed_symbols,
            "source_counts": self.source_counts,
            "errors": self.errors,
        }


_sync_lock = asyncio.Lock()
_cache_lock = asyncio.Lock()
_cached_rows: list[dict[str, Any]] | None = None
_cache_loaded_at: float = 0.0
_last_sync_report: SyncReport | None = None


def invalidate_cdr_cache() -> None:
    """Clear the in-memory `cdr_list` cache."""
    global _cached_rows, _cache_loaded_at
    _cached_rows = None
    _cache_loaded_at = 0.0


def _normalize_compare_value(value: Any) -> Any:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 8)
    if value == "":
        return None
    return value


def _serialize_row(row: bigquery.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    for key in ("listing_date", "first_seen_at", "last_seen_at", "updated_at"):
        val = data.get(key)
        if isinstance(val, (datetime, date)):
            data[key] = val.isoformat()
    return data


def compare_cdrs(
    existing_by_symbol: dict[str, dict[str, Any]],
    scraped_records: list[CdrRecord],
    succeeded_issuers: set[str],
) -> CdrDiff:
    """Compare existing `cdr_list` rows against freshly scraped records."""
    diff = CdrDiff()
    scraped_by_symbol = {rec.symbol: rec for rec in scraped_records}

    for symbol, rec in sorted(scraped_by_symbol.items()):
        existing = existing_by_symbol.get(symbol)
        if existing is None:
            diff.added.append(symbol)
            continue

        was_active = bool(existing.get("is_active", True))
        if not was_active and rec.is_active:
            diff.added.append(symbol)
            continue

        rec_dict = rec.to_payload_dict()
        changed = any(
            _normalize_compare_value(existing.get(field_name))
            != _normalize_compare_value(rec_dict.get(field_name))
            for field_name in _COMPARE_FIELDS
        )
        if changed:
            diff.updated.append(symbol)
        else:
            diff.unchanged.append(symbol)

    for symbol, existing in sorted(existing_by_symbol.items()):
        if (
            bool(existing.get("is_active", True))
            and existing.get("issuer") in succeeded_issuers
            and symbol not in scraped_by_symbol
        ):
            diff.removed.append(symbol)

    return diff


def _read_cdr_rows_sync(client: bigquery.Client) -> list[dict[str, Any]]:
    table_fqn = ensure_cdr_list_table(client)
    query = f"""
        SELECT
            symbol, name, issuer, country, source, source_url,
            underlying_ticker, underlying_symbol, underlying_exchange, underlying_currency,
            cdr_exchange, sector, ratio, fx_forward_rate, listing_date, volume_avg_10d,
            is_active, is_new, first_seen_at, last_seen_at, updated_at
        FROM `{table_fqn}`
        ORDER BY is_active DESC, is_new DESC, issuer ASC, symbol ASC
    """
    rows = client.query(query).result()
    return [_serialize_row(row) for row in rows]


def _merge_cdr_rows_sync(
    client: bigquery.Client,
    records: list[CdrRecord],
    succeeded_issuers: set[str],
    sync_ts: datetime,
    is_initial_seed: bool,
) -> None:
    table_fqn = ensure_cdr_list_table(client)
    payload_json = json.dumps([rec.to_payload_dict() for rec in records])

    merge_sql = f"""
        MERGE `{table_fqn}` AS T
        USING (
            SELECT
                JSON_VALUE(item, '$.symbol') AS symbol,
                JSON_VALUE(item, '$.name') AS name,
                JSON_VALUE(item, '$.issuer') AS issuer,
                NULLIF(JSON_VALUE(item, '$.country'), '') AS country,
                JSON_VALUE(item, '$.source') AS source,
                NULLIF(JSON_VALUE(item, '$.source_url'), '') AS source_url,
                NULLIF(JSON_VALUE(item, '$.underlying_ticker'), '') AS underlying_ticker,
                NULLIF(JSON_VALUE(item, '$.underlying_symbol'), '') AS underlying_symbol,
                NULLIF(JSON_VALUE(item, '$.underlying_exchange'), '') AS underlying_exchange,
                NULLIF(JSON_VALUE(item, '$.underlying_currency'), '') AS underlying_currency,
                NULLIF(JSON_VALUE(item, '$.cdr_exchange'), '') AS cdr_exchange,
                NULLIF(JSON_VALUE(item, '$.sector'), '') AS sector,
                SAFE_CAST(JSON_VALUE(item, '$.ratio') AS FLOAT64) AS ratio,
                SAFE_CAST(JSON_VALUE(item, '$.fx_forward_rate') AS FLOAT64) AS fx_forward_rate,
                SAFE_CAST(NULLIF(JSON_VALUE(item, '$.listing_date'), '') AS DATE) AS listing_date,
                SAFE_CAST(JSON_VALUE(item, '$.volume_avg_10d') AS INT64) AS volume_avg_10d,
                COALESCE(SAFE_CAST(JSON_VALUE(item, '$.is_active') AS BOOL), TRUE) AS is_active
            FROM UNNEST(JSON_QUERY_ARRAY(@payload_json)) AS item
        ) AS S
        ON T.symbol = S.symbol
        WHEN MATCHED THEN
            UPDATE SET
                name = S.name,
                issuer = S.issuer,
                country = S.country,
                source = S.source,
                source_url = S.source_url,
                underlying_ticker = S.underlying_ticker,
                underlying_symbol = S.underlying_symbol,
                underlying_exchange = S.underlying_exchange,
                underlying_currency = S.underlying_currency,
                cdr_exchange = S.cdr_exchange,
                sector = S.sector,
                ratio = S.ratio,
                fx_forward_rate = S.fx_forward_rate,
                listing_date = S.listing_date,
                volume_avg_10d = S.volume_avg_10d,
                is_active = S.is_active,
                is_new = CASE
                    WHEN NOT T.is_active AND S.is_active THEN TRUE
                    ELSE T.is_new AND T.first_seen_at >= TIMESTAMP_SUB(@sync_ts, INTERVAL 7 DAY)
                END,
                last_seen_at = @sync_ts,
                updated_at = CASE
                    WHEN T.name IS DISTINCT FROM S.name
                        OR T.issuer IS DISTINCT FROM S.issuer
                        OR T.country IS DISTINCT FROM S.country
                        OR T.underlying_ticker IS DISTINCT FROM S.underlying_ticker
                        OR T.underlying_symbol IS DISTINCT FROM S.underlying_symbol
                        OR T.underlying_exchange IS DISTINCT FROM S.underlying_exchange
                        OR T.underlying_currency IS DISTINCT FROM S.underlying_currency
                        OR T.cdr_exchange IS DISTINCT FROM S.cdr_exchange
                        OR T.sector IS DISTINCT FROM S.sector
                        OR T.ratio IS DISTINCT FROM S.ratio
                        OR T.listing_date IS DISTINCT FROM S.listing_date
                        OR T.is_active IS DISTINCT FROM S.is_active
                    THEN @sync_ts
                    ELSE T.updated_at
                END
        WHEN NOT MATCHED THEN
            INSERT (
                symbol, name, issuer, country, source, source_url,
                underlying_ticker, underlying_symbol, underlying_exchange, underlying_currency,
                cdr_exchange, sector, ratio, fx_forward_rate, listing_date, volume_avg_10d,
                is_active, is_new, first_seen_at, last_seen_at, updated_at
            )
            VALUES (
                S.symbol, S.name, S.issuer, S.country, S.source, S.source_url,
                S.underlying_ticker, S.underlying_symbol, S.underlying_exchange, S.underlying_currency,
                S.cdr_exchange, S.sector, S.ratio, S.fx_forward_rate, S.listing_date, S.volume_avg_10d,
                S.is_active, NOT @is_initial_seed, @sync_ts, @sync_ts, @sync_ts
            )
        WHEN NOT MATCHED BY SOURCE AND T.is_active = TRUE AND T.issuer IN UNNEST(@succeeded_issuers) THEN
            UPDATE SET
                is_active = FALSE,
                is_new = FALSE,
                updated_at = @sync_ts
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("payload_json", "STRING", payload_json),
            bigquery.ScalarQueryParameter("sync_ts", "TIMESTAMP", sync_ts),
            bigquery.ScalarQueryParameter("is_initial_seed", "BOOL", is_initial_seed),
            bigquery.ArrayQueryParameter("succeeded_issuers", "STRING", sorted(succeeded_issuers)),
        ]
    )
    client.query(merge_sql, job_config=job_config).result()


async def sync_cdrs(
    trigger: str = "manual",
    client: bigquery.Client | None = None,
) -> SyncReport:
    """Scrape CIBC & BMO, compare against BigQuery `cdr_list`, and merge updates."""
    global _cached_rows, _cache_loaded_at, _last_sync_report

    async with _sync_lock:
        started_at = datetime.now(timezone.utc)
        outcome: ScrapeOutcome = await scrape_all_cdrs()

        if not outcome.succeeded_issuers:
            completed_at = datetime.now(timezone.utc)
            report = SyncReport(
                status="error",
                trigger=trigger,
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                table=settings.cdr_list_table_fqn,
                total_scraped=0,
                total_active=0,
                added_count=0,
                updated_count=0,
                removed_count=0,
                unchanged_count=0,
                added_symbols=[],
                updated_symbols=[],
                removed_symbols=[],
                source_counts=outcome.source_counts,
                errors=outcome.errors,
            )
            _last_sync_report = report
            logger.error("CDR sync aborted (%s): all scrapers failed: %s", trigger, outcome.errors)
            return report

        bq_client = client or get_bq_client()

        def _sync_db() -> tuple[CdrDiff, list[dict[str, Any]]]:
            existing_rows = _read_cdr_rows_sync(bq_client)
            existing_by_symbol = {row["symbol"]: row for row in existing_rows}
            diff = compare_cdrs(existing_by_symbol, outcome.records, outcome.succeeded_issuers)
            _merge_cdr_rows_sync(
                client=bq_client,
                records=outcome.records,
                succeeded_issuers=outcome.succeeded_issuers,
                sync_ts=started_at,
                is_initial_seed=len(existing_rows) == 0,
            )
            updated_rows = _read_cdr_rows_sync(bq_client)
            return diff, updated_rows

        diff, updated_rows = await asyncio.to_thread(_sync_db)

        async with _cache_lock:
            _cached_rows = updated_rows
            _cache_loaded_at = time.monotonic()

        total_active = sum(1 for row in updated_rows if row.get("is_active", True))
        completed_at = datetime.now(timezone.utc)
        report = SyncReport(
            status="partial_success" if outcome.errors else "ok",
            trigger=trigger,
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            table=settings.cdr_list_table_fqn,
            total_scraped=len(outcome.records),
            total_active=total_active,
            added_count=len(diff.added),
            updated_count=len(diff.updated),
            removed_count=len(diff.removed),
            unchanged_count=len(diff.unchanged),
            added_symbols=diff.added,
            updated_symbols=diff.updated,
            removed_symbols=diff.removed,
            source_counts=outcome.source_counts,
            errors=outcome.errors,
        )
        _last_sync_report = report
        logger.info(
            "CDR sync (%s) complete: scraped=%d active=%d added=%d updated=%d removed=%d errors=%s",
            trigger,
            report.total_scraped,
            report.total_active,
            report.added_count,
            report.updated_count,
            report.removed_count,
            report.errors or "none",
        )
        return report


async def list_cdrs_from_store(
    issuer: str | None = None,
    active_only: bool = True,
    new_only: bool = False,
    search: str | None = None,
    force_refresh: bool = False,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Return `cdr_list` rows using an in-memory TTL cache backed by BigQuery."""
    global _cached_rows, _cache_loaded_at

    async with _cache_lock:
        cache_age = time.monotonic() - _cache_loaded_at
        cache_valid = (
            not force_refresh
            and _cached_rows is not None
            and cache_age < settings.cdr_cache_ttl_seconds
        )
        if not cache_valid:
            bq_client = client or get_bq_client()
            _cached_rows = await asyncio.to_thread(_read_cdr_rows_sync, bq_client)
            _cache_loaded_at = time.monotonic()
        all_rows = list(_cached_rows or [])

    filtered = all_rows
    if active_only:
        filtered = [r for r in filtered if r.get("is_active", True)]
    if new_only:
        filtered = [r for r in filtered if r.get("is_new", False)]
    if issuer:
        issuer_norm = issuer.strip().upper()
        filtered = [r for r in filtered if (r.get("issuer") or "").upper() == issuer_norm]
    if search:
        term = search.strip().lower()
        filtered = [
            r
            for r in filtered
            if term in (r.get("symbol") or "").lower()
            or term in (r.get("name") or "").lower()
            or term in (r.get("sector") or "").lower()
        ]

    active_rows = [r for r in all_rows if r.get("is_active", True)]
    issuer_counts: dict[str, int] = {}
    for r in active_rows:
        iss = r.get("issuer") or "UNKNOWN"
        issuer_counts[iss] = issuer_counts.get(iss, 0) + 1

    return {
        "status": "ok",
        "table": settings.cdr_list_table_fqn,
        "count": len(filtered),
        "total_active": len(active_rows),
        "total_new": sum(1 for r in active_rows if r.get("is_new", False)),
        "issuer_counts": issuer_counts,
        "last_sync": _last_sync_report.to_dict() if _last_sync_report else None,
        "items": filtered,
        "message": f"Loaded {len(filtered)} CDR(s) from {settings.bq_dataset}.{settings.cdr_table_name}.",
    }


def get_last_sync_status() -> dict[str, Any]:
    """Return metadata for the most recent scrape/sync run in this process."""
    if _last_sync_report is None:
        return {
            "status": "pending",
            "table": settings.cdr_list_table_fqn,
            "last_sync": None,
            "message": "No sync has completed yet in this process.",
        }
    return {
        "status": _last_sync_report.status,
        "table": settings.cdr_list_table_fqn,
        "last_sync": _last_sync_report.to_dict(),
    }
