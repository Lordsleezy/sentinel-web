import asyncio
import json
import logging
import os
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from croniter import croniter

import ai_helper
from browser import BrowserEngine

logger = logging.getLogger(__name__)

JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = asyncio.Lock()
JOB_QUEUE: Optional[asyncio.Queue] = None
WORKER_TASK: Optional[asyncio.Task] = None
SCHEDULER_TASK: Optional[asyncio.Task] = None

DEFAULT_MAX_PAGES = int(os.getenv("SCRAPE_MAX_PAGES", "3"))
MAX_PRODUCTS_PER_PAGE = int(os.getenv("SCRAPE_MAX_PRODUCTS_PER_PAGE", "50"))
CLOUDFLARE_WAIT_S = float(os.getenv("SCRAPE_CLOUDFLARE_WAIT_S", "12"))


async def start_background_tasks() -> None:
    global JOB_QUEUE, WORKER_TASK, SCHEDULER_TASK
    if JOB_QUEUE is None:
        JOB_QUEUE = asyncio.Queue()
    if WORKER_TASK is None or WORKER_TASK.done():
        WORKER_TASK = asyncio.create_task(_worker())
    if SCHEDULER_TASK is None or SCHEDULER_TASK.done():
        SCHEDULER_TASK = asyncio.create_task(_scheduler())


