import os
from pathlib import Path

from google.cloud import bigquery

from app.config import ROOT_ENV_FILE, settings

PROJECT_ROOT = ROOT_ENV_FILE.parent
DEV_DATASET = "cdr_tracking_dev"
PROD_DATASET = "cdr_tracking_prod"


def get_bq_client() -> bigquery.Client:
    """Return a BigQuery client bound to the configured GCP project."""
    if settings.google_application_credentials and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        cred_path = Path(settings.google_application_credentials)
        if not cred_path.is_absolute():
            cred_path = (PROJECT_ROOT / cred_path).resolve()
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)
    return bigquery.Client(
        project=settings.gcp_project_id,
        location=settings.bq_location,
    )


def ensure_datasets(datasets: tuple[str, ...] = (DEV_DATASET, PROD_DATASET)) -> list[str]:
    """Create the target BigQuery datasets (`cdr_tracking_dev`, `cdr_tracking_prod`) if they do not exist."""
    client = get_bq_client()
    created_or_verified: list[str] = []
    for name in datasets:
        dataset_id = f"{settings.gcp_project_id}.{name}"
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = settings.bq_location
        client.create_dataset(dataset, exists_ok=True)
        created_or_verified.append(dataset_id)
    return created_or_verified


if __name__ == "__main__":
    verified = ensure_datasets()
    for ds in verified:
        print(f"Verified BigQuery dataset: {ds} (location={settings.bq_location})")
