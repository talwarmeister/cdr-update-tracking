from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    environment: str = "development"
    port: int = 8080
    cors_origins: str = "http://localhost:5173"
    gcp_project_id: str = "gen-lang-client-0230474681"
    bq_dataset: str = "cdr_tracking_dev"
    bq_location: str = "US"
    cdr_table_name: str = "cdr_list"
    api_tokens_table_name: str = "api_tokens"
    google_application_credentials: str = ""
    scrape_on_startup: bool = True
    scrape_interval_hours: int = 24
    cdr_cache_ttl_seconds: int = 300
    quote_cache_ttl_seconds: int = 60
    questrade_auth_url: str = "https://login.questrade.com/oauth2/token"
    market_data_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=(str(ROOT_ENV_FILE), ".env"),
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def bq_dataset_fqn(self) -> str:
        """Fully-qualified BigQuery dataset ID (`project.dataset`)."""
        return f"{self.gcp_project_id}.{self.bq_dataset}"

    @property
    def cdr_list_table_fqn(self) -> str:
        """Fully-qualified BigQuery table ID for `cdr_list`."""
        return f"{self.bq_dataset_fqn}.{self.cdr_table_name}"

    @property
    def api_tokens_table_fqn(self) -> str:
        """Fully-qualified BigQuery table ID for `api_tokens`."""
        return f"{self.bq_dataset_fqn}.{self.api_tokens_table_name}"


settings = Settings()

