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
- [x] ~~Add `google-cloud-bigquery` to `backend/requirements.txt` and configure `GCP_PROJECT_ID` / `BQ_DATASET` in `backend/app/config.py` and `.env.example`.~~
- [x] ~~Implement CDR source tracking / scraping or API integration with BigQuery persistence and in-memory TTL caching in `backend/app/api/routes.py` (or dedicated service modules).~~
- [x] ~~Build out the React CDR directory UI in `frontend/src/App.tsx` (alphabetical A–Z index, search, issuer/country/new filters, expandable details, and Sync Now).~~
- [x] ~~Populate `MARKET_DATA_API_KEY` in `.env`, implement live market data fetching (`httpx`), and display live market quotes in `frontend/src/App.tsx`.~~

## 3. GCP Cloud Run & BigQuery Deployment
- [x] ~~Create BigQuery datasets `cdr_tracking_dev` (local) and `cdr_tracking_prod` (Cloud Run) in your GCP project.~~
- [x] ~~Configure `sa-cdr-tracking` service account (`GOOGLE_APPLICATION_CREDENTIALS=sa-cdr-tracking.json` in `.env` for local dev; `--service-account` and `--set-env-vars` in `cloudbuild.yaml` for Cloud Run).~~
- [x] ~~Ensure GCP project has Cloud Build, Artifact Registry, Cloud Run, and Secret Manager APIs enabled.~~
- [x] ~~Create a dedicated Questrade API token for prod, store `MARKET_DATA_API_KEY` in GCP Secret Manager, and grant `sa-cdr-tracking` `roles/secretmanager.secretAccessor` (`cloudbuild.yaml` already configures `--set-secrets=MARKET_DATA_API_KEY=MARKET_DATA_API_KEY:latest`).~~
- [ ] Create an Artifact Registry Docker repository matching `_REPOSITORY` (`cdr-tracking-repo`) in `cloudbuild.yaml`:
  - `gcloud artifacts repositories create cdr-tracking-repo --repository-format=docker --location=us-central1 --project=gen-lang-client-0230474681`
- [ ] Build & deploy with Cloud Build and verify the live `/health` endpoint:
  - `gcloud builds submit --config=cloudbuild.yaml --project=gen-lang-client-0230474681 --substitutions=COMMIT_SHA=manual-1`
  - *(Note: If Cloud Build's deploy step asks for impersonation permissions, grant the Cloud Build service account `roles/iam.serviceAccountUser` on `sa-cdr-tracking@gen-lang-client-0230474681.iam.gserviceaccount.com`.)*
- [ ] Configure a daily **Cloud Scheduler** HTTP job calling `POST /api/cdrs/sync?trigger=scheduler` so Cloud Run syncs daily even when scaled to zero.

