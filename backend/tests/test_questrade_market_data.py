import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services.market_data import (
    clear_market_data_cache,
    get_live_quotes,
    resolve_cdr_symbols,
)
from app.services.questrade_auth import (
    QuestradeSession,
    _hash_seed_token,
    clear_questrade_session_cache,
    get_questrade_session,
)


def _mock_httpx_response(
    status_code: int,
    json_data: dict[str, Any],
    url: str = "https://api01.iq.questrade.com/v1/test",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status_code=status_code, json=json_data, request=request)


def test_get_questrade_session_seeds_persists_and_reuses() -> None:
    clear_questrade_session_cache()
    seed_token = "seed-refresh-token-123"
    seed_hash = _hash_seed_token(seed_token)
    saved_state: dict[str, Any] = {}
    post_calls = 0

    def fake_read(_client: Any) -> dict[str, Any] | None:
        return saved_state.get("row")

    def fake_save(_client: Any, effective_seed_hash: str, session: QuestradeSession) -> None:
        saved_state["row"] = {
            "provider": "questrade",
            "seed_token_hash": effective_seed_hash,
            "refresh_token": session.refresh_token,
            "access_token": session.access_token,
            "api_server": session.api_server,
            "token_type": session.token_type,
            "expires_at": session.expires_at,
        }

    async def fake_post(self: Any, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        nonlocal post_calls
        post_calls += 1
        assert params == {"grant_type": "refresh_token", "refresh_token": seed_token}
        return _mock_httpx_response(
            200,
            {
                "access_token": "access-abc",
                "refresh_token": "rotated-refresh-456",
                "api_server": "https://api01.iq.questrade.com/",
                "token_type": "Bearer",
                "expires_in": 1800,
            },
            url=url,
        )

    fake_bq = MagicMock()
    with patch("app.services.questrade_auth.settings.market_data_api_key", seed_token), patch(
        "app.services.questrade_auth.get_bq_client", return_value=fake_bq
    ), patch(
        "app.services.questrade_auth._read_stored_token_sync", side_effect=fake_read
    ), patch(
        "app.services.questrade_auth._save_stored_token_sync", side_effect=fake_save
    ), patch.object(httpx.AsyncClient, "post", new=fake_post):
        first = asyncio.run(get_questrade_session())
        assert first.access_token == "access-abc"
        assert first.refresh_token == "rotated-refresh-456"
        assert post_calls == 1
        assert saved_state["row"]["seed_token_hash"] == seed_hash
        assert saved_state["row"]["refresh_token"] == "rotated-refresh-456"

        # Second call uses in-memory cache without re-exchanging.
        second = asyncio.run(get_questrade_session())
        assert second.access_token == "access-abc"
        assert post_calls == 1

        # Clearing memory cache still reuses persisted non-expired BigQuery row.
        clear_questrade_session_cache()
        third = asyncio.run(get_questrade_session())
        assert third.access_token == "access-abc"
        assert third.refresh_token == "rotated-refresh-456"
        assert post_calls == 1


def test_resolve_cdr_symbols_issuer_priority_and_search_fallback() -> None:
    clear_market_data_cache()
    session = QuestradeSession(
        access_token="access-ok",
        refresh_token="refresh-ok",
        api_server="https://api01.iq.questrade.com/",
        token_type="Bearer",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )

    async def fake_get(
        self: Any,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if url.endswith("/v1/symbols"):
            if (params or {}).get("ids") == "3003":
                return _mock_httpx_response(
                    200,
                    {
                        "symbols": [
                            {
                                "symbol": "MB.NE",
                                "symbolId": 3003,
                                "listingExchange": "NEO",
                                "currency": "CAD",
                                "prevDayClosePrice": 18.25,
                            }
                        ]
                    },
                    url=url,
                )
            return _mock_httpx_response(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "AAPL.TO",
                            "symbolId": 1001,
                            "listingExchange": "TSX",
                            "currency": "CAD",
                            "prevDayClosePrice": 25.00,
                        },
                        {
                            "symbol": "AAPL.NE",
                            "symbolId": 1002,
                            "listingExchange": "NEO",
                            "currency": "CAD",
                        },
                        {
                            "symbol": "ZAM.TO",
                            "symbolId": 2001,
                            "listingExchange": "TSX",
                            "currency": "CAD",
                        },
                        {
                            "symbol": "ZAM.NE",
                            "symbolId": 2002,
                            "listingExchange": "NEO",
                            "currency": "CAD",
                        },
                    ]
                },
                url=url,
            )
        if url.endswith("/v1/symbols/search"):
            prefix = (params or {}).get("prefix")
            if prefix == "MB":
                return _mock_httpx_response(
                    200,
                    {
                        "symbols": [
                            {
                                "symbol": "MB.NE",
                                "symbolId": 3003,
                                "listingExchange": "NEO",
                                "currency": "CAD",
                            }
                        ]
                    },
                    url=url,
                )
        raise AssertionError(f"Unexpected Questrade GET: {url} {params}")

    with patch(
        "app.services.market_data.get_questrade_session",
        new=AsyncMock(return_value=session),
    ), patch.object(httpx.AsyncClient, "get", new=fake_get):
        resolved = asyncio.run(
            resolve_cdr_symbols(
                ["AAPL", "ZAM", "MB"],
                issuer_map={"AAPL": "CIBC", "ZAM": "BMO", "MB": "BMO"},
            )
        )

    assert resolved["AAPL"].questrade_symbol == "AAPL.TO"
    assert resolved["AAPL"].symbol_id == 1001
    assert resolved["AAPL"].prev_day_close == 25.00
    assert resolved["ZAM"].questrade_symbol == "ZAM.NE"
    assert resolved["ZAM"].symbol_id == 2002
    assert resolved["MB"].questrade_symbol == "MB.NE"
    assert resolved["MB"].symbol_id == 3003
    assert resolved["MB"].prev_day_close == 18.25


