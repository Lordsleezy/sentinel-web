"""
processor.py — Natural language query processor for Sentinel Web Agent

Uses Ollama to decompose a raw NL query into a structured object:
  intent, target_url, search_terms, what_to_find, requires_login, site_name
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, List
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

OLLAMA_URL   = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434") + "/api/generate"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")

logger = logging.getLogger(__name__)


def is_ollama_enabled() -> bool:
    return os.getenv("OLLAMA_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ParsedQuery:
    """Structured representation of a user's natural language query."""
    raw_query:      str
    intent:         str          # lookup | compare | monitor | login_required
    target_url:     Optional[str] = None   # Direct URL to navigate to
    search_terms:   Optional[str] = None   # Google search fallback
    what_to_find:   str          = ""      # Specific data to extract
    requires_login: bool         = False
    site_name:      Optional[str] = None   # For credential lookup (e.g. "bestbuy")
    sites:          List[str]    = field(default_factory=list)  # For compare intent
    location:       Optional[str] = None   # Geographic location if mentioned

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Prompt builder ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a web automation assistant. Your job is to parse a user's natural language query and extract structured information about what they want to find on the web.

Intents:
- "lookup"        — find specific information on one website
- "compare"       — compare the same information across multiple sites (prices, availability, etc.)
- "monitor"       — check status of something (account, order, prescription, schedule)
- "login_required" — definitely requires account login to access

Site name is the short identifier for credential lookup (e.g. "bestbuy", "chase", "cvs", "amazon").
If the query mentions multiple sites for comparison, list all of them in the "sites" array.
For target_url: provide the most direct URL if you know it, otherwise null.
For search_terms: what to Google if you need to find the page first.

Always respond with ONLY valid JSON, no markdown, no explanation."""


def _build_parse_prompt(query: str) -> str:
    return f"""{_SYSTEM_PROMPT}

User query: "{query}"

