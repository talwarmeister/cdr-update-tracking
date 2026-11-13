from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import settings

app = FastAPI(title="CDR Update Tracking API")

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
    return {"status": "healthy", "environment": settings.environment}


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