def test_get_live_quotes_caches_and_retries_on_401() -> None:
    clear_market_data_cache()
    stale_session = QuestradeSession(
        access_token="expired-token",
        refresh_token="refresh-1",
        api_server="https://api01.iq.questrade.com/",
        token_type="Bearer",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    fresh_session = QuestradeSession(
        access_token="fresh-token",
        refresh_token="refresh-2",
        api_server="https://api01.iq.questrade.com/",
        token_type="Bearer",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    session_mock = AsyncMock(side_effect=[stale_session, stale_session, fresh_session])
    quote_attempts = 0

    async def fake_get(
        self: Any,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        nonlocal quote_attempts
        if url.endswith("/v1/symbols"):
            return _mock_httpx_response(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "AAPL.TO",
                            "symbolId": 1001,
                            "listingExchange": "TSX",
                            "currency": "CAD",
                            "prevDayClosePrice": 25.00,
                        }
                    ]
                },
                url=url,
            )
        if url.endswith("/v1/markets/quotes"):
            quote_attempts += 1
            if headers == {"Authorization": "Bearer expired-token"}:
                return _mock_httpx_response(401, {"code": 1017, "message": "Access token is invalid"}, url=url)
            assert headers == {"Authorization": "Bearer fresh-token"}
            return _mock_httpx_response(
                200,
                {
                    "quotes": [
                        {
                            "symbol": "AAPL.TO",
                            "symbolId": 1001,
                            "lastTradePrice": 23.90,
                            "openPrice": 24.45,
                            "bidPrice": 24.48,
                            "askPrice": 24.52,
                            "highPrice": 24.75,
                            "lowPrice": 23.90,
                            "volume": 125000,
                            "delay": 0,
                            "isHalted": False,
                            "lastTradeTime": "2026-11-14T19:30:00-05:00",
                        }
                    ]
                },
                url=url,
            )
        raise AssertionError(f"Unexpected Questrade GET: {url}")

    with patch("app.services.market_data.get_questrade_session", new=session_mock), patch.object(
        httpx.AsyncClient, "get", new=fake_get
    ):
        result1 = asyncio.run(get_live_quotes(["AAPL"], issuer_map={"AAPL": "CIBC"}))
        assert quote_attempts == 2  # 401 followed by single forced-refresh retry
        quote = result1["quotes"]["AAPL"]
        assert quote["questrade_symbol"] == "AAPL.TO"
        assert quote["currency"] == "CAD"
        assert quote["last_price"] == 23.90
        assert quote["prev_close_price"] == 25.00
        assert quote["change"] == -1.1
        assert quote["change_percent"] == -4.4
        assert result1["missing"] == []

        # Second call within 60s TTL serves directly from memory cache.
        result2 = asyncio.run(get_live_quotes(["AAPL"], issuer_map={"AAPL": "CIBC"}))
        assert quote_attempts == 2
        assert result2["quotes"]["AAPL"]["last_price"] == 23.90


