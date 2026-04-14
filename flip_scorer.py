"""
Flip score algorithm and property valuation helpers.
"""
import math

DEFAULT_WEIGHTS = {
    # Profitability (40%)
    "w_arv":             30,  # Price vs ARV equity spread
    "w_roi":             10,  # Estimated ROI (target >25%)
    # Execution Efficiency (20%)
    "w_profit_reno":     10,  # Profit-to-Reno ratio (efficiency)
    "w_reno_level":      10,  # Renovation level (light = best)
    # Liquidity & Asset Risk (25%)
    "w_size":            10,  # Property size / liquidity
    "w_structural":      10,  # Structural risk (year built)
    "w_market_velocity":  5,  # Market velocity (DOM vs avg)
    # Market Momentum (15%)
    "w_distress":        10,  # Distress keywords + price reductions
    "w_neighborhood":     5,  # Neighborhood upside
}

UPSIDE_NEIGHBORHOODS_TOP = {
    "rainier valley", "beacon hill", "white center",
    "delridge", "georgetown",
}
UPSIDE_NEIGHBORHOODS_MID = {
    "columbia city", "northgate", "west seattle",
}
# Keep alias for backwards compat
UPSIDE_NEIGHBORHOODS = UPSIDE_NEIGHBORHOODS_TOP

DISTRESS_KEYWORDS = [
    "as-is", "as is", "fixer", "fixer-upper", "estate sale",
    "reo", "bank owned", "bank-owned", "short sale", "tlc",
    "needs work", "needs updating", "investor special", "cash only",
    "handyman", "distressed", "motivated seller", "price reduced",
    "probate", "foreclosure", "pre-foreclosure", "back on market",
]

# Average DOM per neighborhood (days)
NEIGHBORHOOD_AVG_DOM = {
    "rainier valley": 28,
    "beacon hill": 22,
    "white center": 32,
    "delridge": 30,
    "georgetown": 35,
    "columbia city": 18,
    "west seattle": 20,
    "lake city": 25,
    "northgate": 21,
    "bitter lake": 27,
    "crown hill": 24,
    "maple leaf": 22,
    "default": 22,
}

# After-Repair Value per sq ft by neighborhood
NEIGHBORHOOD_ARV_PSF = {
    "rainier valley": 490,
    "beacon hill": 515,
    "white center": 450,
    "delridge": 465,
    "georgetown": 500,
    "columbia city": 530,
    "west seattle": 545,
    "lake city": 495,
    "northgate": 510,
    "bitter lake": 475,
    "crown hill": 520,
    "maple leaf": 545,
    "default": 510,
}


def _nbhd_key(neighborhood: str) -> str:
    n = neighborhood.lower().strip()
    for key in NEIGHBORHOOD_ARV_PSF:
        if key in n or n in key:
            return key
    return "default"


def estimate_arv(neighborhood: str, sqft: int, property_type: str) -> int:
    key = _nbhd_key(neighborhood)
    psf = NEIGHBORHOOD_ARV_PSF.get(key, NEIGHBORHOOD_ARV_PSF["default"])
    if property_type.lower() == "condo":
        psf = int(psf * 0.85)
    elif property_type.lower() == "townhouse":
        psf = int(psf * 0.95)
    return round(psf * sqft / 1000) * 1000


def estimate_renovation(description: str, sqft: int, year_built: int) -> tuple[int, dict, str]:
    desc = description.lower()
    heavy = ["gut", "uninhabitable", "major renovation", "tear down", "condemned", "foundation issue"]
    medium = ["fixer", "fixer-upper", "tlc", "as-is", "as is", "needs work",
              "needs updating", "investor special", "cash only", "handyman",
              "estate sale", "full renovation", "full update"]

    if any(k in desc for k in heavy) or year_built < 1945:
        level, cost_psf = "heavy", 85
    elif any(k in desc for k in medium):
        level, cost_psf = "medium", 52
    else:
        level, cost_psf = "light", 28

    base = sqft * cost_psf
    age = 2025 - year_built
    if age > 65:
        base = int(base * 1.18)
    elif age > 45:
        base = int(base * 1.08)

    breakdown = {
        "Kitchen":                    round(base * 0.20),
        "Bathrooms":                  round(base * 0.15),
        "Flooring":                   round(base * 0.12),
        "Roof & Exterior":            round(base * 0.15),
        "HVAC / Plumbing / Electric": round(base * 0.18),
        "Windows & Doors":            round(base * 0.08),
        "Landscaping":                round(base * 0.05),
        "Permits & Overhead":         round(base * 0.07),
    }
    total = round(sum(breakdown.values()) / 5000) * 5000
    return total, breakdown, level


