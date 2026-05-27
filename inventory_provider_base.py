import re
import os
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus

from extractor import extract_answer
from inventory_models import InventoryProviderResult, UNAVAILABLE_BLOCKED
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

ProgressCallback = Callable[[str, str], Awaitable[None]]


BLOCKED_PATTERNS = [
    r"\bcaptcha\b",
    r"\bare you a human\b",
    r"\bverify you are human\b",
    r"\bsecurity challenge\b",
    r"\baccess denied\b",
    r"\bautomated(?:\s+\w+){0,3}\s+blocked\b",
    r"\bunusual traffic\b",
    r"\btoo many requests\b",
    r"\brate limit(?:ed)?\b",
    r"\blogin required\b",
    r"\bsign in to continue\b",
]


class BrowserInventoryProvider:
    name = "base"
    base_url = ""
    search_domain = ""
    search_path = ""

    def __init__(self, headless: Optional[bool] = None):
        if headless is None:
            headless = os.getenv("HEADLESS", "true").strip().lower() not in {"0", "false", "no", "off"}
        self.headless = headless

    async def search(
        self,
        product: str,
        location: str,
        progress: ProgressCallback,
    ) -> InventoryProviderResult:
        started = time.monotonic()
        await progress("opening retailer", self.name)

        browser_result = await self._load_retailer_page(product, location)

        await progress("checking store availability", self.name)
        elapsed = round(time.monotonic() - started, 2)

        error = browser_result.get("error")
        text = browser_result.get("text") or ""
        source_url = browser_result.get("final_url") or self.base_url
        if error or self._is_blocked(text, error):
            return InventoryProviderResult(
                provider=self.name,
                status=UNAVAILABLE_BLOCKED,
                product=product,
                location=location,
                source_url=source_url,
                error=error or UNAVAILABLE_BLOCKED,
                elapsed_s=elapsed,
            )

        await progress("extracting price", self.name)
        price = extract_answer(
            page_text=text,
            what_to_find=f"price for {product}",
            source_url=source_url,
            use_ollama=False,
        )
        stock = extract_answer(
            page_text=text,
            what_to_find=f"availability or in-store stock for {product}"
            + (f" near {location}" if location else ""),
            source_url=source_url,
            use_ollama=False,
        )

        price_answer = price.get("answer", "") if price.get("found") else ""
        stock_answer = stock.get("answer", "") if stock.get("found") else ""
        status = "completed" if price_answer or stock_answer else "unavailable"

        return InventoryProviderResult(
            provider=self.name,
            status=status,
            availability=stock_answer,
            price=price_answer,
            product=product,
            location=location,
            source_url=source_url,
            confidence=max(float(price.get("confidence", 0.0)), float(stock.get("confidence", 0.0))),
            elapsed_s=elapsed,
        )

    def _build_search_terms(self, product: str, location: str) -> str:
        parts = [product, "price availability"]
        if location:
            parts.append(location)
        if self.search_domain:
            parts.append(f"site:{self.search_domain}")
        return " ".join(parts)

    def build_search_url(self, product: str, location: str) -> str:
        query = product if not location else f"{product} {location}"
        return self.search_path.format(query=quote_plus(query))

    async def _load_retailer_page(self, product: str, location: str) -> dict:
        url = self.build_search_url(product, location)
        result = {"text": "", "final_url": url, "error": None}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                page = await browser.new_page(
                    viewport={"width": 390, "height": 844},
                    locale="en-US",
                    timezone_id="America/Los_Angeles",
                )
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2500)
                    result["final_url"] = page.url
                    result["text"] = (await page.locator("body").inner_text(timeout=10000))[:8000]
                finally:
                    await browser.close()
        except PlaywrightTimeoutError:
            result["error"] = "Retailer page timed out"
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _is_blocked(self, text: str, error: Optional[str]) -> bool:
        combined = f"{error or ''}\n{text or ''}".lower()
        return any(re.search(pattern, combined, re.IGNORECASE) for pattern in BLOCKED_PATTERNS)