def test_market_data_routes_batch_and_single() -> None:
    client = TestClient(app)
    cdr_store = {
        "items": [
            {"symbol": "AAPL", "issuer": "CIBC"},
            {"symbol": "MB", "issuer": "BMO"},
        ]
    }
    aapl_quote = {
        "symbol": "AAPL",
        "questrade_symbol": "AAPL.TO",
        "symbol_id": 1001,
        "exchange": "TSX",
        "currency": "CAD",
        "last_price": 24.5,
        "change": 0.5,
        "change_percent": 2.08,
    }

    with patch("app.api.routes.list_cdrs_from_store", new=AsyncMock(return_value=cdr_store)), patch(
        "app.api.routes.get_live_quotes",
        new=AsyncMock(
            return_value={
                "status": "ok",
                "quotes": {"AAPL": aapl_quote},
                "missing": ["MB"],
                "cached_ttl_seconds": 60,
            }
        ),
    ) as mock_quotes:
        batch_resp = client.get("/api/market-data")
        assert batch_resp.status_code == 200
        batch_data = batch_resp.json()
        assert batch_data["quotes"]["AAPL"]["last_price"] == 24.5
        assert batch_data["missing"] == ["MB"]
        mock_quotes.assert_awaited_once_with(
            ["AAPL", "MB"],
            issuer_map={"AAPL": "CIBC", "MB": "BMO"},
            force_refresh=False,
        )

    with patch(
        "app.api.routes.get_live_quotes",
        new=AsyncMock(
            return_value={
                "status": "ok",
                "quotes": {"AAPL": aapl_quote},
                "missing": [],
                "cached_ttl_seconds": 60,
            }
        ),
    ):
        single_resp = client.get("/api/market-data/aapl?issuer=CIBC")
        assert single_resp.status_code == 200
        assert single_resp.json()["quote"]["questrade_symbol"] == "AAPL.TO"

    with patch(
        "app.api.routes.get_live_quotes",
        new=AsyncMock(
            return_value={
                "status": "ok",
                "quotes": {},
                "missing": ["UNKNOWN"],
                "cached_ttl_seconds": 60,
            }
        ),
    ):
        missing_resp = client.get("/api/market-data/UNKNOWN")
        assert missing_resp.status_code == 404


