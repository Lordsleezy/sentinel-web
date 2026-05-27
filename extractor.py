"""
extractor.py — Content extractor for Sentinel Web Agent

Uses Ollama to extract the exact answer to a query from raw page text.
Handles prices, tables, lists, stock levels, schedules, any content.
"""
import logging
import re
from typing import Optional

from processor import _call_ollama, _parse_json_response, is_ollama_enabled

logger = logging.getLogger(__name__)


# ─── Extraction prompt ────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """You are a precise web content extractor.
You receive raw text scraped from a webpage and a question.
Your job is to find and return ONLY the answer to the question.

Rules:
- Be specific and concise — give the exact value, price, status, or answer
- If information is not found: say exactly "NOT FOUND: <reason>"
- For prices: include the currency symbol and exact amount
- For stock: say "In Stock", "Out of Stock", or the exact quantity
- For schedules: list each item clearly
- For comparisons across one page: extract all values mentioned
- Never make up information — only report what is actually on the page
- If the page has a login wall or error, say "LOGIN REQUIRED" or "PAGE ERROR"

Respond with ONLY valid JSON:
{
    "answer": "<your answer here>",
    "found": <true/false>,
    "confidence": <0.0-1.0>,
    "source_hint": "<which section of the page this came from>"
}"""


def _build_extract_prompt(page_text: str, what_to_find: str, url: str = "") -> str:
    url_hint = f"\nSource URL: {url}" if url else ""
    return f"""{_EXTRACT_SYSTEM}
{url_hint}
Question: {what_to_find}

Page content (scraped text):
{page_text[:6000]}

Respond with ONLY the JSON object:"""


# ─── Pattern-based fast extractors ────────────────────────────────────────────

def _extract_price_pattern(text: str) -> Optional[str]:
    """Fast regex extraction for prices."""
    patterns = [
        r'\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)',
        r'USD\s*(\d+(?:\.\d{2})?)',
        r'Price[:\s]+\$?(\d+(?:\.\d{2})?)',
        r'(\d+(?:\.\d{2})?)\s*dollars?',
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            val = m.group(1).replace(",", "")
            try:
                f = float(val)
                if 0.01 < f < 100_000:
                    found.append(f"${f:.2f}")
            except ValueError:
                continue
    return found[0] if len(found) == 1 else (", ".join(found[:3]) if found else None)


def _extract_stock_pattern(text: str) -> Optional[str]:
    """Fast regex extraction for stock / availability status."""
    patterns = [
        (r"\bIn\s+Stock\b", "In Stock"),
        (r"\bOut\s+of\s+Stock\b", "Out of Stock"),
        (r"\bUnavailable\b", "Unavailable"),
        (r"\bSold\s+Out\b", "Sold Out"),
        (r"\bAvailable\b", "Available"),
        (r"\bLimited\s+Stock\b", "Limited Stock"),
        (r"\bBackorder\b", "On Backorder"),
        (r"\bShips\s+in\s+(\d+[^,\.]{0,20})", None),  # Dynamic — capture group
    ]
    for pat, label in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return label if label else m.group(0).strip()
    return None


# ─── Main extraction function ─────────────────────────────────────────────────

def extract_answer(
    page_text: str,
    what_to_find: str,
    source_url: str = "",
    use_ollama: bool = True,
) -> dict:
    """
    Extract the answer to what_to_find from page_text.

    Returns:
        {answer, found, confidence, source_hint}
    """
    if not page_text or not page_text.strip():
        return {
            "answer": "The page did not return any readable content.",
            "found": False,
            "confidence": 0.0,
            "source_hint": "empty page",
        }

    # ── Fast path: pattern matching for common cases ─────────────────────────
    q_lower = what_to_find.lower()

    if any(kw in q_lower for kw in ["price", "cost", "how much"]):
        price = _extract_price_pattern(page_text)
        if price:
            return {
                "answer": price,
                "found": True,
                "confidence": 0.75,
                "source_hint": "price pattern match",
            }

    if any(kw in q_lower for kw in ["stock", "availability", "available", "in stock"]):
        stock = _extract_stock_pattern(page_text)
        if stock:
            return {
                "answer": stock,
                "found": True,
                "confidence": 0.8,
                "source_hint": "stock pattern match",
            }

    # ── Slow path: Ollama extraction ─────────────────────────────────────────
    if not use_ollama or not is_ollama_enabled():
        return {
            "answer": f"Could not extract '{what_to_find}' without AI assistance.",
            "found": False,
            "confidence": 0.0,
            "source_hint": "no_ollama",
        }

    try:
        prompt = _build_extract_prompt(page_text, what_to_find, source_url)
        raw = _call_ollama(prompt, temperature=0.0)
        data = _parse_json_response(raw)

        if data:
            answer = data.get("answer", "")
            found = bool(data.get("found", bool(answer and "NOT FOUND" not in answer)))
            return {
                "answer": answer,
                "found": found,
                "confidence": float(data.get("confidence", 0.7)),
                "source_hint": data.get("source_hint", ""),
            }

        # Ollama returned something but not JSON — use raw text as answer
        if raw and raw.strip():
            return {
                "answer": raw.strip()[:500],
                "found": True,
                "confidence": 0.5,
                "source_hint": "ollama_raw",
            }

    except Exception as e:
        logger.error(f"Extraction error: {e}")

    return {
        "answer": f"Could not extract the requested information: {what_to_find}",
        "found": False,
        "confidence": 0.0,
        "source_hint": "extraction_failed",
    }


def extract_answer_no_ai(page_text: str, what_to_find: str) -> dict:
    """
    Pattern-only extraction (no Ollama). Used for dry-run / testing.
    """
    return extract_answer(page_text, what_to_find, use_ollama=False)
