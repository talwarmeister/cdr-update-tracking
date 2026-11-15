# CDR Update Tracking

Full-stack web application to automatically track newly listed and updated **Canadian Depositary Receipts (CDRs)** from **CIBC** and **BMO**, persist historical state in **Google Cloud BigQuery**, and display live **CAD market quotes** from **Questrade**.

**Live Production App:** https://cdrtracker.cloud.run/

---

## Features

- **Automated CIBC & BMO Directory Scraping (`backend/app/services/cdr_scraper.py`):**
  - Parses CIBC SSR `<script id="ng-state">` JSON (`https://cdr.cibc.com/en/cdr-directory`).
  - Discovers BMO's `./JS/index.js` and parses its JSON feed (`https://www.bmoinvestorline.com/wealth/journeys/content/micro/cdr/CanadianDepositaryReceipts.html`).
  - Runs automatically on backend startup/wake-from-sleep, every **24 hours** in the background, and on demand via `POST /api/cdrs/sync`.
- **BigQuery Persistence & Diff Tracking (`backend/app/services/cdr_sync.py`):**
  - Atomically compares scraped records against BigQuery table `cdr_list` via SQL `MERGE`.
  - Tracks `first_seen_at`, `last_seen_at`, `is_active`, and marks newly added CDRs with `is_new = TRUE` for 7 days.
  - Protects against partial-source outages (records are only marked inactive for issuers that succeeded).
- **Live Questrade CAD Market Data (`backend/app/services/questrade_auth.py`, `backend/app/services/market_data.py`):**
  - Handles Questrade's **single-use rotating OAuth2 refresh tokens** cleanly by persisting the latest rotated session in BigQuery (`api_tokens`).
  - Resolves CIBC tickers prioritizing `.TO` (TSX) and BMO tickers prioritizing `.NE` (NEO/Cboe Canada), with `/v1/symbols/search` fallback.
  - Fetches Level 1 quotes via `/v1/markets/quotes` with a **60-second** backend TTL cache and automatic single retry on HTTP `401`.
- **Editorial FinTech Table UI (`frontend/src/App.tsx`):**
  - Alphabetical A–Z index rail, search across symbol/name/sector/country/underlying ticker, Issuer (`CIBC` / `BMO`) & Country & `New Only` filters, and Symbol/Name A–Z or Z–A sorting.
  - Live `Price (CAD)` and daily `%` change column, **60-second** auto-refresh while the tab is visible (**Auto 60s** toggle), and a **Live Quote** refresh-icon button for on-demand refreshes.
  - Expandable row details displaying Underlying Symbol, CDR Exchange, Questrade Symbol, Bid/Ask, Day High/Low, Today Volume, FX Forward Rate, 10d Avg Volume, First/Last Verified timestamps, and directory link.

---

## Tech Stack

| Layer | Technology |
| :--- | :--- |
| **Backend** | Python 3.12+, FastAPI, Uvicorn, `httpx`, `pydantic-settings` |
| **Frontend** | React 18, TypeScript, Vite 6 |
| **Database** | Google Cloud BigQuery (`cdr_tracking_dev` locally, `cdr_tracking_prod` in Cloud Run) |
| **Market Data** | Questrade REST API (OAuth2 rotating tokens + Level 1 Quotes) |
| **CI/CD & Hosting** | GitHub Actions, Docker multi-stage build, GCP Artifact Registry, Cloud Build, Cloud Run, Secret Manager |

---

## Project Structure

```text
cdr-update-tracking/
├── .github/workflows/ci.yml      # GitHub Actions CI: backend pytest + frontend tsc/vite build
├── .env.example                  # Environment variable template (secrets excluded from git)
├── Dockerfile                    # Multi-stage Node 20 -> Python 3.12 Cloud Run image
├── cloudbuild.yaml               # Cloud Build -> Artifact Registry -> Cloud Run deployment
├── backend/
│   ├── requirements.txt          # Backend Python dependencies
│   ├── app/
│   │   ├── main.py               # FastAPI app, lifespan sync loop, /health, static SPA mount
│   │   ├── config.py             # Pydantic settings (.env / environment variables)
│   │   ├── db.py                 # BigQuery client + cdr_list & api_tokens table schemas
│   │   ├── api/routes.py         # REST endpoints (/api/cdrs, /api/market-data)
│   │   └── services/             # Scraper, BigQuery sync, Questrade auth & quotes
│   └── tests/                    # Pytest unit tests (10 offline/mocked tests)
├── frontend/
│   ├── package.json              # React/Vite scripts & dependencies
│   └── src/                      # App.tsx, App.css, main.tsx
├── dev_log.md                    # Chronological architecture & change log
└── todo.md                       # Setup, feature, and GCP deployment checklist
```

