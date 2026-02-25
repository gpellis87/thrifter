import statistics
from datetime import datetime, timezone


def analyze_prices(
    active_listings: list[dict],
    sold_listings: list[dict],
    total_active: int = 0,
    total_sold: int = 0,
    total_completed: int = 0,
) -> dict:
    active_prices = [i["price"] for i in active_listings if i.get("price") and i["price"] > 0]
    sold_prices = [i["price"] for i in sold_listings if i.get("price") and i["price"] > 0]

    avg_asking = statistics.mean(active_prices) if active_prices else None
    median_asking = statistics.median(active_prices) if active_prices else None
    avg_sold = statistics.mean(sold_prices) if sold_prices else None
    median_sold = statistics.median(sold_prices) if sold_prices else None

    reference_price = median_sold or median_asking

    sell_through = _calc_sell_through(
        sold_listings, total_sold, total_completed, total_active
    )
    recommendation = _calculate_recommendation(reference_price, sold_prices, sell_through)
    deal_score = _calculate_deal_score(recommendation, sell_through)

    return {
        "active_listings_count": len(active_prices),
        "sold_listings_count": len(sold_prices),
        "total_active_on_market": total_active,
        "asking_price": {
            "average": round(avg_asking, 2) if avg_asking else None,
            "median": round(median_asking, 2) if median_asking else None,
            "low": round(min(active_prices), 2) if active_prices else None,
            "high": round(max(active_prices), 2) if active_prices else None,
        },
        "sold_price": {
            "average": round(avg_sold, 2) if avg_sold else None,
            "median": round(median_sold, 2) if median_sold else None,
            "low": round(min(sold_prices), 2) if sold_prices else None,
            "high": round(max(sold_prices), 2) if sold_prices else None,
        },
        "sell_through": sell_through,
        "recommendation": recommendation,
        "deal_score": deal_score,
    }


def _calc_sell_through(
    sold_listings: list[dict],
    total_sold: int,
    total_completed: int,
    total_active: int,
) -> dict:
    """
    Calculate sell-through rate and average days to sell.
    STR = sold / (sold + unsold completed) over the recent window.
    """
    str_pct = None
    if total_completed > 0:
        str_pct = round((total_sold / total_completed) * 100, 1)

    # Estimate avg days to sell from sold listing dates
    avg_days = None
    now = datetime.now(timezone.utc)
    day_diffs = []
    for item in sold_listings:
        sd = item.get("sold_date", "")
        if not sd:
            continue
        try:
            sold_dt = datetime.fromisoformat(sd.replace("Z", "+00:00"))
            diff = (now - sold_dt).days
            if 0 <= diff <= 180:
                day_diffs.append(diff)
        except (ValueError, TypeError):
            continue

    if day_diffs:
        avg_days = round(statistics.mean(day_diffs), 1)

    # Liquidity score
    if str_pct is not None:
        if str_pct >= 60:
            liquidity = "hot"
        elif str_pct >= 35:
            liquidity = "steady"
        elif str_pct >= 15:
            liquidity = "slow"
        else:
            liquidity = "dead"
    elif total_sold > 10:
        liquidity = "steady"
    else:
        liquidity = "unknown"

    supply_demand = None
    if total_active and total_sold:
        ratio = total_sold / max(total_active, 1)
        if ratio > 1.5:
            supply_demand = "High demand, low supply"
        elif ratio > 0.7:
            supply_demand = "Balanced market"
        elif ratio > 0.3:
            supply_demand = "Moderate supply"
        else:
            supply_demand = "Oversaturated — lots of competition"

    return {
        "sell_through_pct": str_pct,
        "avg_days_to_sell": avg_days,
        "liquidity": liquidity,
        "total_sold_recently": total_sold,
        "total_completed_recently": total_completed,
        "total_active_supply": total_active,
        "supply_demand_note": supply_demand,
    }


