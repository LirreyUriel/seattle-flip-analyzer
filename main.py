"""
Seattle Flip Analyzer — FastAPI backend
Run: uvicorn main:app --reload --port 8000
"""
import os
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import database
from data_fetcher import fetch_all_properties
from flip_scorer import calculate_flip_score, score_color, DEFAULT_WEIGHTS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
REFRESH_HOURS = int(os.getenv("REFRESH_HOURS", "3"))
MAX_PROPERTIES = int(os.getenv("MAX_PROPERTIES", "30"))

_refresh_lock = asyncio.Lock()
_data_source = "demo"


async def refresh_properties():
    global _data_source
    async with _refresh_lock:
        log.info("Refreshing property data...")
        try:
            max_price = database.get_settings().get("max_price", 500000)
            props = await fetch_all_properties(RAPIDAPI_KEY, max_price=max_price)
            database.upsert_properties(props[:MAX_PROPERTIES])
            sources = {p.get("source", "demo") for p in props}
            _data_source = "zillow" if "zillow" in sources else ("redfin" if "redfin" in sources else "demo")
            log.info(f"Loaded {len(props)} properties from {_data_source}")
        except Exception as e:
            log.error(f"Refresh failed: {e}")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    # Always refresh on startup so URL changes take effect immediately
    await refresh_properties()

    scheduler.add_job(refresh_properties, "interval", hours=REFRESH_HOURS, id="refresh")
    scheduler.start()
    log.info(f"Scheduler started — refreshing every {REFRESH_HOURS}h")
    yield
    scheduler.shutdown()


app = FastAPI(title="Seattle Flip Analyzer", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse("static/index.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    props = database.get_all_properties()
    return {
        "property_count": len(props),
        "last_updated": database.get_last_updated(),
        "data_source": _data_source,
        "refresh_hours": REFRESH_HOURS,
        "demo_mode": _data_source == "demo",
    }


@app.get("/api/properties")
async def list_properties(
    min_score: int = Query(0, ge=0, le=100),
    max_score: int = Query(100, ge=0, le=100),
    neighborhood: str = Query(""),
    distress_type: str = Query(""),
    min_price: int = Query(0, ge=0),
    max_price: int = Query(500000, ge=0),
    property_type: str = Query(""),
    sort_by: str = Query("score"),       # score | price | dom | roi
    sort_dir: str = Query("desc"),        # asc | desc
    favorites_only: bool = Query(False),
):
    props = database.get_all_properties()
    favs = database.get_favorites()
    notes_map = {pid: database.get_note(pid) for pid in favs}

    # Enrich with user data
    for p in props:
        p["is_favorite"] = p["id"] in favs
        p["note"] = database.get_note(p["id"])

    # Filter
    filtered = []
    for p in props:
        if not (min_score <= p.get("flip_score", 0) <= max_score):
            continue
        if not (min_price <= p.get("price", 0) <= max_price):
            continue
        if neighborhood and neighborhood.lower() not in p.get("neighborhood", "").lower():
            continue
        if distress_type and distress_type.lower() not in p.get("distress_type", "").lower():
            continue
        if property_type and property_type.lower() not in p.get("property_type", "").lower():
            continue
        if favorites_only and not p.get("is_favorite"):
            continue
        filtered.append(p)

    # Sort
    sort_key_map = {
        "score": "flip_score",
        "price": "price",
        "dom": "dom",
        "roi": "roi_pct",
    }
    key = sort_key_map.get(sort_by, "flip_score")
    reverse = sort_dir.lower() != "asc"
    filtered.sort(key=lambda x: x.get(key, 0), reverse=reverse)

    return {"properties": filtered, "total": len(filtered)}


@app.get("/api/properties/{property_id}")
async def get_property(property_id: str):
    prop = database.get_property(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    favs = database.get_favorites()
    prop["is_favorite"] = property_id in favs
    prop["note"] = database.get_note(property_id)
    return prop




# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

@app.post("/api/favorites/{property_id}")
async def toggle_favorite(property_id: str):
    prop = database.get_property(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    is_fav = database.toggle_favorite(property_id)
    return {"property_id": property_id, "is_favorite": is_fav}


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class NotePayload(BaseModel):
    note: str


@app.get("/api/notes/{property_id}")
async def get_note(property_id: str):
    return {"property_id": property_id, "note": database.get_note(property_id)}


@app.post("/api/notes/{property_id}")
async def save_note(property_id: str, payload: NotePayload):
    prop = database.get_property(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    database.save_note(property_id, payload.note)
    return {"property_id": property_id, "note": payload.note}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsPayload(BaseModel):
    # Profitability (40%)
    w_arv:             float
    w_roi:             float
    # Execution Efficiency (20%)
    w_profit_reno:     float
    w_reno_level:      float
    # Liquidity & Asset Risk (25%)
    w_size:            float
    w_structural:      float
    w_market_velocity: float
    # Market Momentum (15%)
    w_distress:        float
    w_neighborhood:    float
    max_price:         int = 500000


@app.get("/api/settings")
async def get_settings():
    return database.get_settings()


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload):
    old = database.get_settings()
    data = payload.model_dump()

    # Normalize only the weight fields (not max_price)
    weights = {k: v for k, v in data.items() if k.startswith("w_")}
    total = sum(weights.values()) or 1
    weights = {k: round(v / total * 100, 2) for k, v in weights.items()}

    new_settings = {**weights, "max_price": data["max_price"]}
    database.save_settings(new_settings)

    price_changed = old.get("max_price", 500000) != new_settings["max_price"]

    if price_changed:
        # Re-fetch with new price ceiling (background task)
        asyncio.create_task(refresh_properties())
        log.info(f"Max price changed to ${new_settings['max_price']:,} — re-fetching")
        return {"status": "refetching", "settings": new_settings, "rescored": 0}
    else:
        # Just re-score cached properties
        props = database.get_all_properties()
        for p in props:
            p["flip_score"] = calculate_flip_score(p, weights)
            p["score_color"] = score_color(p["flip_score"])
        database.update_property_scores(props)
        log.info(f"Re-scored {len(props)} properties with new weights")
        return {"status": "ok", "settings": new_settings, "rescored": len(props)}


@app.post("/api/settings/reset")
async def reset_settings():
    from database import SETTINGS_DEFAULTS
    database.save_settings(dict(SETTINGS_DEFAULTS))
    props = database.get_all_properties()
    for p in props:
        p["flip_score"] = calculate_flip_score(p)
        p["score_color"] = score_color(p["flip_score"])
    database.update_property_scores(props)
    return {"status": "ok", "settings": SETTINGS_DEFAULTS, "rescored": len(props)}


# ---------------------------------------------------------------------------
# Manual refresh
# ---------------------------------------------------------------------------

@app.post("/api/refresh")
async def manual_refresh():
    if _refresh_lock.locked():
        return {"status": "already_refreshing"}
    asyncio.create_task(refresh_properties())
    return {"status": "refresh_started"}
