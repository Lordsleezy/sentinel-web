import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["SENTINEL_BETA_USERS"] = "test@example.com"
os.environ["SENTINEL_INVENTORY_CACHE_TTL_S"] = "720"
os.environ["AI_HELPER_ENABLED"] = "true"
os.environ["AI_HELPER_REQUIRED"] = "true"
os.environ["AI_HELPER_MODEL"] = "llama3.2:1b"

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import inventory_store

_tmpdir = tempfile.TemporaryDirectory()
inventory_store.DATA_DIR = Path(_tmpdir.name)
inventory_store.DB_PATH = inventory_store.DATA_DIR / "inventory_beta.db"

import inventory_service
import ai_helper
from fastapi.testclient import TestClient
from inventory_models import InventoryProviderResult


class MockBestBuyProvider:
    name = "bestbuy"

    async def search(self, product, location, progress):
        await progress("opening retailer", self.name)
        await progress("checking store availability", self.name)
        await progress("extracting price", self.name)
        return InventoryProviderResult(
            provider=self.name,
            status="completed",
            availability="In Stock",
            price="$123.45",
            product=product,
            location=location,
            source_url="https://www.bestbuy.com/mock",
            confidence=1.0,
            elapsed_s=0.01,
        )


def reset_inventory_service():
    inventory_service.PROVIDERS = {"bestbuy": MockBestBuyProvider}
    inventory_service._jobs = {}
    inventory_service._active_searches = 0
    inventory_service._retailer_locks = {"bestbuy": asyncio.Lock()}
    inventory_service._retailer_last_access = {"bestbuy": 0.0}


def wait_for_job(client, search_id, headers):
    deadline = time.time() + 5
    while time.time() < deadline:
        response = client.get(f"/inventory/search/{search_id}", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        if data["status"] in {"completed", "unavailable"}:
            return data
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for inventory job")


def main():
    reset_inventory_service()

    import api

    headers = {"X-Sentinel-User-Email": "test@example.com"}
    bad_headers = {"X-Sentinel-User-Email": "invalid@example.com"}

    with TestClient(api.app) as client:
        inventory_store.init_inventory_store()
        ai_helper.ai_available = lambda: True
        ai_helper.parse_inventory_query = lambda query: {
            "intent": "inventory_lookup",
            "product_query": query.replace(" near 10001", ""),
            "sku": "",
            "location": "10001" if "10001" in query else "",
            "summary": "",
            "best_option": "",
            "confidence": 0.8,
        }
        ai_helper.summarize_inventory_results = lambda query, provider_results: {
            "intent": "inventory_lookup",
            "product_query": query,
            "sku": "",
            "location": "10001",
            "summary": "Mock summary",
            "best_option": "bestbuy",
            "confidence": 0.9,
        }

        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok", health.text

        anonymous = client.get("/inventory/providers")
        assert anonymous.status_code == 403, anonymous.text

        invited = client.get("/inventory/providers", headers=headers)
        assert invited.status_code == 200, invited.text
        assert "bestbuy" in invited.json()["providers"], invited.text

        invalid = client.get("/inventory/providers", headers=bad_headers)
        assert invalid.status_code == 403, invalid.text

        body = {
            "product": "mock cache sku",
            "location": "10001",
            "providers": ["bestbuy"],
        }
        created = client.post("/inventory/search", json=body, headers=headers)
        assert created.status_code == 202, created.text
        assert created.json()["cache_hit"] is False, created.text

        finished = wait_for_job(client, created.json()["search_id"], headers)
        assert finished["status"] == "completed", finished
        assert finished["results"][0]["price"] == "$123.45", finished

        cached = client.post("/inventory/search", json=body, headers=headers)
        assert cached.status_code == 202, cached.text
        assert cached.json()["cache_hit"] is True, cached.text
        assert cached.json()["result"]["results"][0]["price"] == "$123.45", cached.text

        now_count = inventory_store.count_recent_user_searches("test@example.com")
        for _ in range(max(0, inventory_service.MAX_SEARCHES_PER_HOUR - now_count)):
            inventory_store.record_user_search("test@example.com")

        limited_body = {
            "product": "mock uncached rate sku",
            "location": "10001",
            "providers": ["bestbuy"],
        }
        limited = client.post("/inventory/search", json=limited_body, headers=headers)
        assert limited.status_code == 429, limited.text

    _tmpdir.cleanup()
    print("inventory beta smoke tests passed")


if __name__ == "__main__":
    main()
