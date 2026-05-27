# SentinelWeb Inventory Closed Beta Report

## What changed

- Added authenticated private-beta inventory endpoints under `/inventory/*`.
- Added SQLite-backed beta user allowlist in `data/inventory_beta.db`.
- Added env seeding via `SENTINEL_BETA_USERS` and optional local bearer tokens via `SENTINEL_BETA_API_KEYS`.
- Added per-user limit of 10 inventory searches per hour.
- Added total in-process inventory concurrency limit of 3 active searches.
- Added per-retailer spacing of 10 seconds between browser lookups.
- Added 12-minute inventory cache, configurable with `SENTINEL_INVENTORY_CACHE_TTL_S`.
- Added audit logging for user, query, providers, success/failure, cache hit/miss, and execution time.
- Added modular browser providers:
  - `bestbuy_browser_provider.py`
  - `target_browser_provider.py`
  - `walmart_browser_provider.py`
  - `amazon_browser_provider.py`
- Added PWA Inventory mode with visible progress polling.

## Auth model

Inventory endpoints reject anonymous requests. A request must map to an active beta user through either:

- `Cf-Access-Authenticated-User-Email`, intended for production behind Cloudflare Access.
- `X-Sentinel-User-Email`, intended for local/internal testing.
- `Authorization: Bearer <token>`, when a token hash is seeded by `SENTINEL_BETA_API_KEYS`.

If no invited users exist, inventory endpoints return a setup error rather than allowing anonymous access.

## Provider behavior

The inventory providers use plain Playwright browser automation only. They do not use official retailer APIs yet, and they do not attempt to solve or bypass captchas, security challenges, blocked pages, login walls, or rate-limited pages.

When a provider appears blocked, challenged, rate-limited, or login-walled, it returns:

`Unavailable — retailer blocked automated lookup`

## Follow-up

- Official retailer APIs can replace each browser provider behind the same provider interface.
- If progress needs to survive process restarts, persist job state instead of keeping active job progress in memory.
- Cloudflare Access should be configured to pass authenticated user email to the origin.
