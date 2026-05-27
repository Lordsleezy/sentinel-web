"""
api.py — FastAPI server for Sentinel Web Agent (port 8766)

Endpoints:
  POST /query              Natural language web query
  POST /query/compare      Multi-site comparison
  POST /credentials/save   Save encrypted credentials
  GET  /credentials/list   List saved site names (no passwords)
  DELETE /credentials/{site}
  GET  /health             Status check
"""
import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import credentials as cred_store
import processor
import extractor
import comparison as comp_engine
import inventory_service
import inventory_store
from inventory_auth import require_inventory_user
from browser import BrowserEngine, get_domain

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sentinel Web Agent",
    version="1.0.0",
    description="Natural language web automation microservice",
    docs_url="/docs",
    redoc_url=None,
)

WEBAPP_DIR = Path(__file__).parent / "webapp"


def _allowed_origins() -> List[str]:
    raw = os.getenv(
        "ALLOWED_ORIGINS",
        "https://sentinelprime.org,https://www.sentinelprime.org,"
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Cf-Access-Authenticated-User-Email", "X-Sentinel-User-Email"],
)


@app.get("/app", include_in_schema=False)
async def webapp_redirect():
    return RedirectResponse(url="/app/")


app.mount("/app", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")

# ─── Rate limiting ────────────────────────────────────────────────────────────

_site_last_access: Dict[str, float] = {}  # domain → last access timestamp
_SITE_RATE_LIMIT_S = 2.0                  # Min seconds between same-site requests
_browser_semaphore = asyncio.Semaphore(10) # Max 10 concurrent browser sessions
_request_queue: asyncio.Queue = None       # Populated on startup


async def _check_rate_limit(domain: str):
    """Enforce minimum 2-second gap between requests to the same site."""
    now = time.monotonic()
    last = _site_last_access.get(domain, 0.0)
    wait = _SITE_RATE_LIMIT_S - (now - last)
    if wait > 0:
        logger.debug(f"Rate limit: sleeping {wait:.1f}s for {domain}")
        await asyncio.sleep(wait)
    _site_last_access[domain] = time.monotonic()


# ─── In-memory cache ──────────────────────────────────────────────────────────

_cache: Dict[str, Dict] = {}      # query_hash → {result, expires}
CACHE_TTL_S = 300                  # 5 minutes


def _cache_key(query: str, url: Optional[str]) -> str:
    raw = f"{query}|{url or ''}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[Dict]:
    entry = _cache.get(key)
    if entry and time.monotonic() < entry["expires"]:
        return entry["result"]
    if entry:
        del _cache[key]   # Expired
    return None


def _cache_put(key: str, result: Dict):
    _cache[key] = {"result": result, "expires": time.monotonic() + CACHE_TTL_S}


# ─── Pydantic models ──────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query:     str
    url:       Optional[str]  = None
    site_name: Optional[str]  = None
    headless:  bool           = True

class QueryResponse(BaseModel):
    answer:         str
    source_url:     str
    confidence:     float
    execution_time: float
    cached:         bool = False
    login_used:     bool = False
    error:          Optional[str] = None

class CompareRequest(BaseModel):
    query: str
    sites: List[str]
    headless: bool = True

class CredentialSaveRequest(BaseModel):
    site:     str
    username: str
    password: str

class InventorySearchRequest(BaseModel):
    product: str = Field(..., min_length=2, max_length=200)
    location: str = Field(default="", max_length=120)
    providers: Optional[List[str]] = None

class InventorySearchAccepted(BaseModel):
    search_id: str
    status: str
    cache_hit: bool = False
    result: Optional[Dict[str, Any]] = None


# ─── Query endpoint ───────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
async def query_endpoint(req: QueryRequest):
    t0 = time.monotonic()

    # ── Cache check ──────────────────────────────────────────────────────────
    cache_key = _cache_key(req.query, req.url)
    cached = _cache_get(cache_key)
    if cached:
        logger.info(f"Cache hit: {req.query[:50]}")
        return QueryResponse(**cached, cached=True, execution_time=round(time.monotonic()-t0, 2))

    # ── Parse query ──────────────────────────────────────────────────────────
    parsed = processor.parse_query(req.query)

    # Override with explicit URL/site from request
    if req.url:
        parsed.target_url = req.url
    if req.site_name:
        parsed.site_name = req.site_name

    # ── Handle compare intent ────────────────────────────────────────────────
    if parsed.intent == "compare" and parsed.sites:
        comp_result = await comp_engine.run_comparison(parsed, parsed.sites)
        result = {
            "answer":         comp_result["summary"],
            "source_url":     ", ".join(s["url"] for s in comp_result["site_results"][:3]),
            "confidence":     0.8,
            "execution_time": round(time.monotonic() - t0, 2),
            "login_used":     False,
        }
        _cache_put(cache_key, result)
        return QueryResponse(**result)

    # ── Fetch credentials if needed ──────────────────────────────────────────
    site_creds = None
    if parsed.requires_login or parsed.site_name:
        site_key = parsed.site_name or (get_domain(parsed.target_url) if parsed.target_url else None)
        if site_key:
            site_creds = cred_store.get_credentials(site_key)

    # ── Run browser ──────────────────────────────────────────────────────────
    target_domain = get_domain(parsed.target_url or "") if (parsed.target_url or "") else "unknown"
    await _check_rate_limit(target_domain)

    async with _browser_semaphore:
        engine = BrowserEngine(headless=req.headless)
        await engine.start()
        try:
            browser_result = await engine.run_query(
                url=parsed.target_url,
                search_terms=parsed.search_terms,
                what_to_find=parsed.what_to_find,
                credentials=site_creds,
                headless=req.headless,
            )
        finally:
            await engine.stop()

    # ── Extract answer ───────────────────────────────────────────────────────
    if browser_result.get("error"):
        return QueryResponse(
            answer=browser_result["error"],
            source_url=browser_result.get("final_url", ""),
            confidence=0.0,
            execution_time=round(time.monotonic() - t0, 2),
            error=browser_result["error"],
        )

    extracted = extractor.extract_answer(
        page_text=browser_result["text"],
        what_to_find=parsed.what_to_find,
        source_url=browser_result["final_url"],
    )

    result = {
        "answer":     extracted["answer"],
        "source_url": browser_result["final_url"],
        "confidence": extracted["confidence"],
        "execution_time": round(time.monotonic() - t0, 2),
        "login_used": browser_result.get("login_used", False),
    }

    if extracted["found"]:
        _cache_put(cache_key, result)

    return QueryResponse(**result)


# ─── Compare endpoint ─────────────────────────────────────────────────────────

@app.post("/query/compare")
async def compare_endpoint(req: CompareRequest):
    t0 = time.monotonic()

    if not req.sites:
        raise HTTPException(400, "At least one site required for comparison")

    parsed = processor.parse_query(req.query)
    parsed.intent = "compare"
    parsed.sites  = req.sites

    result = await comp_engine.run_comparison(parsed, req.sites)
    result["elapsed_total"] = round(time.monotonic() - t0, 2)
    return result


# Inventory closed-beta endpoints

@app.get("/inventory/providers")
async def inventory_providers(user: Dict[str, Any] = Depends(require_inventory_user)):
    return {
        "mode": "private_closed_beta",
        "user": user["email"],
        "providers": sorted(inventory_service.PROVIDERS.keys()),
        "limits": {
            "max_users": inventory_store.MAX_BETA_USERS,
            "searches_per_user_per_hour": inventory_service.MAX_SEARCHES_PER_HOUR,
            "concurrent_searches_total": inventory_service.MAX_CONCURRENT_SEARCHES,
            "seconds_between_retailer_requests": inventory_service.RETAILER_GAP_S,
            "cache_ttl_seconds": inventory_store.CACHE_TTL_S,
        },
    }


@app.post("/inventory/search", response_model=InventorySearchAccepted, status_code=202)
async def inventory_search(
    req: InventorySearchRequest,
    user: Dict[str, Any] = Depends(require_inventory_user),
):
    product = req.product.strip()
    location = req.location.strip()
    try:
        providers = inventory_service.normalize_providers(req.providers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        created = await inventory_service.create_search(
            user_email=user["email"],
            product=product,
            location=location,
            providers=providers,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))

    if created.get("cache_hit"):
        return InventorySearchAccepted(
            search_id=created["result"]["search_id"],
            status="completed",
            cache_hit=True,
            result=created["result"],
        )

    asyncio.create_task(inventory_service.run_search(created["search_id"]))
    return InventorySearchAccepted(
        search_id=created["search_id"],
        status="searching",
        cache_hit=False,
    )


@app.get("/inventory/search/{search_id}")
async def inventory_search_status(
    search_id: str,
    user: Dict[str, Any] = Depends(require_inventory_user),
):
    result = await inventory_service.get_job(search_id)
    if not result:
        raise HTTPException(status_code=404, detail="Inventory search not found")
    if result.get("user") != user["email"] and not str(search_id).startswith("cache-"):
        raise HTTPException(status_code=404, detail="Inventory search not found")
    return result


# ─── Credentials endpoints ────────────────────────────────────────────────────

@app.post("/credentials/save", status_code=201)
async def save_credentials(req: CredentialSaveRequest):
    """Save encrypted credentials. Password is never logged or returned."""
    try:
        cred_store.save_credentials(req.site, req.username, req.password)
        return {"status": "saved", "site": req.site.lower()}
    except Exception as e:
        raise HTTPException(500, f"Failed to save credentials: {e}")


@app.get("/credentials/list")
async def list_credentials():
    """Return site names only — NEVER returns passwords."""
    return {"sites": cred_store.list_sites()}


@app.delete("/credentials/{site}")
async def delete_credentials(site: str):
    deleted = cred_store.delete_credentials(site)
    if not deleted:
        raise HTTPException(404, f"No credentials found for site: {site}")
    return {"status": "deleted", "site": site}


# ─── Health endpoint ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    ollama_enabled = processor.is_ollama_enabled()
    ollama_ok = processor.check_ollama_connected() if ollama_enabled else False
    return {
        "status":           "ok",
        "ollama_enabled":   ollama_enabled,
        "ollama_connected": ollama_ok,
        "browser_ready":    True,       # Playwright is instantiated per-request
        "inventory_ready":  inventory_store.inventory_ready(),
        "cache_entries":    len(_cache),
        "saved_sites":      len(cred_store.list_sites()),
    }


# ─── Global error handler ─────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "path": str(request.url.path)},
    )


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("Sentinel Web Agent starting...")
    inventory_store.init_inventory_store()
    if not processor.is_ollama_enabled():
        logger.info("Ollama disabled: deterministic extraction mode")
    else:
        ollama_ok = processor.check_ollama_connected()
        if not ollama_ok:
            logger.warning(
                "Ollama is NOT reachable at "
                f"{processor.OLLAMA_URL.replace('/api/generate','')}. "
                "Pattern-based fallbacks will be used. "
                "Run: ollama serve"
            )
        else:
            logger.info(f"Ollama connected: {processor.OLLAMA_MODEL}")
    logger.info("Sentinel Web Agent ready on port 8766")


@app.on_event("shutdown")
async def shutdown():
    from browser import stop_engine
    await stop_engine()
    logger.info("Sentinel Web Agent shut down")
