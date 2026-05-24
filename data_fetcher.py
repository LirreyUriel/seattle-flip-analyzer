"""
Property data fetching: Zillow API (RapidAPI) + Redfin internal API + King County open data + demo mode.
"""
import os
import re
import json
import uuid
import random
import hashlib
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

from flip_scorer import (
    estimate_arv, estimate_renovation, calculate_flip_score,
    calculate_roi, score_color, get_arv_breakdown, count_distress_keywords,
    NEIGHBORHOOD_AVG_DOM,
)

# ---------------------------------------------------------------------------
# Mock seed data — 30 realistic Seattle distressed properties
# ---------------------------------------------------------------------------
MOCK_SEEDS = [
    # --- Rainier Valley (6) ---
    {"address": "4521 Rainier Ave S", "neighborhood": "Rainier Valley",
     "price": 395000, "beds": 3, "baths": 1.5, "sqft": 1320, "lot_sqft": 4800,
     "year_built": 1948, "property_type": "SFH", "distress_type": "REO",
     "dom": 67, "price_reductions": 2, "price_reduction_pct": 8.5, "back_on_market": False,
     "description": "Bank-owned REO property sold as-is. 3 bed/1.5 bath fixer-upper needs full renovation. TLC required throughout — kitchen, baths, flooring, exterior. Cash or conventional only. Investor special in rapidly appreciating Rainier Valley."},

    {"address": "3847 S Alaska St", "neighborhood": "Rainier Valley",
     "price": 419000, "beds": 4, "baths": 1, "sqft": 1750, "lot_sqft": 5600,
     "year_built": 1942, "property_type": "SFH", "distress_type": "Pre-Foreclosure",
     "dom": 89, "price_reductions": 3, "price_reduction_pct": 12.0, "back_on_market": False,
     "description": "Pre-foreclosure opportunity! Motivated seller needs quick close. Property needs significant updating — kitchen, bathrooms, flooring, and exterior all need work. As-is sale, no disclosures."},

    {"address": "5234 Martin Luther King Jr Way S", "neighborhood": "Rainier Valley",
     "price": 355000, "beds": 2, "baths": 1, "sqft": 980, "lot_sqft": 3200,
     "year_built": 1935, "property_type": "SFH", "distress_type": "Estate Sale",
     "dom": 54, "price_reductions": 1, "price_reduction_pct": 4.8, "back_on_market": True,
     "description": "Back on market — previous financing fell through! Estate sale, family selling as-is. Original condition 1935 home needs full update. Great corner lot. Probate sale, cash preferred."},

    {"address": "6102 Rainier Ave S", "neighborhood": "Rainier Valley",
     "price": 445000, "beds": 3, "baths": 2, "sqft": 1480, "lot_sqft": 5200,
     "year_built": 1955, "property_type": "SFH", "distress_type": "Standard",
     "dom": 38, "price_reductions": 1, "price_reduction_pct": 5.0, "back_on_market": False,
     "description": "Fixer-upper with great bones. Needs cosmetic updates — kitchen remodel, new flooring, fresh paint. Good structure and mechanicals. Large lot with ADU potential. TLC needed throughout."},

    {"address": "4015 S Othello St", "neighborhood": "Rainier Valley",
     "price": 480000, "beds": 3, "baths": 2, "sqft": 1650, "lot_sqft": 6000,
     "year_built": 1960, "property_type": "SFH", "distress_type": "Short Sale",
     "dom": 72, "price_reductions": 2, "price_reduction_pct": 7.5, "back_on_market": False,
     "description": "Short sale subject to lender approval. Sold as-is, no warranties or repairs. Property needs updating throughout. Handyman special priced below market. Investor opportunity."},

    {"address": "3201 Rainier Ave S Unit 4", "neighborhood": "Rainier Valley",
     "price": 285000, "beds": 2, "baths": 1, "sqft": 875, "lot_sqft": 0,
     "year_built": 1968, "property_type": "Condo", "distress_type": "REO",
     "dom": 95, "price_reductions": 3, "price_reduction_pct": 15.2, "back_on_market": True,
     "description": "Bank-owned REO condo back on market after previous offer fell through. Sold strictly as-is, no repairs or credits. Unit needs complete renovation. Low price reflects condition. Cash buyers preferred."},

    # --- Beacon Hill (5) ---
    {"address": "2847 Beacon Ave S", "neighborhood": "Beacon Hill",
     "price": 425000, "beds": 3, "baths": 2, "sqft": 1580, "lot_sqft": 5000,
     "year_built": 1952, "property_type": "SFH", "distress_type": "Estate Sale",
     "dom": 42, "price_reductions": 1, "price_reduction_pct": 5.2, "back_on_market": False,
     "description": "Estate sale — sold as-is. Original 1952 construction, needs full updating. Great bones in desirable Beacon Hill. Kitchen, baths, and flooring all need work. Probate sale, no repairs."},

    {"address": "1508 S Columbian Way", "neighborhood": "Beacon Hill",
     "price": 389000, "beds": 2, "baths": 1, "sqft": 1120, "lot_sqft": 4000,
     "year_built": 1945, "property_type": "SFH", "distress_type": "REO",
     "dom": 58, "price_reductions": 2, "price_reduction_pct": 9.3, "back_on_market": False,
     "description": "REO bank-owned property. As-is sale — no warranties, no disclosures. Fixer-upper needing significant renovation. TLC special in Beacon Hill. Strong post-renovation rental or resale potential."},

    {"address": "3102 Jefferson Ave S", "neighborhood": "Beacon Hill",
     "price": 465000, "beds": 4, "baths": 2, "sqft": 1840, "lot_sqft": 6400,
     "year_built": 1958, "property_type": "SFH", "distress_type": "Pre-Foreclosure",
     "dom": 33, "price_reductions": 1, "price_reduction_pct": 3.8, "back_on_market": False,
     "description": "Pre-foreclosure — motivated seller needs quick close. Property has been rented and needs investor-level updating throughout. As-is sale priced accordingly."},

    {"address": "2411 S Forest St", "neighborhood": "Beacon Hill",
     "price": 499000, "beds": 3, "baths": 2.5, "sqft": 1720, "lot_sqft": 5800,
     "year_built": 1963, "property_type": "SFH", "distress_type": "Back on Market",
     "dom": 47, "price_reductions": 1, "price_reduction_pct": 4.0, "back_on_market": True,
     "description": "Back on market! Buyer financing fell through. Below-market Beacon Hill SFH. Needs cosmetic updates — paint, flooring, minor kitchen refresh. Good mechanicals. Light TLC."},

    {"address": "3560 Beacon Ave S Unit 102", "neighborhood": "Beacon Hill",
     "price": 295000, "beds": 2, "baths": 1, "sqft": 920, "lot_sqft": 0,
     "year_built": 1978, "property_type": "Condo", "distress_type": "Short Sale",
     "dom": 61, "price_reductions": 2, "price_reduction_pct": 8.0, "back_on_market": False,
     "description": "Short sale pending bank approval. Condo sold as-is in original condition. Needs updating but priced well below market. Ground floor unit. As-is, no credits or concessions."},

    # --- White Center (4) ---
    {"address": "9847 16th Ave SW", "neighborhood": "White Center",
     "price": 365000, "beds": 3, "baths": 1, "sqft": 1280, "lot_sqft": 5000,
     "year_built": 1946, "property_type": "SFH", "distress_type": "REO",
     "dom": 78, "price_reductions": 3, "price_reduction_pct": 11.8, "back_on_market": False,
     "description": "Bank-owned REO. Sold as-is, no disclosures. White Center SFH — fixer-upper opportunity in up-and-coming neighborhood. Major renovation needed. Cash buyers preferred. Handyman special."},

    {"address": "10234 8th Ave SW", "neighborhood": "White Center",
     "price": 385000, "beds": 3, "baths": 1.5, "sqft": 1390, "lot_sqft": 5600,
     "year_built": 1952, "property_type": "SFH", "distress_type": "Estate Sale",
     "dom": 51, "price_reductions": 1, "price_reduction_pct": 4.5, "back_on_market": True,
     "description": "Back on market! Estate sale — family selling as-is after long-time owner passed. 1952 home needs full renovation. Probate sale, strictly as-is. Great value-add opportunity."},

    {"address": "9415 Delridge Way SW", "neighborhood": "White Center",
     "price": 420000, "beds": 4, "baths": 2, "sqft": 1680, "lot_sqft": 7200,
     "year_built": 1960, "property_type": "SFH", "distress_type": "Pre-Foreclosure",
     "dom": 44, "price_reductions": 2, "price_reduction_pct": 6.3, "back_on_market": False,
     "description": "Pre-foreclosure opportunity. Large lot SFH needs significant TLC. As-is sale. Kitchen and bathrooms dated, flooring needs replacement. Good structural bones. Motivated seller."},

    {"address": "10521 17th Ave SW", "neighborhood": "White Center",
     "price": 340000, "beds": 2, "baths": 1, "sqft": 980, "lot_sqft": 4200,
     "year_built": 1940, "property_type": "SFH", "distress_type": "REO",
     "dom": 102, "price_reductions": 4, "price_reduction_pct": 16.2, "back_on_market": True,
     "description": "REO bank-owned — back on market for 2nd time. Heavily distressed property needs gut renovation. Foundation issues disclosed. Sold as-is. Deep value play for experienced investor. Cash only."},

    # --- Delridge (4) ---
    {"address": "5821 Delridge Way SW", "neighborhood": "Delridge",
     "price": 378000, "beds": 3, "baths": 1, "sqft": 1340, "lot_sqft": 5200,
     "year_built": 1950, "property_type": "SFH", "distress_type": "REO",
     "dom": 64, "price_reductions": 2, "price_reduction_pct": 7.8, "back_on_market": False,
     "description": "Bank-owned REO property. Fixer-upper sold strictly as-is. Delridge SFH needs full renovation — kitchen, baths, flooring, paint, exterior all need work. TLC needed. Investor opportunity."},

    {"address": "6234 12th Ave SW", "neighborhood": "Delridge",
     "price": 415000, "beds": 3, "baths": 2, "sqft": 1510, "lot_sqft": 6100,
     "year_built": 1955, "property_type": "SFH", "distress_type": "Estate Sale",
     "dom": 38, "price_reductions": 1, "price_reduction_pct": 4.2, "back_on_market": False,
     "description": "Estate sale opportunity. Family selling parents' home of 35 years as-is. Needs updating throughout — kitchen, bathrooms, flooring, windows. Great neighborhood, motivated seller."},

    {"address": "5109 SW Charlestown St", "neighborhood": "Delridge",
     "price": 449000, "beds": 4, "baths": 2, "sqft": 1780, "lot_sqft": 7800,
     "year_built": 1948, "property_type": "SFH", "distress_type": "Back on Market",
     "dom": 55, "price_reductions": 2, "price_reduction_pct": 6.5, "back_on_market": True,
     "description": "Back on market — first buyer got cold feet at inspection. Good value SFH in Delridge. Needs cosmetic work — paint, carpet, kitchen refresh. As-is sale. Seller motivated. Large lot."},

    {"address": "4823 SW Alaska St", "neighborhood": "Delridge",
     "price": 398000, "beds": 3, "baths": 1.5, "sqft": 1290, "lot_sqft": 4600,
     "year_built": 1962, "property_type": "SFH", "distress_type": "Pre-Foreclosure",
     "dom": 47, "price_reductions": 1, "price_reduction_pct": 5.0, "back_on_market": False,
     "description": "Pre-foreclosure listing. Owner behind on payments, motivated to sell quickly. Property needs work but has good potential. Kitchen and baths need update. As-is sale."},

    # --- Georgetown (3) ---
    {"address": "5847 Airport Way S", "neighborhood": "Georgetown",
     "price": 389000, "beds": 2, "baths": 1, "sqft": 1050, "lot_sqft": 3800,
     "year_built": 1938, "property_type": "SFH", "distress_type": "REO",
     "dom": 71, "price_reductions": 2, "price_reduction_pct": 8.9, "back_on_market": False,
     "description": "REO bank-owned Georgetown cottage. Sold as-is, no repairs or credits. Fixer-upper with period charm. Needs significant renovation. Trendy Georgetown location near breweries and restaurants."},

    {"address": "6121 Carleton Ave S", "neighborhood": "Georgetown",
     "price": 425000, "beds": 3, "baths": 1.5, "sqft": 1320, "lot_sqft": 4500,
     "year_built": 1942, "property_type": "SFH", "distress_type": "Estate Sale",
     "dom": 43, "price_reductions": 1, "price_reduction_pct": 4.7, "back_on_market": True,
     "description": "Back on market! Estate sale — previous offer fell through at inspection. Sold as-is, seller will make no repairs. Georgetown character home needs full updating. Good investment potential."},

    {"address": "5312 S Lucile St", "neighborhood": "Georgetown",
     "price": 459000, "beds": 3, "baths": 2, "sqft": 1490, "lot_sqft": 5100,
     "year_built": 1955, "property_type": "SFH", "distress_type": "Short Sale",
     "dom": 58, "price_reductions": 2, "price_reduction_pct": 6.8, "back_on_market": False,
     "description": "Short sale requiring lender approval. Property sold as-is. Fixer with good bones. Kitchen renovation needed, bath updates, new flooring throughout. Motivated short sale situation."},

    # --- Columbia City (3) ---
    {"address": "3721 S Ferdinand St", "neighborhood": "Columbia City",
     "price": 465000, "beds": 3, "baths": 2, "sqft": 1540, "lot_sqft": 5500,
     "year_built": 1948, "property_type": "SFH", "distress_type": "REO",
     "dom": 35, "price_reductions": 1, "price_reduction_pct": 4.5, "back_on_market": False,
     "description": "REO property in desirable Columbia City near light rail. Sold as-is. Needs updating but priced to move. Fixer opportunity. New kitchen and baths needed. Investor special."},

    {"address": "4108 S Angeline St", "neighborhood": "Columbia City",
     "price": 495000, "beds": 4, "baths": 2, "sqft": 1820, "lot_sqft": 6200,
     "year_built": 1955, "property_type": "SFH", "distress_type": "Estate Sale",
     "dom": 29, "price_reductions": 0, "price_reduction_pct": 0.0, "back_on_market": False,
     "description": "Estate sale in prime Columbia City. Sold as-is, no repairs. Well-maintained older home needs modernizing. Great neighborhood near light rail. Family selling after long-time owner passed."},

    {"address": "3540 S Graham St Unit B", "neighborhood": "Columbia City",
     "price": 379000, "beds": 2, "baths": 2, "sqft": 1100, "lot_sqft": 0,
     "year_built": 2004, "property_type": "Townhouse", "distress_type": "Back on Market",
     "dom": 31, "price_reductions": 1, "price_reduction_pct": 3.5, "back_on_market": True,
     "description": "Back on market — buyer financing fell through. Columbia City townhouse near light rail. Needs light updating. Short sale pending bank approval. Motivated seller, as-is."},

    # --- Lake City (2) ---
    {"address": "12847 30th Ave NE", "neighborhood": "Lake City",
     "price": 398000, "beds": 3, "baths": 1.5, "sqft": 1380, "lot_sqft": 5800,
     "year_built": 1958, "property_type": "SFH", "distress_type": "Pre-Foreclosure",
     "dom": 52, "price_reductions": 2, "price_reduction_pct": 6.2, "back_on_market": False,
     "description": "Pre-foreclosure — owner motivated to sell quickly. SFH needs updating — kitchen dated, baths need work, flooring throughout. TLC needed. As-is sale. Good Lake City location."},

    {"address": "11534 28th Ave NE", "neighborhood": "Lake City",
     "price": 425000, "beds": 3, "baths": 2, "sqft": 1520, "lot_sqft": 6400,
     "year_built": 1962, "property_type": "SFH", "distress_type": "Estate Sale",
     "dom": 44, "price_reductions": 1, "price_reduction_pct": 4.8, "back_on_market": False,
     "description": "Estate sale — long-time owner's family selling as-is. Needs renovation throughout. TLC special with good bones. Quiet Lake City neighborhood. Great investor opportunity."},

    # --- Northgate (2) ---
    {"address": "10823 5th Ave NE Unit 12", "neighborhood": "Northgate",
     "price": 298000, "beds": 2, "baths": 1, "sqft": 890, "lot_sqft": 0,
     "year_built": 1972, "property_type": "Condo", "distress_type": "REO",
     "dom": 83, "price_reductions": 3, "price_reduction_pct": 13.5, "back_on_market": True,
     "description": "REO condo back on market. Bank-owned, sold as-is, no warranties. Needs full interior renovation. Near Northgate Link light rail. Great location for post-renovation value. Cash buyers preferred."},

    {"address": "11234 Roosevelt Way NE", "neighborhood": "Northgate",
     "price": 445000, "beds": 3, "baths": 2.5, "sqft": 1580, "lot_sqft": 0,
     "year_built": 2006, "property_type": "Townhouse", "distress_type": "Short Sale",
     "dom": 39, "price_reductions": 1, "price_reduction_pct": 5.5, "back_on_market": False,
     "description": "Short sale opportunity near Northgate light rail. 3-story townhouse needs updating. Sold as-is pending bank approval. Good structural condition, cosmetic updates needed throughout."},

    # --- Bitter Lake (1) ---
    {"address": "14231 Linden Ave N", "neighborhood": "Bitter Lake",
     "price": 368000, "beds": 3, "baths": 1, "sqft": 1290, "lot_sqft": 6400,
     "year_built": 1948, "property_type": "SFH", "distress_type": "REO",
     "dom": 76, "price_reductions": 3, "price_reduction_pct": 10.2, "back_on_market": False,
     "description": "Bank-owned REO in Bitter Lake. Sold as-is, no representations or warranties. Fixer-upper needing full renovation. TLC required throughout. Good lot size. Cash or hard money preferred."},
]


