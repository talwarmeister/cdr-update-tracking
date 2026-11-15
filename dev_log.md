# Development Log — CDR Update Tracking

## 2026-11-13 — Initial Project Scaffolding

### Architecture Overview
- **Backend:** Python with FastAPI and Uvicorn, exposing REST endpoints under `/api` and a `/health` check for Google Cloud Run.
- **Frontend:** React + TypeScript bundled with Vite. During local development, Vite proxies `/api` requests to the FastAPI backend (`http://localhost:8000`). In production, FastAPI serves the compiled React static assets from `static/`.
- **Containerization:** Multi-stage `Dockerfile` builds the React frontend with Node, then copies the static bundle into a slim Python runtime image configured to listen on `$PORT` (default `8080`). `docker-compose.yml` is provided for local hot-reload development.
- **Deployment (GCP Cloud Run):** `cloudbuild.yaml` defines a Cloud Build pipeline to build the container image, push it to Artifact Registry, and deploy it to Cloud Run.

### File Inventory & Purpose

#### Root, Docker & GCP Cloud Run
| File | Purpose |
| :--- | :--- |
| `Dockerfile` | Multi-stage production build (Node builds React `dist/`, Python slim runs FastAPI/Uvicorn on `$PORT`). |
| `.dockerignore` | Excludes `node_modules`, `.venv`, `__pycache__`, `.git`, and `.env` files from Docker build context. |
| `docker-compose.yml` | Local development orchestration with hot-reloading for backend and frontend. |
| `.env.example` | Template documenting required environment variables without exposing secrets. |
| `cloudbuild.yaml` | GCP Cloud Build pipeline to build, push to Artifact Registry, and deploy to Cloud Run. |

#### Python Backend (`backend/`)
| File | Purpose |
| :--- | :--- |
| `backend/requirements.txt` | Pinned Python dependencies (`fastapi`, `uvicorn[standard]`, `httpx`, `pydantic-settings`, `pytest`). |
| `backend/app/__init__.py` | Marks `app` as a Python package. |
| `backend/app/main.py` | FastAPI entry point: CORS setup, `/health` endpoint, startup-from-sleep + daily CDR sync lifespan loop, `/api` router mount, and production static file serving. |
| `backend/app/config.py` | Environment variable loading and validation via `pydantic-settings` (including `GCP_PROJECT_ID`, `BQ_DATASET`, `BQ_LOCATION`, `CDR_TABLE_NAME`, `API_TOKENS_TABLE_NAME`, `MARKET_DATA_API_KEY`, `QUOTE_CACHE_TTL_SECONDS`, scraper schedule, and optional local `GOOGLE_APPLICATION_CREDENTIALS`). |
| `backend/app/db.py` | BigQuery client helper (`get_bq_client`), dataset provisioning (`ensure_datasets`), and table schema/creation (`ensure_cdr_list_table`, `ensure_api_tokens_table`). |
| `backend/app/services/__init__.py` | Marks `services` as a Python package. |
| `backend/app/services/cdr_scraper.py` | Async `httpx` scraper for CIBC (`ng-state` JSON) and BMO (`sunshine.json` discovered via `index.js`) CDR directories with deduplication. |
| `backend/app/services/cdr_sync.py` | Diff comparison (`compare_cdrs`), atomic BigQuery `MERGE` into `cdr_list`, and in-memory TTL caching (`list_cdrs_from_store`). |
| `backend/app/services/questrade_auth.py` | Questrade OAuth2 rotating refresh-token exchange and durable BigQuery persistence in `api_tokens`. |
| `backend/app/services/market_data.py` | Questrade symbol resolution (`.TO` / `.NE` + `/v1/symbols/search`) and Level 1 CAD quote fetching with 60s TTL cache and 401 retry. |
| `backend/app/api/__init__.py` | Marks `api` as a Python package. |
| `backend/app/api/routes.py` | API endpoints (`/api/cdrs`, `/api/cdrs/sync`, `/api/cdrs/sync-status`, `/api/market-data`, `/api/market-data/{symbol}`). |
| `backend/tests/test_main.py` | Smoke tests for `/health` and `/api/cdrs`. |
| `backend/tests/test_cdr_scraper_sync.py` | Unit tests for CIBC/BMO parsing, diff comparison (including partial-source failure safety), sync endpoint, and startup lifespan. |
| `backend/tests/test_questrade_market_data.py` | Unit tests for Questrade rotating OAuth persistence, `.TO`/`.NE` symbol resolution, 60s quote caching, 401 retry, and market-data routes. |

