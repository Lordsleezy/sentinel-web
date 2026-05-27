# Sentinel Web Agent — Build Report

**Build date:** 2026-05-26  
**Status:** COMPLETE — all 6 test suites passed  

---

## Files Created

| File | Lines | Description |
|------|-------|-------------|
| `browser.py` | ~395 | Playwright engine with stealth, login, site memory |
| `credentials.py` | ~135 | Fernet-encrypted credential store (SQLite) |
| `processor.py` | ~205 | NL query parser — pattern + Ollama fallback |
| `extractor.py` | ~145 | Content extractor — regex + Ollama fallback |
| `comparison.py` | ~180 | Parallel multi-site comparison engine |
| `api.py` | ~210 | FastAPI server, rate limiting, 5-min cache |
| `main.py` | ~80 | Entry point + CLI flags |
| `test_agent.py` | ~225 | 6 test suites, all offline |
| `requirements.txt` | 20 | Pinned deps |
| `.env.example` | 12 | Environment variable docs |
| `README.md` | ~195 | Full API docs with examples |

---

## Test Results

```
Test 1: Credential Store (Encrypted)
  [PASS] save_credentials('testsite', ...)
  [PASS] get_credentials: username decrypted correctly
  [PASS] list_sites: ['testsite']
  [PASS] credential update works
  [PASS] delete_credentials works
  [PASS] Password never exposed in list_sites()
  [PASS] Credential store: ALL TESTS PASSED

Test 2: Query Processor (pattern-based, no Ollama)
  [PASS] Price comparison query parsed
  [PASS] Login query detected correctly (Chase account balance)
  [PASS] Location extracted: Sacramento (near Sacramento tonight)
  [PASS] CVS prescription query parsed
  [PASS] Query processor: ALL TESTS PASSED

Test 3: Content Extractor (pattern-based, no Ollama)
  [PASS] Price extracted: $1599.00
  [PASS] Stock status extracted: In Stock
  [PASS] Out-of-stock detected: Out of Stock
  [PASS] Empty page handled correctly
  [PASS] Extractor: ALL TESTS PASSED

Test 4: Browser Engine (unit tests)
  [PASS] get_domain(): all correct
  [PASS] Site memory: remember + recall works
  [PASS] Stealth script contains anti-detection patches
  [PASS] Browser engine units: ALL TESTS PASSED

Test 5: API Models & Cache
  [PASS] Cache key is deterministic
  [PASS] Cache put/get works
  [PASS] Cache miss returns None
  [PASS] API models: ALL TESTS PASSED

Test 6: 3 Sample Queries — Dry Run (no network)
  [PASS] Best Buy stock check — In Stock extracted
  [PASS] RTX 4090 price — $1599.00 extracted
  [PASS] Movie showtimes — processed (needs Ollama for full extraction)

ALL 6 TEST SUITES PASSED -- OK
```

---

## Architecture Notes

### browser.py

**Anti-detection stack:**
- `--disable-blink-features=AutomationControlled` Chromium flag
- `STEALTH_SCRIPT` injected via `add_init_script()` on every page:
  overrides `navigator.webdriver`, fakes `navigator.plugins` (3 entries),
  sets `navigator.languages`, patches `Permissions.query` for notifications
- Rotating user-agents (3 realistic Chrome 130/131 strings)
- Viewport 1366×768 (most common desktop), `en-US` locale, LA timezone
- `ignore_https_errors=True` for sites with cert issues

**Cookie banner dismissal:**
- 20+ selectors covering most CMPs: OneTrust, CookieBot, Quantcast, generic
- Text-based selectors (`:has-text()`) as the most reliable cross-site fallback
- Runs before and after content wait

**Login detection:**
- Password field presence check
- URL pattern matching (`/login`, `/signin`, `/auth`)
- Page title analysis
- Body text phrase search (`"please sign in"`, etc.)
- On detection: fills username → password with human-like delays → submits
- Credentials are NEVER logged at any log level (INFO, DEBUG, or WARNING)

**Site memory** (`data/site_memory.db`):
- `remember_selector(domain, query_type, selector)` — increments success count
- `recall_selectors(domain, query_type)` — returns top-5 most successful selectors
- Used by the extraction layer to try known-good selectors first

