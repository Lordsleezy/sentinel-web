# Deploy SentinelWeb Backend to Render

Render should use Docker for SentinelWeb because Playwright Chromium requires browser and system dependencies.

## Architecture

- Frontend: Netlify at `https://sentinelprime.org`
- Backend: Render Docker web service
- Browser automation: Playwright Chromium inside Docker
- AI/Ollama: disabled by default with `OLLAMA_ENABLED=false`
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

## No-AI behavior

With `OLLAMA_ENABLED=false`:

- Startup does not check Ollama.
- `/health` reports `ollama_enabled: false`.
- Inventory providers use deterministic extraction only.
- Future Ollama support remains available by setting `OLLAMA_ENABLED=true`.