def _make_id(address: str) -> str:
    return hashlib.md5(address.encode()).hexdigest()[:12]


NEIGHBORHOOD_COORDS = {
    "rainier valley": (47.5502, -122.2785),
    "beacon hill":    (47.5666, -122.3073),
    "white center":   (47.5181, -122.3618),
    "delridge":       (47.5485, -122.3714),
    "georgetown":     (47.5475, -122.3216),
    "columbia city":  (47.5590, -122.2913),
    "lake city":      (47.7189, -122.2924),
    "northgate":      (47.7043, -122.3272),
    "bitter lake":    (47.7289, -122.3496),
}


def _address_coords(address: str, neighborhood: str) -> tuple[float, float]:
    """Return deterministic lat/lng for a property using neighborhood center + address-hash jitter."""
    base_lat, base_lng = NEIGHBORHOOD_COORDS.get(neighborhood.lower(), (47.6062, -122.3321))
    h = int(hashlib.md5(address.encode()).hexdigest()[:8], 16)
    lat_jitter = ((h & 0xFF) / 255.0 - 0.5) * 0.006       # ±~330 m
    lng_jitter = ((h >> 8 & 0xFF) / 255.0 - 0.5) * 0.008  # ±~330 m
    return round(base_lat + lat_jitter, 6), round(base_lng + lng_jitter, 6)


