# SentinelWeb Closed-Beta Deployment Plan

## Goal

Deploy SentinelWeb as a private, invite-only closed beta for no more than 15 users. Inventory lookup remains authenticated, rate-limited, cached, audited, and browser-provider based until official retailer APIs are added later.

## Frontend route

- Serve the PWA at `https://sentinelprime.org/app/` or `https://app.sentinelprime.org/app/`.
- The current FastAPI app already mounts `webapp/` at `/app/`.
- Keep the frontend same-origin with the backend so PWA calls can use relative routes:
  - `/health`
  - `/inventory/providers`
  - `/inventory/search`
  - `/inventory/search/{search_id}`

Netlify is not recommended for this first closed beta unless it reverse-proxies all API traffic back to the protected backend and preserves Cloudflare Access identity headers. The simplest safe deployment is one protected origin behind Cloudflare Tunnel.

## Backend hosting recommendation

- Run FastAPI on the Windows dev box or a small private VM.
- Bind the app locally on port `8766`.
- Expose it through Cloudflare Tunnel.
- Put Cloudflare Access in front of the hostname before traffic reaches FastAPI.
- Do not expose the origin port publicly.

Recommended command:

```powershell
py main.py --host 127.0.0.1 --port 8766
```

Cloudflare Tunnel should route the public hostname to `http://127.0.0.1:8766`.

## Required environment variables

```env
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5-coder:14b
PORT=8766
SENTINEL_BETA_USERS=user1@example.com,user2@example.com
SENTINEL_INVENTORY_CACHE_TTL_S=720
```

Optional for local/API testing only:

```env
SENTINEL_BETA_API_KEYS=user1@example.com:replace-with-long-random-token
```

Keep `.env` local. It is ignored by `.gitignore`.

## Cloudflare Access setup

1. Create a Cloudflare Access application for the SentinelWeb hostname.
2. Restrict access to the invited closed-beta users.
3. Keep the invite list to 15 users or fewer.
4. Require identity provider login before users can reach the app.
5. Ensure Cloudflare Access forwards authenticated identity headers to the origin.

FastAPI reads the authenticated user from:

```http
Cf-Access-Authenticated-User-Email: user@example.com
```

That email must also be present in `SENTINEL_BETA_USERS`; Cloudflare authentication alone is not enough. The backend allowlist remains the final gate.

## Local test header behavior

For local testing without Cloudflare Access, requests may use:

```http
X-Sentinel-User-Email: test@example.com
```

This only works if `test@example.com` is in `SENTINEL_BETA_USERS`. Anonymous and non-invited users receive `403`.

## Closed-beta allowlist setup

Set invited users in `.env`:

```env
SENTINEL_BETA_USERS=alice@example.com,bob@example.com
```

On startup, SentinelWeb seeds active beta users into `data/inventory_beta.db`. If there are no invited users, inventory endpoints fail closed with `403`.

## Safety defaults

- Anonymous inventory access is blocked.
- No invited users means inventory is disabled/fails closed.
- Per-user limit is 10 inventory searches per hour.
- Total active inventory searches are capped at 3.
- Same product/location/provider results are cached for 12 minutes by default.
- Each retailer has a 10-second spacing guard.
- Captcha, block, rate-limit, login-wall, or security-challenge pages return:

```text
Unavailable — retailer blocked automated lookup
```

No proxy rotation, captcha bypass, bot-protection bypass, or public anonymous access should be added for the beta.