def test_get_live_quotes_day_rollover_refreshes_prev_close_and_gap_down() -> None:
    clear_market_data_cache()
    session = QuestradeSession(
        access_token="access-ok",
        refresh_token="refresh-ok",
        api_server="https://api01.iq.questrade.com/",
        token_type="Bearer",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )

    market_day = date(2026, 11, 15)
    symbol_id_calls = 0

    async def fake_get(
        self: Any,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        nonlocal symbol_id_calls
        if url.endswith("/v1/symbols") and (params or {}).get("names"):
            return _mock_httpx_response(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "AMD.TO",
                            "symbolId": 4001,
                            "listingExchange": "TSX",
                            "currency": "CAD",
                            "prevDayClosePrice": 20.00,
                        }
                    ]
                },
                url=url,
            )
        if url.endswith("/v1/symbols") and (params or {}).get("ids") == "4001":
            symbol_id_calls += 1
            return _mock_httpx_response(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "AMD.TO",
                            "symbolId": 4001,
                            "listingExchange": "TSX",
                            "currency": "CAD",
                            "prevDayClosePrice": 20.50,
                        }
                    ]
                },
                url=url,
            )
        if url.endswith("/v1/markets/quotes"):
            if market_day == date(2026, 11, 15):
                return _mock_httpx_response(
                    200,
                    {
                        "quotes": [
                            {
                                "symbol": "AMD.TO",
                                "symbolId": 4001,
                                "lastTradePrice": 20.50,
                                "openPrice": 20.10,
                            }
                        ]
                    },
                    url=url,
                )
            return _mock_httpx_response(
                200,
                {
                    "quotes": [
                        {
                            "symbol": "AMD.TO",
                            "symbolId": 4001,
                            "lastTradePrice": 19.60,
                            "openPrice": 19.65,
                        }
                    ]
                },
                url=url,
            )
        raise AssertionError(f"Unexpected Questrade GET: {url} {params}")

    with patch("app.services.market_data._current_market_date", side_effect=lambda: market_day), patch(
        "app.services.market_data.get_questrade_session",
        new=AsyncMock(return_value=session),
    ), patch.object(httpx.AsyncClient, "get", new=fake_get):
        day1 = asyncio.run(get_live_quotes(["AMD"], issuer_map={"AMD": "CIBC"}))
        assert day1["quotes"]["AMD"]["prev_close_price"] == 20.00
        assert day1["quotes"]["AMD"]["change"] == 0.5
        assert day1["quotes"]["AMD"]["change_percent"] == 2.5
        assert symbol_id_calls == 0

        # New trading day: force_refresh fetches new prevDayClosePrice via /v1/symbols?ids=4001
        market_day = date(2026, 11, 16)
        day2 = asyncio.run(get_live_quotes(["AMD"], issuer_map={"AMD": "CIBC"}, force_refresh=True))
        assert symbol_id_calls == 1
        assert day2["quotes"]["AMD"]["prev_close_price"] == 20.50
        assert day2["quotes"]["AMD"]["change"] == -0.9
        assert day2["quotes"]["AMD"]["change_percent"] == -4.39


def test_get_live_quotes_prev_close_override_and_missing_values() -> None:
    clear_market_data_cache()
    session = QuestradeSession(
        access_token="access-ok",
        refresh_token="refresh-ok",
        api_server="https://api01.iq.questrade.com/",
        token_type="Bearer",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )

    async def fake_get(
        self: Any,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if url.endswith("/v1/symbols"):
            return _mock_httpx_response(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "INTC.TO",
                            "symbolId": 5001,
                            "listingExchange": "TSX",
                            "currency": "CAD",
                            "prevDayClosePrice": 10.00,
                        },
                        {
                            "symbol": "NOPREV.TO",
                            "symbolId": 5002,
                            "listingExchange": "TSX",
                            "currency": "CAD",
                            "prevDayClosePrice": 0,
                        },
                    ]
                },
                url=url,
            )
        if url.endswith("/v1/markets/quotes"):
            return _mock_httpx_response(
                200,
                {
                    "quotes": [
                        {
                            "symbol": "INTC.TO",
                            "symbolId": 5001,
                            "lastTradePrice": 9.50,
                            "openPrice": 9.55,
                            "prevDayClosePrice": 10.20,
                        },
                        {
                            "symbol": "NOPREV.TO",
                            "symbolId": 5002,
                            "lastTradePrice": 15.00,
                            "openPrice": 14.00,
                        },
                    ]
                },
                url=url,
            )
        raise AssertionError(f"Unexpected Questrade GET: {url} {params}")

    with patch(
        "app.services.market_data.get_questrade_session",
        new=AsyncMock(return_value=session),
    ), patch.object(httpx.AsyncClient, "get", new=fake_get):
        res = asyncio.run(get_live_quotes(["INTC", "NOPREV"]))

    intc = res["quotes"]["INTC"]
    assert intc["prev_close_price"] == 10.20
    assert intc["change"] == -0.7
    assert intc["change_percent"] == -6.86

    noprev = res["quotes"]["NOPREV"]
    assert noprev["prev_close_price"] is None
    assert noprev["change"] is None
    assert noprev["change_percent"] is None
