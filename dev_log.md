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
| `backend/app/main.py` | FastAPI entry point: CORS setup, `/health` endpoint, `/api` router mount, and production static file serving. |
| `backend/app/config.py` | Environment variable loading and validation via `pydantic-settings`. |
| `backend/app/api/__init__.py` | Marks `api` as a Python package. |
| `backend/app/api/routes.py` | Starter API endpoints (`/api/cdrs`, `/api/market-data`) for CDR tracking and live market data. |
| `backend/tests/test_main.py` | Smoke tests for `/health` and starter API endpoints. |

#### React Frontend (`frontend/`)
| File | Purpose |
| :--- | :--- |
| `frontend/package.json` | React, Vite, and TypeScript dependencies and npm scripts (`dev`, `build`, `preview`). |
| `frontend/tsconfig.json` | TypeScript compiler configuration. |
| `frontend/vite.config.ts` | Vite configuration with `/api` dev proxy to `http://localhost:8000`. |
| `frontend/index.html` | HTML entry point for mounting the React application. |
| `frontend/src/main.tsx` | React DOM entry point rendering `<App />`. |
| `frontend/src/App.tsx` | Starter UI component fetching from `/api/cdrs` and displaying status. |
| `frontend/src/App.css` | Basic starter layout styling. |

