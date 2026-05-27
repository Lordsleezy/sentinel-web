# SentinelWeb Cloud No-AI Report

Report date: 2026-05-27

## Goal

Prepare SentinelWeb for Railway/Render deployment as a closed-beta FastAPI + Playwright inventory backend without requiring Ollama or local models.

## What changed

- Added `OLLAMA_ENABLED=false` as the default mode.
- Startup no longer checks Ollama unless `OLLAMA_ENABLED=true`.
- `/health` now reports:
  - `ollama_enabled`
  - `ollama_connected`
  - `browser_ready`
  - `inventory_ready`
- Query parsing and extraction skip Ollama when disabled.
- Comparison summarization skips Ollama when disabled and uses the existing deterministic aggregate fallback.
- Inventory browser providers call deterministic extraction only with `use_ollama=False`.
- Inventory providers read `HEADLESS=true/false` from the environment.
- Added CORS for:
  - `https://sentinelprime.org`
  - `https://www.sentinelprime.org`
  - `http://localhost:5173`
  - `http://127.0.0.1:5173`
- Updated `.env.example` with cloud-safe defaults and empty placeholders.

## Deployment files added

- `Dockerfile`
- `.dockerignore`
- `railway.json`
- `render.yaml`
- `DEPLOY_RAILWAY.md`
- `DEPLOY_RENDER.md`

The Dockerfile installs Python dependencies and Playwright Chromium with system dependencies:

```dockerfile
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium
```

The Railway start command is:

```sh
uvicorn api:app --host 0.0.0.0 --port $PORT
```

## Cloud environment

Recommended production variables:

```env
OLLAMA_ENABLED=false
HEADLESS=true
ALLOWED_ORIGINS=https://sentinelprime.org,https://www.sentinelprime.org,http://localhost:5173,http://127.0.0.1:5173
SENTINEL_BETA_USERS=user1@example.com,user2@example.com
SENTINEL_AUTH_TOKEN=
SENTINEL_INVENTORY_CACHE_TTL_S=720
```

Optional future AI mode:

```env
OLLAMA_ENABLED=true
OLLAMA_HOST=http://your-ollama-host:11434
OLLAMA_MODEL=qwen2.5-coder:14b
```

## Frontend/backend architecture

- Netlify hosts `sentinelprime.org`.
- Railway or Render hosts the FastAPI backend.
- Netlify frontend calls the backend through configured HTTPS backend routes.
- CORS allows the SentinelPrime origins and local Vite dev origins.
- Inventory access remains backend-authenticated and invite-only.

## Protected behavior

- Anonymous inventory access remains blocked.
- No invited users means inventory fails closed.
- Rate limits remain active:
  - 10 searches per user per hour.
  - 3 concurrent inventory searches total.
  - 10 seconds between requests to the same retailer.
- Cache remains enabled with a 720-second default TTL.
- Audit logs remain enabled.
- Provider blocked/captcha/rate-limited/login-wall failures return graceful unavailable status.
- No proxy rotation was added.
- No captcha or bot-protection bypass was added.
- No public inventory mode was added.

## Validation

Ran:

```powershell
python test_cloud_no_ai.py
```

Result:

```text
cloud no-ai smoke tests passed
```

Ran:

```powershell
$files = Get-ChildItem -Filter *.py | ForEach-Object { $_.FullName }; py -m py_compile @files
```

Result: PASS.

Additional regression:

```powershell
python test_inventory_beta.py
```

Result:

```text
inventory beta smoke tests passed
```

Direct no-AI extraction check:

- `processor.is_ollama_enabled()` returned `False`.
- `processor.check_ollama_connected()` returned `False` without requiring a model.
- `extractor.extract_answer(...)` returned the deterministic `no_ollama` path instead of calling Ollama.

## Known limitations

- Browser providers are deterministic and best-effort; page layout changes can reduce extraction quality.
- Retailers may block automated lookup; this is handled as unavailable rather than bypassed.
- In-memory progress jobs do not survive process restart.
- SQLite cache/audit state is local to the container instance unless persistent storage is configured.
- Official retailer APIs are still the recommended long-term replacement for browser providers.

## Success condition

SentinelWeb can now be deployed to Railway or Render as a closed-beta FastAPI + Playwright inventory backend with no Ollama dependency.
