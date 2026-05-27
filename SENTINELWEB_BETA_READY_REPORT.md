# SentinelWeb Beta Ready Report

Report date: 2026-05-27

## Status

SentinelWeb is ready for a private closed beta behind Cloudflare Access and the backend invite allowlist. It is not configured for public anonymous use.

## What works

- FastAPI backend serves the PWA at `/app/`.
- `/health` returns OK.
- Inventory endpoints exist under `/inventory/*`.
- The PWA includes an Inventory tab with:
  - Product/SKU input.
  - Location input.
  - Retailer selection.
  - Progress polling.
  - Cache-hit indicator.
  - Readable provider failure messages.
- Mobile render check at `390x844` passed with no horizontal overflow.
- Smoke test script passes with mocked providers:
  - `/health` OK.
  - Anonymous inventory access returns `403`.
  - Invited user can access providers.
  - Invalid user returns `403`.
  - Cache hit works on repeat query.
  - Rate limit returns `429`.

## What is protected

- `.gitignore` now excludes:
  - `.env`
  - local DB files
  - logs
  - screenshots
  - bytecode caches
- `.env.example` contains only placeholder values.
- Inventory auth fails closed:
  - No invited users means inventory endpoints return `403`.
  - Anonymous users return `403`.
  - Non-allowlisted users return `403`.
- Inventory requests require one of:
  - `Cf-Access-Authenticated-User-Email`
  - `X-Sentinel-User-Email` for local testing
  - Optional local bearer token seeded through `SENTINEL_BETA_API_KEYS`
- Safety limits remain active:
  - 10 searches per user per hour.
  - 3 concurrent inventory searches total.
  - 10 seconds between requests to the same retailer.
  - 12-minute cache by default.
- Captcha, block, rate-limit, login-wall, or security-challenge pages return:

```text
Unavailable — retailer blocked automated lookup
```

No proxy rotation, captcha bypass, bot-protection bypass, or public anonymous route was added.

## How to deploy

Use one protected origin for the closed beta:

1. Run SentinelWeb on the host:

   ```powershell
   py main.py --host 127.0.0.1 --port 8766
   ```

2. Expose `http://127.0.0.1:8766` through Cloudflare Tunnel.
3. Put Cloudflare Access in front of `sentinelprime.org` or `app.sentinelprime.org`.
4. Configure Cloudflare Access to allow only invited beta users.
5. Ensure the authenticated user email reaches FastAPI as:

   ```http
   Cf-Access-Authenticated-User-Email: user@example.com
   ```

6. Add the same invited emails to `.env`:

   ```env
   SENTINEL_BETA_USERS=user1@example.com,user2@example.com
   ```

7. Open the PWA at:

   ```text
   https://sentinelprime.org/app/
   ```

See `DEPLOYMENT_PLAN.md` for the full plan.

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

## Official APIs still needed later

The current provider modules use browser automation only:

- `bestbuy_browser_provider.py`
- `target_browser_provider.py`
- `walmart_browser_provider.py`
- `amazon_browser_provider.py`

Official retailer APIs should replace those modules later behind the existing provider interface. The service layer already isolates auth, rate limits, caching, audit logging, job status, and progress from provider implementation details.

## Known limitations

- Browser automation may be blocked or challenged by retailers.
- Provider extraction is best-effort and can misread page content.
- Progress jobs are in memory and do not survive backend restarts.
- Audit logs and cache are stored in local SQLite.
- Local PWA testing of inventory endpoints needs the test header or Cloudflare Access equivalent; production should rely on Cloudflare Access identity headers.
- This remains a small closed beta design, not a public scraping platform.

## Validation commands

Requested smoke command:

```powershell
python test_inventory_beta.py
```

Result: PASS.

The literal command below does not work on this Windows shell because `*.py` is passed to Python as a literal path:

```powershell
py -m py_compile *.py
```

Equivalent expanded compile check:

```powershell
$files = Get-ChildItem -Filter *.py | ForEach-Object { $_.FullName }; py -m py_compile @files
```

Result: PASS.

## Next recommended step

Put the app behind Cloudflare Access on the beta hostname, invite one internal tester, and run one supervised inventory lookup from the installed PWA before inviting the rest of the closed-beta group.
