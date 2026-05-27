"""
browser.py — Playwright browser engine for Sentinel Web Agent

Features:
- Headless Chromium, realistic anti-detection
- Smart navigation: direct URL or Google fallback
- Cookie banner / popup auto-dismiss
- Login form detection and auto-fill
- Smart wait for dynamic content
- Screenshot on error
- Site memory for faster repeat visits
"""
import asyncio
import os
import re
import time
import logging
import sqlite3
from contextlib import asynccontextmanager
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from urllib.parse import urlparse, quote_plus

from playwright.async_api import (
    async_playwright, Browser, BrowserContext, Page,
    TimeoutError as PWTimeout, Error as PWError
)

SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
SITE_MEM_DB     = Path(__file__).parent / "data" / "site_memory.db"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

# ─── Anti-detection config ────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

STEALTH_SCRIPT = """
// Remove webdriver fingerprint
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// Fake plugins
Object.defineProperty(navigator, 'plugins', {get: () => [{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}]});
// Fake languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
// Fake hardware concurrency (looks human)
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4});
// Remove automation-specific Chrome runtime properties
if (window.chrome) {
    Object.defineProperty(window.chrome, 'runtime', {get: () => ({})});
}
// Permissions API spoof
const originalQuery = window.navigator.permissions ? window.navigator.permissions.query.bind(window.navigator.permissions) : null;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : originalQuery(parameters);
}
"""

# ─── Cookie banner selectors (common CMPs + patterns) ─────────────────────────

COOKIE_DISMISS_SELECTORS = [
    # Text-based buttons (most reliable cross-site)
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('Accept Cookies')",
    "button:has-text('I Accept')",
    "button:has-text('I agree')",
    "button:has-text('Agree')",
    "button:has-text('Got it')",
    "button:has-text('OK')",
    "button:has-text('Close')",
    "button:has-text('Dismiss')",
    # ID/class patterns
    "[id*='cookie'] button",
    "[class*='cookie-accept']",
    "[class*='cookie-consent'] button",
    "[id*='consent'] button[class*='accept']",
    "[id*='onetrust-accept']",
    "#onetrust-accept-btn-handler",
    ".js-accept-cookies",
    "[data-testid*='cookie'] button",
    # CMP-specific
    ".cc-btn.cc-allow",
    "#CybotCookiebotDialogBodyButtonAccept",
    ".cookieConsent__Button--Accept",
]

# ─── Login form detection ─────────────────────────────────────────────────────

LOGIN_USERNAME_SELECTORS = [
    'input[type="email"]',
    'input[type="text"][name*="user"]',
    'input[type="text"][name*="email"]',
    'input[type="text"][name*="login"]',
    'input[name*="username"]',
    'input[id*="username"]',
    'input[id*="email"]',
    'input[id*="user"]',
    'input[placeholder*="email" i]',
    'input[placeholder*="username" i]',
    'input[autocomplete="username"]',
    'input[autocomplete="email"]',
]

LOGIN_PASSWORD_SELECTORS = [
    'input[type="password"]',
]

LOGIN_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Sign In")',
    '[data-testid*="login"]',
    '[data-testid*="signin"]',
]

# ─── Site Memory DB ───────────────────────────────────────────────────────────