def _generate_price_history(current_price: int, reductions: int, reduction_pct: float,
                             dom: int, back_on_market: bool) -> list[dict]:
    """Generate 12-month price history for charting."""
    history = []
    original_price = round(current_price / (1 - reduction_pct / 100)) if reduction_pct > 0 else current_price
    original_price = round(original_price / 5000) * 5000

    first_listed_date = datetime.now(timezone.utc) - timedelta(days=dom)

    for months_ago in range(11, -1, -1):
        dt = datetime.now(timezone.utc) - timedelta(days=months_ago * 30)
        if dt < first_listed_date:
            continue
        months_into_listing = (dt - first_listed_date).days / 30
        total_listing_months = dom / 30
        if total_listing_months > 0:
            progress = months_into_listing / total_listing_months
        else:
            progress = 1.0
        price_at = original_price - (original_price - current_price) * progress
        price_at = round(price_at / 1000) * 1000

        history.append({"date": dt.strftime("%Y-%m-%d"), "price": int(price_at)})

    if back_on_market and len(history) >= 2:
        history[-2]["note"] = "BOM"

    return history


def _zillow_url_demo(neighborhood: str) -> str:
    slug = neighborhood.lower().replace(" ", "-")
    return f"https://www.zillow.com/homes/for_sale/{slug}-seattle-wa/"


