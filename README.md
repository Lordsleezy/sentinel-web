# Sentinel Web Agent

**Natural language web automation microservice — 100% local, zero paid APIs.**

Ask questions in plain English. Sentinel Web Agent opens a real browser, navigates the web, and returns a clean answer. Works for price lookups, account checks, stock availability, showtimes, schedules — anything on the public web or sites you have credentials for.

---

## Quick Start

```bash
# 1. Install Python deps
pip install -r requirements.txt
python -m playwright install chromium

# 2. Pull Ollama model (one-time, ~8 GB)
ollama pull qwen2.5-coder:14b

# 3. Start the server
python main.py
# → http://localhost:8766
# → API docs: http://localhost:8766/docs
```

---

## Example Queries

```bash
# Price check on one site
curl -X POST http://localhost:8766/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the current price of RTX 4090 on Newegg?"}'

# Stock check with SKU
curl -X POST http://localhost:8766/query \
  -d '{"query": "Check stock for SKU 6564327 at Best Buy near Sacramento"}'

# Price comparison across 3 sites
curl -X POST http://localhost:8766/query/compare \
  -d '{
    "query": "Price of RTX 4090",
    "sites": ["https://amazon.com", "https://newegg.com", "https://bestbuy.com"]
  }'

# Account check with saved credentials
curl -X POST http://localhost:8766/query \
  -d '{"query": "What is my Chase account balance", "site_name": "chase"}'

# Movie showtimes
curl -X POST http://localhost:8766/query \
  -d '{"query": "What movies are playing near Sacramento tonight"}'
```

---

## Endpoints

### `POST /query`
Natural language query against one website.

**Request body:**
```json
{
  "query":     "What is my CVS prescription status",
  "url":       "https://www.cvs.com",     // optional — omit to let agent find it
  "site_name": "cvs",                      // optional — for saved credentials
  "headless":  true                        // default true; false shows browser
}
```

**Response:**
```json
{
  "answer":         "Your prescription for Lisinopril is ready for pickup.",
  "source_url":     "https://www.cvs.com/pharmacy/status",
  "confidence":     0.87,
  "execution_time": 4.2,
  "cached":         false,
  "login_used":     true,
  "error":          null
}
```

---

### `POST /query/compare`
Compare the same query across multiple sites in parallel.

**Request body:**
```json
{
  "query": "Price of Sony WH-1000XM5 headphones",
  "sites": [
    "https://www.amazon.com",
    "https://www.bestbuy.com",
    "https://www.bhphotovideo.com"
  ]
}
```

**Response:**
```json
{
  "summary":     "Sony WH-1000XM5: Amazon $279, BestBuy $299, B&H $279. Best price: Amazon.",
  "site_results": [
    {"site": "amazon.com",      "answer": "$279.00", "found": true, "confidence": 0.9},
    {"site": "bestbuy.com",     "answer": "$299.99", "found": true, "confidence": 0.8},
    {"site": "bhphotovideo.com","answer": "$279.95", "found": true, "confidence": 0.85}
  ],
  "best_site":    "amazon.com",
  "elapsed_total": 8.1
}
```

---

### `POST /credentials/save`
Save encrypted credentials for a site.

```json
{"site": "chase", "username": "myemail@gmail.com", "password": "mypassword"}
```

Credentials are encrypted with Fernet symmetric encryption using a key derived from your machine ID. They **never leave your device** and are **never logged**.

---

### `GET /credentials/list`
Returns site names only — **never returns passwords**.

```json
{"sites": ["amazon", "bestbuy", "chase", "cvs"]}
```

---

### `DELETE /credentials/{site}`
Remove credentials for a site.

---

### `GET /health`
```json
{
  "status":           "ok",
  "ollama_connected": true,
  "browser_ready":    true,
  "cache_entries":    3,
  "saved_sites":      4
}
```

---

## CLI Options

| Flag | Description |
|------|-------------|
| `--headed` | Show browser window (useful for debugging) |
| `--port N` | Custom port (default 8766) |
| `--no-ollama` | Disable Ollama; use pattern extraction only |
| `--host` | Bind host (default 0.0.0.0) |

---

## Architecture

```
User Query (NL)
       │
       ▼
  processor.py         Parse query: intent, URL, what_to_find, login?
  (Ollama + patterns)
       │
       ├─ compare? ──► comparison.py  (parallel browser sessions)
       │
       ▼
  browser.py           Navigate, dismiss cookie banners, handle login,
  (Playwright)         extract clean text
       │
       ▼
  extractor.py         Extract exact answer from page text
  (Ollama + patterns)
       │
       ▼
  api.py               Cache result, return QueryResponse
```

**browser.py** — Playwright headless Chromium with anti-detection (stealth patches, realistic user-agent, human-like timing), cookie banner auto-dismiss, login form detection and auto-fill, smart content waiting, site memory for repeat visits.

**credentials.py** — Fernet encryption, machine-ID based key derivation (PBKDF2-SHA256), SQLite backend, passwords never exposed in any API response or log.

**processor.py** — Fast pattern matching for common queries (price compare, login, location) + Ollama for complex NL understanding.

**extractor.py** — Regex fast path for prices and stock, Ollama for everything else.

**comparison.py** — asyncio.gather for parallel browser sessions, Ollama for aggregation.

---

## Smart Behaviors

| Behavior | Description |
|----------|-------------|
| **Site Memory** | Records working CSS selectors per domain. Reuses them for faster extraction on repeat visits. |
| **Auto Login** | Detects login forms automatically. Checks credential store and logs in if credentials are saved. |
| **Search Fallback** | If a direct URL fails, falls back to Google/Bing search to find the right page. |
| **Rate Limiting** | Min 2 seconds between requests to the same site. Max 10 concurrent browser sessions. |
| **5-min Cache** | Identical queries within 5 minutes return cached results instantly without re-browsing. |

---

## Security

- All credentials are Fernet-encrypted at rest
- Encryption key is derived from machine ID (PBKDF2-SHA256, 200,000 iterations)
- Key never stored anywhere — re-derived on each process start
- Credentials never appear in logs, API responses, or error messages
- `GET /credentials/list` returns site names only

---

## Running the Tests

```bash
python test_agent.py
```

Tests run entirely offline — no browser, no Ollama, no network needed.

---

## Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen2.5-coder:14b` | Model to use |
| `PORT` | `8766` | Server port |
