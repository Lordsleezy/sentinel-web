"""
test_agent.py — Test suite for Sentinel Web Agent
Runs 3+ sample queries in dry mode (no real browser/Ollama needed for most tests).

Run: python test_agent.py
"""
import sys
import json
import time
import asyncio
import logging

# Force UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(level=logging.WARNING)

PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"


def section(title: str):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print('='*55)


# ─── Test 1: Credentials module ───────────────────────────────────────────────

def test_credentials():
    section("Test 1: Credential Store (Encrypted)")
    import credentials as creds

    # Save
    creds.save_credentials("testsite", "alice@example.com", "s3cr3t!")
    print(f"  {PASS} save_credentials('testsite', ...)")

    # Retrieve
    got = creds.get_credentials("testsite")
    assert got is not None, "get_credentials returned None"
    assert got["username"] == "alice@example.com", f"Username mismatch: {got['username']}"
    assert got["password"] == "s3cr3t!", "Password mismatch"
    print(f"  {PASS} get_credentials: username decrypted correctly")

    # List (should NOT show passwords)
    sites = creds.list_sites()
    assert "testsite" in sites
    print(f"  {PASS} list_sites: {sites}")

    # Update
    creds.save_credentials("testsite", "alice@example.com", "newpass456")
    updated = creds.get_credentials("testsite")
    assert updated["password"] == "newpass456", "Update failed"
    print(f"  {PASS} credential update works")

    # Delete
    deleted = creds.delete_credentials("testsite")
    assert deleted, "delete_credentials returned False"
    assert creds.get_credentials("testsite") is None, "Credential still exists after delete"
    print(f"  {PASS} delete_credentials works")

    # Verify no leakage in list
    creds.save_credentials("amazon", "shopper@gmail.com", "amazon_pass")
    creds.save_credentials("bestbuy", "buyer@email.com", "bb_pass")
    all_sites = creds.list_sites()
    assert "amazon" in all_sites and "bestbuy" in all_sites
    for site_data in [str(all_sites)]:
        assert "amazon_pass" not in site_data, "PASSWORD LEAKED IN list_sites!"
        assert "bb_pass" not in site_data, "PASSWORD LEAKED IN list_sites!"
    print(f"  {PASS} Password never exposed in list_sites()")

    # Cleanup
    creds.delete_credentials("amazon")
    creds.delete_credentials("bestbuy")
    print(f"  {PASS} Credential store: ALL TESTS PASSED")


# ─── Test 2: Query processor (pattern mode, no Ollama) ────────────────────────

def test_processor():
    section("Test 2: Query Processor (pattern-based, no Ollama)")
    from processor import parse_query

    # Price comparison query
    q1 = parse_query("What is the price of RTX 4090 on Amazon, Newegg, and BestBuy", use_ollama=False)
    print(f"  Query: 'RTX 4090 price comparison'")
    print(f"    intent:     {q1.intent}")
    print(f"    site_name:  {q1.site_name}")
    print(f"    sites:      {q1.sites}")
    # For compare queries detected by pattern, intent should be compare or sites should have entries
    assert q1.intent in ("compare", "lookup"), f"Expected compare/lookup, got {q1.intent}"
    print(f"  {PASS} Price comparison query parsed")

    # Login query
    q2 = parse_query("Check my Chase account balance", use_ollama=False)
    print(f"\n  Query: 'Check my Chase account balance'")
    print(f"    intent:        {q2.intent}")
    print(f"    requires_login:{q2.requires_login}")
    print(f"    site_name:     {q2.site_name}")
    assert q2.requires_login, "Should detect login requirement"
    assert q2.site_name == "chase", f"Expected site_name='chase', got '{q2.site_name}'"
    print(f"  {PASS} Login query detected correctly")

    # Location-based query
    q3 = parse_query("What movies are playing near Sacramento tonight", use_ollama=False)
    print(f"\n  Query: 'Movies playing near Sacramento tonight'")
    print(f"    intent:   {q3.intent}")
    print(f"    location: {q3.location}")
    print(f"    search:   {q3.search_terms}")
    assert q3.location is not None, "Location not extracted"
    print(f"  {PASS} Location extracted: {q3.location}")

    # CVS prescription query
    q4 = parse_query("Is my CVS prescription ready", use_ollama=False)
    print(f"\n  Query: 'Is my CVS prescription ready'")
    print(f"    intent:        {q4.intent}")
    print(f"    requires_login:{q4.requires_login}")
    print(f"    site_name:     {q4.site_name}")
    assert q4.site_name == "cvs", f"Expected site_name='cvs', got '{q4.site_name}'"
    print(f"  {PASS} CVS prescription query parsed")

    print(f"\n  {PASS} Query processor: ALL TESTS PASSED")