def _redfin_url_demo(neighborhood: str) -> str:
    slug = neighborhood.lower().replace(" ", "-")
    return f"https://www.redfin.com/city/16163/WA/Seattle/filter/neighborhood={slug}"


def build_property(seed: dict, comps: list[dict] = None) -> dict:
    """Enrich a seed dict with computed fields. Dynamic ARV calculation from comps attached if available."""
    prop_type = seed["property_type"]
    neighborhood = seed["neighborhood"]
    price = seed["price"]
    sqft = seed["sqft"]
    year_built = seed["year_built"]
    description = seed["description"]

    # 📊 --- DYNAMIC ARV CALCULATION VIA COMPS (FIXED NAMESPACE) ---
    arv_calculated = False
    arv = 0
    arv_meta = {}

    if comps:
        # מיפוי חכם: אם השכונה היא חלק מסיאטל, או שיש הכלה בין שמות האזורים
        n_low = neighborhood.lower().strip()
        seattle_sub_neighborhoods = ["rainier valley", "beacon hill", "white center", "delridge", "georgetown", "columbia city", "lake city", "northgate", "bitter lake"]

        # ── שלב 1: ניסיון התאמה לפי שכונה ──────────────────────────────────────
        matched_comps = []
        match_scope = ""
        for c in comps:
            c_nbhd = c.get("neighborhood", "").lower().strip()
            if c_nbhd == n_low or (c_nbhd == "seattle" and n_low in seattle_sub_neighborhoods) or (n_low in c_nbhd or c_nbhd in n_low):
                matched_comps.append(c)
        if matched_comps:
            match_scope = "neighborhood"

        # ── שלב 2: Fallback לפי סוג נכס (כל העיר/אזור) ─────────────────────────
        if not matched_comps:
            same_type = [c for c in comps if c.get("property_type", "SFH") == prop_type]
            if len(same_type) >= 3:
                matched_comps = same_type
                match_scope = f"city-wide {prop_type}"

        # ── שלב 3: Fallback אחרון — כל הקומפס הזמינים ──────────────────────────
        if not matched_comps and len(comps) >= 3:
            matched_comps = list(comps)
            match_scope = "city-wide (all types)"

        if matched_comps:
            # שימוש בחציון להגנה מפני outliers בכמויות גדולות
            psf_values = sorted(c["psf"] for c in matched_comps)
            mid = len(psf_values) // 2
            avg_psf = psf_values[mid] if len(psf_values) % 2 else (psf_values[mid - 1] + psf_values[mid]) / 2
            base_arv = avg_psf * sqft

            if prop_type == "Townhouse":
                base_arv *= 0.95
                adjustment_label = "-5% Townhouse Adjustment"
            elif prop_type == "Condo":
                base_arv *= 0.85
                adjustment_label = "-15% Condo Adjustment"
            else:
                adjustment_label = "No adjustment (SFH)"

            arv = int(base_arv)
            arv_meta = {
                "price_per_sqft": round(avg_psf, 2),
                "sqft": sqft,
                "property_type_adjustment": adjustment_label,
                "calculated_from_comps": True,
                "comps_count": len(matched_comps),
                "match_scope": match_scope,
            }
            arv_calculated = True
            log.info(f"🎯 ARV דינמי לכתובת {seed['address']} מ-{len(matched_comps)} קומפס ({match_scope}). חציון PSF: ${round(avg_psf, 2)}")

    if not arv_calculated:
        arv, arv_meta = estimate_arv(neighborhood, sqft, prop_type)
        arv_meta["calculated_from_comps"] = False

    reno_cost, reno_breakdown, reno_level = estimate_renovation(description, sqft, year_built)
    roi = calculate_roi(price, arv, reno_cost)
    _, found_keywords = count_distress_keywords(description)

    address = seed["address"]
    from urllib.parse import quote_plus as _qp
    propwire_query = _qp(f"{address}, Seattle, WA")
    prop = {
        "id": _make_id(address),
        "address": address,
        "neighborhood": neighborhood,
        "price": price,
        "beds": seed["beds"],
        "baths": seed["baths"],
        "sqft": sqft,
        "lot_sqft": seed.get("lot_sqft", 0),
        "year_built": year_built,
        "property_type": prop_type,
        "distress_type": seed["distress_type"],
        "dom": seed["dom"],
        "neighborhood_avg_dom": NEIGHBORHOOD_AVG_DOM.get(
            neighborhood.lower(), NEIGHBORHOOD_AVG_DOM["default"]),
        "price_reductions": seed["price_reductions"],
        "price_reduction_pct": seed["price_reduction_pct"],
        "back_on_market": seed["back_on_market"],
        "description": description,
        "distress_keywords": found_keywords,
        "arv": arv,
        "arv_psf": round(arv / sqft) if sqft else 0,
        "renovation_cost": reno_cost,
        "renovation_level": reno_level,
        "renovation_breakdown": reno_breakdown,
        "roi_pct": roi,
        "zillow_url": _zillow_url_demo(neighborhood),
        "redfin_url": _redfin_url_demo(neighborhood),
        "propwire_url": f"https://propwire.com/search?address={propwire_query}",
        "source": "demo",
        "lat": _address_coords(address, neighborhood)[0],
        "lng": _address_coords(address, neighborhood)[1],
        "price_history": _generate_price_history(
            price, seed["price_reductions"], seed["price_reduction_pct"],
            seed["dom"], seed["back_on_market"]
        ),
    }

    prop["flip_score"] = calculate_flip_score(prop)
    prop["score_color"] = score_color(prop["flip_score"])
    
    if arv_meta.get("calculated_from_comps"):
        scope = arv_meta.get("match_scope", "neighborhood")
        method_label = "Live Comps Pipeline" if scope == "neighborhood" else f"Live Comps Pipeline ({scope})"
        confidence = "High (Live Market Data)" if scope == "neighborhood" else "Medium (city-wide fallback)"
        prop["arv_breakdown"] = {
            "estimated_arv": arv,
            "neighborhood": neighborhood,
            "price_per_sqft": arv_meta["price_per_sqft"],
            "static_psf": arv_meta["price_per_sqft"],
            "sqft": arv_meta["sqft"],
            "property_type_adjustment": arv_meta["property_type_adjustment"],
            "arv_method": method_label,
            "arv_confidence": confidence,
            "n_comps": arv_meta["comps_count"],
        }
    else:
        prop["arv_breakdown"] = get_arv_breakdown(arv, neighborhood, sqft, prop_type, arv_meta=arv_meta)
        
    prop["hoa_monthly"] = 0
    prop["tax_annual"] = round(price * 0.011 / 100) * 100
    prop["buyers_agent_pct"] = 2.5

    return prop


