from fastapi import APIRouter, HTTPException, Query

from app.services.cdr_sync import get_last_sync_status, list_cdrs_from_store, sync_cdrs
from app.services.market_data import get_live_quotes

router = APIRouter()


@router.get("/cdrs")
async def list_cdrs(
    issuer: str | None = Query(default=None, description="Filter by issuer (e.g. CIBC, BMO)"),
    active_only: bool = Query(default=True, description="Only return active CDRs"),
    new_only: bool = Query(default=False, description="Only return newly discovered CDRs"),
    search: str | None = Query(default=None, description="Search by symbol, name, or sector"),
    refresh: bool = Query(default=False, description="Bypass in-memory cache and re-read BigQuery"),
) -> dict:
    """List tracked Canadian Depositary Receipts from BigQuery `cdr_list`."""
    return await list_cdrs_from_store(
        issuer=issuer,
        active_only=active_only,
        new_only=new_only,
        search=search,
        force_refresh=refresh,
    )


@router.post("/cdrs/sync")
async def trigger_cdr_sync(
    trigger: str = Query(default="manual", description="Sync trigger label (manual, scheduler, startup)"),
) -> dict:
    """Scrape CIBC & BMO CDR directories, compare against BigQuery `cdr_list`, and update."""
    report = await sync_cdrs(trigger=trigger)
    return report.to_dict()


@router.get("/cdrs/sync-status")
async def cdr_sync_status() -> dict:
    """Return metadata for the latest CDR scrape and comparison run."""
    return get_last_sync_status()


@router.get("/market-data")
async def get_batch_market_data(
    symbols: str | None = Query(
        default=None,
        description="Comma-separated CDR ticker symbols (defaults to all active CDRs)",
    ),
    refresh: bool = Query(default=False, description="Bypass 60s quote cache"),
) -> dict:
    """Fetch live CAD quotes from Questrade for requested or all active CDR symbols."""
    cdr_store = await list_cdrs_from_store(active_only=True)
    issuer_map = {
        str(item["symbol"]).upper(): str(item.get("issuer") or "")
        for item in cdr_store.get("items", [])
    }

    if symbols:
        target_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        target_symbols = list(issuer_map.keys())

    try:
        return await get_live_quotes(
            target_symbols,
            issuer_map=issuer_map,
            force_refresh=refresh,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Questrade market data error: {exc}") from exc


@router.get("/market-data/{symbol}")
async def get_market_data(
    symbol: str,
    issuer: str | None = Query(default=None, description="Optional issuer hint (CIBC or BMO)"),
    refresh: bool = Query(default=False, description="Bypass 60s quote cache"),
) -> dict:
    """Fetch live CAD quote from Questrade for a single CDR ticker symbol."""
    clean_sym = symbol.strip().upper()
    issuer_map = {clean_sym: issuer} if issuer else None
    try:
        result = await get_live_quotes([clean_sym], issuer_map=issuer_map, force_refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Questrade market data error: {exc}") from exc

    quote = result["quotes"].get(clean_sym)
    if quote is None:
        raise HTTPException(status_code=404, detail=f"No Questrade quote found for CDR symbol '{clean_sym}'.")
    return {"status": "ok", "quote": quote}

