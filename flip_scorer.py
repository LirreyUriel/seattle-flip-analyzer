"""
Flip score algorithm and property valuation helpers.
All parameters are driven by config dicts (loaded from DB) rather than hardcoded constants.
"""

DEFAULT_WEIGHTS = {
    # Profitability (40%)
    "w_arv":             30,
    "w_roi":             10,
    # Execution Efficiency (20%)
    "w_profit_reno":     10,
    "w_reno_level":      10,
    # Liquidity & Asset Risk (25%)
    "w_size":            10,
    "w_structural":      10,
    "w_market_velocity":  5,
    # Market Momentum (15%)
    "w_distress":        10,
    "w_neighborhood":     5,
}

# ---------------------------------------------------------------------------
# Config helpers — build lookup dicts from DB config rows
# ---------------------------------------------------------------------------

def _build_nbhd_lookup(neighborhoods: list[dict]) -> dict:
    """Returns {name_lower: {arv_psf, avg_dom, tier}}"""
    return {n["name"].lower(): n for n in neighborhoods}


def _nbhd_key(neighborhood: str, lookup: dict) -> str:
    n = neighborhood.lower().strip()
    for key in lookup:
        if key == "default":
            continue
        if key in n or n in key:
            return key
    return "default"


# ---------------------------------------------------------------------------
# ARV estimation
# ---------------------------------------------------------------------------

def estimate_arv(neighborhood: str, sqft: int, property_type: str,
                 config: dict | None = None) -> int:
    from database import get_model_config
    cfg = config or get_model_config()
    lookup = _build_nbhd_lookup(cfg["neighborhoods"])
    reno_cfg = cfg["reno_config"]

    key = _nbhd_key(neighborhood, lookup)
    nbhd = lookup.get(key, lookup.get("default", {"arv_psf": 510}))
    psf = nbhd["arv_psf"]

    discounts = reno_cfg.get("property_type_discounts", {"condo": 0.85, "townhouse": 0.95})
    pt = property_type.lower()
    if pt in discounts:
        psf = int(psf * discounts[pt])

    return round(psf * sqft / 1000) * 1000


# ---------------------------------------------------------------------------
# Renovation estimation
# ---------------------------------------------------------------------------

def estimate_renovation(description: str, sqft: int, year_built: int,
                        config: dict | None = None) -> tuple[int, dict, str]:
    from database import get_model_config
    cfg = config or get_model_config()
    reno_cfg = cfg["reno_config"]
    levels = reno_cfg["levels"]
    age_mults = reno_cfg["age_multipliers"]
    heavy_kw = reno_cfg["heavy_keywords"]
    medium_kw = reno_cfg["medium_keywords"]
    breakdown_pct = reno_cfg["breakdown_pct"]

    desc = description.lower()
    heavy_year_cutoff = 1945  # properties older than this default to heavy

    if any(k in desc for k in heavy_kw) or year_built < heavy_year_cutoff:
        level = "heavy"
    elif any(k in desc for k in medium_kw):
        level = "medium"
    else:
        level = "light"

    cost_psf = levels[level]["cost_psf"]
    base = sqft * cost_psf

    age = 2025 - year_built
    multiplier = 1.0
    for tier in sorted(age_mults, key=lambda x: x["min_age"], reverse=True):
        if age >= tier["min_age"]:
            multiplier = tier["multiplier"]
            break
    base = int(base * multiplier)

    breakdown = {k: round(base * v) for k, v in breakdown_pct.items()}
    total = round(sum(breakdown.values()) / 5000) * 5000
    return total, breakdown, level


# ---------------------------------------------------------------------------
# Distress keywords
# ---------------------------------------------------------------------------

