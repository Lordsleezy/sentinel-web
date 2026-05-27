import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["AI_HELPER_ENABLED"] = "true"
os.environ["AI_HELPER_REQUIRED"] = "true"
os.environ["AI_HELPER_PROVIDER"] = "ollama"
os.environ["AI_HELPER_MODEL"] = "llama3.2:1b"
os.environ["AI_HELPER_HOST"] = "http://127.0.0.1:11434"
os.environ["AI_HELPER_TIMEOUT_S"] = "1"
os.environ["OLLAMA_ENABLED"] = "false"
os.environ["SENTINEL_BETA_USERS"] = "tiny-ai@example.com"

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import ai_helper
import inventory_store

_tmpdir = tempfile.TemporaryDirectory()
inventory_store.DATA_DIR = Path(_tmpdir.name)
inventory_store.DB_PATH = inventory_store.DATA_DIR / "inventory_beta.db"

import inventory_service
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
            price="$99.99",
            product=product,
            location=location,
            source_url="https://www.bestbuy.com/mock",
            confidence=0.9,
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

    headers = {"X-Sentinel-User-Email": "tiny-ai@example.com"}
    original_available = ai_helper.ai_available
    original_parse = ai_helper.parse_inventory_query
    original_summary = ai_helper.summarize_inventory_results

    ai_helper.ai_available = lambda: False
    with TestClient(api.app) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        health_json = health.json()
        assert health_json["ai_helper_required"] is True, health_json
        assert health_json["ai_helper_connected"] is False, health_json
        assert health_json["ai_helper_model"] == "llama3.2:1b", health_json
        assert health_json["inventory_ready"] is False, health_json

        blocked = client.post(
            "/inventory/search",
            json={"product": "Check SKU 123 near 10001", "providers": ["bestbuy"]},
            headers=headers,
        )
        assert blocked.status_code == 503, blocked.text
        assert blocked.json()["detail"] == "AI helper unavailable", blocked.text

        captured = {}
        ai_helper.ai_available = lambda: True

        def mocked_parse(query):
            assert "<html" not in query.lower()
            assert "cookie" not in query.lower()
            assert "password" not in query.lower()
            return {
                "intent": "inventory_lookup",
                "product_query": "SKU 123",
                "sku": "123",
                "location": "10001",
                "summary": "",
                "best_option": "",
                "confidence": 0.88,
            }

        def mocked_summary(query, provider_results):
            captured["query"] = query
            captured["provider_results"] = provider_results
            serialized = str(provider_results).lower()
            assert "<html" not in serialized
            assert "cookie" not in serialized
            assert "password" not in serialized
            return {
                "intent": "inventory_lookup",
                "product_query": "SKU 123",
                "sku": "123",
                "location": "10001",
                "summary": "Best Buy has SKU 123 in stock for $99.99.",
                "best_option": "Best Buy",
                "confidence": 0.91,
            }

        ai_helper.parse_inventory_query = mocked_parse
        ai_helper.summarize_inventory_results = mocked_summary

        created = client.post(
            "/inventory/search",
            json={"product": "Can you check SKU 123 near 10001?", "providers": ["bestbuy"]},
            headers=headers,
        )
        assert created.status_code == 202, created.text
        data = created.json()
        assert data["cache_hit"] is False, data

        finished = wait_for_job(client, data["search_id"], headers)
        assert finished["status"] == "completed", finished
        assert finished["ai_parse"]["product_query"] == "SKU 123", finished
        assert finished["ai_summary"]["summary"] == "Best Buy has SKU 123 in stock for $99.99.", finished
        assert finished["ai_summary"]["best_option"] == "Best Buy", finished
        assert finished["confidence"] == 0.91, finished
        assert finished["provider_results"][0]["provider"] == "bestbuy", finished
        assert captured["provider_results"][0]["price"] == "$99.99"

    ai_helper.ai_available = original_available
    ai_helper.parse_inventory_query = original_parse
    ai_helper.summarize_inventory_results = original_summary
    _tmpdir.cleanup()
    print("required tiny AI tests passed")


if __name__ == "__main__":
    main()
