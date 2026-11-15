from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from google.cloud import bigquery

from app.config import settings
from app.db import ensure_api_tokens_table, get_bq_client

logger = logging.getLogger(__name__)

PROVIDER_NAME = "questrade"
_EXPIRY_BUFFER_SECONDS = 60


@dataclass(slots=True)
class QuestradeSession:
    """Active Questrade API session credentials."""

    access_token: str
    refresh_token: str
    api_server: str
    token_type: str
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= (
            self.expires_at - timedelta(seconds=_EXPIRY_BUFFER_SECONDS)
        )

    @property
    def base_api_url(self) -> str:
        return self.api_server.rstrip("/")


_auth_lock = asyncio.Lock()
_cached_session: QuestradeSession | None = None


def clear_questrade_session_cache() -> None:
    """Clear the in-memory Questrade session cache (useful for testing)."""
    global _cached_session
    _cached_session = None


def _hash_seed_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _read_stored_token_sync(client: bigquery.Client) -> dict[str, Any] | None:
    table_fqn = ensure_api_tokens_table(client)
    query = f"""
        SELECT provider, seed_token_hash, refresh_token, access_token, api_server, token_type, expires_at
        FROM `{table_fqn}`
        WHERE provider = @provider
        ORDER BY updated_at DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("provider", "STRING", PROVIDER_NAME)]
    )
    rows = list(client.query(query, job_config=job_config).result())
    if not rows:
        return None
    return dict(rows[0])


def _save_stored_token_sync(
    client: bigquery.Client,
    seed_token_hash: str,
    session: QuestradeSession,
) -> None:
    table_fqn = ensure_api_tokens_table(client)
    merge_sql = f"""
        MERGE `{table_fqn}` AS T
        USING (
            SELECT
                @provider AS provider,
                @seed_token_hash AS seed_token_hash,
                @refresh_token AS refresh_token,
                @access_token AS access_token,
                @api_server AS api_server,
                @token_type AS token_type,
                @expires_at AS expires_at,
                CURRENT_TIMESTAMP() AS updated_at
        ) AS S
        ON T.provider = S.provider
        WHEN MATCHED THEN
            UPDATE SET
                seed_token_hash = S.seed_token_hash,
                refresh_token = S.refresh_token,
                access_token = S.access_token,
                api_server = S.api_server,
                token_type = S.token_type,
                expires_at = S.expires_at,
                updated_at = S.updated_at
        WHEN NOT MATCHED THEN
            INSERT (
                provider, seed_token_hash, refresh_token, access_token,
                api_server, token_type, expires_at, updated_at
            )
            VALUES (
                S.provider, S.seed_token_hash, S.refresh_token, S.access_token,
                S.api_server, S.token_type, S.expires_at, S.updated_at
            )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("provider", "STRING", PROVIDER_NAME),
            bigquery.ScalarQueryParameter("seed_token_hash", "STRING", seed_token_hash),
            bigquery.ScalarQueryParameter("refresh_token", "STRING", session.refresh_token),
            bigquery.ScalarQueryParameter("access_token", "STRING", session.access_token),
            bigquery.ScalarQueryParameter("api_server", "STRING", session.api_server),
            bigquery.ScalarQueryParameter("token_type", "STRING", session.token_type),
            bigquery.ScalarQueryParameter("expires_at", "TIMESTAMP", session.expires_at),
        ]
    )
    client.query(merge_sql, job_config=job_config).result()


async def _exchange_refresh_token(refresh_token: str) -> QuestradeSession:
    """Redeem a single-use Questrade refresh token for a fresh access/refresh token pair."""
    async with httpx.AsyncClient(timeout=15.0) as http_client:
        resp = await http_client.post(
            settings.questrade_auth_url,
            params={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.strip(),
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    access_token = payload.get("access_token")
    new_refresh_token = payload.get("refresh_token")
    api_server = payload.get("api_server")
    if not access_token or not new_refresh_token or not api_server:
        raise ValueError("Questrade OAuth response missing access_token, refresh_token, or api_server.")

    expires_in = int(payload.get("expires_in") or 1800)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    return QuestradeSession(
        access_token=access_token,
        refresh_token=new_refresh_token,
        api_server=api_server,
        token_type=str(payload.get("token_type") or "Bearer"),
        expires_at=expires_at,
    )


async def get_questrade_session(
    force_refresh: bool = False,
    client: bigquery.Client | None = None,
) -> QuestradeSession:
    """Return a valid Questrade session, persisting any rotated refresh token to BigQuery."""
    global _cached_session

    if not force_refresh and _cached_session is not None and not _cached_session.is_expired:
        return _cached_session

    async with _auth_lock:
        if not force_refresh and _cached_session is not None and not _cached_session.is_expired:
            return _cached_session

        seed_token = settings.market_data_api_key.strip()
        seed_hash = _hash_seed_token(seed_token) if seed_token else ""
        bq_client = client or get_bq_client()

        stored = await asyncio.to_thread(_read_stored_token_sync, bq_client)

        if stored and (not seed_hash or stored.get("seed_token_hash") == seed_hash):
            expires_at = stored["expires_at"]
            if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            stored_session = QuestradeSession(
                access_token=stored["access_token"],
                refresh_token=stored["refresh_token"],
                api_server=stored["api_server"],
                token_type=stored.get("token_type") or "Bearer",
                expires_at=expires_at,
            )
            if not force_refresh and not stored_session.is_expired:
                _cached_session = stored_session
                return stored_session
            token_to_redeem = stored_session.refresh_token
            effective_seed_hash = stored["seed_token_hash"]
        else:
            if not seed_token:
                raise RuntimeError(
                    "Questrade refresh token is not configured. Set MARKET_DATA_API_KEY in .env."
                )
            token_to_redeem = seed_token
            effective_seed_hash = seed_hash

        fresh_session = await _exchange_refresh_token(token_to_redeem)
        await asyncio.to_thread(_save_stored_token_sync, bq_client, effective_seed_hash, fresh_session)
        _cached_session = fresh_session
        logger.info("Refreshed Questrade OAuth session and persisted rotated refresh token.")
        return fresh_session
