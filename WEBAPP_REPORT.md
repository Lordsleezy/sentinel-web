# SentinelWeb PWA Frontend Report

## What got built

- Added a no-build Progressive Web App under `webapp/`.
- Built a dark, touch-friendly chat UI with Single and Compare modes.
- Single mode posts to `/query` with `{ query, url?, headless: true }`.
- Compare mode posts to `/query/compare` with `{ query, sites, headless: true }`.
- Added local query history in `localStorage`, capped at the most recent 50 entries, with tap-to-rerun and clear-all.
- Added a service worker that caches the app shell and keeps API calls network-only.
- Added a PWA manifest with standalone display, theme/background colors, 192/512 icons, and maskable icon entries.
- Added iOS PWA meta tags, an Apple touch icon, and common iPhone startup images.
- Updated `api.py` to serve the frontend at `/app/` and redirect `/app` to `/app/`.
- Added `webapp/README.md` with local testing and iOS/Android install instructions.

## Assumptions made

- Cloudflare Access will authenticate users before requests reach FastAPI, so the frontend does not include login UI.
- Same-origin deployment means `/query`, `/query/compare`, and `/health` can be called with normal same-origin credentials.
- The backend has no status-streaming endpoint today, so the loading state uses client-side elapsed time and staged status text instead of backend progress events.
- Compare mode expects coworkers to enter one site URL per line or comma-separated.

## Endpoints hit by the frontend

- `GET /health` for a lightweight readiness badge.
- `POST /query` for default single-query lookups.
- `POST /query/compare` for multi-site comparisons.

The PWA intentionally does not call or expose any `/credentials/*` endpoint.

## Existing API notes and follow-up

- `/query` returns `answer`, `source_url`, `confidence`, `execution_time`, `cached`, `login_used`, and optional `error`, which maps cleanly to the chat result UI.
- `/query/compare` returns `summary`, `site_results`, `best_site`, and `elapsed_total`, which maps cleanly to a summary plus compact comparison table.
- There is no backend progress stream or job polling endpoint. The frontend can make long requests feel active, but true status lines like "opening browser" or "reading page" would require a small future API addition.
- The existing browser error message for login-required pages mentions `POST /credentials/save`; the coworker-facing UI hides credential management as requested, so users may see that backend text if they query a login-only page without saved credentials.
