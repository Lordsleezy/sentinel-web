# SentinelWeb Inventory Beta Validation Report

Validation date: 2026-05-27

## Runtime

- Started SentinelWeb locally on port `8766`.
- Backend process PID during validation: `18708`.
- `.env` was created with:
  - `SENTINEL_BETA_USERS=test@example.com`
  - `SENTINEL_INVENTORY_CACHE_TTL_S=720`

## Endpoint checks

- `GET /health`: PASS
  - Returned `status: ok`
  - Returned `ollama_connected: true`
  - Returned `browser_ready: true`

- Anonymous `GET /inventory/providers`: PASS after hardening
  - Required result: `403`
  - Actual result: `403`
  - Note: first validation pass returned `401`; hardened `inventory_auth.py` to fail closed with `403` for anonymous inventory access.

- Authenticated `GET /inventory/providers` with `X-Sentinel-User-Email: test@example.com`: PASS
  - Returned private closed-beta provider list.
  - Returned configured limits:
    - Max users: `15`
    - Searches/user/hour: `10`
    - Concurrent searches total: `3`
    - Retailer spacing: `10s`
    - Cache TTL: `720s`

## Inventory search validation

Test query:

- Product: `Best Buy SKU 6533161`
- Location: `10001`
- Providers: `bestbuy`

Initial API search:

- `POST /inventory/search`: PASS
  - Returned `202`
  - Returned `status: searching`
  - Returned a `search_id`

Progress polling:

- `GET /inventory/search/{search_id}`: PASS
  - Observed progress states:
    - `searching`
    - `opening retailer`
    - `checking store availability`
    - `extracting price`
    - `completed`

Final result:

- Status: `completed`
- Provider: `bestbuy`
- Availability: `In Stock`
- Price: `$200.00, $249.99, $250.00`
- Source URL: `https://www.bestbuy.com/site/searchpage.jsp?st=Best+Buy+SKU+6533161+10001`
- Execution time: `38.89s`

Headed browser validation:

- Ran `BestBuyBrowserProvider(headless=False)` directly for the same SKU/location.
- Visible browser run completed.
- Observed provider progress:
  - `opening retailer`
  - `checking store availability`
  - `extracting price`
- Result: `completed`

## Cache validation

Repeated the same product/location/provider query.

- Result: PASS
- Returned immediately with:
  - `status: completed`
  - `cache_hit: true`
  - `search_id` beginning with `cache-`

## Rate-limit validation

Seeded the rate counter for `test@example.com` to the configured threshold, then submitted a new uncached inventory request.

- Result: PASS
- Returned `429`
- Message: `Rate limit exceeded: max 10 inventory searches per hour.`

Cleanup:

- Synthetic rate-limit rows for `test@example.com` were cleared after validation so the test account remains usable.

## Audit log validation

Queried `data/inventory_beta.db`.

- Result: PASS
- Audit log recorded:
  - User: `test@example.com`
  - Query: `Best Buy SKU 6533161 near 10001`
  - Providers checked: `["bestbuy"]`
  - Cache miss entry with execution time `38.89`
  - Cache hit entry with execution time `0.0`
  - Success flag recorded as `1`

## Provider failure handling

Simulated a retailer block/captcha page inside the provider path.

- Result: PASS
- Provider returned clean status:
  - `Unavailable — retailer blocked automated lookup`
- Provider did not attempt captcha solving, bot bypass, proxy rotation, or login-wall circumvention.

## Closed-beta posture

- Inventory endpoints remain private and authenticated.
- Anonymous inventory access is denied.
- No public inventory route was added.
- No official retailer APIs were added.
- No captcha or bot-protection bypass was added.
- No proxy rotation was added.
