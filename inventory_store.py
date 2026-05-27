import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional


DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "inventory_beta.db"
CACHE_TTL_S = int(os.getenv("SENTINEL_INVENTORY_CACHE_TTL_S", "720"))
MAX_BETA_USERS = 15


@contextmanager
def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def init_inventory_store() -> None:
    with _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS beta_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT,
                api_key_hash TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                invited_at REAL NOT NULL,
                last_seen_at REAL
            );

            CREATE TABLE IF NOT EXISTS inventory_cache (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_rate_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_inventory_rate_user_time
                ON inventory_rate_events(user_email, created_at);

            CREATE TABLE IF NOT EXISTS inventory_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                query TEXT NOT NULL,
                providers_checked TEXT NOT NULL,
                success INTEGER NOT NULL,
                failure_reason TEXT,
                cache_status TEXT NOT NULL,
                execution_time REAL NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
    seed_users_from_env()


def seed_users_from_env() -> None:
    raw_users = os.getenv("SENTINEL_BETA_USERS", "")
    raw_keys = os.getenv("SENTINEL_BETA_API_KEYS", "")
    users = [u.strip().lower() for u in raw_users.split(",") if u.strip()]
    key_map: Dict[str, str] = {}
    for item in [x.strip() for x in raw_keys.split(",") if x.strip()]:
        if ":" in item:
            email, token = item.split(":", 1)
            key_map[email.strip().lower()] = hash_token(token.strip())

    now = time.time()
    with _conn() as con:
        existing = con.execute("SELECT COUNT(*) AS c FROM beta_users").fetchone()["c"]
        for email in users[: max(0, MAX_BETA_USERS - existing)]:
            con.execute(
                """
                INSERT INTO beta_users (email, display_name, api_key_hash, active, invited_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(email) DO UPDATE SET
                    api_key_hash=COALESCE(excluded.api_key_hash, beta_users.api_key_hash),
                    active=1
                """,
                (email, email.split("@")[0], key_map.get(email), now),
            )


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM beta_users WHERE lower(email)=lower(?) AND active=1",
            (email.strip(),),
        ).fetchone()
        if not row:
            return None
        con.execute("UPDATE beta_users SET last_seen_at=? WHERE id=?", (time.time(), row["id"]))
        return dict(row)


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    token_hash = hash_token(token.strip())
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM beta_users WHERE api_key_hash=? AND active=1",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        con.execute("UPDATE beta_users SET last_seen_at=? WHERE id=?", (time.time(), row["id"]))
        return dict(row)


def user_count() -> int:
    with _conn() as con:
        return int(con.execute("SELECT COUNT(*) AS c FROM beta_users WHERE active=1").fetchone()["c"])


def inventory_ready() -> bool:
    try:
        with _conn() as con:
            con.execute("SELECT 1")
        return True
    except Exception:
        return False


def cache_key(product: str, location: str, providers: List[str]) -> str:
    raw = json.dumps(
        {
            "product": product.strip().lower(),
            "location": location.strip().lower(),
            "providers": sorted(p.strip().lower() for p in providers),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cache(key: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _conn() as con:
        row = con.execute(
            "SELECT payload_json FROM inventory_cache WHERE cache_key=? AND expires_at>?",
            (key, now),
        ).fetchone()
        if row:
            return json.loads(row["payload_json"])
        con.execute("DELETE FROM inventory_cache WHERE expires_at<=?", (now,))
        return None


def put_cache(key: str, payload: Dict[str, Any], ttl_s: int = CACHE_TTL_S) -> None:
    now = time.time()
    with _conn() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO inventory_cache
                (cache_key, payload_json, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, json.dumps(payload), now + ttl_s, now),
        )


def count_recent_user_searches(email: str, window_s: int = 3600) -> int:
    cutoff = time.time() - window_s
    with _conn() as con:
        con.execute("DELETE FROM inventory_rate_events WHERE created_at<?", (cutoff - 60,))
        return int(
            con.execute(
                "SELECT COUNT(*) AS c FROM inventory_rate_events WHERE user_email=? AND created_at>=?",
                (email, cutoff),
            ).fetchone()["c"]
        )


def record_user_search(email: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO inventory_rate_events (user_email, created_at) VALUES (?, ?)",
            (email, time.time()),
        )


def write_audit_log(
    user_email: str,
    query: str,
    providers_checked: List[str],
    success: bool,
    cache_status: str,
    execution_time: float,
    failure_reason: Optional[str] = None,
) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT INTO inventory_audit_logs
                (user_email, query, providers_checked, success, failure_reason,
                 cache_status, execution_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_email,
                query,
                json.dumps(providers_checked),
                1 if success else 0,
                failure_reason,
                cache_status,
                execution_time,
                time.time(),
            ),
        )