@contextmanager
def _mem_conn():
    SITE_MEM_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(SITE_MEM_DB))
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _init_site_memory():
    with _mem_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS site_selectors (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                domain        TEXT    NOT NULL,
                query_type    TEXT    NOT NULL,
                selector      TEXT,
                description   TEXT,
                success_count INTEGER DEFAULT 1,
                last_used     TEXT    DEFAULT (datetime('now')),
                created_at    TEXT    DEFAULT (datetime('now')),
                UNIQUE(domain, query_type, selector)
            )
        """)


def remember_selector(domain: str, query_type: str, selector: str, description: str = ""):
    """Record a successful CSS selector for a site+query_type combination."""
    try:
        with _mem_conn() as con:
            con.execute("""
                INSERT INTO site_selectors (domain, query_type, selector, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(domain, query_type, selector) DO UPDATE SET
                    success_count = success_count + 1,
                    last_used = datetime('now')
            """, (domain, query_type, selector, description))
    except Exception as e:
        logger.debug(f"Site memory write error: {e}")


def recall_selectors(domain: str, query_type: str) -> List[str]:
    """Return known-good selectors for a site+query_type, most successful first."""
    try:
        with _mem_conn() as con:
            rows = con.execute("""
                SELECT selector FROM site_selectors
                WHERE domain = ? AND query_type = ?
                ORDER BY success_count DESC, last_used DESC
                LIMIT 5
            """, (domain, query_type)).fetchall()
        return [r["selector"] for r in rows]
    except Exception:
        return []


_init_site_memory()


# ─── Browser Engine ───────────────────────────────────────────────────────────

class BrowserEngine:
    """
    Manages a single Playwright Chromium browser instance.
    Provides high-level navigation, extraction, and login helpers.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        import random
        self._ua = random.choice(USER_AGENTS)

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--no-first-run",
                "--disable-default-apps",
            ],
        )
        logger.debug(f"Browser started (headless={self.headless})")

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.debug("Browser stopped")

    async def new_context(self) -> BrowserContext:
        if not self._browser:
            raise RuntimeError("Browser not started — call start() first")
        ctx = await self._browser.new_context(
            user_agent=self._ua,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/Los_Angeles",
            java_script_enabled=True,
            accept_downloads=False,
            ignore_https_errors=True,
        )
        return ctx

    @asynccontextmanager
    async def page_context(self):
        """Async context manager that yields a stealth page and cleans up after."""
        ctx = await self.new_context()
        page = await ctx.new_page()
        await page.add_init_script(STEALTH_SCRIPT)
        try:
            yield page
        finally:
            await ctx.close()

    async def navigate(self, page: Page, url: str, timeout: int = 30_000) -> bool:
        """
        Navigate to URL. Returns True on success, False on failure.
        Handles redirects and waits for meaningful content.
        """
        try:
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            # Wait for network idle (up to 5s extra)
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except PWTimeout:
                pass  # OK — page loaded, just not fully idle
            return True
        except PWTimeout:
            logger.warning(f"Navigation timeout: {url}")
            return False
        except PWError as e:
            logger.warning(f"Navigation error: {url}: {e}")
            return False

    async def dismiss_cookie_banners(self, page: Page) -> int:
        """
        Try to dismiss cookie consent banners.
        Returns number of banners dismissed.
        """
        dismissed = 0
        for sel in COOKIE_DISMISS_SELECTORS:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click(timeout=2_000)
                    dismissed += 1
                    await asyncio.sleep(0.5)
                    break  # One dismiss per page load is usually enough
            except Exception:
                continue
        if dismissed:
            logger.debug(f"Dismissed {dismissed} cookie banner(s)")
        return dismissed

    async def close_popups(self, page: Page):
        """Try to close common overlay popups / modal dialogs."""
        popup_selectors = [
            "button[aria-label='Close']",
            "button[aria-label='close']",
            "[class*='modal'] button[class*='close']",
            "[class*='popup'] button[class*='close']",
            "[data-testid='close-button']",
            ".close-button",
            ".modal-close",
        ]
        for sel in popup_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click(timeout=1_500)
                    await asyncio.sleep(0.3)
            except Exception:
                continue

    async def detect_login_required(self, page: Page) -> bool:
        """Return True if the current page appears to be/require a login."""
        try:
            # Check for password field
            pwd_fields = await page.query_selector_all('input[type="password"]')
            if pwd_fields:
                return True

            # Check URL patterns
            url = page.url.lower()
            if any(s in url for s in ["/login", "/signin", "/sign-in", "/auth", "/account/login"]):
                return True

            # Check page title/content
            title = (await page.title()).lower()
            if any(s in title for s in ["sign in", "log in", "login", "signin"]):
                return True

            # Check for redirect-to-login text
            content = await page.content()
            content_low = content.lower()
            login_phrases = ["please sign in", "please log in", "you must be logged in",
                             "sign in to continue", "login required", "authenticate"]
            if any(p in content_low for p in login_phrases):
                return True

        except Exception:
            pass
        return False

    async def attempt_login(
        self, page: Page, username: str, password: str
    ) -> bool:
        """
        Detect login form on current page and fill + submit it.
        Returns True if login form was found and submitted.
        Credentials are NEVER logged at any level.
        """
        # Find username field
        user_field = None
        for sel in LOGIN_USERNAME_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    user_field = el
                    break
            except Exception:
                continue

        # Find password field
        pass_field = None
        for sel in LOGIN_PASSWORD_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    pass_field = el
                    break
            except Exception:
                continue

        if not pass_field:
            logger.debug("No password field found — cannot auto-login")
            return False

        try:
            # Type credentials with realistic human-like delays
            if user_field:
                await user_field.click()
                await asyncio.sleep(0.2)
                await user_field.fill(username)
                await asyncio.sleep(0.3 + 0.1 * len(username) * 0.05)

            await pass_field.click()
            await asyncio.sleep(0.2)
            await pass_field.fill(password)
            await asyncio.sleep(0.4)

            # Find and click submit
            submitted = False
            for sel in LOGIN_SUBMIT_SELECTORS:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                # Fallback: press Enter on the password field
                await pass_field.press("Enter")
                submitted = True

            if submitted:
                # Wait for navigation after login
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except PWTimeout:
                    pass
                logger.info("Login form submitted")
                return True

        except Exception as e:
            logger.warning(f"Login attempt error: {e}")
        return False

    async def extract_text(self, page: Page) -> str:
        """
        Extract meaningful text from the page, stripping navigation,
        ads, headers, footers, and scripts.
        Returns clean readable text, max ~8000 chars.
        """
        try:
            content = await page.evaluate("""
                () => {
                    // Remove noise elements
                    const noiseSelectors = [
                        'script', 'style', 'noscript', 'iframe',
                        'nav', 'header', 'footer',
                        '[role="navigation"]', '[role="banner"]', '[role="complementary"]',
                        '[id*="cookie"]', '[class*="cookie"]',
                        '[id*="ad"]', '[class*="advertisement"]',
                        '[class*="sidebar"]', '[id*="sidebar"]',
                        '[class*="popup"]', '[class*="modal"]',
                        '[aria-hidden="true"]',
                    ];
                    const clone = document.body.cloneNode(true);
                    noiseSelectors.forEach(sel => {
                        try { clone.querySelectorAll(sel).forEach(el => el.remove()); } catch(e){}
                    });
                    // Get text with spacing
                    function getText(node) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            return node.textContent.trim();
                        }
                        const display = window.getComputedStyle
                            ? window.getComputedStyle(node).display : 'block';
                        const parts = [];
                        for (const child of node.childNodes) {
                            const t = getText(child);
                            if (t) parts.push(t);
                        }
                        const tag = node.tagName ? node.tagName.toLowerCase() : '';
                        const blockTags = new Set(['p','div','h1','h2','h3','h4','h5','h6',
                            'li','tr','td','th','section','article','br','hr']);
                        const sep = blockTags.has(tag) ? '\\n' : ' ';
                        return parts.join(sep);
                    }
                    return getText(clone);
                }
            """)
            # Clean up whitespace
            lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
            cleaned = "\n".join(lines)
            # Truncate to ~8000 chars (fits comfortably in Ollama context)
            return cleaned[:8000]
        except Exception as e:
            logger.warning(f"Text extraction error: {e}")
            return ""

    async def screenshot(self, page: Page, label: str = "error") -> str:
        """Take a screenshot and save to screenshots/ dir. Returns file path."""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(SCREENSHOTS_DIR / f"{label}_{ts}.png")
            await page.screenshot(path=path, full_page=False)
            logger.info(f"Screenshot saved: {path}")
            return path
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return ""

    async def google_search(self, page: Page, query: str) -> Optional[str]:
        """
        Search Google for query, return URL of the first organic result.
        Falls back to Bing if Google fails.
        """
        search_engines = [
            f"https://www.google.com/search?q={quote_plus(query)}",
            f"https://www.bing.com/search?q={quote_plus(query)}",
        ]
        for search_url in search_engines:
            try:
                ok = await self.navigate(page, search_url)
                if not ok:
                    continue
                await self.dismiss_cookie_banners(page)

                # Extract first organic result link
                result_url = await page.evaluate("""
                    () => {
                        // Google: organic results
                        const anchors = document.querySelectorAll(
                            'a[href^="http"]:not([href*="google"]):not([href*="youtube.com"])'
                        );
                        for (const a of anchors) {
                            const href = a.href;
                            if (href && href.startsWith('http') &&
                                !href.includes('google') &&
                                !href.includes('accounts.') &&
                                a.closest('[class]')) {
                                return href;
                            }
                        }
                        return null;
                    }
                """)
                if result_url:
                    logger.debug(f"Search result: {result_url}")
                    return result_url
            except Exception as e:
                logger.debug(f"Search engine error: {e}")
                continue
        return None

    async def smart_wait(self, page: Page, hint: str = ""):
        """
        Wait for dynamic content using multiple signals.
        hint can guide what we wait for (e.g. "price", "table", "list").
        """
        hint_lower = hint.lower()

        # Wait for commonly needed elements based on hint
        wait_selectors = {
            "price":    ["[class*='price']", "[data-price]", "[itemprop='price']"],
            "stock":    ["[class*='stock']", "[class*='availability']", "[class*='inventory']"],
            "schedule": ["table", "[class*='schedule']", "[class*='shift']"],
            "table":    ["table", "[role='table']"],
            "list":     ["ul", "ol", "[class*='list']"],
        }

        for key, selectors in wait_selectors.items():
            if key in hint_lower:
                for sel in selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=5_000)
                        return
                    except PWTimeout:
                        continue
                break

        # Generic fallback: wait for network idle
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except PWTimeout:
            pass

    async def run_query(
        self,
        url: Optional[str],
        search_terms: Optional[str],
        what_to_find: str,
        credentials: Optional[Dict[str, str]] = None,
        headless: bool = True,
    ) -> Dict[str, Any]:
        """
        Full navigation + extraction pipeline.
        Returns {text, final_url, screenshot_path, login_used, error}.
        """
        result = {
            "text": "",
            "final_url": url or "",
            "screenshot_path": "",
            "login_used": False,
            "error": None,
        }

        async with self.page_context() as page:
            try:
                # ── Step 1: Navigate ────────────────────────────────────────
                target_url = url
                if not target_url and search_terms:
                    logger.info(f"Searching for: {search_terms}")
                    found = await self.google_search(page, search_terms)
                    if found:
                        target_url = found
                    else:
                        result["error"] = f"Could not find a URL for: {search_terms}"
                        return result

                if not target_url:
                    result["error"] = "No URL or search terms provided"
                    return result

                logger.info(f"Navigating to: {target_url}")
                ok = await self.navigate(page, target_url)
                if not ok:
                    # Search fallback
                    if search_terms:
                        logger.info("Direct navigation failed, trying search fallback")
                        found = await self.google_search(page, search_terms)
                        if found:
                            await self.navigate(page, found)
                    else:
                        result["error"] = f"Failed to load: {target_url}"
                        await self.screenshot(page, "nav_error")
                        return result

                result["final_url"] = page.url

                # ── Step 2: Dismiss cookie banners ──────────────────────────
                await self.dismiss_cookie_banners(page)
                await self.close_popups(page)
                await asyncio.sleep(0.5)

                # ── Step 3: Handle login if needed ──────────────────────────
                if await self.detect_login_required(page):
                    if credentials:
                        logger.info("Login required — attempting auto-login")
                        login_ok = await self.attempt_login(
                            page,
                            credentials["username"],
                            credentials["password"],
                        )
                        if login_ok:
                            result["login_used"] = True
                            await asyncio.sleep(1.5)
                            # Navigate back to original target after login
                            if page.url != target_url:
                                await self.navigate(page, target_url)
                                result["final_url"] = page.url
                    else:
                        result["error"] = (
                            "Login required for this page. "
                            "Save credentials with POST /credentials/save "
                            "using the site name."
                        )
                        return result

                # ── Step 4: Smart wait for content ──────────────────────────
                await self.smart_wait(page, what_to_find)
                await self.dismiss_cookie_banners(page)

                # ── Step 5: Extract text ────────────────────────────────────
                text = await self.extract_text(page)
                result["text"] = text

                if not text.strip():
                    result["error"] = "Page loaded but no readable content was found"
                    await self.screenshot(page, "empty_content")

            except Exception as e:
                logger.exception(f"run_query error: {e}")
                result["error"] = str(e)
                try:
                    await self.screenshot(page, "exception")
                except Exception:
                    pass

        return result


# ─── Convenience: get domain ─────────────────────────────────────────────────

def get_domain(url: str) -> str:
    """Extract domain from URL, e.g. 'bestbuy.com'."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        # Strip 'www.'
        return re.sub(r"^www\.", "", host)
    except Exception:
        return url


# ─── Module-level singleton (shared by comparison engine) ─────────────────────

_engine: Optional[BrowserEngine] = None


async def get_engine(headless: bool = True) -> BrowserEngine:
    """Get or create the shared browser engine."""
    global _engine
    if _engine is None or _engine._browser is None:
        _engine = BrowserEngine(headless=headless)
        await _engine.start()
    return _engine


async def stop_engine():
    global _engine
    if _engine:
        await _engine.stop()
        _engine = None