#### React Frontend (`frontend/`)
| File | Purpose |
| :--- | :--- |
| `frontend/package.json` | React, Vite, and TypeScript dependencies and npm scripts (`dev`, `build`, `preview`). |
| `frontend/tsconfig.json` | TypeScript compiler configuration. |
| `frontend/vite.config.ts` | Vite configuration with `/api` dev proxy to `http://localhost:8000`. |
| `frontend/index.html` | HTML entry point loading Inter & JetBrains Mono fonts and mounting the React application. |
| `frontend/src/main.tsx` | React DOM entry point rendering `<App />`. |
| `frontend/src/App.tsx` | Editorial FinTech Table UI displaying BigQuery `cdr_list` in alphabetical order with A–Z index, issuer/country/new filters, expandable metadata rows, and Sync Now trigger. |
| `frontend/src/App.css` | Styling for the Editorial FinTech Table layout, sticky filter/A–Z toolbar, issuer badges, and responsive breakpoints. |

---

## 2026-11-14 — BigQuery Database Setup & Environment Routing

- **Database Selection:** Adopted **Google Cloud BigQuery** (`google-cloud-bigquery`) in project `gen-lang-client-0230474681` as the primary datastore, with `cdr_tracking_dev` for local development and `cdr_tracking_prod` for Cloud Run production.
- **Authentication:**
  - **Local development:** Uses `GOOGLE_APPLICATION_CREDENTIALS=sa-cdr-tracking.json` in `.env` (git-ignored and docker-ignored).
  - **Cloud Run production:** Uses keyless IAM authentication via `--service-account=sa-cdr-tracking@${PROJECT_ID}.iam.gserviceaccount.com` in `cloudbuild.yaml`.

---

## 2026-11-14 — CIBC & BMO CDR Web Scraper and `cdr_list` Sync

- **Sources Monitored:**
  - `https://cdr.cibc.com/en/cdr-directory` — parsed from SSR `<script id="ng-state">` JSON (authoritative source for 144 CIBC-issued CDRs).
  - `https://www.bmoinvestorline.com/wealth/journeys/content/micro/cdr/CanadianDepositaryReceipts.html` — dynamically discovers `./JS/index.js` and its JSON feed (`./JS/sunshine.json`) for 84 BMO-issued CDRs, avoiding stale CIBC entries (`CHEV`, `MCDS`) present on BMO's mirror.
- **BigQuery Persistence (`cdr_list`):** Uses an atomic SQL `MERGE` (`JSON_QUERY_ARRAY`) to insert new CDRs (`is_new = TRUE` for 7 days post-baseline), update changed metadata, track `first_seen_at` / `last_seen_at` / `updated_at`, and safely mark missing rows inactive only for sources that succeeded.
- **Scheduling:** FastAPI `lifespan` triggers an immediate background sync on startup / wake-from-sleep (`trigger="startup"`), followed by a 24-hour background loop (`trigger="daily"`), plus `POST /api/cdrs/sync` for manual or GCP Cloud Scheduler triggers.

---

## 2026-11-14 — Alphabetical CDR Directory Frontend (Option 1: Editorial FinTech Table)

- Implemented the **Editorial FinTech Table** UI in `frontend/src/App.tsx` and `frontend/src/App.css`:
  - KPI strip (`Total Active CDRs`, `CIBC Issued`, `BMO Issued`, `Newly Added (7d)`).
  - Sticky filter toolbar with search, issuer segmented toggle, country filter, `New Only` filter, Symbol/Name A–Z or Z–A sort, and an interactive A–Z quick-filter rail.
  - Expandable table rows showing underlying ticker/exchange, CDR exchange, Questrade symbol, bid/ask, day high/low, today volume, FX forward rate, 10d avg volume, first/last verified timestamps, and source links, plus **Refresh Quotes** and **Sync Now** buttons.

---

## 2026-11-14 — Live Questrade Market Data Integration

- **Rotating OAuth Persistence (`api_tokens`):** Because Questrade uses single-use rotating refresh tokens, `backend/app/services/questrade_auth.py` exchanges `MARKET_DATA_API_KEY` on first boot (or when the seed hash changes) and persists the newest `refresh_token`, `access_token`, `api_server`, and `expires_at` to BigQuery table `api_tokens` via SQL `MERGE`.
- **Symbol Resolution & Quote Caching:** `backend/app/services/market_data.py` resolves CIBC tickers prioritizing `.TO` (TSX) and BMO tickers prioritizing `.NE` (NEO/Cboe Canada) with `/v1/symbols/search` fallback, then batches Level 1 `/v1/markets/quotes` requests with a 60s TTL cache (`QUOTE_CACHE_TTL_SECONDS`) and automatic single retry on HTTP `401`.
- **Frontend Quote Integration:** `frontend/src/App.tsx` loads batch CAD prices from `GET /api/market-data`, displays `Price (CAD)` and signed daily `%` change in the table, fetches missing quotes on row expansion via `GET /api/market-data/{symbol}`, and provides a **Refresh Quotes** trigger.




