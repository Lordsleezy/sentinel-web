# Deploy SentinelWeb Backend to Railway

This deployment runs SentinelWeb as a closed-beta FastAPI + Playwright backend with Ollama disabled by default.

## Architecture

- Frontend: Netlify at `https://sentinelprime.org`
- Backend: Railway Docker service
- Browser automation: Playwright Chromium inside Docker
- AI/Ollama: disabled unless `OLLAMA_ENABLED=true`
- Access: Cloudflare Access or another trusted identity layer in front of the backend, plus SentinelWeb's own invited-user allowlist

## Railway setup

1. Create a new Railway project from the SentinelWeb repository.
2. Use Dockerfile deployment. Railway recommends Docker for Playwright because Chromium requires system dependencies.
3. Confirm Railway detects `railway.json`.
4. Set the start command:

   ```sh
   uvicorn api:app --host 0.0.0.0 --port $PORT
   ```

## Required Railway variables

```env
OLLAMA_ENABLED=false
HEADLESS=true
ALLOWED_ORIGINS=https://sentinelprime.org,https://www.sentinelprime.org,http://localhost:5173,http://127.0.0.1:5173
SENTINEL_BETA_USERS=user1@example.com,user2@example.com
SENTINEL_AUTH_TOKEN=
SENTINEL_INVENTORY_CACHE_TTL_S=720
```

Do not set real secrets in `.env.example`.

## Health check

Use:

```text
/health
```

Expected no-AI response includes:

```json
{
  "ollama_enabled": false,
  "ollama_connected": false,
  "browser_ready": true,
  "inventory_ready": true
}
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

## Safety

- No anonymous inventory access.
- No proxy rotation.
- No captcha bypass.
- No public scraping mode.
- Rate limits and cache stay enabled.
