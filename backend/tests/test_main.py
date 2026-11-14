from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["gcp_project_id"] == "gen-lang-client-0230474681"
    assert data["bq_dataset"] == "cdr_tracking_dev"


def test_list_cdrs() -> None:
    response = client.get("/api/cdrs")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

