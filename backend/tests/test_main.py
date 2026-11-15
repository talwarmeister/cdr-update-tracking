from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.cdr_sync import invalidate_cdr_cache

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["gcp_project_id"] == "gen-lang-client-0230474681"
    assert data["bq_dataset"] == "cdr_tracking_dev"


def test_list_cdrs() -> None:
    invalidate_cdr_cache()
    sample_rows = [
        {
            "symbol": "AMZN",
            "name": "Amazon.com CDR (CAD Hedged)",
            "issuer": "CIBC",
            "country": "US",
            "source": "cibc_directory",
            "is_active": True,
            "is_new": False,
        }
    ]
    with patch("app.services.cdr_sync.get_bq_client"), patch(
        "app.services.cdr_sync._read_cdr_rows_sync", return_value=sample_rows
    ):
        response = client.get("/api/cdrs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["items"][0]["symbol"] == "AMZN"

