# Codex Progress - Sentinel Web

## Current Repo Understanding

Sentinel Web is a FastAPI service intended to run on SLEEZY at port `8766` and be exposed through Cloudflare Tunnel. It already had:

- FastAPI app in `api.py`
- Playwright Chromium automation in `browser.py`
- Stealth init script, user-agent rotation, cookie banner dismissal, popup handling, realistic delays during login, and content extraction
- Natural language `/query` and `/query/compare`
- Closed-beta inventory job APIs under `/inventory/*`
- Ollama/AI helper integration through `ai_helper.py` and `processor.py`
- PWA frontend under `webapp/`

## Added This Pass

- `POST /scrape`
  - Accepts `target_url`, natural language `instructions`, `max_pages`, optional `cron_schedule`, and optional `wait`.
  - Creates a queued scrape job and returns job state.
  - If `wait=true`, waits briefly and returns completed structured results when available.

- `GET /jobs/{job_id}`
  - Returns queued/running/completed/failed status, progress messages, and structured results.

- Structured product extraction
  - Returns JSON fields: `title`, `price`, `condition`, `specs`, `images`, `source_url`, `seller_info`, `timestamp`.
  - Keeps `raw_text` for debugging and downstream listing generation.

- Pagination
  - Follows `rel=next`, aria-label next links, and visible next links up to `max_pages`.

- Anti-detection and Cloudflare behavior
  - Reuses existing stealth script and user-agent rotation.
  - Adds randomized scrape delays.
  - Detects Cloudflare/Turnstile challenge pages and waits for browser-managed completion.
  - Does not bypass captchas or defeat access controls; unresolved challenges are reported in job progress.

- Scheduled recurring scrape jobs
  - Accepts cron syntax in `cron_schedule`.
  - Uses an in-memory scheduler loop backed by `croniter`.

- `POST /score`
  - Scores product JSON from 0-100.
  - Uses Ollama via the existing local AI helper when available.
  - Falls back to deterministic scoring based on price, estimated market value, specs, and condition.

- `GET /health`
  - Adds `active_jobs_count` and `cloudflare_tunnel_compatible`.
  - Existing health fields continue to report Ollama/AI helper and inventory readiness.

## Operational Notes

- Jobs are in memory and do not survive process restarts yet.
- Recurring schedules are also in memory and should be persisted later if Sentinel Web becomes a long-running production scheduler.
- Cloudflare Tunnel compatibility is achieved by exposing normal HTTP endpoints with no special host assumptions.
- For marketplace scraping through Sentinel Market, set `SENTINEL_WEB_URL` to the Cloudflare Tunnel URL that points to `http://127.0.0.1:8766`.

## Verification

Completed:

- Python syntax compile
  - `python -m py_compile api.py scrape_service.py ai_helper.py browser.py`
  - `python -m py_compile api.py scrape_service.py`
- `croniter==3.0.3` installed successfully in the local Python user environment.

Blocked locally:

- Full FastAPI/Playwright import smoke tests cannot run under the active local Python `3.14` interpreter because pinned dependencies do not currently install cleanly there:
  - `greenlet==3.1.1` fails building for Python 3.14.
  - `pydantic-core==2.27.1` fails because its PyO3 version supports up to Python 3.13.
- This repo should be validated on its intended runtime Python version, preferably Python 3.11 or 3.12, on SLEEZY.

Pending:

- Git push to `Lordsleezy/sentinel-web`