def count_distress_keywords(description: str) -> tuple[int, list[str]]:
    desc = description.lower()
    found = [kw for kw in DISTRESS_KEYWORDS if kw in desc]
    return len(found), found


def calculate_flip_score(prop: dict, weights: dict | None = None) -> int:
    """Calculate 0–100 flip score.

    Model (4 categories, 9 factors):
      Profitability        40% — Price vs ARV (30), ROI (10)
      Execution Efficiency 20% — Profit/Reno ratio (10), Reno level (10)
      Liquidity & Risk     25% — Size (10), Structural (10), Market velocity (5)
      Market Momentum      15% — Distress & reductions (10), Neighborhood (5)

    Weights are pulled from DEFAULT_WEIGHTS, overridden by `weights` param,
    then normalized to sum to 100. Unknown keys in `weights` are ignored.
    """
    # Only use recognised keys; fill missing ones from defaults
    w = {k: float((weights or {}).get(k, DEFAULT_WEIGHTS[k])) for k in DEFAULT_WEIGHTS}
    total = sum(w.values()) or 100
    w = {k: v / total for k, v in w.items()}

    price            = prop.get("price", 0)
    arv              = prop.get("arv", 0)
    reno_cost        = prop.get("renovation_cost", 0)
    roi_pct          = prop.get("roi_pct", 0.0)
    sqft             = prop.get("sqft", 0)
    year_built       = prop.get("year_built", 1970)
    dom              = prop.get("dom", 0)
    price_reductions = prop.get("price_reductions", 0)
    price_red_pct    = prop.get("price_reduction_pct", 0.0)
    back_on_market   = prop.get("back_on_market", False)
    description      = prop.get("description", "")
    neighborhood     = prop.get("neighborhood", "")
    reno_level       = prop.get("renovation_level", "medium")

    score = 0.0

    # ── 1. Price vs ARV (30%) ────────────────────────────────────────────────
    # Target: >30% equity spread = 100 pts.  No equity = 0 pts.
    if arv > 0 and price > 0 and arv > price:
        equity_pct = (arv - price) / arv * 100
        arv_score = min(100.0, equity_pct / 30 * 100)
    else:
        arv_score = 0.0
    score += arv_score * w["w_arv"]

    # ── 2. Estimated ROI (10%) ───────────────────────────────────────────────
    # Target: >25% ROI = 100 pts.  Negative ROI = 0 pts.
    roi_score = min(100.0, roi_pct / 25 * 100) if roi_pct > 0 else 0.0
    score += roi_score * w["w_roi"]

    # ── 3. Profit-to-Reno Ratio (10%) ───────────────────────────────────────
    # Efficiency: Est. Profit / Reno Cost.  ≥2.0x = 100 pts.
    if reno_cost > 0:
        profit = arv - price - reno_cost
        ratio = profit / reno_cost
        if   ratio >= 2.0: pr_score = 100.0
        elif ratio >= 1.0: pr_score = 75.0
        elif ratio >= 0.5: pr_score = 50.0
        elif ratio >= 0:   pr_score = 25.0
        else:              pr_score = 0.0
    else:
        pr_score = 0.0
    score += pr_score * w["w_profit_reno"]

    # ── 4. Renovation Level (10%) ────────────────────────────────────────────
    # Light = max score; heavy/structural = large penalty.
    reno_level_scores = {"light": 100, "medium": 50, "heavy": 10}
    reno_score = float(reno_level_scores.get((reno_level or "medium").lower(), 50))
    score += reno_score * w["w_reno_level"]

    # ── 5. Property Size / Liquidity (10%) ──────────────────────────────────
    # Sweet spot 800–2,500 sqft. <500 or >4,000 = narrow buyer pool = penalty.
    if   sqft >= 800  and sqft <= 2500: size_score = 100.0
    elif sqft >= 600  and sqft <  800:  size_score = 75.0
    elif sqft >  2500 and sqft <= 3500: size_score = 75.0
    elif sqft >= 500  and sqft <  600:  size_score = 45.0
    elif sqft >  3500 and sqft <= 4500: size_score = 45.0
    elif sqft > 0:                      size_score = 10.0   # <500 or >4500
    else:                               size_score = 50.0   # unknown
    score += size_score * w["w_size"]

    # ── 6. Structural Risk (10%) ─────────────────────────────────────────────
    # Post-2000 = low risk = 100 pts. Pre-1960 = high risk = 8 pts.
    if   year_built >= 2000: struct_score = 100.0
    elif year_built >= 1990: struct_score = 82.0
    elif year_built >= 1980: struct_score = 60.0
    elif year_built >= 1970: struct_score = 38.0
    elif year_built >= 1960: struct_score = 20.0
    else:                    struct_score = 8.0
    score += struct_score * w["w_structural"]

    # ── 7. Market Velocity (5%) ──────────────────────────────────────────────
    # HIGH DOM vs avg = liquidity risk = PENALTY (opposite of old model).
    key = _nbhd_key(neighborhood)
    avg_dom = NEIGHBORHOOD_AVG_DOM.get(key, NEIGHBORHOOD_AVG_DOM["default"])
    if avg_dom > 0 and dom > 0:
        ratio = dom / avg_dom
        if   ratio <= 0.50: vel_score = 100.0
        elif ratio <= 0.75: vel_score = 85.0
        elif ratio <= 1.00: vel_score = 70.0
        elif ratio <= 1.50: vel_score = 50.0
        elif ratio <= 2.00: vel_score = 30.0
        elif ratio <= 3.00: vel_score = 15.0
        else:               vel_score = 5.0
    else:
        vel_score = 50.0
    score += vel_score * w["w_market_velocity"]

    # ── 8. Distress & Reductions (10%) ───────────────────────────────────────
    # Keywords (max 50) + price cuts (max 30) + back-on-market bonus (20).
    kw_count, _ = count_distress_keywords(description)
    kw_sub      = min(50.0, kw_count * 15)
    red_sub     = min(30.0, price_reductions * 8 + price_red_pct * 1.2)
    bom_sub     = 20.0 if back_on_market else 0.0
    distress_score = min(100.0, kw_sub + red_sub + bom_sub)
    score += distress_score * w["w_distress"]

    # ── 9. Neighborhood Upside (5%) ──────────────────────────────────────────
    # Top-tier appreciation areas = 100; moderate = 65; others = 35.
    nbhd_lower = neighborhood.lower()
    if any(n in nbhd_lower for n in UPSIDE_NEIGHBORHOODS_TOP):
        nbhd_score = 100.0
    elif any(n in nbhd_lower for n in UPSIDE_NEIGHBORHOODS_MID):
        nbhd_score = 65.0
    else:
        nbhd_score = 35.0
    score += nbhd_score * w["w_neighborhood"]

    return round(min(100, max(0, score)))


def calculate_roi(price: int, arv: int, renovation_cost: int) -> float:
    total_in = price + renovation_cost
    if total_in <= 0:
        return 0.0
    profit = arv - total_in
    return round(profit / total_in * 100, 1)


def score_color(score: int) -> str:
    if score >= 70:
        return "green"
    elif score >= 40:
        return "yellow"
    return "red"


def get_arv_breakdown(arv: int, neighborhood: str, sqft: int, property_type: str) -> dict:
    key = _nbhd_key(neighborhood)
    psf = NEIGHBORHOOD_ARV_PSF.get(key, NEIGHBORHOOD_ARV_PSF["default"])
    if property_type.lower() == "condo":
        psf = int(psf * 0.85)
    elif property_type.lower() == "townhouse":
        psf = int(psf * 0.95)

    adj_note = {
        "condo": "15% discount applied for condo",
        "townhouse": "5% discount applied for townhouse",
    }.get(property_type.lower(), "No adjustment")

    return {
        "estimated_arv": arv,
        "neighborhood": neighborhood,
        "price_per_sqft": psf,
        "sqft": sqft,
        "property_type_adjustment": adj_note,
    }
