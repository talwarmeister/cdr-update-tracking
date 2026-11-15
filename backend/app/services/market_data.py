from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.services.questrade_auth import QuestradeSession, get_questrade_session

logger = logging.getLogger(__name__)

_CANADIAN_EXCHANGES = {"TSX", "NEO", "CBOE", "TSXV", "CNQ", "ALPHA", "CX2", "OMEGA"}
_SYMBOL_BATCH_SIZE = 25
_QUOTE_BATCH_SIZE = 50


@dataclass(slots=True)
class ResolvedSymbol:
    """Cached mapping from CDR ticker to Questrade symbol metadata."""

    cdr_symbol: str
    questrade_symbol: str
    symbol_id: int
    listing_exchange: str | None
    currency: str | None


@dataclass(slots=True)
class CachedQuote:
    """Cached Level 1 quote payload with monotonic fetch timestamp."""

    data: dict[str, Any]
    fetched_monotonic: float


_symbol_lock = asyncio.Lock()
_quote_lock = asyncio.Lock()
_resolved_symbols: dict[str, ResolvedSymbol] = {}
_quote_cache: dict[str, CachedQuote] = {}


def clear_market_data_cache() -> None:
    """Clear in-memory Questrade symbol resolution and quote caches."""
    _resolved_symbols.clear()
    _quote_cache.clear()


def _candidate_names(cdr_symbol: str, issuer: str | None = None) -> list[str]:
    clean = cdr_symbol.strip().upper()
    if "." in clean:
        return [clean]
    if (issuer or "").strip().upper() == "BMO":
        return [f"{clean}.NE", f"{clean}.TO"]
    return [f"{clean}.TO", f"{clean}.NE"]


def _pick_best_symbol_match(
    cdr_symbol: str,
    candidates: list[dict[str, Any]],
    issuer: str | None = None,
) -> ResolvedSymbol | None:
    clean = cdr_symbol.strip().upper()
    preferred_order = _candidate_names(clean, issuer=issuer)
    by_exact = {
        str(item.get("symbol") or "").strip().upper(): item
        for item in candidates
        if item.get("symbolId") is not None
    }

    for name in preferred_order:
        matched = by_exact.get(name)
        if matched is not None:
            return ResolvedSymbol(
                cdr_symbol=clean,
                questrade_symbol=str(matched["symbol"]).strip().upper(),
                symbol_id=int(matched["symbolId"]),
                listing_exchange=matched.get("listingExchange"),
                currency=matched.get("currency"),
            )

    for item in candidates:
        sym = str(item.get("symbol") or "").strip().upper()
        exch = str(item.get("listingExchange") or "").strip().upper()
        curr = str(item.get("currency") or "").strip().upper()
        if item.get("symbolId") is None:
            continue
        if (sym.startswith(f"{clean}.") or sym == clean) and (
            curr == "CAD" or exch in _CANADIAN_EXCHANGES
        ):
            return ResolvedSymbol(
                cdr_symbol=clean,
                questrade_symbol=sym,
                symbol_id=int(item["symbolId"]),
                listing_exchange=item.get("listingExchange"),
                currency=item.get("currency"),
            )
    return None


async def _questrade_get(
    http_client: httpx.AsyncClient,
    session: QuestradeSession,
    path: str,
    params: dict[str, Any],
) -> tuple[httpx.Response, QuestradeSession]:
    """Execute a Questrade GET request, refreshing the access token once on HTTP 401."""
    url = f"{session.base_api_url}{path}"
    headers = {"Authorization": f"{session.token_type} {session.access_token}"}
    resp = await http_client.get(url, params=params, headers=headers)
    if resp.status_code == 401:
        session = await get_questrade_session(force_refresh=True)
        url = f"{session.base_api_url}{path}"
        headers = {"Authorization": f"{session.token_type} {session.access_token}"}
        resp = await http_client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp, session


async def resolve_cdr_symbols(
    symbols: list[str],
    issuer_map: dict[str, str] | None = None,
) -> dict[str, ResolvedSymbol]:
    """Resolve CDR ticker symbols (`AAPL`, `MB`, ...) to Questrade `symbolId`s."""
    normalized = [s.strip().upper() for s in symbols if s and s.strip()]
    unique_symbols = list(dict.fromkeys(normalized))
    if not unique_symbols:
        return {}

    async with _symbol_lock:
        missing = [sym for sym in unique_symbols if sym not in _resolved_symbols]
        if missing:
            session = await get_questrade_session()
            async with httpx.AsyncClient(timeout=15.0) as http_client:
                for i in range(0, len(missing), _SYMBOL_BATCH_SIZE):
                    batch = missing[i : i + _SYMBOL_BATCH_SIZE]
                    candidate_names: list[str] = []
                    for sym in batch:
                        issuer = (issuer_map or {}).get(sym)
                        for name in _candidate_names(sym, issuer=issuer):
                            if name not in candidate_names:
                                candidate_names.append(name)

                    resp, session = await _questrade_get(
                        http_client,
                        session,
                        "/v1/symbols",
                        {"names": ",".join(candidate_names)},
                    )
                    found = resp.json().get("symbols") or []
                    for sym in batch:
                        match = _pick_best_symbol_match(
                            sym, found, issuer=(issuer_map or {}).get(sym)
                        )
                        if match is not None:
                            _resolved_symbols[sym] = match

                # Fallback search for any symbols not returned by exact .TO/.NE batch lookup.
                still_missing = [sym for sym in missing if sym not in _resolved_symbols]
                for sym in still_missing:
                    resp, session = await _questrade_get(
                        http_client,
                        session,
                        "/v1/symbols/search",
                        {"prefix": sym},
                    )
                    search_hits = resp.json().get("symbols") or []
                    match = _pick_best_symbol_match(
                        sym, search_hits, issuer=(issuer_map or {}).get(sym)
                    )
                    if match is not None:
                        _resolved_symbols[sym] = match

        return {sym: _resolved_symbols[sym] for sym in unique_symbols if sym in _resolved_symbols}


