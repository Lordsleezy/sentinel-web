"""
comparison.py — Multi-site comparison engine for Sentinel Web Agent

Runs browser sessions in parallel across multiple sites and aggregates
results into a clean natural language comparison response.

Example output:
  "RTX 4090: Amazon $899, Newegg $879, BestBuy $949. Best price: Newegg ($879)"
"""
import asyncio
import logging
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from browser import BrowserEngine, get_domain
from extractor import extract_answer
from processor import _call_ollama, _parse_json_response, ParsedQuery, is_ollama_enabled

logger = logging.getLogger(__name__)

MAX_PARALLEL_SESSIONS = 5  # Hard cap for parallel browser instances


# ─── Per-site result ─────────────────────────────────────────────────────────

class SiteResult:
    def __init__(self, site_url: str):
        self.site_url   = site_url
        self.domain     = get_domain(site_url)
        self.answer     = ""
        self.found      = False
        self.confidence = 0.0
        self.error      = None
        self.elapsed    = 0.0

    def __repr__(self):
        return f"SiteResult({self.domain}: {self.answer!r} found={self.found})"


# ─── Single-site worker ───────────────────────────────────────────────────────

async def _fetch_single_site(
    engine: BrowserEngine,
    site_url: str,
    what_to_find: str,
    search_terms: str,
    credentials: Optional[Dict] = None,
) -> SiteResult:
    """Run browser session for one site and return extracted result."""
    result = SiteResult(site_url)
    t0 = time.monotonic()

    try:
        browser_result = await engine.run_query(
            url=site_url,
            search_terms=search_terms,
            what_to_find=what_to_find,
            credentials=credentials,
            headless=True,
        )

        if browser_result.get("error"):
            result.error = browser_result["error"]
        else:
            extracted = extract_answer(
                page_text=browser_result["text"],
                what_to_find=what_to_find,
                source_url=browser_result["final_url"],
            )
            result.answer     = extracted["answer"]
            result.found      = extracted["found"]
            result.confidence = extracted["confidence"]

    except Exception as e:
        logger.error(f"Comparison worker error ({site_url}): {e}")
        result.error = str(e)

    result.elapsed = time.monotonic() - t0
    return result


# ─── Aggregation prompt ───────────────────────────────────────────────────────

def _build_aggregate_prompt(
    query: str,
    site_results: List[SiteResult],
) -> str:
    lines = []
    for sr in site_results:
        if sr.found:
            lines.append(f"- {sr.domain}: {sr.answer}")
        elif sr.error:
            lines.append(f"- {sr.domain}: ERROR — {sr.error}")
        else:
            lines.append(f"- {sr.domain}: not found")

    data_block = "\n".join(lines)

    return f"""You are summarizing a price/availability comparison for a user.

User query: "{query}"

Data collected from sites:
{data_block}

Write a single, concise natural language response that:
1. States the key finding for each site
2. Identifies the best option (lowest price, best availability, etc.)
3. Is easy to read on a phone screen

Keep it under 3 sentences. Be direct and specific.
Example format: "RTX 4090: Amazon $899, Newegg $879, BestBuy $949. Best price: Newegg at $879."

Respond with ONLY the summary sentence(s), no JSON:"""


def _rule_based_aggregate(query: str, site_results: List[SiteResult]) -> str:
    """
    Fast rule-based aggregation for price comparisons.
    Falls back to this if Ollama is unavailable.
    """
    found_results = [(sr.domain, sr.answer) for sr in site_results if sr.found]
    error_results = [(sr.domain, sr.error)  for sr in site_results if sr.error]

    if not found_results:
        if error_results:
            return f"Could not retrieve data from any site. Errors: " + \
                   ", ".join(f"{d}: {e}" for d, e in error_results[:3])
        return "No results found on any of the requested sites."

    # Try to extract prices and find best
    prices: List[Tuple[str, float]] = []
    for domain, answer in found_results:
        m = re.search(r'\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)', answer)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                prices.append((domain, val))
            except ValueError:
                pass

    parts = [f"{d}: {a}" for d, a in found_results]
    summary = ", ".join(parts)

    if prices:
        best_site, best_price = min(prices, key=lambda x: x[1])
        summary += f". Best price: {best_site} (${best_price:.2f})"

    if error_results:
        err_str = ", ".join(d for d, _ in error_results)
        summary += f". Could not check: {err_str}"

    return summary


# ─── Main comparison runner ───────────────────────────────────────────────────

async def run_comparison(
    parsed: ParsedQuery,
    sites: List[str],
    credentials_map: Optional[Dict[str, Dict]] = None,
) -> Dict:
    """
    Run parallel browser sessions across all sites, aggregate results.

    Returns:
        {summary, site_results, best_site, elapsed_total}
    """
    if not sites:
        return {
            "summary": "No sites specified for comparison.",
            "site_results": [],
            "best_site": None,
            "elapsed_total": 0.0,
        }

    t0 = time.monotonic()
    credentials_map = credentials_map or {}

    # Cap parallelism
    semaphore = asyncio.Semaphore(MAX_PARALLEL_SESSIONS)
    engine = BrowserEngine(headless=True)
    await engine.start()

    async def _guarded_fetch(site_url: str) -> SiteResult:
        async with semaphore:
            domain = get_domain(site_url)
            creds = credentials_map.get(domain) or credentials_map.get(site_url)
            return await _fetch_single_site(
                engine=engine,
                site_url=site_url,
                what_to_find=parsed.what_to_find or parsed.raw_query,
                search_terms=f"{parsed.raw_query} site:{get_domain(site_url)}",
                credentials=creds,
            )

    try:
        tasks = [_guarded_fetch(url) for url in sites[:MAX_PARALLEL_SESSIONS]]
        site_results: List[SiteResult] = await asyncio.gather(*tasks, return_exceptions=False)
    except Exception as e:
        logger.error(f"Comparison gather error: {e}")
        site_results = []
    finally:
        await engine.stop()

    elapsed = time.monotonic() - t0

    # ── Aggregate with Ollama (or fast fallback) ──────────────────────────────
    try:
        if not is_ollama_enabled():
            raise RuntimeError("Ollama disabled")
        agg_prompt = _build_aggregate_prompt(parsed.raw_query, site_results)
        summary_raw = _call_ollama(agg_prompt, temperature=0.1)
        summary = summary_raw.strip() if summary_raw.strip() else \
                  _rule_based_aggregate(parsed.raw_query, site_results)
    except Exception as e:
        logger.warning(f"Aggregation Ollama error: {e} — using rule-based")
        summary = _rule_based_aggregate(parsed.raw_query, site_results)

    # Find best site (lowest price or highest confidence)
    best_site = None
    best_price = float("inf")
    for sr in site_results:
        if sr.found:
            m = re.search(r'\$\s*(\d+(?:\.\d{2})?)', sr.answer)
            if m:
                p = float(m.group(1))
                if p < best_price:
                    best_price = p
                    best_site = sr.domain

    return {
        "summary": summary,
        "site_results": [
            {
                "site": sr.domain,
                "url":  sr.site_url,
                "answer": sr.answer,
                "found": sr.found,
                "confidence": sr.confidence,
                "error": sr.error,
                "elapsed_s": round(sr.elapsed, 2),
            }
            for sr in site_results
        ],
        "best_site": best_site,
        "elapsed_total": round(elapsed, 2),
    }
