from fastapi import APIRouter

router = APIRouter()


@router.get("/cdrs")
async def list_cdrs() -> dict:
    """Placeholder endpoint for newly created Canadian Depositary Receipts."""
    return {
        "status": "ok",
        "items": [],
        "message": "CDR tracking endpoint ready.",
    }


@router.get("/market-data/{symbol}")
async def get_market_data(symbol: str) -> dict:
    """Placeholder endpoint for live market data lookup by ticker symbol."""
    return {
        "symbol": symbol.upper(),
        "price": None,
        "message": "Market data integration pending.",
    }