def count_distress_keywords(description: str,
                             config: dict | None = None) -> tuple[int, list[str]]:
    from database import get_model_config
    cfg = config or get_model_config()
    keywords = cfg["distress_keywords"]
    desc = description.lower()
    found = [kw for kw in keywords if kw in desc]
    return len(found), found


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def calculate_flip_score(prop: dict, weights: dict | None = None,
                         config: dict | None = None) -> int:
    """Calculate 0–100 flip score using config-driven parameters.

    All scoring thresholds, tiers, and lookup tables are read from `config`
    (loaded from DB). Falls back to DB defaults if config is None.
    """
    from database import get_model_config
    cfg = config or get_model_config()
    reno_cfg = cfg["reno_config"]
    thresholds = reno_cfg["score_thresholds"]
    lookup = _build_nbhd_lookup(cfg["neighborhoods"])

    # Resolve weights
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

    # ── 1. Price vs ARV ──────────────────────────────────────────────────────
    arv_target = thresholds["arv_target_equity_pct"]
    if arv > 0 and price > 0 and arv > price:
        equity_pct = (arv - price) / arv * 100
        arv_score = min(100.0, equity_pct / arv_target * 100)
    else:
        arv_score = 0.0
    score += arv_score * w["w_arv"]

    # ── 2. ROI ───────────────────────────────────────────────────────────────
    roi_target = thresholds["roi_target_pct"]
    roi_score = min(100.0, roi_pct / roi_target * 100) if roi_pct > 0 else 0.0
    score += roi_score * w["w_roi"]

    # ── 3. Profit-to-Reno Ratio ──────────────────────────────────────────────
    if reno_cost > 0:
        profit = arv - price - reno_cost
        ratio = profit / reno_cost
        pr_score = 0.0
        for tier in sorted(thresholds["profit_reno_ratio_tiers"],
                           key=lambda x: x["min"], reverse=True):
            if ratio >= tier["min"]:
                pr_score = float(tier["score"])
                break
    else:
        pr_score = 0.0
    score += pr_score * w["w_profit_reno"]

    # ── 4. Renovation Level ──────────────────────────────────────────────────
    levels = reno_cfg["levels"]
    reno_score = float(levels.get((reno_level or "medium").lower(), {}).get("score", 50))
    score += reno_score * w["w_reno_level"]

    # ── 5. Property Size / Liquidity ─────────────────────────────────────────
    size_score = 50.0  # unknown
    if sqft > 0:
        size_score = 10.0
        for tier in thresholds["size_tiers"]:
            if tier["min"] <= sqft <= tier["max"]:
                size_score = float(tier["score"])
                break
    score += size_score * w["w_size"]

    # ── 6. Structural Risk ───────────────────────────────────────────────────
    struct_score = 8.0
    for tier in sorted(thresholds["struct_year_tiers"],
                       key=lambda x: x["min_year"], reverse=True):
        if year_built >= tier["min_year"]:
            struct_score = float(tier["score"])
            break
    score += struct_score * w["w_structural"]

    # ── 7. Market Velocity ───────────────────────────────────────────────────
    key = _nbhd_key(neighborhood, lookup)
    nbhd_data = lookup.get(key, lookup.get("default", {"avg_dom": 22}))
    avg_dom = nbhd_data.get("avg_dom", 22)

    if avg_dom > 0 and dom > 0:
        ratio = dom / avg_dom
        vel_score = 5.0
        for tier in thresholds["dom_ratio_tiers"]:
            if ratio <= tier["max_ratio"]:
                vel_score = float(tier["score"])
                break
    else:
        vel_score = 50.0
    score += vel_score * w["w_market_velocity"]

    # ── 8. Distress & Reductions ─────────────────────────────────────────────
    kw_count, _ = count_distress_keywords(description, cfg)
    kw_pts   = thresholds["distress_kw_points"]
    kw_max   = thresholds["distress_kw_max"]
    red_pts  = thresholds["distress_reduction_pts"]
    red_pct_pts = thresholds["distress_reduction_pct_pts"]
    red_max  = thresholds["distress_reduction_max"]
    bom_bonus = thresholds["distress_bom_bonus"]

    kw_sub  = min(float(kw_max),  kw_count * kw_pts)
    red_sub = min(float(red_max), price_reductions * red_pts + price_red_pct * red_pct_pts)
    bom_sub = float(bom_bonus) if back_on_market else 0.0
    distress_score = min(100.0, kw_sub + red_sub + bom_sub)
    score += distress_score * w["w_distress"]

    # ── 9. Neighborhood Upside ───────────────────────────────────────────────
    nbhd_lower = neighborhood.lower()
    tier_scores = {"top": 100.0, "mid": 65.0, "other": 35.0}
    nbhd_score = 35.0
    for name, data in lookup.items():
        if name == "default":
            continue
        if name in nbhd_lower or nbhd_lower in name:
            nbhd_score = tier_scores.get(data.get("tier", "other"), 35.0)
            break
    score += nbhd_score * w["w_neighborhood"]

    return round(min(100, max(0, score)))


# ---------------------------------------------------------------------------
# ROI, color, ARV breakdown
# ---------------------------------------------------------------------------

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


def get_arv_breakdown(arv: int, neighborhood: str, sqft: int, property_type: str,
                      config: dict | None = None) -> dict:
    from database import get_model_config
    cfg = config or get_model_config()
    lookup = _build_nbhd_lookup(cfg["neighborhoods"])
    reno_cfg = cfg["reno_config"]

    key = _nbhd_key(neighborhood, lookup)
    nbhd = lookup.get(key, lookup.get("default", {"arv_psf": 510}))
    psf = nbhd["arv_psf"]

    discounts = reno_cfg.get("property_type_discounts", {"condo": 0.85, "townhouse": 0.95})
    pt = property_type.lower()
    adj_note = "No adjustment"
    if pt in discounts:
        psf = int(psf * discounts[pt])
        adj_note = f"{int((1 - discounts[pt]) * 100)}% discount applied for {pt}"

    return {
        "estimated_arv": arv,
        "neighborhood": neighborhood,
        "price_per_sqft": psf,
        "sqft": sqft,
        "property_type_adjustment": adj_note,
    }


# ---------------------------------------------------------------------------
# Legacy module-level constants (for data_fetcher.py backward compat)
# ---------------------------------------------------------------------------

def _get_neighborhood_avg_dom_map() -> dict:
    """Returns avg_dom lookup dict — used by data_fetcher at import time."""
    try:
        from database import get_model_config
        cfg = get_model_config()
        result = {}
        for n in cfg["neighborhoods"]:
            result[n["name"].lower()] = n["avg_dom"]
        return result
    except Exception:
        # Fallback if DB not yet initialized
        return {
            "rainier valley": 28, "beacon hill": 22, "white center": 32,
            "delridge": 30, "georgetown": 35, "columbia city": 18,
            "west seattle": 20, "lake city": 25, "northgate": 21,
            "bitter lake": 27, "crown hill": 24, "maple leaf": 22,
            "default": 22,
        }


# data_fetcher imports this at module level — keep it as a live dict proxy
NEIGHBORHOOD_AVG_DOM = _get_neighborhood_avg_dom_map()