def _calculate_recommendation(
    reference_price: float | None,
    sold_prices: list[float],
    sell_through: dict,
) -> dict:
    if not reference_price:
        return {
            "max_buy_price": None,
            "estimated_profit": None,
            "roi_percent": None,
            "confidence": "low",
            "note": "Not enough data to make a recommendation",
        }

    selling_fees_pct = 0.13
    avg_shipping = 7.00
    target_roi = 0.40

    net_after_fees = reference_price * (1 - selling_fees_pct) - avg_shipping
    max_buy = net_after_fees / (1 + target_roi)
    estimated_profit = net_after_fees - max_buy

    confidence = "low"
    if len(sold_prices) >= 10:
        confidence = "high"
    elif len(sold_prices) >= 5:
        confidence = "medium"

    spread_warning = None
    if len(sold_prices) >= 3:
        stdev = statistics.stdev(sold_prices)
        cv = stdev / statistics.mean(sold_prices)
        if cv > 0.5:
            spread_warning = "High price variance — condition and exact model matter a lot. Be cautious."
            max_buy *= 0.8
            estimated_profit = net_after_fees - max_buy

    # Penalize slow-moving items
    liquidity = sell_through.get("liquidity", "unknown")
    liquidity_warning = None
    if liquidity == "dead":
        liquidity_warning = "Very low sell-through. This item may sit for months."
        max_buy *= 0.6
        estimated_profit = net_after_fees - max_buy
    elif liquidity == "slow":
        liquidity_warning = "Slow seller — be patient or price aggressively."
        max_buy *= 0.85
        estimated_profit = net_after_fees - max_buy

    return {
        "max_buy_price": round(max(max_buy, 0), 2),
        "estimated_sell_price": round(reference_price, 2),
        "net_after_fees": round(net_after_fees, 2),
        "estimated_profit": round(max(estimated_profit, 0), 2),
        "roi_percent": round((estimated_profit / max_buy) * 100, 1) if max_buy > 0 else 0,
        "confidence": confidence,
        "spread_warning": spread_warning,
        "liquidity_warning": liquidity_warning,
        "assumptions": {
            "ebay_fees_pct": selling_fees_pct * 100,
            "shipping_cost": avg_shipping,
            "target_roi_pct": target_roi * 100,
        },
    }


def _calculate_deal_score(recommendation: dict, sell_through: dict) -> dict:
    """
    Composite deal score: HOT DEAL / GOOD DEAL / OKAY / PASS.

    Weights:
      Profit margin  40%
      Sell-through   35%
      Confidence     15%
      Risk           10%
    """
    profit = recommendation.get("estimated_profit")
    roi = recommendation.get("roi_percent", 0)
    conf = recommendation.get("confidence", "low")
    str_pct = sell_through.get("sell_through_pct")
    liquidity = sell_through.get("liquidity", "unknown")

    if profit is None or recommendation.get("max_buy_price") is None:
        return {"score": 0, "verdict": "NO DATA", "color": "gray", "summary": "Not enough market data."}

    # Profit component (0-100)
    if roi >= 100:
        profit_score = 100
    elif roi >= 60:
        profit_score = 80
    elif roi >= 40:
        profit_score = 60
    elif roi >= 20:
        profit_score = 40
    else:
        profit_score = max(roi, 0)

    # Sell-through component (0-100)
    if str_pct is not None:
        str_score = min(str_pct * 1.5, 100)
    elif liquidity == "hot":
        str_score = 80
    elif liquidity == "steady":
        str_score = 55
    else:
        str_score = 30

    # Confidence component (0-100)
    conf_score = {"high": 100, "medium": 60, "low": 25}.get(conf, 25)

    # Risk penalty (0-100, higher = less risk = better)
    risk_score = 80
    if recommendation.get("spread_warning"):
        risk_score -= 30
    if recommendation.get("liquidity_warning"):
        risk_score -= 20
    risk_score = max(risk_score, 0)

    composite = (
        profit_score * 0.40
        + str_score * 0.35
        + conf_score * 0.15
        + risk_score * 0.10
    )

    if composite >= 75:
        verdict, color = "HOT DEAL", "green"
    elif composite >= 55:
        verdict, color = "GOOD DEAL", "blue"
    elif composite >= 35:
        verdict, color = "OKAY", "yellow"
    else:
        verdict, color = "PASS", "red"

    summaries = {
        "HOT DEAL": "Strong profit margin with solid demand. Buy with confidence.",
        "GOOD DEAL": "Decent profit potential. Worth picking up at the right price.",
        "OKAY": "Marginal opportunity. Only buy if priced well below the max.",
        "PASS": "Low margin or poor sell-through. Skip this one.",
    }

    return {
        "score": round(composite),
        "verdict": verdict,
        "color": color,
        "summary": summaries[verdict],
        "breakdown": {
            "profit": round(profit_score),
            "demand": round(str_score),
            "confidence": round(conf_score),
            "risk": round(risk_score),
        },
    }