---

## Local Development Setup

### 1. Configure Environment & Credentials
Copy `.env.example` to `.env` in the repository root:

```powershell
Copy-Item .env.example .env
```

Set the values in `.env`:
- `GCP_PROJECT_ID` (default: `gen-lang-client-0230474681`)
- `BQ_DATASET=cdr_tracking_dev` (keep separate from `cdr_tracking_prod` used on Cloud Run)
- `GOOGLE_APPLICATION_CREDENTIALS=sa-cdr-tracking.json` (service account key placed in repo root; git-ignored and docker-ignored)
- `MARKET_DATA_API_KEY` (Questrade manual refresh token — **use separate Questrade tokens for local dev vs Cloud Run prod** because Questrade refresh tokens are single-use and rotate on redemption)

### 2. Backend Setup (Python `.venv`)
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### 3. Frontend Setup (Node.js 20+)
```powershell
cd frontend
npm install
cd ..
```

### 4. Run Locally (Two Terminals)
- **Terminal 1 — Backend API (`http://127.0.0.1:8000`):**
  ```powershell
  cd backend
  ..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
  ```
- **Terminal 2 — Frontend UI (`http://localhost:5173`):**
  ```powershell
  cd frontend
  npm run dev
  ```
  Vite proxies `/api/*` requests to `http://localhost:8000`.

---

## Build & Test Commands

### Run Backend Unit Tests
```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -v
```

### Type-Check & Build Frontend Production Bundle
```powershell
cd frontend
npm run build
```

### Build & Smoke-Test Production Docker Container
```powershell
docker build -t cdr-update-tracking .
```

---

## API Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health check returning environment, `gcp_project_id`, and `bq_dataset` |
| `GET` | `/api/cdrs` | List CDRs from BigQuery (cached 300s; `?refresh=true` bypasses cache) |
| `POST` | `/api/cdrs/sync` | Run CIBC + BMO scrape, diff, and BigQuery `MERGE` (`?trigger=manual\|scheduler`) |
| `GET` | `/api/cdrs/sync-status` | Return last sync status report and active CDR count |
| `GET` | `/api/market-data` | Fetch batch Questrade CAD quotes (`?symbols=...&refresh=true`) |
| `GET` | `/api/market-data/{symbol}` | Fetch single Questrade CAD quote (`?issuer=CIBC\|BMO&refresh=true`) |

---

## CI/CD & GCP Cloud Run Deployment

### Continuous Integration (`.github/workflows/ci.yml`)
Every pull request or push targeting `main` automatically runs:
1. **`backend-tests`** (Python `3.12`): installs `backend/requirements.txt` and runs `pytest -v`.
2. **`frontend-build`** (Node `20`): runs `npm ci` and `npm run build` (`tsc && vite build`).

### Production Deployment (`cloudbuild.yaml`)
[cloudbuild.yaml](cloudbuild.yaml) builds the multi-stage Docker image, pushes it to GCP Artifact Registry (`us-central1-docker.pkg.dev/<PROJECT_ID>/cdr-tracking-repo/cdr-update-tracking`), and deploys to **Cloud Run** with:
- Runtime service account: `sa-cdr-tracking@<PROJECT_ID>.iam.gserviceaccount.com`
- Env vars: `ENVIRONMENT=production`, `GCP_PROJECT_ID`, `BQ_DATASET=cdr_tracking_prod`, `BQ_LOCATION=US`
- Secret Manager mount: `MARKET_DATA_API_KEY=MARKET_DATA_API_KEY:latest`

#### Initial GCP Setup Commands (Single-Line)
```bash
# Create Artifact Registry repository (one-time)
gcloud artifacts repositories create cdr-tracking-repo --repository-format=docker --location=us-central1 --project=gen-lang-client-0230474681

# Create & grant access to production Questrade token secret (use a fresh dedicated token)
printf '%s' 'YOUR_NEW_PROD_QUESTRADE_TOKEN' | gcloud secrets create MARKET_DATA_API_KEY --project=gen-lang-client-0230474681 --replication-policy="automatic" --data-file="-"
gcloud secrets add-iam-policy-binding MARKET_DATA_API_KEY --project=gen-lang-client-0230474681 --member="serviceAccount:sa-cdr-tracking@gen-lang-client-0230474681.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
```

Connect the GitHub repository's `main` branch (`^main$`) to a **Cloud Build Trigger** pointing to `/cloudbuild.yaml` so merges to `main` automatically deploy to Cloud Run (**https://cdrtracker.cloud.run/**), and run a daily **Cloud Scheduler** job (`cdr-daily-sync`) calling `POST https://cdrtracker.cloud.run/api/cdrs/sync?trigger=scheduler`.
