import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional, Type

from amazon_browser_provider import AmazonBrowserProvider
from bestbuy_browser_provider import BestBuyBrowserProvider
from inventory_models import InventoryProgress, InventoryProviderResult, InventorySearchResult
from inventory_provider_base import BrowserInventoryProvider
import inventory_store
from target_browser_provider import TargetBrowserProvider
from walmart_browser_provider import WalmartBrowserProvider


PROVIDERS: Dict[str, Type[BrowserInventoryProvider]] = {
    "bestbuy": BestBuyBrowserProvider,
    "target": TargetBrowserProvider,
    "walmart": WalmartBrowserProvider,
    "amazon": AmazonBrowserProvider,
}

MAX_SEARCHES_PER_HOUR = 10
MAX_CONCURRENT_SEARCHES = 3
RETAILER_GAP_S = 10.0

_jobs: Dict[str, InventorySearchResult] = {}
_jobs_lock = asyncio.Lock()
_active_lock = asyncio.Lock()
_active_searches = 0
_retailer_locks = {name: asyncio.Lock() for name in PROVIDERS}
_retailer_last_access = {name: 0.0 for name in PROVIDERS}


def normalize_providers(providers: Optional[List[str]]) -> List[str]:
    if not providers:
        return list(PROVIDERS.keys())
    normalized = []
    for provider in providers:
        key = provider.strip().lower()
        if key not in PROVIDERS:
            raise ValueError(f"Unsupported inventory provider: {provider}")
        if key not in normalized:
            normalized.append(key)
    return normalized


async def add_progress(search_id: str, state: str, provider: Optional[str] = None, detail: str = "") -> None:
    async with _jobs_lock:
        job = _jobs.get(search_id)
        if not job:
            return
        job.progress.append(
            InventoryProgress(
                state=state,
                provider=provider,
                detail=detail,
                at=time.time(),
            )
        )
        if state in {"searching", "completed", "unavailable"}:
            job.status = state


async def create_search(
    user_email: str,
    product: str,
    location: str,
    providers: List[str],
) -> Dict[str, Any]:
    providers = normalize_providers(providers)
    key = inventory_store.cache_key(product, location, providers)
    cached = inventory_store.get_cache(key)
    if cached:
        cached["user"] = user_email
        cached["cache_hit"] = True
        cached["status"] = "completed"
        cached["search_id"] = f"cache-{uuid.uuid4().hex[:12]}"
        inventory_store.write_audit_log(
            user_email=user_email,
            query=_query_label(product, location),
            providers_checked=providers,
            success=True,
            cache_status="hit",
            execution_time=0.0,
        )
        return {"cache_hit": True, "result": cached}

    recent_count = inventory_store.count_recent_user_searches(user_email)
    if recent_count >= MAX_SEARCHES_PER_HOUR:
        raise RuntimeError("Rate limit exceeded: max 10 inventory searches per hour.")

    global _active_searches
    async with _active_lock:
        if _active_searches >= MAX_CONCURRENT_SEARCHES:
            raise RuntimeError("Too many inventory searches are already running. Try again shortly.")
        _active_searches += 1

    inventory_store.record_user_search(user_email)
    search_id = uuid.uuid4().hex
    job = InventorySearchResult(
        search_id=search_id,
        user=user_email,
        product=product,
        location=location,
        providers_checked=providers,
        status="searching",
        cache_hit=False,
        progress=[
            InventoryProgress(
                state="searching",
                detail="Search accepted for closed beta inventory lookup.",
                at=time.time(),
            )
        ],
    )
    async with _jobs_lock:
        _jobs[search_id] = job
    return {"cache_hit": False, "search_id": search_id}


async def run_search(search_id: str) -> None:
    started = time.monotonic()
    failure_reason = None
    success = False
    cache_payload = None

    try:
        async with _jobs_lock:
            job = _jobs[search_id]
            product = job.product
            location = job.location
            providers = list(job.providers_checked)
            user_email = job.user

        results: List[InventoryProviderResult] = []
        for provider_name in providers:
            await _wait_for_retailer_slot(provider_name, search_id)
            provider = PROVIDERS[provider_name]()

            async def progress(state: str, provider_label: str = provider_name) -> None:
                await add_progress(search_id, state, provider_label)

            try:
                result = await provider.search(product, location, progress)
            except Exception as exc:
                result = InventoryProviderResult(
                    provider=provider_name,
                    status="unavailable",
                    product=product,
                    location=location,
                    error=str(exc),
                )
            results.append(result)

            async with _jobs_lock:
                _jobs[search_id].results = list(results)

        execution_time = round(time.monotonic() - started, 2)
        success = any(r.status == "completed" for r in results)
        final_status = "completed" if success else "unavailable"
        await add_progress(search_id, final_status)

        async with _jobs_lock:
            job = _jobs[search_id]
            job.status = final_status
            job.execution_time = execution_time
            job.results = results
            if not success:
                failure_reason = "No provider returned a completed result."
                job.error = failure_reason
            cache_payload = job.to_dict()

        inventory_store.put_cache(
            inventory_store.cache_key(product, location, providers),
            cache_payload,
        )
        inventory_store.write_audit_log(
            user_email=user_email,
            query=_query_label(product, location),
            providers_checked=providers,
            success=success,
            failure_reason=failure_reason,
            cache_status="miss",
            execution_time=execution_time,
        )
    finally:
        global _active_searches
        async with _active_lock:
            _active_searches = max(0, _active_searches - 1)


async def get_job(search_id: str) -> Optional[Dict[str, Any]]:
    async with _jobs_lock:
        job = _jobs.get(search_id)
        return job.to_dict() if job else None


async def _wait_for_retailer_slot(provider_name: str, search_id: str) -> None:
    lock = _retailer_locks[provider_name]
    async with lock:
        now = time.monotonic()
        wait = RETAILER_GAP_S - (now - _retailer_last_access[provider_name])
        if wait > 0:
            await add_progress(
                search_id,
                "opening retailer",
                provider_name,
                f"Waiting {wait:.1f}s for retailer rate limit.",
            )
            await asyncio.sleep(wait)
        _retailer_last_access[provider_name] = time.monotonic()


def _query_label(product: str, location: str) -> str:
    return product if not location else f"{product} near {location}"