# ---------------------------------------------------------------------------
# Zillow RapidAPI fetcher
# ---------------------------------------------------------------------------
ZILLOW_API_URL = "https://zillow-com1.p.rapidapi.com/propertyExtendedSearch"
ZILLOW_DETAIL_URL = "https://zillow-com1.p.rapidapi.com/property"


async def fetch_zillow_listings(api_key: str) -> list[dict]:
    """Fetch Seattle distressed listings from Zillow via RapidAPI."""
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "zillow-com1.p.rapidapi.com",
    }
    params = {
        "location": "Seattle, WA",
        "status_type": "ForSale",
        "home_type": "Houses,Townhomes,Condos",
        "maxPrice": "1500000",
        "isForSaleForeclosure": "true",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ZILLOW_API_URL, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    raw_props = data.get("props", [])
    results = []
    for p in raw_props[:30]:
        try:
            results.append(_normalize_zillow(p))
        except Exception:
            continue
    return results


def _normalize_zillow(p: dict) -> dict:
    """Convert Zillow API response to internal format."""
    address = p.get("address", "Unknown")
    neighborhood = p.get("homeSubType", p.get("streetAddress", "Seattle"))
    price = int(p.get("price", 0))
    sqft = int(p.get("livingArea", 1200) or 1200)
    beds = p.get("bedrooms", 3) or 3
    baths = p.get("bathrooms", 1) or 1
    prop_type_raw = p.get("homeType", "SINGLE_FAMILY")
    prop_type_map = {
        "SINGLE_FAMILY": "SFH",
        "TOWNHOUSE": "Townhouse",
        "CONDO": "Condo",
        "MULTI_FAMILY": "SFH",
    }
    prop_type = prop_type_map.get(prop_type_raw, "SFH")
    dom = int(p.get("daysOnMarket", 0) or 0)
    description = p.get("description", "")
    zpid = p.get("zpid", "")
    year_built = int(p.get("yearBuilt", 1960) or 1960)

    desc_lower = description.lower()
    if "reo" in desc_lower or "bank owned" in desc_lower or "bank-owned" in desc_lower:
        distress_type = "REO"
    elif "short sale" in desc_lower:
        distress_type = "Short Sale"
    elif "pre-foreclosure" in desc_lower or "pre foreclosure" in desc_lower:
        distress_type = "Pre-Foreclosure"
    elif "estate sale" in desc_lower or "probate" in desc_lower:
        distress_type = "Estate Sale"
    elif p.get("isBackOnMarket") or "back on market" in desc_lower:
        distress_type = "Back on Market"
    else:
        distress_type = "Standard"

    back_on_market = bool(p.get("isBackOnMarket") or "back on market" in desc_lower)

    price_reductions = int(p.get("priceReduction", 0) or 0)
    price_reduction_pct = 0.0
    if p.get("zestimate") and price:
        zest = p.get("zestimate", 0)
        if zest and zest > price:
            price_reduction_pct = round((zest - price) / zest * 100, 1)

    seed = {
        "address": address,
        "neighborhood": neighborhood,
        "price": price,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "lot_sqft": int(p.get("lotAreaValue", 0) or 0),
        "year_built": year_built,
        "property_type": prop_type,
        "distress_type": distress_type,
        "dom": dom,
        "price_reductions": price_reductions,
        "price_reduction_pct": price_reduction_pct,
        "back_on_market": back_on_market,
        "description": description,
    }

    prop = build_property(seed)
    prop["source"] = "zillow"
    if zpid:
        prop["zillow_url"] = f"https://www.zillow.com/homedetails/{zpid}_zpid/"
    return prop


# ---------------------------------------------------------------------------
# King County open data (Socrata)
# ---------------------------------------------------------------------------
KING_COUNTY_URL = "https://data.kingcounty.gov/resource/vfmt-pvgb.json"


async def fetch_king_county_foreclosures() -> list[dict]:
    """Fetch King County foreclosure/distressed property records."""
    params = {
        "$limit": 50,
        "$where": "city = 'SEATTLE'",
        "$order": "sale_date DESC",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(KING_COUNTY_URL, params=params)
            if resp.status_code != 200:
                return []
            records = resp.json()
        return [_normalize_king_county(r) for r in records if r.get("address")]
    except Exception:
        return []


def _normalize_king_county(r: dict) -> dict:
    address = f"{r.get('address', '')} Seattle WA"
    price_raw = r.get("sale_price", "0") or "0"
    price = int(float(price_raw))
    if price > 500000 or price < 50000:
        price = 350000

    seed = {
        "address": address,
        "neighborhood": r.get("districtname", "Seattle"),
        "price": price,
        "beds": 3, "baths": 1,
        "sqft": int(r.get("sqft_living", 1200) or 1200),
        "lot_sqft": int(r.get("sqft_lot", 5000) or 5000),
        "year_built": int(r.get("yr_built", 1960) or 1960),
        "property_type": "SFH",
        "distress_type": "REO",
        "dom": 45,
        "price_reductions": 1,
        "price_reduction_pct": 5.0,
        "back_on_market": False,
        "description": f"King County public record. Sold as-is. Fixer-upper opportunity. TLC needed. {r.get('sale_reason', '')}",
    }
    prop = build_property(seed)
    prop["source"] = "king_county"
    return prop


# ---------------------------------------------------------------------------
# Redfin internal API
# ---------------------------------------------------------------------------
REDFIN_GIS_URL = "https://www.redfin.com/stingray/api/gis"
REDFIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
}

_UI_PROP_TYPES = {1: "SFH", 2: "Condo", 3: "Townhouse", 4: "SFH"}


def _rf(obj, fallback=None):
    if obj is None:
        return fallback
    if isinstance(obj, dict):
        v = obj.get("value")
        return v if v is not None else fallback
    return obj


def _normalize_redfin(h: dict, max_price: int = 500000, comps: list[dict] = None) -> dict | None:
    """Convert a Redfin GIS home object to our internal property format. Integrates real pipeline comps."""
    address = str(_rf(h.get("streetLine"), "") or "").strip()
    unit = str(_rf(h.get("unitNumber"), "") or "").strip()
    if unit:
        address = f"{address} {unit}"
    if not address:
        log.info("❌ Filtered out property: Empty address field")
        return None

    price = int(_rf(h.get("price"), 0) or 0)
    if price < 120000:
        log.info(f"❌ Filtered out '{address}': Price too low (${price})")
        return None
    if price > max_price:
        log.info(f"❌ Filtered out '{address}': Price (${price}) exceeds max_price limit (${max_price})")
        return None

    # 🛡️ Defensive state/city filter — Redfin region IDs occasionally collide
    # across states (e.g. a Seattle region_id can leak a Wisconsin result whose
    # streetLine reads like "W 5300 CTH A"). Drop anything not in WA / Seattle metro.
    state = str(_rf(h.get("state"), "") or h.get("stateCode", "") or "").strip().upper()
    city  = str(_rf(h.get("city"), "") or "").strip().lower()
    if state and state not in ("WA", "WASHINGTON"):
        log.info(f"❌ Filtered out '{address}': non-WA state ({state}, city={city or 'n/a'})")
        return None
    if city and city not in SEATTLE_METRO_CITIES:
        log.info(f"❌ Filtered out '{address}': city '{city}' not in Seattle metro")
        return None

    neighborhood = str(_rf(h.get("location"), "") or "").strip() or "Seattle"
    ui_type = int(h.get("uiPropertyType", 1) or 1)
    prop_type = _UI_PROP_TYPES.get(ui_type, "SFH")

    sqft = max(400, min(int(_rf(h.get("sqFt"), 1200) or 1200), 15000))
    lot_sqft = int(_rf(h.get("lotSize"), 0) or 0)
    beds = int(h.get("beds") or 3)
    baths = float(h.get("baths") or 1.0)
    year_built = int(_rf(h.get("yearBuilt"), 1960) or 1960)
    dom = int(_rf(h.get("dom"), 0) or 0)

    sashes = h.get("sashes") or []
    sash_names = " ".join(
        str(s.get("sashTypeName", "") if isinstance(s, dict) else s)
        for s in sashes
    ).lower()
    price_reductions = 1 if "price" in sash_names and "reduc" in sash_names else 0
    price_drop_pct = 0.0
    back_on_market = "back on market" in sash_names or bool(h.get("isBackOnMarket"))

    remarks = str(h.get("listingRemarks") or "").strip()
    tags_list = h.get("listingTags") or []
    if tags_list:
        tag_str = ", ".join(str(t) for t in tags_list).lower()
        if tag_str and tag_str not in remarks.lower():
            remarks = f"{remarks} [{tag_str}]".strip()

    r = remarks.lower()
    tags = " ".join(str(t) for t in (h.get("listingTags") or [])).lower()
    listing_type = int(h.get("listingType", 0) or 0)

    if listing_type == 6 or any(x in r for x in ["reo", "bank owned", "bank-owned", "foreclosure"]):
        distress_type = "REO"
    elif "short sale" in r or "short sale" in tags:
        distress_type = "Short Sale"
    elif "pre-foreclosure" in r:
        distress_type = "Pre-Foreclosure"
    elif "estate sale" in r or "probate" in r:
        distress_type = "Estate Sale"
    elif back_on_market:
        distress_type = "Back on Market"
    elif any(x in r for x in ["fixer", "tlc", "needs work", "as-is", "as is", "handyman"]):
        distress_type = "Fixer-Upper"
    else:
        distress_type = "Standard"

    url_path = h.get("url", "")
    redfin_url = f"https://www.redfin.com{url_path}" if url_path else "https://www.redfin.com/city/16163/WA/Seattle"
    from urllib.parse import quote_plus
    city_name = str(h.get("city", "Seattle")).strip()
    propwire_query = quote_plus(f"{address}, {city_name}, WA")
    propwire_url = f"https://propwire.com/search?address={propwire_query}"

    seed = {
        "address": address,
        "neighborhood": neighborhood,
        "price": price,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "lot_sqft": lot_sqft,
        "year_built": year_built,
        "property_type": prop_type,
        "distress_type": distress_type,
        "dom": dom,
        "price_reductions": price_reductions,
        "price_reduction_pct": price_drop_pct,
        "back_on_market": back_on_market,
        "description": remarks,
    }

    try:
        # ✓ מציבים מפורשות את הקומפס המוזרקים
        prop = build_property(seed, comps=comps)
        prop["source"] = "redfin"
        prop["redfin_url"] = redfin_url
        prop["zillow_url"] = _zillow_url_demo(neighborhood)
        prop["propwire_url"] = propwire_url
        
        log.info(f"✅ Property built successfully: '{address}' in region '{neighborhood}' ({prop_type})")
        return prop
    except Exception as e:
        log.warning(f"❌ Failed to build property framework for '{address}' (Region: {neighborhood}). Error trace: {str(e)}")
        return None


# ---------------------------------------------------------------------------
# Redfin region IDs — Seattle + close-in suburbs only.
# Pierce County (Tacoma metro) and outer Snohomish (Everett metro) deliberately
# excluded — those are separate metro areas, not Seattle suburbs.
# ---------------------------------------------------------------------------
REGIONS = [
    # Seattle proper
    {"name": "Seattle",          "region_id": "16163"},
    # King County — immediate Seattle suburbs
    {"name": "Shoreline",        "region_id": "16165"},
    {"name": "Burien",           "region_id": "16742"},
    {"name": "Tukwila",          "region_id": "16170"},
    {"name": "SeaTac",           "region_id": "16793"},
    {"name": "Kenmore",          "region_id": "16747"},
    {"name": "Renton",           "region_id": "16057"},
    {"name": "Bellevue",         "region_id": "16706"},
    {"name": "Kirkland",         "region_id": "16749"},
    {"name": "Redmond",          "region_id": "16786"},
    {"name": "Bothell",          "region_id": "16712"},
    {"name": "Des Moines",       "region_id": "16727"},
    {"name": "Normandy Park",    "region_id": "16773"},
    # Snohomish County — only the cities that border Seattle / are part of the Seattle commute
    {"name": "Edmonds",          "region_id": "17023"},
    {"name": "Mountlake Terrace","region_id": "17038"},
    {"name": "Lynnwood",         "region_id": "17034"},
]

# Set of city names that count as "Seattle metro" for defensive city-field filtering.
# Used in _normalize_redfin to drop any home Redfin returns whose city field
# doesn't match — guards against cross-region/cross-state leaks.
SEATTLE_METRO_CITIES = {r["name"].lower() for r in REGIONS} | {
    # Tiny adjacent cities Redfin sometimes labels homes with — accept these too.
    "lake forest park", "mercer island", "newcastle", "clyde hill",
    "medina", "yarrow point", "hunts point", "beaux arts village",
    "woodinville", "kenmore", "lake forest", "white center",
}


async def _fetch_region(client: httpx.AsyncClient, region: dict, max_price: int, comps: list[dict] = None) -> list[dict]:
    """Fetch SFH listings for a single Redfin region."""
    region_results = []
    base = {
        "al": 1, "num_homes": 150, "page_number": 1,
        "status": 1, "uipt": "1",
        "v": 8,
        "region_id": region["region_id"], "region_type": "6",
        "max_price": max_price,
    }
    queries = [
        {**base, "ord": "price-asc"},
        {**base, "ord": "price-asc", "page_number": 2},
    ]
    for params in queries:
        try:
            resp = await client.get(REDFIN_GIS_URL, params=params, headers=REDFIN_HEADERS)
            resp.raise_for_status()
            text = resp.text
            if text.startswith("{}&&"):
                text = text[4:]
            data = json.loads(text)
            err = data.get("errorMessage", "")
            if err != "Success":
                log.warning(f"Redfin [{region['name']}] API error: {err}")
                break
            homes = data.get("payload", {}).get("homes", [])
            log.info(f"Redfin [{region['name']}] p{params['page_number']}: {len(homes)} raw homes")
            for h in homes:
                try:
                    # ✓ מזריקים את רשימת הקומפס האזורית
                    prop = _normalize_redfin(h, max_price=max_price, comps=comps)
                    if prop:
                        region_results.append(prop)
                except Exception as e:
                    log.debug(f"Skipped home in {region['name']}: {e}")
        except Exception as e:
            log.warning(f"Redfin query failed for {region['name']}: {e}")
            break
    log.info(f"Redfin [{region['name']}]: {len(region_results)} SFH after filter")
    return region_results


async def fetch_redfin_listings(max_price: int = 1500000, comps: list[dict] = None) -> list[dict]:
    """Fetch SFH listings from Redfin across regions and ingest live historical comps tracking."""
    all_results = []
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        # ✓ מוסרים את הקומפס כארגומנט למשיכת המחוזות
        tasks = [_fetch_region(client, r, max_price, comps=comps) for r in REGIONS]
        region_batches = await asyncio.gather(*tasks, return_exceptions=True)
        for batch in region_batches:
            if isinstance(batch, list):
                all_results.extend(batch)

    seen: set[str] = set()
    unique = []
    for p in all_results:
        key = p["address"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    if not unique:
        raise ValueError("Redfin returned 0 usable properties across all regions")

    unique.sort(key=lambda x: x["flip_score"], reverse=True)
    log.info(f"Redfin total: {len(unique)} unique SFH across {len(REGIONS)} regions")
    return unique


async def fetch_redfin_comps(neighborhoods: list[str]) -> list[dict]:
    """Fetch recently sold SFH comps from Redfin."""
    import hashlib as _hashlib
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    # Use the full Seattle-metro REGIONS for comps — wider coverage gives better
    # ARV signal, but stays inside Seattle suburbs (no Tacoma/Everett metro).
    COMP_REGIONS = list(REGIONS)

    all_comps = []

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for region in COMP_REGIONS:
            params = {
                "al": 1, "num_homes": 150, "page_number": 1,
                "status": 3, "uipt": "1,2,3", "v": 8,   # SFH + Condo + Townhouse
                "region_id": region["region_id"], "region_type": "6",
                "sold_within_days": 180,
            }
            try:
                resp = await client.get(REDFIN_GIS_URL, params=params, headers=REDFIN_HEADERS)
                resp.raise_for_status()
                text = resp.text
                if text.startswith("{}&&"):
                    text = text[4:]
                data = json.loads(text)

                if data.get("errorMessage") != "Success":
                    log.warning(f"Redfin comps error for {region['name']}: {data.get('errorMessage')}")
                    continue

                homes = data.get("payload", {}).get("homes", [])
                log.info(f"Redfin comps [{region['name']}]: {len(homes)} sold homes")

                for h in homes:
                    try:
                        price = int(_rf(h.get("price"), 0) or 0)
                        sqft  = int(_rf(h.get("sqft"), 0) or _rf(h.get("sqFt"), 0) or 0)
                        
                        if price < 100000 or sqft < 400:
                            continue

                        raw_remarks = _rf(h.get("remarksAccessInfo"), "") or _rf(h.get("listingRemarks"), "") or ""
                        remarks = str(raw_remarks).lower()
                        
                        if remarks and any(s in remarks for s in ["as-is", "as is", "reo", "bank owned",
                                                       "short sale", "estate sale", "foreclosure"]):
                            continue

                        location = str(_rf(h.get("location"), "") or "").strip()
                        nbhd = location if location else region["name"]

                        psf = round(price / sqft, 2)
                        address = str(_rf(h.get("streetLine"), "") or "").strip()

                        sold_ts = _rf(h.get("soldDate"), None)
                        try:
                            sold_date = _dt.fromtimestamp(
                                int(sold_ts) / 1000, tz=_tz.utc).strftime("%Y-%m-%d") if sold_ts else _dt.now(_tz.utc).strftime("%Y-%m-%d")
                        except Exception:
                            sold_date = _dt.now(_tz.utc).strftime("%Y-%m-%d")

                        comp_ui_type = int(h.get("uiPropertyType", 1) or 1)
                        comp_prop_type = _UI_PROP_TYPES.get(comp_ui_type, "SFH")

                        comp_id = _hashlib.md5(f"{address}{price}{sqft}".encode()).hexdigest()[:12]
                        all_comps.append({
                            "id":            comp_id,
                            "neighborhood":  nbhd,
                            "property_type": comp_prop_type,
                            "sold_price":    price,
                            "sqft":          sqft,
                            "psf":           psf,
                            "sold_date":     sold_date,
                            "address":       address,
                            "region":        region["name"],   # ✓ לשרשור fallback אזורי
                        })
                    except Exception as e:
                        log.debug(f"Skipped comp processing: {e}")

            except Exception as e:
                log.warning(f"Redfin comps fetch failed for {region['name']}: {e}")

    log.info(f"Redfin comps: {len(all_comps)} usable sold comps across {len(neighborhoods)} neighborhoods")
    return all_comps


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def fetch_all_properties(api_key: str = "", max_price: int = 500000) -> list[dict]:
    """Priority: Zillow API (if key) → Redfin (real, no key) → demo data."""
    if api_key:
        try:
            zillow = await fetch_zillow_listings(api_key)
            kc = await fetch_king_county_foreclosures()
            seen_addresses: set[str] = set()
            merged = []
            for p in zillow + kc:
                if p.get("price", 0) > max_price:
                    continue
                key = p.get("address", "").lower().strip()
                if key not in seen_addresses:
                    seen_addresses.add(key)
                    merged.append(p)
            merged.sort(key=lambda x: x["flip_score"], reverse=True)
            if merged:
                # ✓ הגדלת מכסת החיתוך ל-200 גם ב-Zillow pipeline
                return merged[:200]
        except Exception as e:
            log.warning(f"Zillow fetch failed: {e}")

    # 📊 Live Redfin Pipeline Tracking With Integrated Real Comps Mapping
    try:
        neighborhood_names = [r["name"] for r in REGIONS]
        log.info("⏳ Pre-fetching Redfin historical closed market comps...")
        live_comps = await fetch_redfin_comps(neighborhood_names)
        
        # ✓ הזרקה ישירה של ה-Comps למערך הליסטינגס הראשי
        props = await fetch_redfin_listings(max_price=max_price, comps=live_comps)
        
        # ✓ שינוי מכסת החיתוך ל-200 כדי שיאגר מספיק מידע במאגר הפנימי
        return props[:200]
    except Exception as e:
        log.warning(f"Redfin fetch failed: {e} — falling back to demo data")

    properties = [build_property(s) for s in MOCK_SEEDS]
    properties = [p for p in properties if p.get("price", 0) <= max_price]
    properties.sort(key=lambda x: x["flip_score"], reverse=True)
    
    # ✓ הגדלת מכסת החיתוך ל-200 גם במצב ה-Demo כגיבוי
    return properties[:200]
