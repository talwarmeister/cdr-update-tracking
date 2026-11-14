# Project TODOs

## Decisions
- **Day-to-day local dev:** Run FastAPI (`uvicorn app.main:app --reload`) and Vite (`npm run dev`) **natively** for faster HMR, lower overhead, and direct IDE/debugger integration.
- **Pre-deployment verification:** Run `docker build` / `docker run` (or `docker compose up`) to verify the containerized Linux build works end-to-end before deploying to Cloud Run.
- **Database choice — GCP BigQuery (Current Decision):**
  - **Why BigQuery now:** 100% GCP-native, **$0/month** within the free tier (10 GB active storage + 1 TB queries/month, plenty for low-volume CDR tracking), SQL support, and **keyless IAM authentication** (`gcloud auth application-default login` locally; Cloud Run Service Account with `roles/bigquery.dataEditor` and `roles/bigquery.jobUser` in prod—no DB passwords or `DATABASE_URL` needed).
  - **Environment separation & design:** Use separate datasets (`cdr_tracking_dev` locally vs. `cdr_tracking_prod` in Cloud Run via `BQ_DATASET`). Mitigate BigQuery's ~0.5–1.5s query overhead with lightweight in-memory TTL caching in FastAPI, and use append-only snapshots or SQL `MERGE` queries rather than legacy streaming inserts.
  - **Future evolution options (if millisecond OLTP response times are needed later):**
    - **Cloud Firestore:** 100% GCP-native serverless document DB, $0/month free tier (1 GiB storage + 50k reads/20k writes per day), ~10–30ms single-document reads/upserts, keyless IAM auth.
    - **Neon (Serverless Postgres):** $0/month free tier, scales to zero, supports Git-like database branching (`dev` vs. `main`), full relational Postgres with millisecond OLTP queries via `DATABASE_URL`.
    - **GCP Cloud SQL (Postgres/MySQL):** 100% GCP-native managed relational DB with millisecond OLTP latency, automated backups, and native Cloud Run Auth Proxy integration (does not scale to zero; ~$7–$10+/month minimum).
  - **Container storage:** Do **not** store SQLite or data files inside the Cloud Run container; Cloud Run disks are ephemeral and wiped on scale-to-zero or restart.
- **Secrets management:** Use `.env` locally (git-ignored) and **GCP Secret Manager** in production (`--set-secrets=MARKET_DATA_API_KEY=MARKET_DATA_API_KEY:latest` on Cloud Run, $0/month within free tier).

## 1. ~~Local Environment Setup~~
- [x] ~~Copy `.env.example` to `.env` and populate `MARKET_DATA_API_KEY` and any other secrets.~~
- [x] ~~Set up local Python `.venv`, install `backend/requirements.txt`, and install `frontend/` npm dependencies (`npm install`).~~
- [x] ~~Verify native local dev environment (`uvicorn app.main:app --reload` in `backend/` and `npm run dev` in `frontend/`).~~
- [x] ~~Run backend tests (`pytest`) in `backend/` to verify `/health` and `/api/cdrs`.~~

## 2. Core Feature Implementation
- [ ] Add `google-cloud-bigquery` to `backend/requirements.txt` and configure `GCP_PROJECT_ID` / `BQ_DATASET` in `backend/app/config.py` and `.env.example`.
- [ ] Implement CDR source tracking / scraping or API integration with BigQuery persistence and in-memory TTL caching in `backend/app/api/routes.py` (or dedicated service modules).
- [ ] Populate `MARKET_DATA_API_KEY` in `.env` and implement live market data fetching for CDR symbols using `httpx`.
- [ ] Build out the React UI in `frontend/src/App.tsx` to display newly created CDRs and live market data.

## 3. GCP Cloud Run & BigQuery Deployment
- [ ] Create BigQuery datasets `cdr_tracking_dev` (local) and `cdr_tracking_prod` (Cloud Run) in your GCP project.
- [ ] Ensure GCP project has BigQuery, Cloud Build, Artifact Registry, Cloud Run, and Secret Manager APIs enabled.
- [ ] Grant the Cloud Run service account `roles/bigquery.dataEditor`, `roles/bigquery.jobUser`, and `roles/secretmanager.secretAccessor`, and store prod `MARKET_DATA_API_KEY` in GCP Secret Manager.
- [ ] Create an Artifact Registry Docker repository matching `_REPOSITORY` (`cdr-tracking-repo`) in `cloudbuild.yaml`.
- [ ] Update `cloudbuild.yaml` `gcloud run deploy` args with `--set-env-vars=GCP_PROJECT_ID=$PROJECT_ID,BQ_DATASET=cdr_tracking_prod` and `--set-secrets=MARKET_DATA_API_KEY=MARKET_DATA_API_KEY:latest`.
- [ ] Deploy with `gcloud builds submit --config cloudbuild.yaml` and verify the live `/health` endpoint.

