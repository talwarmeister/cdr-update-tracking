# Project TODOs

## Decisions
- **Day-to-day local dev:** Run FastAPI (`uvicorn app.main:app --reload`) and Vite (`npm run dev`) **natively** for faster HMR, lower overhead, and direct IDE/debugger integration.
- **Pre-deployment verification:** Run `docker build` / `docker run` (or `docker compose up`) to verify the containerized Linux build works end-to-end before deploying to Cloud Run.
- **Database choice (Neon vs. Cloud SQL):**
  - **Neon (Serverless Postgres — Recommended for \$0/month):** Free tier, scales to zero, supports Git-like database branching (`dev` branch for local `.env`, `main` branch for Cloud Run prod) so dev and prod run the exact same Postgres engine without SQLite/Postgres dialect mismatches. Connects via `DATABASE_URL`.
  - **GCP Cloud SQL (Alternative if 100% GCP-native is required):** Managed Postgres/MySQL with native Cloud Run Unix socket/Auth Proxy integration and automated backups, but **does not scale to zero** (~\$7–\$10+/month minimum even with zero traffic).
  - **Container storage:** Do **not** store SQLite or data files inside the Cloud Run container; Cloud Run disks are ephemeral and wiped on scale-to-zero or restart.
- **Secrets management:** Use `.env` locally (git-ignored) and **GCP Secret Manager** in production (`--set-secrets=DATABASE_URL=DATABASE_URL:latest` on Cloud Run, \$0/month within free tier).

## 1. ~~Local Environment Setup~~
- [x] ~~Copy `.env.example` to `.env` and populate `MARKET_DATA_API_KEY` and any other secrets.~~
- [x] ~~Set up local Python `.venv`, install `backend/requirements.txt`, and install `frontend/` npm dependencies (`npm install`).~~
- [x] ~~Verify native local dev environment (`uvicorn app.main:app --reload` in `backend/` and `npm run dev` in `frontend/`).~~
- [x] ~~Run backend tests (`pytest`) in `backend/` to verify `/health` and `/api/cdrs`.~~

## 2. Core Feature Implementation
- [ ] Implement CDR source tracking / scraping or API integration in `backend/app/api/routes.py` (or dedicated service modules).
- [ ] Populate `MARKET_DATA_API_KEY` in `.env` and implement live market data fetching for CDR symbols using `httpx`.
- [ ] Build out the React UI in `frontend/src/App.tsx` to display newly created CDRs and live market data.

## 3. GCP Cloud Run & Database Deployment
- [ ] Create Neon project with `main` (prod) and `dev` (local) branches (or provision a GCP Cloud SQL Postgres instance if choosing 100% GCP-native).
- [ ] Ensure GCP project has Cloud Build, Artifact Registry, Cloud Run, and Secret Manager APIs enabled.
- [ ] Store prod `DATABASE_URL` and `MARKET_DATA_API_KEY` in GCP Secret Manager and grant the Cloud Run service account `roles/secretmanager.secretAccessor`.
- [ ] Create an Artifact Registry Docker repository matching `_REPOSITORY` (`cdr-tracking-repo`) in `cloudbuild.yaml`.
- [ ] Update `cloudbuild.yaml` `gcloud run deploy` args with `--set-secrets=DATABASE_URL=DATABASE_URL:latest,MARKET_DATA_API_KEY=MARKET_DATA_API_KEY:latest`.
- [ ] Deploy with `gcloud builds submit --config cloudbuild.yaml` and verify the live `/health` endpoint.

