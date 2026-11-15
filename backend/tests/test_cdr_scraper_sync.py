import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.cdr_scraper import CdrRecord, parse_bmo_json, parse_cibc_html
from app.services.cdr_sync import SyncReport, compare_cdrs


def test_parse_cibc_html_extracts_and_normalizes_records() -> None:
    payload = {
        "cdr_list": {
            "response": [
                {
                    "ticker": " amzn ",
                    "name": "Amazon.com CDR (CAD Hedged)",
                    "country": "U.S.A.",
                    "underlyingTicker": "AMZN.OQ",
                    "underlyingTradingSymbol": "AMZN US",
                    "underlyingExchangeEn": "Nasdaq",
                    "underlyingCurrency": "USD",
                    "cdrExchangeEn": "TSX",
                    "sector": "Consumer Discretionary",
                    "ratio": 0.07971373,
                    "fxForwardRate": 1.4249758,
                    "listingDate": "2021-07-27T00:00:00Z",
                    "volumeAverageTenDay": 1795927,
                    "active": True,
                    "visible": True,
                }
            ]
        }
    }
    html = f'<html><body><script id="ng-state" type="application/json">{json.dumps(payload)}</script></body></html>'

    records = parse_cibc_html(html)
    assert len(records) == 1
    rec = records[0]
    assert rec.symbol == "AMZN"
    assert rec.issuer == "CIBC"
    assert rec.country == "US"
    assert rec.listing_date == "2021-07-27"
    assert rec.sector == "Consumer Discretionary"
    assert rec.is_active is True


def test_parse_bmo_json_extracts_and_normalizes_records() -> None:
    items = [
        {"SYMBOL": "mb", "NAME": "MERCEDES-BENZ GROUP AG CDR (CAD Hedged)", "COUNTRY": "Germany", "Issuer": "BMO"},
        {"SYMBOL": "amzn", "NAME": "AMAZON.COM CDR (CAD HEDGED)", "COUNTRY": "US", "Issuer": "CIBC"},
    ]
    records = parse_bmo_json(items)
    assert [r.symbol for r in records] == ["MB", "AMZN"]
    assert [r.issuer for r in records] == ["BMO", "CIBC"]
    assert records[0].country == "Germany"


def test_compare_cdrs_detects_added_updated_removed_and_respects_partial_failures() -> None:
    existing = {
        "AMZN": {
            "symbol": "AMZN",
            "name": "Amazon.com CDR (CAD Hedged)",
            "issuer": "CIBC",
            "country": "US",
            "underlying_ticker": "AMZN.OQ",
            "underlying_symbol": "AMZN US",
            "underlying_exchange": "Nasdaq",
            "underlying_currency": "USD",
            "cdr_exchange": "TSX",
            "sector": "Consumer Discretionary",
            "ratio": 0.08,
            "listing_date": "2021-07-27",
            "is_active": True,
        },
        "OLD1": {
            "symbol": "OLD1",
            "name": "Old CIBC CDR",
            "issuer": "CIBC",
            "country": "US",
            "is_active": True,
        },
        "MB": {
            "symbol": "MB",
            "name": "MERCEDES-BENZ GROUP AG CDR (CAD Hedged)",
            "issuer": "BMO",
            "country": "Germany",
            "is_active": True,
        },
    }
    scraped = [
        CdrRecord(
            symbol="AMZN",
            name="Amazon.com CDR (CAD Hedged)",
            issuer="CIBC",
            country="US",
            source="cibc_directory",
            source_url="https://cdr.cibc.com/en/cdr-directory",
            underlying_ticker="AMZN.OQ",
            underlying_symbol="AMZN US",
            underlying_exchange="Nasdaq",
            underlying_currency="USD",
            cdr_exchange="TSX",
            sector="Consumer Discretionary",
            ratio=0.09,  # changed
            listing_date="2021-07-27",
            is_active=True,
        ),
        CdrRecord(
            symbol="NEW1",
            name="New CIBC CDR",
            issuer="CIBC",
            country="US",
            source="cibc_directory",
            source_url="https://cdr.cibc.com/en/cdr-directory",
        ),
    ]

    # Only CIBC succeeded; BMO failed -> MB must NOT be marked removed.
    diff = compare_cdrs(existing, scraped, succeeded_issuers={"CIBC"})
    assert diff.added == ["NEW1"]
    assert diff.updated == ["AMZN"]
    assert diff.removed == ["OLD1"]
    assert diff.unchanged == []


def test_sync_endpoint_and_startup_lifespan() -> None:
    fake_report = SyncReport(
        status="ok",
        trigger="manual",
        started_at="2026-11-14T18:00:00+00:00",
        completed_at="2026-11-14T18:00:01+00:00",
        table="gen-lang-client-0230474681.cdr_tracking_dev.cdr_list",
        total_scraped=228,
        total_active=228,
        added_count=2,
        updated_count=1,
        removed_count=0,
        unchanged_count=225,
        added_symbols=["NEW1", "NEW2"],
        updated_symbols=["AMZN"],
        removed_symbols=[],
        source_counts={"cibc_directory": 144, "bmo_directory": 84},
        errors={},
    )

    with patch("app.main.sync_cdrs", new=AsyncMock(return_value=fake_report)) as mock_startup_sync, patch(
        "app.api.routes.sync_cdrs", new=AsyncMock(return_value=fake_report)
    ) as mock_route_sync:
        with TestClient(app) as client:
            resp = client.post("/api/cdrs/sync?trigger=scheduler")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total_scraped"] == 228
            assert body["added_symbols"] == ["NEW1", "NEW2"]

        mock_startup_sync.assert_awaited_once_with(trigger="startup")
        mock_route_sync.assert_awaited_once_with(trigger="scheduler")
