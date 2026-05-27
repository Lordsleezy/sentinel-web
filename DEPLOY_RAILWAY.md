# Deploy SentinelWeb Backend to Railway

SentinelWeb now deploys to Railway as a closed-beta FastAPI + Playwright + tiny Ollama service.

## Architecture

- Frontend: Netlify at `https://sentinelprime.org`
- Backend: Railway Docker service
- Browser automation: Playwright Chromium inside Docker
- AI helper: Ollama with `llama3.2:1b`
- Access: Cloudflare Access or another trusted identity layer, plus SentinelWeb's own invited-user allowlist

Inventory flow:

```text
FastAPI request
-> required tiny AI helper parses product/SKU/location
-> Playwright providers run deterministic extraction
-> tiny AI helper summarizes structured provider results
-> FastAPI returns provider_results, ai_summary, confidence, cache_hit, progress
```

The AI helper never receives raw HTML, cookies, credentials, or full page dumps. It receives only the cleaned user query and structured provider result objects.

## Railway setup

1. Create a Railway project from the SentinelWeb repo.
2. Use Dockerfile deployment. Railway recommends Docker for Playwright because Chromium needs system dependencies.
3. Confirm Railway detects `railway.json`.
4. The Docker image installs:
   - Python dependencies
   - Playwright Chromium and OS dependencies
   - Ollama
5. Railway starts `/app/start.sh`.

`start.sh` will:

1. Start `ollama serve` in the background.
2. Wait for Ollama on port `11434`.
3. Pull `llama3.2:1b` if missing.
4. Verify the model responds.
5. Start FastAPI with:

   ```sh
   uvicorn api:app --host 0.0.0.0 --port $PORT
   ```

First boot may take longer because Railway must download the model.

## Required Railway variables

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

## Memory expectations

`llama3.2:1b` is the target because it is small enough for a lightweight closed-beta service. Expect higher memory usage during first model load and while Playwright Chromium is running. Keep total concurrent searches capped at 3.

If memory pressure appears in Railway logs:

- Keep `AI_HELPER_MODEL=llama3.2:1b`.
- Keep `HEADLESS=true`.
- Reduce beta users or concurrent usage.
- Do not switch to 7B/14B models.

## Health check

Use:

```text
/health
```

Healthy response should include:

```json
{
  "status": "ok",
  "ai_helper_required": true,
  "ai_helper_connected": true,
  "ai_helper_model": "llama3.2:1b",
  "ollama_status": "connected",
  "browser_ready": true,
  "inventory_ready": true
}
```

If the AI helper is unavailable, the app still starts but health reports `status: degraded` and `inventory_ready: false`. Inventory requests return:

```text
503 AI helper unavailable
```

## Auth

Inventory endpoints fail closed unless a request maps to an active invited user.

Preferred production header from Cloudflare Access:

```http
Cf-Access-Authenticated-User-Email: user@example.com
```

Local/internal test header:

```http
X-Sentinel-User-Email: user@example.com
```

The email must exist in `SENTINEL_BETA_USERS`.

## Logs

Check Railway deploy logs for:

- `Starting Ollama helper...`
- `Waiting for Ollama...`
- `Pulling tiny AI helper model llama3.2:1b...`
- `Verifying tiny AI helper model...`
- `Starting SentinelWeb...`

Runtime app logs will show degraded startup if the AI helper is required but unavailable.

## Emergency AI disable

For emergency diagnostics only:

```env
AI_HELPER_REQUIRED=false
```

Do not use this for normal beta operation. The intended Railway mode requires the tiny AI helper.

## Safety

- No anonymous inventory access.
- No proxy rotation.
- No captcha bypass.
- No public scraping mode.
- Rate limits and cache stay enabled.
- Captcha, bot challenge, login wall, and retailer block pages return unavailable rather than being bypassed.
