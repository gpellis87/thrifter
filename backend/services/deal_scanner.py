"""
Background deal scanner.

Periodically scans eBay for undervalued listings matching user-configured
watch queries, scores them, and saves opportunities to the database.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from backend.services import inventory
from backend.services.ebay_service import search_active_listings, search_sold_listings
from backend.services.pricing import analyze_prices

logger = logging.getLogger("thrifter.scanner")

_scanner_task: asyncio.Task | None = None
_scanner_running = False

INTERVAL_MINUTES = int(os.getenv("SCANNER_INTERVAL_MINUTES", "10"))


async def _scan_one_query(wq: dict) -> int:
    """Scan a single watch query. Returns number of new opportunities found."""
    query = wq["query"]
    max_buy = wq.get("max_buy_price")
    min_profit = wq.get("min_profit", 5.0)
    min_score = wq.get("min_deal_score", 50)

    try:
        active_result, sold_result = await asyncio.gather(
            search_active_listings(query, limit=50),
            search_sold_listings(query, limit=50),
            return_exceptions=True,
        )
    except Exception as e:
        logger.warning("Scan failed for '%s': %s", query, e)
        return 0

    if isinstance(active_result, Exception):
        logger.warning("Active search failed for '%s': %s", query, active_result)
        return 0

    active_items, total_active = active_result
    sold_items, total_sold = ([], 0) if isinstance(sold_result, Exception) else sold_result

    if not active_items:
        return 0

    pricing = analyze_prices(
        active_items, sold_items,
        total_active=total_active,
        total_sold=total_sold,
        total_completed=total_sold,  # approximation when we skip the third call
    )

    deal_score_data = pricing.get("deal_score", {})
    rec = pricing.get("recommendation", {})
    est_sell = rec.get("estimated_sell_price")

    new_count = 0
    for item in active_items:
        price = item.get("price")
        if price is None or price <= 0:
            continue

        if max_buy is not None and price > max_buy:
            continue

        if est_sell and est_sell > 0:
            item_profit = est_sell * 0.87 - 7.0 - price  # net after 13% fees + $7 ship
        else:
            continue

        if item_profit < min_profit:
            continue

        item_score = deal_score_data.get("score", 0)
        item_roi = (item_profit / price * 100) if price > 0 else 0
        if item_roi > 100:
            item_score = min(item_score + 15, 100)
        elif item_roi > 60:
            item_score = min(item_score + 5, 100)

        if item_score < min_score:
            continue

        verdict = deal_score_data.get("verdict", "OKAY")
        if item_roi >= 80:
            verdict = "HOT DEAL"
        elif item_roi >= 50:
            verdict = "GOOD DEAL"

        opp = await inventory.add_opportunity({
            "watch_query_id": wq["id"],
            "ebay_item_id": _extract_ebay_id(item.get("item_url", "")),
            "title": item.get("title", ""),
            "current_price": price,
            "estimated_sell_price": est_sell,
            "estimated_profit": round(item_profit, 2),
            "deal_score": item_score,
            "deal_verdict": verdict,
            "item_url": item.get("item_url", ""),
            "image_url": item.get("image_url", ""),
            "condition": item.get("condition", ""),
            "seller": item.get("seller", ""),
        })
        if opp:
            new_count += 1

    return new_count


def _extract_ebay_id(url: str) -> str:
    """Pull the eBay item ID from a URL, or use the full URL as fallback."""
    if "/itm/" in url:
        parts = url.split("/itm/")
        tail = parts[-1].split("?")[0].split("/")[-1]
        if tail.isdigit():
            return tail
    return url


async def run_scan_cycle():
    """Execute one full scan cycle across all enabled watch queries."""
    queries = await inventory.list_watch_queries(enabled_only=True)
    if not queries:
        return

    logger.info("Starting scan cycle: %d watch queries", len(queries))
    total_new = 0
    for wq in queries:
        try:
            new_count = await _scan_one_query(wq)
            await inventory.mark_watch_scanned(wq["id"], new_count)
            total_new += new_count
        except Exception as e:
            logger.error("Error scanning '%s': %s", wq["query"], e)

        await asyncio.sleep(1)  # rate-limit politeness between queries

    logger.info("Scan cycle complete: %d new opportunities found", total_new)


async def _scanner_loop():
    """Background loop that runs scan cycles on an interval."""
    global _scanner_running
    _scanner_running = True
    logger.info("Scanner started (interval=%dm)", INTERVAL_MINUTES)

    while _scanner_running:
        try:
            await run_scan_cycle()
        except Exception as e:
            logger.error("Scanner cycle error: %s", e)

        for _ in range(INTERVAL_MINUTES * 60):
            if not _scanner_running:
                break
            await asyncio.sleep(1)

    logger.info("Scanner stopped")


def start_scanner() -> bool:
    global _scanner_task, _scanner_running
    if _scanner_task and not _scanner_task.done():
        return False  # already running
    _scanner_running = True
    _scanner_task = asyncio.create_task(_scanner_loop())
    return True


def stop_scanner() -> bool:
    global _scanner_running, _scanner_task
    if not _scanner_running:
        return False
    _scanner_running = False
    return True


def is_scanner_running() -> bool:
    return _scanner_running and _scanner_task is not None and not _scanner_task.done()