Respond with ONLY this JSON structure:
{{
    "intent": "lookup|compare|monitor|login_required",
    "target_url": "<direct URL or null>",
    "search_terms": "<google search query or null>",
    "what_to_find": "<exactly what information to extract from the page>",
    "requires_login": <true/false>,
    "site_name": "<site identifier for credentials or null>",
    "sites": ["<url1>", "<url2>"],
    "location": "<city or region mentioned or null>"
}}"""


# ─── Pattern-based fallback (no Ollama needed for common cases) ───────────────

_SITE_URL_MAP = {
    "bestbuy":   "https://www.bestbuy.com",
    "amazon":    "https://www.amazon.com",
    "newegg":    "https://www.newegg.com",
    "walmart":   "https://www.walmart.com",
    "target":    "https://www.target.com",
    "cvs":       "https://www.cvs.com",
    "walgreens": "https://www.walgreens.com",
    "chase":     "https://www.chase.com",
    "bankofamerica": "https://www.bankofamerica.com",
    "gmail":     "https://mail.google.com",
    "netflix":   "https://www.netflix.com",
    "youtube":   "https://www.youtube.com",
    "reddit":    "https://www.reddit.com",
    "fandango":  "https://www.fandango.com",
    "imdb":      "https://www.imdb.com",
    "yelp":      "https://www.yelp.com",
}

_LOGIN_KEYWORDS = [
    "my account", "my balance", "my order", "my prescription",
    "my schedule", "log in", "sign in", "logged in", "my profile",
    "bank account", "check my", "account balance",
]

_COMPARE_KEYWORDS = ["compare", "versus", "vs", "price comparison", "cheapest",
                     "best price", "lowest price", "price check"]


def _pattern_fallback(query: str) -> Optional[ParsedQuery]:
    """
    Fast rule-based parse for common query patterns.
    Avoids Ollama call for simple cases.
    """
    q_lower = query.lower()
    parsed = ParsedQuery(raw_query=query, intent="lookup", what_to_find=query)

    # Detect compare intent
    if any(kw in q_lower for kw in _COMPARE_KEYWORDS):
        parsed.intent = "compare"

    # Detect login requirement
    if any(kw in q_lower for kw in _LOGIN_KEYWORDS):
        parsed.requires_login = True
        parsed.intent = "login_required" if parsed.intent == "lookup" else parsed.intent

    # Detect known sites
    for site, url in _SITE_URL_MAP.items():
        if site in q_lower:
            parsed.site_name = site
            if parsed.intent != "compare":
                parsed.target_url = url
            elif site not in parsed.sites:
                parsed.sites.append(url)

    # Extract location hint — several patterns
    for loc_pat in [
        r"near\s+([\w\s]+?)(?=\s+tonight|\s+today|\s+this\s|\s*$)",
        r"in\s+([\w\s]+?)(?=\s+tonight|\s+today|\s+this\s|\s*$)",
        r"(?:near|in|around)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    ]:
        m = re.search(loc_pat, query)  # Use original case for title()
        if m:
            loc = m.group(1).strip()
            if 2 < len(loc) < 40:
                parsed.location = loc.title()
                break

    # Always populate search terms
    if not parsed.target_url:
        parsed.search_terms = query
    parsed.what_to_find = query

    # If we have ANY useful signals, return immediately (skip Ollama)
    if (parsed.site_name or parsed.requires_login
            or parsed.intent == "compare" or parsed.location):
        return parsed

    return None  # Defer to Ollama for unknown query types


# ─── Ollama helpers (shared with extractor.py) ────────────────────────────────

def _call_ollama(prompt: str, temperature: float = 0.0, max_retries: int = 3) -> str:
    """Synchronous Ollama call with exponential backoff."""
    if not is_ollama_enabled():
        raise RuntimeError("Ollama is disabled. Set OLLAMA_ENABLED=true to enable AI extraction.")

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 2048},
    }
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = httpx.post(OLLAMA_URL, json=payload, timeout=120.0)
            resp.raise_for_status()
            return resp.json().get("response", "")
        except httpx.ConnectError as e:
            last_err = e
            logger.error(f"Ollama not reachable (attempt {attempt+1})")
        except Exception as e:
            last_err = e
            logger.warning(f"Ollama error (attempt {attempt+1}): {e}")
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Ollama unavailable after {max_retries} attempts: {last_err}")


def _parse_json_response(raw: str) -> Optional[dict]:
    """Extract and parse JSON from model response, handling fences and prose."""
    if not raw:
        return None
    cleaned = raw.strip()
    # Strip markdown fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start:end+1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try fixing trailing commas
        try:
            fixed = re.sub(r",\s*([\}\]])", r"\1", cleaned)
            return json.loads(fixed)
        except Exception:
            return None


# ─── Main parse function ──────────────────────────────────────────────────────

def parse_query(query: str, use_ollama: bool = True) -> ParsedQuery:
    """
    Parse a natural language query into a structured ParsedQuery.
    First tries pattern matching (fast), then falls back to Ollama.
    """
    # Fast path: pattern-based
    fast = _pattern_fallback(query)
    if fast:
        logger.debug(f"Pattern match: intent={fast.intent} site={fast.site_name}")
        return fast

    # Slow path: Ollama
    if not use_ollama or not is_ollama_enabled():
        # Pure fallback with no Ollama
        return ParsedQuery(
            raw_query=query,
            intent="lookup",
            search_terms=query,
            what_to_find=query,
        )

    try:
        prompt = _build_parse_prompt(query)
        raw = _call_ollama(prompt)
        data = _parse_json_response(raw)

        if not data:
            logger.warning("Ollama parse returned no JSON — using fallback")
            return ParsedQuery(
                raw_query=query,
                intent="lookup",
                search_terms=query,
                what_to_find=query,
            )

        return ParsedQuery(
            raw_query=query,
            intent=data.get("intent", "lookup"),
            target_url=data.get("target_url"),
            search_terms=data.get("search_terms") or query,
            what_to_find=data.get("what_to_find", query),
            requires_login=bool(data.get("requires_login", False)),
            site_name=data.get("site_name"),
            sites=data.get("sites", []),
            location=data.get("location"),
        )

    except Exception as e:
        logger.error(f"Query parse error: {e} — using fallback")
        return ParsedQuery(
            raw_query=query,
            intent="lookup",
            search_terms=query,
            what_to_find=query,
        )


def check_ollama_connected() -> bool:
    """Return True if Ollama is reachable."""
    if not is_ollama_enabled():
        return False
    try:
        base = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        resp = httpx.get(f"{base}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False
