# Deploy SentinelWeb Backend to Render

Render should use Docker for SentinelWeb because Playwright Chromium and the tiny Ollama helper require system dependencies.

## Architecture

- Frontend: Netlify at `https://sentinelprime.org`
- Backend: Render Docker web service
- Browser automation: Playwright Chromium inside Docker
- AI helper: Ollama with `llama3.2:1b`
- Auth: Cloudflare Access or equivalent in front of the backend, plus SentinelWeb's invited-user allowlist

## Render setup

1. Create a new Render Web Service.
2. Choose Docker environment.
3. Use `Dockerfile`.
4. Optionally use `render.yaml` for infrastructure-as-code.
5. Health check path:

   ```text
   /health
   ```

## Required Render variables

```env
OLLAMA_ENABLED=false
AI_HELPER_ENABLED=true
AI_HELPER_REQUIRED=true
AI_HELPER_PROVIDER=ollama
AI_HELPER_MODEL=llama3.2:1b
AI_HELPER_HOST=http://127.0.0.1:11434
AI_HELPER_TIMEOUT_S=10
HEADLESS=true
ALLOWED_ORIGINS=https://sentinelprime.org,https://www.sentinelprime.org,http://localhost:5173,http://127.0.0.1:5173
SENTINEL_BETA_USERS=user1@example.com,user2@example.com
SENTINEL_AUTH_TOKEN=
SENTINEL_INVENTORY_CACHE_TTL_S=720
```

Render provides `PORT`; the Docker command uses it automatically.

## Auth headers

SentinelWeb accepts:

```http
Cf-Access-Authenticated-User-Email: user@example.com
```

For local/internal testing only:

```http
X-Sentinel-User-Email: user@example.com
```

The backend still checks `SENTINEL_BETA_USERS`, so Cloudflare/Render reachability alone does not grant inventory access.

## Tiny AI behavior

With `AI_HELPER_REQUIRED=true`:

- The app starts even if Ollama is unavailable, but `/health` reports degraded.
- Inventory requests return `503 AI helper unavailable` until the model is ready.
- Provider extraction remains deterministic.
- The AI helper only sees cleaned query text and structured provider results.
