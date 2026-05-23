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
from data_fetcher import fetch_all_properties, fetch_redfin_comps
from flip_scorer import calculate_flip_score, score_color, DEFAULT_WEIGHTS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
REFRESH_HOURS = int(os.getenv("REFRESH_HOURS", "3"))
MAX_PROPERTIES = int(os.getenv("MAX_PROPERTIES", "200"))

_refresh_lock = asyncio.Lock()
_comps_lock   = asyncio.Lock()
_data_source = "demo"


async def refresh_comps():
    """Fetch sold comps from Redfin once daily — used for ARV calculation."""
    async with _comps_lock:
        log.info("Refreshing comps data...")
        try:
            cfg = database.get_model_config()
            neighborhoods = [
                n["name"] for n in cfg["neighborhoods"]
                if n["name"].lower() != "default"
            ]
            comps = await fetch_redfin_comps(neighborhoods)
            if comps:
                database.upsert_comps(comps)
                log.info(f"Comps updated: {len(comps)} records")
            else:
                log.warning("No comps returned — keeping existing data")
        except Exception as e:
            log.error(f"Comps refresh failed: {e}")


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
    await refresh_properties()
    await refresh_comps()   # initial comps load on startup
    scheduler.add_job(refresh_properties, "interval", hours=REFRESH_HOURS, id="refresh")
    scheduler.add_job(refresh_comps, "interval", hours=24, id="refresh_comps")
    scheduler.start()
    log.info(f"Scheduler started — properties every {REFRESH_HOURS}h, comps every 24h")
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
# API routes — status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    props = database.get_all_properties()
    return {
        "property_count": len(props),
        "last_updated": database.get_last_updated(),
        "comps_last_updated": database.get_comps_last_updated(),
        "data_source": _data_source,
        "refresh_hours": REFRESH_HOURS,
        "demo_mode": _data_source == "demo",
    }


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@app.get("/api/properties")
async def list_properties(
    min_score: int = Query(0, ge=0, le=100),
    max_score: int = Query(100, ge=0, le=100),
    neighborhood: str = Query(""),
    distress_type: str = Query(""),
    min_price: int = Query(0, ge=0),
    max_price: int = Query(1500000, ge=0),
    property_type: str = Query(""),
    status_filter: str = Query(""),          # new | waiting | ongoing | irrelevant
    sort_by: str = Query("score"),            # score | price | dom | roi
    sort_dir: str = Query("desc"),
    favorites_only: bool = Query(False),
    comps_only: bool = Query(False),
):
    props = database.get_all_properties()
    favs = database.get_favorites()
    statuses = database.get_all_statuses()

    for p in props:
        p["is_favorite"] = p["id"] in favs
        p["note"] = database.get_note(p["id"])
        p["status"] = statuses.get(p["id"], "new")

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
        if status_filter and p.get("status") != status_filter:
            continue
        if comps_only:
            arv_bd = p.get("arv_breakdown", {})
            # אם אין מחיר למטר רבוע מבוסס קומפס, או שהנכס לא סומן כחישוב מקומפס, נדלג עליו
        if not arv_bd or not arv_bd.get("price_per_sqft"):
            continue
        filtered.append(p)

    sort_key_map = {"score": "flip_score", "price": "price", "dom": "dom", "roi": "roi_pct"}
    key = sort_key_map.get(sort_by, "flip_score")
    reverse = sort_dir.lower() != "asc"
    filtered.sort(key=lambda x: x.get(key, 0), reverse=reverse)
    final_properties = filtered[:MAX_PROPERTIES]
    return {"properties": filtered, "total": len(filtered)}