def _normalize_quote(resolved: ResolvedSymbol, raw_quote: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    last_price = raw_quote.get("lastTradePrice")
    open_price = raw_quote.get("openPrice")
    change = None
    change_percent = None
    if isinstance(last_price, (int, float)) and isinstance(open_price, (int, float)) and open_price > 0:
        change = round(last_price - open_price, 4)
        change_percent = round(((last_price - open_price) / open_price) * 100, 2)

    return {
        "symbol": resolved.cdr_symbol,
        "questrade_symbol": resolved.questrade_symbol,
        "symbol_id": resolved.symbol_id,
        "exchange": resolved.listing_exchange,
        "currency": resolved.currency or "CAD",
        "last_price": last_price,
        "bid_price": raw_quote.get("bidPrice"),
        "ask_price": raw_quote.get("askPrice"),
        "open_price": open_price,
        "high_price": raw_quote.get("highPrice"),
        "low_price": raw_quote.get("lowPrice"),
        "volume": raw_quote.get("volume"),
        "change": change,
        "change_percent": change_percent,
        "delay_seconds": raw_quote.get("delay"),
        "is_halted": bool(raw_quote.get("isHalted", False)),
        "last_trade_time": raw_quote.get("lastTradeTime"),
        "fetched_at": fetched_at,
    }


async def get_live_quotes(
    symbols: list[str],
    issuer_map: dict[str, str] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch Level 1 live/delayed Questrade quotes for CDR symbols with TTL caching."""
    normalized = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
    if not normalized:
        return {
            "status": "ok",
            "quotes": {},
            "missing": [],
            "cached_ttl_seconds": settings.quote_cache_ttl_seconds,
        }

    now_mono = time.monotonic()
    quotes_out: dict[str, dict[str, Any]] = {}
    stale_symbols: list[str] = []

    async with _quote_lock:
        for sym in normalized:
            cached = _quote_cache.get(sym)
            if (
                not force_refresh
                and cached is not None
                and (now_mono - cached.fetched_monotonic) < settings.quote_cache_ttl_seconds
            ):
                quotes_out[sym] = cached.data
            else:
                stale_symbols.append(sym)

    if stale_symbols:
        resolved_map = await resolve_cdr_symbols(stale_symbols, issuer_map=issuer_map)
        id_to_resolved = {res.symbol_id: res for res in resolved_map.values()}
        ids_to_fetch = list(id_to_resolved.keys())

        if ids_to_fetch:
            session = await get_questrade_session()
            fetched_iso = datetime.now(timezone.utc).isoformat()
            fetched_mono = time.monotonic()
            fresh_quotes: dict[str, dict[str, Any]] = {}

            async with httpx.AsyncClient(timeout=15.0) as http_client:
                for i in range(0, len(ids_to_fetch), _QUOTE_BATCH_SIZE):
                    batch_ids = ids_to_fetch[i : i + _QUOTE_BATCH_SIZE]
                    resp, session = await _questrade_get(
                        http_client,
                        session,
                        "/v1/markets/quotes",
                        {"ids": ",".join(str(sid) for sid in batch_ids)},
                    )
                    for raw_q in resp.json().get("quotes") or []:
                        sid = raw_q.get("symbolId")
                        if sid is None:
                            continue
                        resolved = id_to_resolved.get(int(sid))
                        if resolved is None:
                            continue
                        norm = _normalize_quote(resolved, raw_q, fetched_iso)
                        fresh_quotes[resolved.cdr_symbol] = norm

            async with _quote_lock:
                for sym, qdata in fresh_quotes.items():
                    _quote_cache[sym] = CachedQuote(data=qdata, fetched_monotonic=fetched_mono)
                    quotes_out[sym] = qdata

    missing_symbols = [sym for sym in normalized if sym not in quotes_out]
    return {
        "status": "ok",
        "quotes": quotes_out,
        "missing": missing_symbols,
        "cached_ttl_seconds": settings.quote_cache_ttl_seconds,
    }
