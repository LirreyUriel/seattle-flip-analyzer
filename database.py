"""
SQLite persistence for property cache, favorites, notes, and settings.
"""
import sqlite3
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from flip_scorer import DEFAULT_WEIGHTS

DB_PATH = Path(__file__).parent / "flip_analyzer.db"
_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS properties (
            id          TEXT PRIMARY KEY,
            data        TEXT NOT NULL,
            fetched_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS favorites (
            property_id TEXT PRIMARY KEY,
            added_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            property_id TEXT PRIMARY KEY,
            note        TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    c.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Properties ---

def upsert_properties(properties: list[dict]):
    c = _conn()
    ts = now_iso()
    c.execute("DELETE FROM properties")
    c.executemany(
        "INSERT INTO properties (id, data, fetched_at) VALUES (?, ?, ?)",
        [(p["id"], json.dumps(p), ts) for p in properties],
    )
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_updated', ?)", (ts,))
    c.commit()


def get_all_properties() -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT data FROM properties").fetchall()
    return [json.loads(r["data"]) for r in rows]


def get_property(pid: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT data FROM properties WHERE id = ?", (pid,)).fetchone()
    return json.loads(row["data"]) if row else None


def get_last_updated() -> str | None:
    c = _conn()
    row = c.execute("SELECT value FROM meta WHERE key = 'last_updated'").fetchone()
    return row["value"] if row else None


# --- Favorites ---

def get_favorites() -> set[str]:
    c = _conn()
    rows = c.execute("SELECT property_id FROM favorites").fetchall()
    return {r["property_id"] for r in rows}


def toggle_favorite(pid: str) -> bool:
    """Returns True if now favorited, False if unfavorited."""
    c = _conn()
    row = c.execute("SELECT property_id FROM favorites WHERE property_id = ?", (pid,)).fetchone()
    if row:
        c.execute("DELETE FROM favorites WHERE property_id = ?", (pid,))
        c.commit()
        return False
    else:
        c.execute("INSERT INTO favorites (property_id, added_at) VALUES (?, ?)", (pid, now_iso()))
        c.commit()
        return True


# --- Notes ---

def get_note(pid: str) -> str:
    c = _conn()
    row = c.execute("SELECT note FROM notes WHERE property_id = ?", (pid,)).fetchone()
    return row["note"] if row else ""


def save_note(pid: str, note: str):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO notes (property_id, note, updated_at) VALUES (?, ?, ?)",
        (pid, note, now_iso()),
    )
    c.commit()


# --- Settings ---

SETTINGS_DEFAULTS = {**DEFAULT_WEIGHTS, "max_price": 500000}


def get_settings() -> dict:
    c = _conn()
    row = c.execute("SELECT value FROM meta WHERE key = 'score_settings'").fetchone()
    if row:
        stored = json.loads(row["value"])
        return {**SETTINGS_DEFAULTS, **stored}
    return dict(SETTINGS_DEFAULTS)


def save_settings(settings: dict):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('score_settings', ?)",
        (json.dumps(settings),),
    )
    c.commit()


def update_property_scores(props: list[dict]):
    """Overwrite stored properties in-place (used after re-scoring)."""
    c = _conn()
    c.executemany(
        "UPDATE properties SET data = ? WHERE id = ?",
        [(json.dumps(p), p["id"]) for p in props],
    )
    c.commit()
