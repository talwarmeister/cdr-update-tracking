# Project TODOs

## Decisions
- **Day-to-day local dev:** Run FastAPI (`uvicorn app.main:app --reload`) and Vite (`npm run dev`) **natively** for faster HMR, lower overhead, and direct IDE/debugger integration.
- **Pre-deployment verification:** Run `docker build` / `docker run` (or `docker compose up`) to verify the containerized Linux build works end-to-end before deploying to Cloud Run.

## 1. Local Environment Setup
- [ ] Copy `.env.example` to `.env` and populate `MARKET_DATA_API_KEY` and any other secrets.
- [ ] Set up local Python `.venv`, install `backend/requirements.txt`, and install `frontend/` npm dependencies (`npm install`).
- [ ] Verify native local dev environment (`uvicorn app.main:app --reload` in `backend/` and `npm run dev` in `frontend/`).
- [ ] Run backend tests (`pytest`) in `backend/` to verify `/health` and `/api/cdrs`.

## 2. Core Feature Implementation
- [ ] Implement CDR source tracking / scraping or API integration in `backend/app/api/routes.py` (or dedicated service modules).
- [ ] Implement live market data fetching for CDR symbols using `httpx` and `MARKET_DATA_API_KEY`.
- [ ] Build out the React UI in `frontend/src/App.tsx` to display newly created CDRs and live market data.

## 3. GCP Cloud Run Deployment
- [ ] Ensure GCP project has Cloud Build, Artifact Registry, and Cloud Run APIs enabled.
- [ ] Create an Artifact Registry Docker repository matching `_REPOSITORY` (`cdr-tracking-repo`) in `cloudbuild.yaml`.
- [ ] Configure Cloud Run environment variables / Secret Manager for `MARKET_DATA_API_KEY`.
- [ ] Deploy with `gcloud builds submit --config cloudbuild.yaml` and verify the live `/health` endpoint.