# ─── Test 3: Extractor (pattern mode, no Ollama) ─────────────────────────────

def test_extractor():
    section("Test 3: Content Extractor (pattern-based, no Ollama)")
    from extractor import extract_answer_no_ai

    # Price extraction
    page_text_price = """
    NVIDIA GeForce RTX 4090 Founders Edition
    Buy Now
    $1,599.00
    In Stock - Ships in 2-3 days
    Add to Cart
    """
    r1 = extract_answer_no_ai(page_text_price, "What is the price of the RTX 4090?")
    print(f"  Price test: '{r1['answer']}'")
    assert "$" in r1["answer"] or "1599" in r1["answer"].replace(",",""), \
        f"Price not extracted: {r1['answer']}"
    print(f"  {PASS} Price extracted: {r1['answer']}")

    # Stock extraction
    r2 = extract_answer_no_ai(page_text_price, "Is the RTX 4090 in stock?")
    print(f"  Stock test: '{r2['answer']}'")
    assert "Stock" in r2["answer"] or "stock" in r2["answer"].lower(), \
        f"Stock not extracted: {r2['answer']}"
    print(f"  {PASS} Stock status extracted: {r2['answer']}")

    # Out of stock
    page_oos = "Product: Blue Widget\nPrice: $29.99\nOut of Stock\nNotify me when available"
    r3 = extract_answer_no_ai(page_oos, "Is this product available?")
    print(f"  Out-of-stock test: '{r3['answer']}'")
    assert "Out of Stock" in r3["answer"] or "out" in r3["answer"].lower()
    print(f"  {PASS} Out-of-stock detected: {r3['answer']}")

    # Empty page
    r4 = extract_answer_no_ai("", "What is the price?")
    assert not r4["found"], "Empty page should not report found"
    print(f"  {PASS} Empty page handled correctly")

    print(f"\n  {PASS} Extractor: ALL TESTS PASSED")


# ─── Test 4: Browser engine (unit tests, no actual browsing) ─────────────────

def test_browser_units():
    section("Test 4: Browser Engine (unit tests)")
    from browser import get_domain, recall_selectors, remember_selector

    # Domain extraction
    assert get_domain("https://www.bestbuy.com/product/123") == "bestbuy.com"
    assert get_domain("https://api.github.com/repos/x/y") == "api.github.com"
    assert get_domain("https://www.amazon.com/dp/B0C3J1NV3V") == "amazon.com"
    print(f"  {PASS} get_domain(): all correct")

    # Site memory
    remember_selector("bestbuy.com", "price", ".priceView-customer-price", "main price block")
    remember_selector("bestbuy.com", "price", ".priceView-customer-price", "main price block")
    remembered = recall_selectors("bestbuy.com", "price")
    assert ".priceView-customer-price" in remembered, f"Selector not recalled: {remembered}"
    print(f"  {PASS} Site memory: remember + recall works")
    print(f"    Recalled selectors: {remembered}")

    # Text extraction stealth script check
    from browser import STEALTH_SCRIPT
    assert "webdriver" in STEALTH_SCRIPT
    assert "plugins" in STEALTH_SCRIPT
    print(f"  {PASS} Stealth script contains anti-detection patches")

    print(f"\n  {PASS} Browser engine units: ALL TESTS PASSED")


# ─── Test 5: API models ───────────────────────────────────────────────────────