**Text extraction:**
- Clones DOM, removes script/style/nav/header/footer/ads/hidden elements
- Walks remaining nodes, preserves block-level line breaks
- Strips blank lines, truncates to 8,000 chars (Ollama context budget)

### credentials.py

**Encryption design:**
1. Machine ID sourced from Windows Registry `MachineGuid` → `/etc/machine-id` → MAC address
2. PBKDF2-SHA256 with 200,000 iterations and fixed salt `b"sentinel_web_cred_v1"`
3. Produces 32-byte key → Fernet
4. Both username and password encrypted separately
5. Fernet singleton derived once per process startup
6. `ON CONFLICT DO UPDATE` ensures upsert semantics

**Zero-leakage guarantees:**
- `get_credentials()` returns plaintext dict in memory only, never stored
- `list_sites()` queries `site` column only — password columns never selected
- No log statement anywhere in the module contains credential values

### processor.py

**Two-stage parsing:**
1. **Pattern fallback** (fast, zero Ollama): detects 15 known sites (bestbuy, amazon, chase, cvs, ...), compare keywords, login keywords, location via 3 regex patterns
2. **Ollama** (for unknown queries): structured JSON output with intent/URL/what_to_find

The pattern stage now always returns a ParsedQuery for queries containing any known signal (site, login, compare, or location). Unknown query types fall through to Ollama.

**`_SITE_URL_MAP`** maps short site identifiers (as used in credential store) to base URLs — allows the pattern stage to construct a `target_url` without Ollama.

### extractor.py

**Two-stage extraction:**
1. **Regex fast path**: `_extract_price_pattern()` (5 patterns, returns first match in range $0.01–$100k), `_extract_stock_pattern()` (8 status patterns)
2. **Ollama fallback**: structured JSON `{answer, found, confidence, source_hint}`

### comparison.py

**Parallel execution:**
- `asyncio.Semaphore(MAX_PARALLEL_SESSIONS=5)` caps concurrency
- `asyncio.gather()` runs all site sessions simultaneously
- Each site gets its own `BrowserEngine` instance (separate Chromium process)
- Credentials map allows per-domain login

**Aggregation:**
- Ollama produces a 2-3 sentence natural language comparison summary
- `_rule_based_aggregate()` fallback: extracts prices via regex, finds minimum, formats as `"Site: $price"` list
- Best site determined by minimum detected price

### api.py

**Rate limiting:** Per-domain timestamp dict + `asyncio.sleep()` when < 2s since last request to same domain.

**Cache:** MD5 hash of `query|url` → `{result, expires}` dict. 5-minute TTL. Only caches successful (`found=True`) results.

**Request concurrency:** `asyncio.Semaphore(10)` limits max simultaneous browser sessions. Requests wait in the semaphore queue rather than failing.

---

## Startup Sequence

```
python main.py
  ↓ uvicorn.run(api.app, ...)
  ↓ @app.on_event("startup")
     → check_ollama_connected()  — logs warning if Ollama unreachable
     → credentials.init_db()     — creates tables if needed
     → browser._init_site_memory() — creates site_memory.db tables
  ↓ Server ready on :8766
  ↓ /docs available at http://localhost:8766/docs
```

---

## What Needs Ollama

| Feature | Without Ollama | With Ollama |
|---------|---------------|-------------|
| Price extraction | Regex (fast) | AI extraction (fallback) |
| Stock extraction | Regex (fast) | AI extraction (fallback) |
| Login detection | URL/DOM pattern | N/A (purely structural) |
| Query parsing | 15 known sites + keywords | Full NL understanding |
| Comparison summary | Rule-based sentence | Natural language summary |
| General questions | Not supported | Full extraction |

The server starts and works without Ollama for common patterns. For general queries like "what movies are playing tonight", Ollama is required for meaningful extraction.

---

## Quick-Start Without Ollama

```bash
python main.py --no-ollama
```

Works for: price extraction, stock checks, login flows.  
Does not work for: general NL queries, schedules, complex page content.

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | 0.115.5 | REST API framework |
| `uvicorn` | 0.32.1 | ASGI server |
| `httpx` | 0.27.2 | Ollama HTTP calls |
| `playwright` | 1.49.0 | Browser automation |
| `cryptography` | 43.0.3 | Fernet credential encryption |
| `pydantic` | 2.10.3 | Request/response models |
| `python-dotenv` | 1.0.1 | .env loading |
