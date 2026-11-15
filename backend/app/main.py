import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import settings
from app.services.cdr_sync import sync_cdrs

logger = logging.getLogger(__name__)


async def _startup_and_daily_sync_loop() -> None:
    """Run an immediate CDR scrape/compare on startup (or wake from sleep), then daily."""
    try:
        await sync_cdrs(trigger="startup")
    except Exception:
        logger.exception("Startup CDR sync failed unexpectedly.")

    interval_seconds = max(1, settings.scrape_interval_hours) * 3600
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await sync_cdrs(trigger="daily")
        except Exception:
            logger.exception("Scheduled daily CDR sync failed unexpectedly.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage background startup and daily CDR scraping lifecycle."""
    sync_task: asyncio.Task[None] | None = None
    if settings.scrape_on_startup:
        sync_task = asyncio.create_task(_startup_and_daily_sync_loop())
    try:
        yield
    finally:
        if sync_task is not None and not sync_task.done():
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="CDR Update Tracking API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint for Cloud Run and container probes."""
    return {
        "status": "healthy",
        "environment": settings.environment,
        "gcp_project_id": settings.gcp_project_id,
        "bq_dataset": settings.bq_dataset,
    }


app.include_router(api_router, prefix="/api")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve the React SPA for any non-API route in production."""
        requested = STATIC_DIR / full_path
        if full_path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(STATIC_DIR / "index.html")