async def stop_background_tasks() -> None:
    for task in (WORKER_TASK, SCHEDULER_TASK):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def create_job(
    target_url: str,
    instructions: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    cron_schedule: Optional[str] = None,
) -> Dict[str, Any]:
    if not JOB_QUEUE:
        await start_background_tasks()

    job_id = str(uuid.uuid4())
    now = _now()
    job = {
        "id": job_id,
        "status": "queued",
        "progress": [{"timestamp": now, "message": "Job queued"}],
        "target_url": target_url,
        "instructions": instructions,
        "max_pages": max(1, min(max_pages, 20)),
        "cron_schedule": cron_schedule,
        "next_run_at": _next_run(cron_schedule) if cron_schedule else None,
        "results": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    async with JOBS_LOCK:
        JOBS[job_id] = job
    await JOB_QUEUE.put(job_id)
    return _public_job(job)


async def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    async with JOBS_LOCK:
        job = JOBS.get(job_id)
        return _public_job(job) if job else None


async def active_jobs_count() -> int:
    async with JOBS_LOCK:
        return sum(1 for job in JOBS.values() if job["status"] in {"queued", "running"})


async def _worker() -> None:
    assert JOB_QUEUE is not None
    while True:
        job_id = await JOB_QUEUE.get()
        try:
            await _run_job(job_id)
        except Exception as exc:
            logger.exception("Scrape job failed: %s", job_id)
            await _fail_job(job_id, str(exc))
        finally:
            JOB_QUEUE.task_done()


async def _scheduler() -> None:
    while True:
        await asyncio.sleep(30)
        now = datetime.now(timezone.utc)
        due_ids: List[str] = []
        async with JOBS_LOCK:
            for job in JOBS.values():
                next_run_at = job.get("next_run_at")
                if job.get("cron_schedule") and next_run_at and datetime.fromisoformat(next_run_at) <= now:
                    due_ids.append(job["id"])
                    job["next_run_at"] = _next_run(job["cron_schedule"])
        for job_id in due_ids:
            if JOB_QUEUE:
                await JOB_QUEUE.put(job_id)


async def _run_job(job_id: str) -> None:
    await _update_job(job_id, status="running", started_at=_now(), message="Browser scrape started")
    async with JOBS_LOCK:
        job = JOBS[job_id]
        target_url = job["target_url"]
        instructions = job["instructions"]
        max_pages = job["max_pages"]

    engine = BrowserEngine(headless=os.getenv("HEADLESS", "true").lower() != "false")
    await engine.start()
    products: List[Dict[str, Any]] = []
    visited: set[str] = set()
    current_url = target_url

    try:
        async with engine.page_context() as page:
            for page_number in range(1, max_pages + 1):
                if not current_url or current_url in visited:
                    break
                visited.add(current_url)
                await _update_job(job_id, message=f"Loading page {page_number}: {current_url}")
                ok = await engine.navigate(page, current_url, timeout=45_000)
                if not ok:
                    await _update_job(job_id, message=f"Navigation failed: {current_url}")
                    break

                await _human_delay()
                await engine.dismiss_cookie_banners(page)
                await engine.close_popups(page)
                await _handle_cloudflare(page, job_id)
                await engine.smart_wait(page, instructions or "product price list")
                await _human_delay()

                page_products = await _extract_products(page, instructions)
                products.extend(page_products)
                await _update_job(
                    job_id,
                    message=f"Extracted {len(page_products)} product candidates from page {page_number}",
                    progress_count=len(products),
                )

                current_url = await _next_page_url(page, current_url)

        deduped = _dedupe_products(products)
        await _update_job(
            job_id,
            status="completed",
            results=deduped,
            finished_at=_now(),
            message=f"Completed with {len(deduped)} structured products",
        )
    finally:
        await engine.stop()


async def _extract_products(page, instructions: str) -> List[Dict[str, Any]]:
    raw_items = await page.evaluate(
        f"""
        () => {{
          const maxItems = {MAX_PRODUCTS_PER_PAGE};
          const cardSelectors = [
            'article', 'li.s-item', '.s-item', '.item-cell',
            '[data-testid*="product" i]', '[class*="product" i]',
            '[class*="listing" i]', '[class*="result" i]'
          ];
          const seen = new Set();
          const cards = [];
          for (const selector of cardSelectors) {{
            for (const el of document.querySelectorAll(selector)) {{
              if (cards.length >= maxItems) break;
              const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
              if (!text || text.length < 20 || seen.has(text.slice(0, 160))) continue;
              seen.add(text.slice(0, 160));
              const anchor = el.querySelector('a[href]');
              const image = el.querySelector('img[src], img[data-src]');
              cards.push({{
                text,
                title: (
                  el.querySelector('h1,h2,h3,[class*="title" i],[data-testid*="title" i]')?.innerText ||
                  anchor?.innerText ||
                  text.split('$')[0] ||
                  ''
                ).replace(/\\s+/g, ' ').trim(),
                price: (
                  el.querySelector('[class*="price" i],[data-testid*="price" i],[itemprop="price"]')?.innerText ||
                  text.match(/\\$\\s?\\d[\\d,]*(?:\\.\\d{{2}})?/)?.[0] ||
                  ''
                ).replace(/\\s+/g, ' ').trim(),
                href: anchor ? anchor.href : location.href,
                image: image ? (image.getAttribute('src') || image.getAttribute('data-src') || '') : '',
              }});
            }}
            if (cards.length >= maxItems) break;
          }}
          return cards;
        }}
        """
    )

    timestamp = _now()
    products: List[Dict[str, Any]] = []
    for item in raw_items:
        text = str(item.get("text") or "")
        title = _clean_title(str(item.get("title") or text[:120]))
        if not title:
            continue
        products.append(
            {
                "title": title,
                "price": _parse_price(str(item.get("price") or text)),
                "condition": _extract_condition(text),
                "specs": _extract_specs(text),
                "images": [item["image"]] if item.get("image") else [],
                "source_url": item.get("href") or page.url,
                "seller_info": _extract_seller_info(text),
                "timestamp": timestamp,
                "raw_text": text[:1200],
                "instructions": instructions,
            }
        )
    return products


async def _next_page_url(page, base_url: str) -> Optional[str]:
    href = await page.evaluate(
        """
        () => {
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          const next = anchors.find((a) => {
            const label = `${a.innerText || ''} ${a.getAttribute('aria-label') || ''} ${a.rel || ''}`.toLowerCase();
            return label.includes('next') || a.rel === 'next';
          });
          return next ? next.href : null;
        }
        """
    )
    return urljoin(base_url, href) if href else None


async def _handle_cloudflare(page, job_id: str) -> None:
    if not await _cloudflare_detected(page):
        return
    await _update_job(job_id, message="Cloudflare challenge detected; waiting for browser-managed completion")
    await asyncio.sleep(CLOUDFLARE_WAIT_S)
    if await _cloudflare_detected(page):
        await _update_job(job_id, message="Cloudflare challenge still present; returning available page data only")


async def _cloudflare_detected(page) -> bool:
    try:
        title = (await page.title()).lower()
        content = (await page.content()).lower()
        signals = ["just a moment", "checking your browser", "cf-challenge", "cloudflare", "turnstile"]
        return any(signal in title or signal in content for signal in signals)
    except Exception:
        return False


def score_product(product: Dict[str, Any]) -> Dict[str, Any]:
    price = _coerce_float(product.get("price"))
    market_value = _estimate_market_value(product)
    discount = max(0.0, (market_value - price) / market_value) if market_value > 0 else 0.0
    specs_score = min(25, 8 + len(product.get("specs") or {}) * 3)
    condition = str(product.get("condition") or "good").lower()
    condition_score = {"new": 16, "excellent": 13, "good": 10, "fair": 4, "parts": -10}.get(condition, 8)
    score = max(0, min(100, round(discount * 120 + specs_score + condition_score + 12)))
    return {
        "score": score,
        "market_value": round(market_value, 2),
        "rationale": "Score uses observed price versus estimated market value, detected specs, and condition.",
    }


async def score_product_with_ollama(product: Dict[str, Any]) -> Dict[str, Any]:
    fallback = score_product(product)
    if not ai_helper.ai_available():
        return {**fallback, "model": None, "ai_used": False}

    prompt = f"""Score this product deal from 0-100 using price vs market value, specs, and condition.
Return strict JSON with score, market_value, rationale.
Product JSON: {json.dumps(product)[:4000]}"""
    try:
        response = httpx.post(
            f"{ai_helper.ai_helper_host()}/api/generate",
            json={
                "model": ai_helper.ai_helper_model(),
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 220},
            },
            timeout=ai_helper.ai_helper_timeout_s(),
        )
        response.raise_for_status()
        parsed = _parse_json(response.json().get("response", ""))
        if not parsed:
            return {**fallback, "model": ai_helper.ai_helper_model(), "ai_used": False}
        score = max(0, min(100, int(parsed.get("score", fallback["score"]))))
        return {
            "score": score,
            "market_value": _coerce_float(parsed.get("market_value")) or fallback["market_value"],
            "rationale": str(parsed.get("rationale") or fallback["rationale"])[:600],
            "model": ai_helper.ai_helper_model(),
            "ai_used": True,
        }
    except Exception as exc:
        logger.warning("Ollama scoring failed, using deterministic fallback: %s", exc)
        return {**fallback, "model": ai_helper.ai_helper_model(), "ai_used": False}