@app.get("/api/properties/{property_id}")
async def get_property(property_id: str):
    prop = database.get_property(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    favs = database.get_favorites()
    prop["is_favorite"] = property_id in favs
    prop["note"] = database.get_note(property_id)
    prop["status"] = database.get_status(property_id)
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
# Property Status
# ---------------------------------------------------------------------------

class StatusPayload(BaseModel):
    status: str  # new | waiting | ongoing | irrelevant


@app.post("/api/status/{property_id}")
async def set_property_status(property_id: str, payload: StatusPayload):
    prop = database.get_property(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    try:
        status = database.set_status(property_id, payload.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"property_id": property_id, "status": status}


# ---------------------------------------------------------------------------
# Settings (weights + max_price)
# ---------------------------------------------------------------------------

class SettingsPayload(BaseModel):
    w_arv:             float
    w_roi:             float
    w_profit_reno:     float
    w_reno_level:      float
    w_size:            float
    w_structural:      float
    w_market_velocity: float
    w_distress:        float
    w_neighborhood:    float
    max_price:         int = 1500000


@app.get("/api/settings")
async def get_settings():
    return database.get_settings()


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload):
    old = database.get_settings()
    data = payload.model_dump()

    weights = {k: v for k, v in data.items() if k.startswith("w_")}
    total = sum(weights.values()) or 1
    weights = {k: round(v / total * 100, 2) for k, v in weights.items()}

    new_settings = {**weights, "max_price": data["max_price"]}
    database.save_settings(new_settings)

    price_changed = old.get("max_price", 500000) != new_settings["max_price"]

    if price_changed:
        asyncio.create_task(refresh_properties())
        log.info(f"Max price changed to ${new_settings['max_price']:,} — re-fetching")
        return {"status": "refetching", "settings": new_settings, "rescored": 0}
    else:
        cfg = database.get_model_config()
        props = database.get_all_properties()
        for p in props:
            p["flip_score"] = calculate_flip_score(p, weights, cfg)
            p["score_color"] = score_color(p["flip_score"])
        database.update_property_scores(props)
        log.info(f"Re-scored {len(props)} properties with new weights")
        return {"status": "ok", "settings": new_settings, "rescored": len(props)}


@app.post("/api/settings/reset")
async def reset_settings():
    from database import SETTINGS_DEFAULTS
    database.save_settings(dict(SETTINGS_DEFAULTS))
    cfg = database.get_model_config()
    props = database.get_all_properties()
    for p in props:
        p["flip_score"] = calculate_flip_score(p, None, cfg)
        p["score_color"] = score_color(p["flip_score"])
    database.update_property_scores(props)
    return {"status": "ok", "settings": SETTINGS_DEFAULTS, "rescored": len(props)}


# ---------------------------------------------------------------------------
# Model Config (neighborhoods, reno, keywords) — single GET/POST/reset
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    return database.get_model_config()


@app.post("/api/config")
async def save_config(config: dict):
    # Basic validation
    required = {"neighborhoods", "reno_config", "distress_keywords"}
    missing = required - set(config.keys())
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing keys: {missing}")

    database.save_model_config(config)

    # Re-score all properties with new config
    settings = database.get_settings()
    weights = {k: v for k, v in settings.items() if k.startswith("w_")}
    props = database.get_all_properties()
    for p in props:
        p["flip_score"] = calculate_flip_score(p, weights, config)
        p["score_color"] = score_color(p["flip_score"])
    database.update_property_scores(props)

    log.info(f"Model config updated — re-scored {len(props)} properties")
    return {"status": "ok", "rescored": len(props)}


@app.post("/api/config/reset")
async def reset_config():
    database.reset_model_config()
    cfg = database.get_model_config()
    settings = database.get_settings()
    weights = {k: v for k, v in settings.items() if k.startswith("w_")}
    props = database.get_all_properties()
    for p in props:
        p["flip_score"] = calculate_flip_score(p, weights, cfg)
        p["score_color"] = score_color(p["flip_score"])
    database.update_property_scores(props)
    return {"status": "ok", "config": cfg, "rescored": len(props)}


# ---------------------------------------------------------------------------
# Manual refresh
# ---------------------------------------------------------------------------

@app.post("/api/refresh")
async def manual_refresh():
    if _refresh_lock.locked():
        return {"status": "already_refreshing"}
    asyncio.create_task(refresh_properties())
    return {"status": "refresh_started"}
