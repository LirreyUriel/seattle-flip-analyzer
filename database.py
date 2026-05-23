"""
SQLite persistence for property cache, favorites, notes, settings, and model config.
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


# ---------------------------------------------------------------------------
# Default config values (mirrors what was hardcoded in flip_scorer.py)
# ---------------------------------------------------------------------------

DEFAULT_NEIGHBORHOODS = [
    {"name": "Rainier Valley",  "arv_psf": 490, "avg_dom": 28, "tier": "top"},
    {"name": "Beacon Hill",     "arv_psf": 515, "avg_dom": 22, "tier": "top"},
    {"name": "White Center",    "arv_psf": 450, "avg_dom": 32, "tier": "top"},
    {"name": "Delridge",        "arv_psf": 465, "avg_dom": 30, "tier": "top"},
    {"name": "Georgetown",      "arv_psf": 500, "avg_dom": 35, "tier": "top"},
    {"name": "Columbia City",   "arv_psf": 530, "avg_dom": 18, "tier": "mid"},
    {"name": "West Seattle",    "arv_psf": 545, "avg_dom": 20, "tier": "mid"},
    {"name": "Northgate",       "arv_psf": 510, "avg_dom": 21, "tier": "mid"},
    {"name": "Lake City",       "arv_psf": 495, "avg_dom": 25, "tier": "other"},
    {"name": "Bitter Lake",     "arv_psf": 475, "avg_dom": 27, "tier": "other"},
    {"name": "Crown Hill",      "arv_psf": 520, "avg_dom": 24, "tier": "other"},
    {"name": "Maple Leaf",      "arv_psf": 545, "avg_dom": 22, "tier": "other"},
    {"name": "default",         "arv_psf": 510, "avg_dom": 22, "tier": "other"},
]

DEFAULT_RENO_CONFIG = {
    "levels": {
        "light":  {"cost_psf": 28, "score": 100},
        "medium": {"cost_psf": 52, "score": 50},
        "heavy":  {"cost_psf": 85, "score": 10},
    },
    "age_multipliers": [
        {"min_age": 66, "multiplier": 1.18},
        {"min_age": 46, "multiplier": 1.08},
        {"min_age": 0,  "multiplier": 1.00},
    ],
    "heavy_keywords": ["gut", "uninhabitable", "major renovation", "tear down", "condemned", "foundation issue"],
    "medium_keywords": ["fixer", "fixer-upper", "tlc", "as-is", "as is", "needs work",
                        "needs updating", "investor special", "cash only", "handyman",
                        "estate sale", "full renovation", "full update"],
    "breakdown_pct": {
        "Kitchen":                    0.20,
        "Bathrooms":                  0.15,
        "Flooring":                   0.12,
        "Roof & Exterior":            0.15,
        "HVAC / Plumbing / Electric": 0.18,
        "Windows & Doors":            0.08,
        "Landscaping":                0.05,
        "Permits & Overhead":         0.07,
    },
    "property_type_discounts": {
        "condo":     0.85,
        "townhouse": 0.95,
    },
    "score_thresholds": {
        "arv_target_equity_pct":  30,
        "roi_target_pct":         25,
        "profit_reno_ratio_tiers": [
            {"min": 2.0, "score": 100},
            {"min": 1.0, "score": 75},
            {"min": 0.5, "score": 50},
            {"min": 0.0, "score": 25},
        ],
        "size_tiers": [
            {"min": 800,  "max": 2500, "score": 100},
            {"min": 600,  "max": 799,  "score": 75},
            {"min": 2501, "max": 3500, "score": 75},
            {"min": 500,  "max": 599,  "score": 45},
            {"min": 3501, "max": 4500, "score": 45},
            {"min": 1,    "max": 499,  "score": 10},
        ],
        "struct_year_tiers": [
            {"min_year": 2000, "score": 100},
            {"min_year": 1990, "score": 82},
            {"min_year": 1980, "score": 60},
            {"min_year": 1970, "score": 38},
            {"min_year": 1960, "score": 20},
            {"min_year": 0,    "score": 8},
        ],
        "dom_ratio_tiers": [
            {"max_ratio": 0.50, "score": 100},
            {"max_ratio": 0.75, "score": 85},
            {"max_ratio": 1.00, "score": 70},
            {"max_ratio": 1.50, "score": 50},
            {"max_ratio": 2.00, "score": 30},
            {"max_ratio": 3.00, "score": 15},
            {"max_ratio": 999,  "score": 5},
        ],
        "distress_kw_points":       15,
        "distress_kw_max":          50,
        "distress_reduction_pts":   8,
        "distress_reduction_pct_pts": 1.2,
        "distress_reduction_max":   30,
        "distress_bom_bonus":       20,
    },
}

DEFAULT_DISTRESS_KEYWORDS = [
    "as-is", "as is", "fixer", "fixer-upper", "estate sale",
    "reo", "bank owned", "bank-owned", "short sale", "tlc",
    "needs work", "needs updating", "investor special", "cash only",
    "handyman", "distressed", "motivated seller", "price reduced",
    "probate", "foreclosure", "pre-foreclosure", "back on market",
]


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
        CREATE TABLE IF NOT EXISTS property_status (
            property_id TEXT PRIMARY KEY,
            status      TEXT NOT NULL DEFAULT 'new',
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    c.commit()
    _seed_config_if_empty()


def _seed_config_if_empty():
    """Populate config tables with defaults on first run."""
    c = _conn()
    row = c.execute("SELECT value FROM meta WHERE key = 'model_config'").fetchone()
    if not row:
        save_model_config({
            "neighborhoods":     DEFAULT_NEIGHBORHOODS,
            "reno_config":       DEFAULT_RENO_CONFIG,
            "distress_keywords": DEFAULT_DISTRESS_KEYWORDS,
        })


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

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


def update_property_scores(props: list[dict]):
    """Overwrite stored properties in-place (used after re-scoring)."""
    c = _conn()
    c.executemany(
        "UPDATE properties SET data = ? WHERE id = ?",
        [(json.dumps(p), p["id"]) for p in props],
    )
    c.commit()


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

def get_favorites() -> set[str]:
    c = _conn()
    rows = c.execute("SELECT property_id FROM favorites").fetchall()
    return {r["property_id"] for r in rows}


def toggle_favorite(pid: str) -> bool:
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


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Property Status
# ---------------------------------------------------------------------------

VALID_STATUSES = {"new", "waiting", "ongoing", "irrelevant"}


def get_status(pid: str) -> str:
    c = _conn()
    row = c.execute("SELECT status FROM property_status WHERE property_id = ?", (pid,)).fetchone()
    return row["status"] if row else "new"


def get_all_statuses() -> dict[str, str]:
    c = _conn()
    rows = c.execute("SELECT property_id, status FROM property_status").fetchall()
    return {r["property_id"]: r["status"] for r in rows}


def set_status(pid: str, status: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO property_status (property_id, status, updated_at) VALUES (?, ?, ?)",
        (pid, status, now_iso()),
    )
    c.commit()
    return status


# ---------------------------------------------------------------------------
# Settings (weights + max_price)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Model Config (neighborhoods, reno, keywords)
# ---------------------------------------------------------------------------

def get_model_config() -> dict:
    c = _conn()
    row = c.execute("SELECT value FROM meta WHERE key = 'model_config'").fetchone()
    if row:
        return json.loads(row["value"])
    return {
        "neighborhoods":     DEFAULT_NEIGHBORHOODS,
        "reno_config":       DEFAULT_RENO_CONFIG,
        "distress_keywords": DEFAULT_DISTRESS_KEYWORDS,
    }


def save_model_config(config: dict):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('model_config', ?)",
        (json.dumps(config),),
    )
    c.commit()


def reset_model_config():
    save_model_config({
        "neighborhoods":     DEFAULT_NEIGHBORHOODS,
        "reno_config":       DEFAULT_RENO_CONFIG,
        "distress_keywords": DEFAULT_DISTRESS_KEYWORDS,
    })
