import os
import sys
import tempfile
from pathlib import Path

os.environ["OLLAMA_ENABLED"] = "false"
os.environ["SENTINEL_BETA_USERS"] = "cloud-test@example.com"
os.environ["SENTINEL_INVENTORY_CACHE_TTL_S"] = "720"
os.environ["ALLOWED_ORIGINS"] = (
    "https://sentinelprime.org,https://www.sentinelprime.org,"
    "http://localhost:5173,http://127.0.0.1:5173"
)
os.environ["HEADLESS"] = "true"

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import inventory_store

_tmpdir = tempfile.TemporaryDirectory()
inventory_store.DATA_DIR = Path(_tmpdir.name)
inventory_store.DB_PATH = inventory_store.DATA_DIR / "inventory_beta.db"

import processor
from fastapi.testclient import TestClient


def main():
    assert processor.is_ollama_enabled() is False

    import api

    headers = {"X-Sentinel-User-Email": "cloud-test@example.com"}

    with TestClient(api.app) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        health_json = health.json()
        assert health_json["status"] == "ok", health_json
        assert health_json["ollama_enabled"] is False, health_json
        assert health_json["ollama_connected"] is False, health_json
        assert health_json["browser_ready"] is True, health_json
        assert health_json["inventory_ready"] is True, health_json

        anonymous = client.get("/inventory/providers")
        assert anonymous.status_code == 403, anonymous.text

        invited = client.get("/inventory/providers", headers=headers)
        assert invited.status_code == 200, invited.text
        invited_json = invited.json()
        assert "bestbuy" in invited_json["providers"], invited_json
        assert "amazon" in invited_json["providers"], invited_json

        assert inventory_store.inventory_ready() is True
        assert inventory_store.user_count() == 1

    _tmpdir.cleanup()
    print("cloud no-ai smoke tests passed")


if __name__ == "__main__":
    main()
