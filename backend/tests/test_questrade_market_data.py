import asyncio
from datetime import datetime, timedelta, timezone
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
            return _mock_httpx_response(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "AAPL.TO",
                            "symbolId": 1001,
                            "listingExchange": "TSX",
                            "currency": "CAD",
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
    assert resolved["ZAM"].questrade_symbol == "ZAM.NE"
    assert resolved["ZAM"].symbol_id == 2002
    assert resolved["MB"].questrade_symbol == "MB.NE"
    assert resolved["MB"].symbol_id == 3003


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
                            "lastTradePrice": 24.50,
                            "openPrice": 24.00,
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
        assert quote["last_price"] == 24.50
        assert quote["change"] == 0.5
        assert quote["change_percent"] == 2.08
        assert result1["missing"] == []

        # Second call within 60s TTL serves directly from memory cache.
        result2 = asyncio.run(get_live_quotes(["AAPL"], issuer_map={"AAPL": "CIBC"}))
        assert quote_attempts == 2
        assert result2["quotes"]["AAPL"]["last_price"] == 24.50


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
