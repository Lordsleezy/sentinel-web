import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx


AI_SCHEMA = {
    "intent": "inventory_lookup",
    "product_query": "",
    "sku": "",
    "location": "",
    "summary": "",
    "best_option": "",
    "confidence": 0.0,
}


def ai_helper_enabled() -> bool:
    return os.getenv("AI_HELPER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def ai_helper_required() -> bool:
    return os.getenv("AI_HELPER_REQUIRED", "true").strip().lower() in {"1", "true", "yes", "on"}


def ai_helper_provider() -> str:
    return os.getenv("AI_HELPER_PROVIDER", "ollama").strip().lower()


def ai_helper_model() -> str:
    return os.getenv("AI_HELPER_MODEL", "llama3.2:1b").strip()


def ai_helper_host() -> str:
    return os.getenv("AI_HELPER_HOST", "http://127.0.0.1:11434").rstrip("/")


def ai_helper_timeout_s() -> float:
    try:
        return float(os.getenv("AI_HELPER_TIMEOUT_S", "10"))
    except ValueError:
        return 10.0


def ai_available() -> bool:
    if not ai_helper_enabled() or ai_helper_provider() != "ollama":
        return False
    try:
        response = httpx.get(f"{ai_helper_host()}/api/tags", timeout=ai_helper_timeout_s())
        if response.status_code != 200:
            return False
        models = response.json().get("models", [])
        wanted = ai_helper_model()
        return any(m.get("name") == wanted or m.get("model") == wanted for m in models)
    except Exception:
        return False


def ensure_model_available() -> bool:
    return ai_available()


def parse_inventory_query(query: str) -> Dict[str, Any]:
    prompt = f"""Parse this retail inventory request into strict JSON.

Rules:
- Never include credentials, cookies, HTML, or page text.
- Extract only the product, SKU/model if present, and store location if present.
- Use intent: inventory_lookup, price_compare, or product_search.

Request: {json.dumps(query[:500])}

Return ONLY this JSON object:
{{
  "intent": "inventory_lookup | price_compare | product_search",
  "product_query": "...",
  "sku": "...",
  "location": "...",
  "summary": "",
  "best_option": "",
  "confidence": 0.0
}}"""
    data = _call_json(prompt)
    normalized = _normalize_schema(data)
    if not normalized["product_query"]:
        normalized["product_query"] = query.strip()
    return normalized


def summarize_inventory_results(query: str, provider_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    safe_results = [_safe_provider_result(r) for r in provider_results]
    prompt = f"""Summarize retail inventory provider results for a phone UI.

Rules:
- Use only this structured data.
- Do not infer unavailable prices or stock.
- Never ask for credentials.
- Keep summary concise.

User request: {json.dumps(query[:500])}
Provider results JSON: {json.dumps(safe_results)[:4000]}

Return ONLY this JSON object:
{{
  "intent": "inventory_lookup",
  "product_query": "...",
  "sku": "",
  "location": "",
  "summary": "...",
  "best_option": "...",
  "confidence": 0.0
}}"""
    data = _call_json(prompt)
    return _normalize_schema(data)


def _call_json(prompt: str) -> Dict[str, Any]:
    if not ai_available():
        raise RuntimeError("AI helper unavailable")
    payload = {
        "model": ai_helper_model(),
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 256,
        },
    }
    response = httpx.post(
        f"{ai_helper_host()}/api/generate",
        json=payload,
        timeout=ai_helper_timeout_s(),
    )
    response.raise_for_status()
    raw = response.json().get("response", "")
    parsed = _parse_json_response(raw)
    if parsed is None:
        raise RuntimeError("AI helper returned non-JSON output")
    return parsed


def _parse_json_response(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(AI_SCHEMA)
    for key in out:
        if key in data and data[key] is not None:
            out[key] = data[key]
    if out["intent"] not in {"inventory_lookup", "price_compare", "product_search"}:
        out["intent"] = "inventory_lookup"
    try:
        out["confidence"] = max(0.0, min(1.0, float(out["confidence"])))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    for key in ("product_query", "sku", "location", "summary", "best_option"):
        out[key] = str(out.get(key) or "")[:500]
    return out


def _safe_provider_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": str(result.get("provider", ""))[:80],
        "status": str(result.get("status", ""))[:160],
        "availability": str(result.get("availability", ""))[:300],
        "price": str(result.get("price", ""))[:120],
        "product": str(result.get("product", ""))[:200],
        "location": str(result.get("location", ""))[:120],
        "source_url": str(result.get("source_url", ""))[:500],
        "confidence": result.get("confidence", 0.0),
        "error": str(result.get("error", ""))[:300],
    }
