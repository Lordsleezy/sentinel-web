# SentinelWeb Tiny AI Railway Report

Report date: 2026-05-27

## Goal

Prepare SentinelWeb for Railway deployment as a closed-beta FastAPI + Playwright + tiny Ollama service, with `llama3.2:1b` required for inventory request understanding and result summarization.

## What changed

- Added `ai_helper.py`.
- Added required tiny AI config defaults:
  - `AI_HELPER_ENABLED=true`
  - `AI_HELPER_REQUIRED=true`
  - `AI_HELPER_PROVIDER=ollama`
  - `AI_HELPER_MODEL=llama3.2:1b`
  - `AI_HELPER_HOST=http://127.0.0.1:11434`
  - `AI_HELPER_TIMEOUT_S=10`
- Added AI helper functions:
  - `ai_available()`
  - `ensure_model_available()`
  - `parse_inventory_query(query)`
  - `summarize_inventory_results(query, provider_results)`
- Inventory search now:
  - Parses natural-language inventory requests with the required AI helper.
  - Runs deterministic browser providers.
  - Sends only structured provider result objects to the AI helper.
  - Adds `ai_parse`, `ai_summary`, `confidence`, and `provider_results` to job output.
- If the required AI helper is unavailable:
  - App still starts.
  - `/health` reports degraded/unready inventory state.
  - Inventory search returns `503 AI helper unavailable`.

## Safety boundaries

The AI helper does not receive:

- Raw HTML
- Cookies
- Credentials
- Full page dumps
- Browser storage

It receives only:

- Cleaned user query text
- Structured provider result fields:
  - provider
  - status
  - availability
  - price
  - product
  - location
  - source_url
  - confidence
  - error

No proxy rotation, captcha bypass, bot-protection bypass, public inventory mode, auth removal, cache removal, or rate-limit removal was added.

## Health behavior

`/health` now includes:

- `ai_helper_required`
- `ai_helper_connected`
- `ai_helper_model`
- `ollama_status`
- `browser_ready`
- `inventory_ready`

When the required helper is unavailable, health reports:

```json
{
  "status": "degraded",
  "ai_helper_required": true,
  "ai_helper_connected": false,
  "ai_helper_model": "llama3.2:1b",
  "ollama_status": "unavailable",
  "browser_ready": true,
  "inventory_ready": false
}
```

## Railway deployment

Added/updated:

- `Dockerfile`
- `start.sh`
- `railway.json`
- `DEPLOY_RAILWAY.md`

The Docker image installs:

- Python dependencies
- Playwright Chromium and dependencies
- Ollama

`start.sh`:

1. Starts `ollama serve` in the background.
2. Waits for Ollama on port `11434`.
3. Pulls `llama3.2:1b` if missing.
4. Verifies the model responds.
5. Starts Uvicorn on `$PORT`.

Railway start command:

```sh
/app/start.sh
```

First boot may take longer because Railway must download `llama3.2:1b`.

## Required Railway env

```env
AI_HELPER_ENABLED=true
AI_HELPER_REQUIRED=true
AI_HELPER_PROVIDER=ollama
AI_HELPER_MODEL=llama3.2:1b
AI_HELPER_HOST=http://127.0.0.1:11434
AI_HELPER_TIMEOUT_S=10
OLLAMA_ENABLED=false
HEADLESS=true
ALLOWED_ORIGINS=https://sentinelprime.org,https://www.sentinelprime.org,http://localhost:5173,http://127.0.0.1:5173
SENTINEL_BETA_USERS=user1@example.com,user2@example.com
SENTINEL_AUTH_TOKEN=
SENTINEL_INVENTORY_CACHE_TTL_S=720
```

Do not use `qwen2.5-coder:14b`, 7B, or 14B models on Railway. Use `llama3.2:1b` first. Try `llama3.2:3b` only if 1B is too weak and the Railway plan has enough memory.

## Tests

Ran:

```powershell
python test_required_tiny_ai.py
```

Result:

```text
required tiny AI tests passed
```

Ran:

```powershell
$files = Get-ChildItem -Filter *.py | ForEach-Object { $_.FullName }; py -m py_compile @files
```

Result: PASS.

Regression:

```powershell
python test_inventory_beta.py
```

Result:

```text
inventory beta smoke tests passed
```

## Success condition

SentinelWeb is ready to deploy to Railway as a closed-beta FastAPI + Playwright + tiny Ollama model service, with `llama3.2:1b` required for inventory request understanding and provider-result summarization.
