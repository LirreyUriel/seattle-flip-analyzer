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
            if not neighborhoods:
                neighborhoods = ["Rainier Valley", "Beacon Hill", "Ballard", "Capitol Hill"]
            
            comps = await fetch_redfin_comps(neighborhoods)
            if comps:
                database.save_comps(comps)
                log.info(f"Successfully cached {len(comps)} live sold comps.")
            else:
                log.warning("No comps returned — keeping existing data")
        except Exception as e:
            log.error(f"Failed to refresh comps: {e}")


async def refresh_properties():
    global _data_source
    async with _refresh_lock:
        log.info("Refreshing property data...")
        try:
            max_price = database.get_settings().get("max_price", 500000)
            props = await fetch_all_properties(RAPIDAPI_KEY, max_price=max_price)
            
            # ✓ שומרים מאגר עשיר של עד 200 נכסים כדי שיהיה ממה לפלטר בממשק
            database.upsert_properties(props[:200])
            
            sources = {p.get("source", "demo") for p in props}
            _data_source = "zillow" if "zillow" in sources else ("redfin" if "redfin" in sources else "demo")
            log.info(f"Loaded {len(props)} properties into database cache.")
        except Exception as e:
            log.error(f"Refresh failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    
    # Run initial refresh in background so server starts instantly
    asyncio.create_task(refresh_properties())
    asyncio.create_task(refresh_comps())
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(refresh_properties, 'interval', hours=REFRESH_HOURS)
    scheduler.add_job(refresh_comps, 'cron', hour=2, minute=0)  # once daily at 2 AM
    scheduler.start()
    
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SettingsUpdate(BaseModel):
    max_price: int
    w_roi: float
    w_renovation: float
    w_dom: float
    w_discount: float
    w_keywords: float


class NoteUpdate(BaseModel):
    note: str


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

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
    comps_only: bool = Query(False),      # ✓ הפרמטר החדש מחובר לחלוטין
):
    props = database.get_all_properties()
    favs = database.get_favorites()

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
            
        # 📊 סינון דינמי לפי קומפס בלבד (מיושר פיקס עם 8 ו-12 רווחים ללא שגיאות)
        if comps_only:
            arv_bd = p.get("arv_breakdown", {})
            if not arv_bd or not arv_bd.get("price_per_sqft"):
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

    # ✓ פתרון קריסת ה-500: שליפת מגבלה בטוחה מהמשתנה הגלובלי
    limit = globals().get("MAX_PROPERTIES", 30)
    final_properties = filtered[:limit]

    return {"properties": final_properties, "total": len(final_properties)}


@app.get("/api/properties/{prop_id}")
async def get_property(prop_id: str):
    props = database.get_all_properties()
    prop = next((p for p in props if p["id"] == prop_id), None)
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    
    prop["is_favorite"] = prop["id"] in database.get_favorites()
    prop["note"] = database.get_note(prop["id"])
    
    # Attach matched comps details if available
    all_comps = database.get_comps()
    n_low = prop["neighborhood"].lower().strip()
    
    # Use exact same match rule as builder
    seattle_sub_neighborhoods = ["rainier valley", "beacon hill", "white center", "delridge", "georgetown", "columbia city", "lake city", "northgate", "bitter lake"]
    matched = []
    for c in all_comps:
        c_nbhd = c.get("neighborhood", "").lower().strip()
        if c_nbhd == n_low or (c_nbhd == "seattle" and n_low in seattle_sub_neighborhoods) or (n_low in c_nbhd or c_nbhd in n_low):
            matched.append(c)
            
    prop["matched_comps"] = matched[:5]  # Top 5 recent comps for display
    return prop


@app.post("/api/properties/{prop_id}/favorite")
async def toggle_favorite(prop_id: str):
    favs = database.get_favorites()
    if prop_id in favs:
        database.remove_favorite(prop_id)
        return {"status": "removed"}
    else:
        database.add_favorite(prop_id)
        return {"status": "added"}


@app.post("/api/properties/{prop_id}/note")
async def update_note(prop_id: str, update: NoteUpdate):
    database.save_note(prop_id, update.note)
    return {"status": "ok"}


@app.get("/api/settings")
async def get_settings():
    return database.get_settings()


@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate):
    database.save_settings(settings.model_dump())
    
    # Force scores re-calculation instantly across all database entries
    current_settings = database.get_settings()
    weights = {k: v for k, v in current_settings.items() if k.startswith("w_")}
    cfg = database.get_model_config()
    
    props = database.get_all_properties()
    for p in props:
        p["flip_score"] = calculate_flip_score(p, weights, cfg)
        p["score_color"] = score_color(p["flip_score"])
    database.update_property_scores(props)
    
    return {"status": "ok"}


@app.get("/api/status")
async def get_status():
    props = database.get_all_properties()
    comps = database.get_comps()
    return {
        "data_source": _data_source,
        "total_properties": len(props),
        "total_comps_cached": len(comps),
        "rapidapi_key_configured": bool(RAPIDAPI_KEY)
    }


# ---------------------------------------------------------------------------
# Advanced Model Weights Configuration
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    return database.get_model_config()


@app.post("/api/config")
async def update_config(config: dict):
    # Quick validation structural keys
    required = ["neighborhoods", "distress_types", "renovation_levels", "keywords"]
    missing = [r for r in required if r not in config]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing structure keys: {missing}")

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


# ---------------------------------------------------------------------------
# SPA Static Files Routing
# ---------------------------------------------------------------------------

@app.get("/")
async def serve_index():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return {"detail": "Frontend application template static/index.html not found"}

# Mount remaining static artifacts assets scripts
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static"), name="static")
