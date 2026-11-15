from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

CIBC_DIRECTORY_URL = "https://cdr.cibc.com/en/cdr-directory"
BMO_DIRECTORY_URL = (
    "https://www.bmoinvestorline.com/wealth/journeys/content/micro/cdr/"
    "CanadianDepositaryReceipts.html"
)
BMO_FALLBACK_JSON_URL = (
    "https://www.bmoinvestorline.com/wealth/journeys/content/micro/cdr/JS/sunshine.json"
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}

_NG_STATE_RE = re.compile(
    r'<script[^>]*\bid=["\']ng-state["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_BMO_INDEX_JS_RE = re.compile(
    r'<script[^>]+src=["\']([^"\']*index\.js[^"\']*)["\']',
    re.IGNORECASE,
)
_BMO_FETCH_JSON_RE = re.compile(
    r"""fetch\(\s*['"]([^'"]+\.json[^'"]*)['"]\s*\)""",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CdrRecord:
    """Normalized CDR entry scraped from CIBC or BMO directories."""

    symbol: str
    name: str
    issuer: str
    country: str
    source: str
    source_url: str
    underlying_ticker: str | None = None
    underlying_symbol: str | None = None
    underlying_exchange: str | None = None
    underlying_currency: str | None = None
    cdr_exchange: str | None = None
    sector: str | None = None
    ratio: float | None = None
    fx_forward_rate: float | None = None
    listing_date: str | None = None
    volume_avg_10d: int | None = None
    is_active: bool = True

    def to_payload_dict(self) -> dict[str, Any]:
        """Serialize record for BigQuery JSON MERGE parameters and API responses."""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "issuer": self.issuer,
            "country": self.country,
            "source": self.source,
            "source_url": self.source_url,
            "underlying_ticker": self.underlying_ticker,
            "underlying_symbol": self.underlying_symbol,
            "underlying_exchange": self.underlying_exchange,
            "underlying_currency": self.underlying_currency,
            "cdr_exchange": self.cdr_exchange,
            "sector": self.sector,
            "ratio": self.ratio,
            "fx_forward_rate": self.fx_forward_rate,
            "listing_date": self.listing_date,
            "volume_avg_10d": self.volume_avg_10d,
            "is_active": self.is_active,
        }


@dataclass(slots=True)
class ScrapeOutcome:
    """Combined result from scraping all configured CDR directories."""

    records: list[CdrRecord] = field(default_factory=list)
    succeeded_issuers: set[str] = field(default_factory=set)
    source_counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def normalize_country(raw: str | None) -> str:
    """Normalize country labels across CIBC (`U.S.A.`) and BMO (`US`)."""
    cleaned = (raw or "").strip()
    upper = cleaned.upper().replace(".", "")
    if upper in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        return "US"
    return cleaned


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_cibc_html(html_text: str) -> list[CdrRecord]:
    """Extract normalized CIBC CDR records from the SSR Angular `ng-state` payload."""
    match = _NG_STATE_RE.search(html_text)
    if not match:
        raise ValueError("Could not locate <script id='ng-state'> JSON payload in CIBC page.")

    raw_json = html.unescape(match.group(1)).strip()
    state = json.loads(raw_json)
    cdr_items = state.get("cdr_list", {}).get("response")
    if not isinstance(cdr_items, list):
        raise ValueError("Missing 'cdr_list.response' list in CIBC ng-state payload.")

    records: list[CdrRecord] = []
    seen_symbols: set[str] = set()
    for item in cdr_items:
        if not isinstance(item, dict):
            continue
        symbol = _clean_str(item.get("ticker"))
        if not symbol:
            continue
        symbol = symbol.upper()
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)

        is_active = bool(item.get("active", True)) and bool(item.get("visible", True))
        listing_date = _clean_str(item.get("listingDate"))
        if listing_date and len(listing_date) > 10:
            listing_date = listing_date[:10]

        records.append(
            CdrRecord(
                symbol=symbol,
                name=_clean_str(item.get("name")) or symbol,
                issuer="CIBC",
                country=normalize_country(item.get("country")),
                source="cibc_directory",
                source_url=CIBC_DIRECTORY_URL,
                underlying_ticker=_clean_str(item.get("underlyingTicker")),
                underlying_symbol=_clean_str(item.get("underlyingTradingSymbol")),
                underlying_exchange=_clean_str(item.get("underlyingExchangeEn")),
                underlying_currency=_clean_str(item.get("underlyingCurrency")),
                cdr_exchange=_clean_str(item.get("cdrExchangeEn")),
                sector=_clean_str(item.get("sector")),
                ratio=_clean_float(item.get("ratio")),
                fx_forward_rate=_clean_float(item.get("fxForwardRate")),
                listing_date=listing_date,
                volume_avg_10d=_clean_int(item.get("volumeAverageTenDay")),
                is_active=is_active,
            )
        )
    return records


