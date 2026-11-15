import os
from pathlib import Path

from google.cloud import bigquery

from app.config import ROOT_ENV_FILE, settings

PROJECT_ROOT = ROOT_ENV_FILE.parent
DEV_DATASET = "cdr_tracking_dev"
PROD_DATASET = "cdr_tracking_prod"

CDR_LIST_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("symbol", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("issuer", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("country", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("source_url", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("underlying_ticker", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("underlying_symbol", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("underlying_exchange", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("underlying_currency", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("cdr_exchange", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("sector", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("ratio", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("fx_forward_rate", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("listing_date", "DATE", mode="NULLABLE"),
    bigquery.SchemaField("volume_avg_10d", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("is_active", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("is_new", "BOOL", mode="REQUIRED"),
    bigquery.SchemaField("first_seen_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("last_seen_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

API_TOKENS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("seed_token_hash", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("refresh_token", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("access_token", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("api_server", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("token_type", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("expires_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]


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


def ensure_datasets(
    datasets: tuple[str, ...] = (DEV_DATASET, PROD_DATASET),
    client: bigquery.Client | None = None,
) -> list[str]:
    """Create the target BigQuery datasets (`cdr_tracking_dev`, `cdr_tracking_prod`) if they do not exist."""
    bq_client = client or get_bq_client()
    created_or_verified: list[str] = []
    for name in datasets:
        dataset_id = f"{settings.gcp_project_id}.{name}"
        dataset = bigquery.Dataset(dataset_id)
        dataset.location = settings.bq_location
        bq_client.create_dataset(dataset, exists_ok=True)
        created_or_verified.append(dataset_id)
    return created_or_verified


def ensure_cdr_list_table(client: bigquery.Client | None = None) -> str:
    """Ensure the `cdr_list` table exists in the configured BigQuery dataset."""
    bq_client = client or get_bq_client()
    table_id = settings.cdr_list_table_fqn
    table = bigquery.Table(table_id, schema=CDR_LIST_SCHEMA)
    bq_client.create_table(table, exists_ok=True)
    return table_id


def ensure_api_tokens_table(client: bigquery.Client | None = None) -> str:
    """Ensure the `api_tokens` table exists for persisting rotating OAuth tokens."""
    bq_client = client or get_bq_client()
    table_id = settings.api_tokens_table_fqn
    table = bigquery.Table(table_id, schema=API_TOKENS_SCHEMA)
    bq_client.create_table(table, exists_ok=True)
    return table_id


if __name__ == "__main__":
    bq_client = get_bq_client()
    verified = ensure_datasets(client=bq_client)
    for ds in verified:
        print(f"Verified BigQuery dataset: {ds} (location={settings.bq_location})")
    table_fqn = ensure_cdr_list_table(client=bq_client)
    print(f"Verified BigQuery table: {table_fqn}")
    tokens_fqn = ensure_api_tokens_table(client=bq_client)
    print(f"Verified BigQuery table: {tokens_fqn}")