def test_api_models():
    section("Test 5: API Models & Cache")
    from api import _cache_key, _cache_put, _cache_get, _cache

    # Cache key consistency
    k1 = _cache_key("Check Amazon price", "https://amazon.com")
    k2 = _cache_key("Check Amazon price", "https://amazon.com")
    k3 = _cache_key("Different query",    "https://amazon.com")
    assert k1 == k2, "Cache key should be deterministic"
    assert k1 != k3, "Different queries should have different cache keys"
    print(f"  {PASS} Cache key is deterministic")

    # Cache put and get
    _cache_put(k1, {"answer": "RTX 4090: $1,599", "source_url": "https://amazon.com",
                    "confidence": 0.9, "execution_time": 2.1, "login_used": False})
    result = _cache_get(k1)
    assert result is not None, "Cache get returned None immediately after put"
    assert result["answer"] == "RTX 4090: $1,599"
    print(f"  {PASS} Cache put/get works")

    # Cache miss for different key
    miss = _cache_get("nonexistent_key_xyz")
    assert miss is None
    print(f"  {PASS} Cache miss returns None")

    print(f"\n  {PASS} API models: ALL TESTS PASSED")


# ─── Test 6: Sample dry queries (no browser, no Ollama) ──────────────────────

def test_sample_queries():
    section("Test 6: 3 Sample Queries — Dry Run (no network)")
    from processor import parse_query
    from extractor import extract_answer_no_ai

    sample_queries = [
        {
            "query": "Check stock for SKU 6564327 at Best Buy near Sacramento",
            "fake_page": "SKU: 6564327 | Laptop Stand\nPrice: $49.99\nIn Stock\nPickup available at Sacramento store",
            "find": "stock status for SKU 6564327",
        },
        {
            "query": "What is the price of RTX 4090 on Amazon, Newegg, and BestBuy",
            "fake_page": "NVIDIA RTX 4090\nBuy Now: $1,599.00\nIn Stock",
            "find": "price of RTX 4090",
        },
        {
            "query": "What movies are playing near me tonight",
            "fake_page": "Now Playing\nOppenheimer - 7:00 PM, 9:30 PM\nBarbie - 6:30 PM, 8:45 PM, 11:00 PM\nMission Impossible - 7:15 PM",
            "find": "movies playing tonight",
        },
    ]

    for i, sample in enumerate(sample_queries, 1):
        print(f"\n  Query {i}: '{sample['query'][:55]}'")
        parsed = parse_query(sample["query"], use_ollama=False)
        print(f"    Intent:     {parsed.intent}")
        print(f"    Site:       {parsed.site_name or 'N/A'}")
        print(f"    Login:      {parsed.requires_login}")
        print(f"    Location:   {parsed.location or 'N/A'}")

        result = extract_answer_no_ai(sample["fake_page"], sample["find"])
        print(f"    Extracted:  '{result['answer']}'")
        print(f"    Found:      {result['found']}")
        print(f"  {PASS} Query {i} processed successfully")


# ─── Run all tests ────────────────────────────────────────────────────────────

def main():
    print()
    print("  Sentinel Web Agent — Test Suite")
    print("  " + "-"*40)

    failures = []
    tests = [
        ("Credential Store", test_credentials),
        ("Query Processor",  test_processor),
        ("Content Extractor", test_extractor),
        ("Browser Engine Units", test_browser_units),
        ("API Models & Cache", test_api_models),
        ("Sample Queries Dry Run", test_sample_queries),
    ]

    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"\n  {FAIL} {name}: ASSERTION FAILED — {e}")
            failures.append((name, str(e)))
        except Exception as e:
            print(f"\n  {FAIL} {name}: EXCEPTION — {type(e).__name__}: {e}")
            failures.append((name, f"{type(e).__name__}: {e}"))

    print()
    print("=" * 55)
    if failures:
        print(f"  RESULTS: {len(tests)-len(failures)}/{len(tests)} passed")
        for name, err in failures:
            print(f"  FAIL: {name} -- {err}")
        sys.exit(1)
    else:
        print(f"  ALL {len(tests)} TEST SUITES PASSED -- OK")
    print("=" * 55)
    print()


if __name__ == "__main__":
    main()