def parse_bmo_json(items: list[dict[str, Any]], source_url: str = BMO_DIRECTORY_URL) -> list[CdrRecord]:
    """Normalize BMO directory JSON items into `CdrRecord` entries."""
    records: list[CdrRecord] = []
    seen_keys: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = _clean_str(item.get("SYMBOL"))
        if not symbol:
            continue
        symbol = symbol.upper()
        issuer = (_clean_str(item.get("Issuer")) or "BMO").upper()
        if (symbol, issuer) in seen_keys:
            continue
        seen_keys.add((symbol, issuer))

        records.append(
            CdrRecord(
                symbol=symbol,
                name=_clean_str(item.get("NAME")) or symbol,
                issuer=issuer,
                country=normalize_country(item.get("COUNTRY")),
                source="bmo_directory",
                source_url=source_url,
                is_active=True,
            )
        )
    return records


async def fetch_cibc_cdrs(client: httpx.AsyncClient) -> list[CdrRecord]:
    """Fetch and parse the live CIBC CDR directory page."""
    response = await client.get(CIBC_DIRECTORY_URL)
    response.raise_for_status()
    return parse_cibc_html(response.text)


async def fetch_bmo_cdrs(client: httpx.AsyncClient) -> list[CdrRecord]:
    """Discover the JSON data feed from the BMO CDR page and parse all entries."""
    json_url = BMO_FALLBACK_JSON_URL
    try:
        page_resp = await client.get(BMO_DIRECTORY_URL)
        page_resp.raise_for_status()
        js_match = _BMO_INDEX_JS_RE.search(page_resp.text)
        if js_match:
            js_url = urljoin(BMO_DIRECTORY_URL, js_match.group(1))
            js_resp = await client.get(js_url)
            js_resp.raise_for_status()
            fetch_match = _BMO_FETCH_JSON_RE.search(js_resp.text)
            if fetch_match:
                json_url = urljoin(BMO_DIRECTORY_URL, fetch_match.group(1))
    except httpx.HTTPError:
        # Fall back to the known JSON feed URL if HTML/JS discovery fails transiently.
        json_url = BMO_FALLBACK_JSON_URL

    data_resp = await client.get(json_url)
    data_resp.raise_for_status()
    payload = data_resp.json()
    if not isinstance(payload, list):
        raise ValueError(f"Expected JSON list from BMO CDR feed {json_url}, got {type(payload).__name__}.")
    return parse_bmo_json(payload, source_url=BMO_DIRECTORY_URL)


async def scrape_all_cdrs(timeout_seconds: float = 30.0) -> ScrapeOutcome:
    """Scrape CIBC and BMO CDR directories concurrently and return deduplicated records.

    CIBC's directory is authoritative for CIBC-issued CDRs (BMO's feed contains a subset
    plus historical stale tickers like CHEV/MCDS). BMO's feed provides all non-CIBC
    issuers (primarily BMO-issued CDRs).
    """
    outcome = ScrapeOutcome()

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as client:
        cibc_task = fetch_cibc_cdrs(client)
        bmo_task = fetch_bmo_cdrs(client)
        cibc_res, bmo_res = await asyncio.gather(cibc_task, bmo_task, return_exceptions=True)

    by_symbol: dict[str, CdrRecord] = {}

    if isinstance(cibc_res, Exception):
        outcome.errors["cibc_directory"] = f"{type(cibc_res).__name__}: {cibc_res}"
    else:
        outcome.succeeded_issuers.add("CIBC")
        outcome.source_counts["cibc_directory"] = len(cibc_res)
        for rec in cibc_res:
            by_symbol[rec.symbol] = rec

    if isinstance(bmo_res, Exception):
        outcome.errors["bmo_directory"] = f"{type(bmo_res).__name__}: {bmo_res}"
    else:
        non_cibc_bmo = [rec for rec in bmo_res if rec.issuer != "CIBC"]
        bmo_issuers = {rec.issuer for rec in non_cibc_bmo} or {"BMO"}
        outcome.succeeded_issuers.update(bmo_issuers)
        outcome.source_counts["bmo_directory"] = len(non_cibc_bmo)
        for rec in non_cibc_bmo:
            # Never overwrite an authoritative CIBC entry if a ticker ever collided.
            if rec.symbol not in by_symbol:
                by_symbol[rec.symbol] = rec

    outcome.records = sorted(by_symbol.values(), key=lambda r: (r.issuer, r.symbol))
    return outcome