def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


async def _update_job(job_id: str, **updates: Any) -> None:
    message = updates.pop("message", None)
    async with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = _now()
        if message:
            entry = {"timestamp": job["updated_at"], "message": message}
            if "progress_count" in updates:
                entry["count"] = updates["progress_count"]
            job["progress"].append(entry)


async def _fail_job(job_id: str, error: str) -> None:
    await _update_job(job_id, status="failed", error=error, finished_at=_now(), message=f"Failed: {error}")


def _public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    return dict(job)


def _next_run(cron_schedule: Optional[str]) -> Optional[str]:
    if not cron_schedule:
        return None
    return croniter(cron_schedule, datetime.now(timezone.utc)).get_next(datetime).isoformat()


async def _human_delay() -> None:
    await asyncio.sleep(random.uniform(0.8, 2.6))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_price(text: str) -> Optional[float]:
    match = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text or "")
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    title = re.sub(r"\$\s?[\d,]+(?:\.\d{2})?.*$", "", title).strip()
    return title[:180]


def _extract_condition(text: str) -> str:
    lower = (text or "").lower()
    for condition in ("new", "excellent", "good", "fair", "parts"):
        if condition in lower:
            return condition
    if "open box" in lower or "refurb" in lower:
        return "good"
    return "unknown"


def _extract_specs(text: str) -> Dict[str, Any]:
    specs: Dict[str, Any] = {}
    patterns = {
        "cpu": r"\b(i[3579]-?\d{3,5}[a-zA-Z]*|ryzen\s?[3579][\w\s-]*|m[1234]\s?(?:pro|max|ultra)?)\b",
        "ram": r"\b(\d{1,3}\s?GB)\s+(?:RAM|memory)\b",
        "storage": r"\b(\d+(?:\.\d+)?\s?(?:TB|GB))\s+(?:SSD|NVMe|HDD|storage)\b",
        "gpu": r"\b(RTX\s?\d{3,4}|GTX\s?\d{3,4}|Radeon\s?[\w\d]+)\b",
        "screen": r"\b(\d{2}(?:\.\d)?\")\b",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            specs[key] = match.group(1).strip()
    return specs


def _extract_seller_info(text: str) -> Dict[str, Any]:
    rating = re.search(r"(\d{2,3}(?:\.\d+)?)\s?% positive", text or "", re.IGNORECASE)
    sold_by = re.search(r"(?:sold by|seller)\s*:?\s*([A-Za-z0-9 ._-]{2,60})", text or "", re.IGNORECASE)
    return {
        "seller": sold_by.group(1).strip() if sold_by else "",
        "rating": float(rating.group(1)) if rating else None,
    }


def _estimate_market_value(product: Dict[str, Any]) -> float:
    price = _coerce_float(product.get("price"))
    if price <= 0:
        return 0.0
    specs = product.get("specs") or {}
    multiplier = 1.18
    if specs.get("cpu"):
        multiplier += 0.05
    if specs.get("ram"):
        multiplier += 0.04
    if specs.get("gpu"):
        multiplier += 0.08
    return price * multiplier


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for product in products:
        key = (product.get("source_url"), product.get("title"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped
