"""
credentials.py — Encrypted credential store for Sentinel Web Agent

Fernet symmetric encryption, key derived from machine ID.
SQLite backend. Credentials NEVER logged, NEVER transmitted.
"""
import os
import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional, List, Dict

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "credentials.db")
logger = logging.getLogger(__name__)

# ─── Key derivation ───────────────────────────────────────────────────────────

def _get_machine_id() -> str:
    """
    Derive a stable machine-unique identifier for encryption key.
    Never transmitted — used only to derive local encryption key.
    """
    # Windows: most reliable source
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography"
        )
        guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return f"win-{guid}"
    except Exception:
        pass

    # Linux/macOS: /etc/machine-id
    for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
        try:
            with open(path) as f:
                mid = f.read().strip()
                if mid:
                    return f"linux-{mid}"
        except Exception:
            pass

    # Fallback: MAC address (stable per NIC)
    import uuid
    return f"mac-{uuid.getnode()}"


def _derive_fernet_key(machine_id: str) -> bytes:
    """Derive a 32-byte Fernet key from machine ID using PBKDF2-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"sentinel_web_cred_v1",
        iterations=200_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))


# Singleton cipher (derived once per process)
_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        machine_id = _get_machine_id()
        key = _derive_fernet_key(machine_id)
        _fernet = Fernet(key)
    return _fernet


def _encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ─── Database ─────────────────────────────────────────────────────────────────

def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def _conn():
    _ensure_dir()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                site       TEXT    UNIQUE NOT NULL,
                username_e TEXT    NOT NULL,
                password_e TEXT    NOT NULL,
                created_at TEXT    DEFAULT (datetime('now')),
                updated_at TEXT    DEFAULT (datetime('now'))
            )
        """)


# ─── Public CRUD ──────────────────────────────────────────────────────────────

def save_credentials(site: str, username: str, password: str) -> None:
    """
    Encrypt and store credentials for a site.
    Silently replaces if site already exists.
    """
    site = site.strip().lower()
    u_enc = _encrypt(username)
    p_enc = _encrypt(password)

    with _conn() as con:
        con.execute("""
            INSERT INTO credentials (site, username_e, password_e)
            VALUES (?, ?, ?)
            ON CONFLICT(site) DO UPDATE SET
                username_e = excluded.username_e,
                password_e = excluded.password_e,
                updated_at = datetime('now')
        """, (site, u_enc, p_enc))

    logger.info(f"Credentials saved for site: {site}")


def get_credentials(site: str) -> Optional[Dict[str, str]]:
    """
    Return decrypted {username, password} for a site, or None.
    Credentials are NEVER logged at any level.
    """
    site = site.strip().lower()
    with _conn() as con:
        row = con.execute(
            "SELECT username_e, password_e FROM credentials WHERE site = ?", (site,)
        ).fetchone()

    if not row:
        return None

    try:
        return {
            "username": _decrypt(row["username_e"]),
            "password": _decrypt(row["password_e"]),
        }
    except Exception as e:
        logger.error(f"Failed to decrypt credentials for {site}: {type(e).__name__}")
        return None


def list_sites() -> List[str]:
    """Return list of site names only — never returns credentials."""
    with _conn() as con:
        rows = con.execute(
            "SELECT site FROM credentials ORDER BY site"
        ).fetchall()
    return [r["site"] for r in rows]


def delete_credentials(site: str) -> bool:
    """Delete credentials for a site. Returns True if deleted, False if not found."""
    site = site.strip().lower()
    with _conn() as con:
        cur = con.execute("DELETE FROM credentials WHERE site = ?", (site,))
        deleted = cur.rowcount > 0
    if deleted:
        logger.info(f"Credentials deleted for site: {site}")
    return deleted


# Initialise on import
init_db()
