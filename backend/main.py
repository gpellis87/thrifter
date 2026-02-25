import asyncio
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from backend.services.image_analyzer import analyze_image, refine_text_query
from backend.services.ebay_service import search_all, search_by_upc
from backend.services.pricing import analyze_prices
from backend.services.listing_generator import generate_listing
from backend.services.marketplace import search_all_platforms
from backend.services.barcode import lookup_upc
from backend.services import inventory
from backend.services.deal_scanner import (
    start_scanner, stop_scanner, is_scanner_running, run_scan_cycle,
)
from backend.services.auto_relister import purchase_and_relist
from backend.services import ebay_auth
from backend.services import settings as user_settings
from backend.services import fb_scraper

logging.basicConfig(level=logging.INFO)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("SCANNER_ENABLED", "true").lower() in ("true", "1", "yes"):
        start_scanner()
    yield
    stop_scanner()


app = FastAPI(title="Thrifter", version="3.0.0", lifespan=lifespan)


@app.get("/api/status")
async def api_status():
    """Report which API keys are configured so the frontend can show helpful messages."""
    import httpx
    ebay_ok = bool(os.getenv("EBAY_APP_ID", "")) and os.getenv("EBAY_APP_ID", "") != "your-ebay-app-id"
    openai_ok = bool(os.getenv("OPENAI_API_KEY", "")) and os.getenv("OPENAI_API_KEY", "") != "sk-your-openai-key-here"
    settings = user_settings.load()

    # Quick network check
    network_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.head("https://www.ebay.com/", follow_redirects=True)
            network_ok = r.status_code < 400
    except Exception:
        pass

    has_proxy = bool(os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"))

    return {
        "ebay_configured": ebay_ok,
        "openai_configured": openai_ok,
        "scanner_running": is_scanner_running(),
        "seller_access": ebay_auth.has_seller_access(),
        "ebay_mode": settings.get("ebay_mode", "auto"),
        "fb_connected": fb_scraper.is_fb_connected(),
        "fb_enabled": settings.get("fb_marketplace_enabled", True),
        "settings": settings,
        "network_ok": network_ok,
        "has_proxy": has_proxy,
    }


def _build_response(identification: dict, query: str, ebay: dict, extra_listings: dict | None = None):
    pricing = analyze_prices(
        ebay["active"], ebay["sold"],
        total_active=ebay["total_active"],
        total_sold=ebay["total_sold"],
        total_completed=ebay["total_completed"],
    )
    resp = {
        "identification": identification,
        "search_query": query,
        "listings": {
            "ebay_active": ebay["active"][:15],
            "ebay_sold": ebay["sold"][:15],
        },
        "pricing": pricing,
        "ebay_source_mode": ebay.get("source_mode", "unknown"),
    }
    if extra_listings:
        resp["listings"]["poshmark"] = extra_listings.get("poshmark", [])[:10]
        resp["listings"]["mercari"] = extra_listings.get("mercari", [])[:10]
        resp["listings"]["facebook"] = extra_listings.get("facebook", [])[:15]
    return resp


# ── Search endpoints ──────────────────────────────────────────────

@app.post("/api/search/image")
async def search_by_image(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Please upload an image file")

    image_data = await file.read()
    if len(image_data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 10MB")

    try:
        identification = await analyze_image(image_data, file.content_type)
    except Exception as e:
        raise HTTPException(500, f"Image analysis failed: {str(e)}")

    query = identification.get("search_query", identification.get("title", ""))
    if not query:
        raise HTTPException(422, "Could not identify the item in the image")

    ebay_task = asyncio.create_task(search_all(query))
    mp_task = asyncio.create_task(search_all_platforms(query))
    ebay, extra = await asyncio.gather(ebay_task, mp_task)

    return _build_response(identification, query, ebay, extra)


@app.post("/api/search/text")
async def search_by_text(query: str = Form(...)):
    if not query.strip():
        raise HTTPException(400, "Please provide a description")

    try:
        identification = await refine_text_query(query.strip())
    except Exception:
        identification = {
            "title": query.strip(),
            "search_query": query.strip(),
            "category": None,
            "brand": None,
        }

    search_q = identification.get("search_query", query.strip())

    ebay_task = asyncio.create_task(search_all(search_q))
    mp_task = asyncio.create_task(search_all_platforms(search_q))
    ebay, extra = await asyncio.gather(ebay_task, mp_task)

    return _build_response(identification, search_q, ebay, extra)


@app.post("/api/search/barcode")
async def search_by_barcode(upc: str = Form(...)):
    upc = upc.strip()
    if not upc:
        raise HTTPException(400, "Please provide a barcode/UPC")

    identification = await lookup_upc(upc)
    if not identification:
        identification = {"title": upc, "search_query": upc, "brand": None, "category": None, "upc": upc}

    search_q = identification.get("search_query", upc)

    ebay_task = asyncio.create_task(search_all(search_q))
    mp_task = asyncio.create_task(search_all_platforms(search_q))
    ebay, extra = await asyncio.gather(ebay_task, mp_task)

    return _build_response(identification, search_q, ebay, extra)


# ── Listing generator ────────────────────────────────────────────

@app.post("/api/listing/generate")
async def generate_listing_endpoint(
    identification: str = Form(...),
    pricing_json: str = Form(None),
):
    import json
    try:
        ident = json.loads(identification)
    except Exception:
        raise HTTPException(400, "Invalid identification JSON")

    pricing_data = None
    if pricing_json:
        try:
            pricing_data = json.loads(pricing_json)
        except Exception:
            pass

    try:
        listing = await generate_listing(ident, pricing_data)
    except Exception as e:
        raise HTTPException(500, f"Listing generation failed: {str(e)}")

    return listing


# ── Inventory CRUD ───────────────────────────────────────────────

class InventoryItem(BaseModel):
    title: str
    brand: str | None = None
    category: str | None = None
    purchase_price: float | None = None
    purchase_date: str | None = None
    purchase_location: str | None = None
    storage_location: str | None = None
    status: str = "unlisted"
    listed_price: float | None = None
    listed_platform: str | None = None
    image_url: str | None = None
    notes: str | None = None
    search_query: str | None = None


class InventoryUpdate(BaseModel):
    title: str | None = None
    brand: str | None = None
    category: str | None = None
    purchase_price: float | None = None
    purchase_date: str | None = None
    purchase_location: str | None = None
    storage_location: str | None = None
    status: str | None = None
    listed_price: float | None = None
    listed_date: str | None = None
    listed_platform: str | None = None
    sold_price: float | None = None
    sold_date: str | None = None
    sold_platform: str | None = None
    shipping_cost: float | None = None
    platform_fees: float | None = None
    image_url: str | None = None
    notes: str | None = None


@app.get("/api/inventory")
async def list_inventory_endpoint(
    status: str | None = None,
    sort_by: str = "created_at",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
):
    return await inventory.list_items(status, sort_by, order, limit, offset)


@app.get("/api/inventory/dashboard")
async def inventory_dashboard():
    return await inventory.get_dashboard_stats()


@app.post("/api/inventory")
async def add_inventory_item(item: InventoryItem):
    return await inventory.add_item(item.model_dump(exclude_none=True))


@app.get("/api/inventory/{item_id}")
async def get_inventory_item(item_id: str):
    item = await inventory.get_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    return item


@app.put("/api/inventory/{item_id}")
async def update_inventory_item(item_id: str, data: InventoryUpdate):
    existing = await inventory.get_item(item_id)
    if not existing:
        raise HTTPException(404, "Item not found")
    return await inventory.update_item(item_id, data.model_dump(exclude_none=True))


@app.delete("/api/inventory/{item_id}")
async def delete_inventory_item(item_id: str):
    if not await inventory.delete_item(item_id):
        raise HTTPException(404, "Item not found")
    return {"ok": True}


# ── Watch Queries CRUD ───────────────────────────────────────────

class WatchQueryCreate(BaseModel):
    query: str
    category: str | None = None
    max_buy_price: float | None = None
    min_profit: float = 5.0
    min_deal_score: int = 50
    enabled: bool = True


class WatchQueryUpdate(BaseModel):
    query: str | None = None
    category: str | None = None
    max_buy_price: float | None = None
    min_profit: float | None = None
    min_deal_score: int | None = None
    enabled: bool | None = None


@app.get("/api/watch")
async def list_watches():
    return await inventory.list_watch_queries()


@app.post("/api/watch")
async def add_watch(data: WatchQueryCreate):
    return await inventory.add_watch_query(data.model_dump())


@app.put("/api/watch/{wq_id}")
async def update_watch(wq_id: str, data: WatchQueryUpdate):
    existing = await inventory.get_watch_query(wq_id)
    if not existing:
        raise HTTPException(404, "Watch query not found")
    return await inventory.update_watch_query(wq_id, data.model_dump(exclude_none=True))


@app.delete("/api/watch/{wq_id}")
async def delete_watch(wq_id: str):
    if not await inventory.delete_watch_query(wq_id):
        raise HTTPException(404, "Watch query not found")
    return {"ok": True}


# ── Opportunities ────────────────────────────────────────────────

@app.get("/api/opportunities")
async def list_opportunities_endpoint(
    status: str | None = None,
    watch_query_id: str | None = None,
    min_score: int | None = None,
    min_profit: float | None = None,
    sort_by: str = "found_at",
    order: str = "desc",
    limit: int = 100,
):
    return await inventory.list_opportunities(
        status=status,
        watch_query_id=watch_query_id,
        min_score=min_score,
        min_profit=min_profit,
        sort_by=sort_by,
        order=order,
        limit=limit,
    )


class PurchaseRequest(BaseModel):
    purchase_price: float
    purchase_location: str = "eBay Flip"


@app.post("/api/opportunities/{opp_id}/purchase")
async def purchase_opportunity(opp_id: str, data: PurchaseRequest):
    opp = await inventory.get_opportunity(opp_id)
    if not opp:
        raise HTTPException(404, "Opportunity not found")
    try:
        result = await purchase_and_relist(opp_id, data.purchase_price, data.purchase_location)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/opportunities/{opp_id}/dismiss")
async def dismiss_opportunity(opp_id: str):
    opp = await inventory.get_opportunity(opp_id)
    if not opp:
        raise HTTPException(404, "Opportunity not found")
    return await inventory.update_opportunity_status(opp_id, "dismissed")


# ── Scanner Control ──────────────────────────────────────────────

@app.get("/api/scanner/status")
async def scanner_status():
    stats = await inventory.get_scanner_stats()
    stats["running"] = is_scanner_running()
    stats["has_seller_access"] = ebay_auth.has_seller_access()
    return stats


@app.post("/api/scanner/start")
async def scanner_start():
    if start_scanner():
        return {"ok": True, "message": "Scanner started"}
    return {"ok": False, "message": "Scanner already running"}


@app.post("/api/scanner/stop")
async def scanner_stop():
    if stop_scanner():
        return {"ok": True, "message": "Scanner stopped"}
    return {"ok": False, "message": "Scanner not running"}


@app.post("/api/scanner/scan-now")
async def scanner_scan_now():
    """Trigger an immediate scan cycle (doesn't wait for timer)."""
    asyncio.create_task(run_scan_cycle())
    return {"ok": True, "message": "Scan triggered"}


# ── eBay OAuth ───────────────────────────────────────────────────

@app.get("/api/ebay/auth")
async def ebay_auth_start():
    url = ebay_auth.get_consent_url()
    if not url:
        raise HTTPException(400, "eBay credentials or redirect URI not configured")
    return {"auth_url": url}


@app.get("/api/ebay/callback")
async def ebay_auth_callback(code: str = Query(...)):
    try:
        await ebay_auth.exchange_code(code)
        return RedirectResponse("/?ebay_connected=1")
    except Exception as e:
        raise HTTPException(500, f"OAuth exchange failed: {str(e)}")


# ── Settings ──────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    return user_settings.load()


class SettingsUpdate(BaseModel):
    ebay_mode: str | None = None
    fb_marketplace_enabled: bool | None = None


@app.put("/api/settings")
async def update_settings(body: SettingsUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "ebay_mode" in updates and updates["ebay_mode"] not in ("api", "scrape", "auto"):
        raise HTTPException(400, "ebay_mode must be 'api', 'scrape', or 'auto'")
    return user_settings.save(updates)


# ── Facebook Marketplace ─────────────────────────────────────────

@app.get("/api/fb/status")
async def fb_status():
    return {
        "connected": fb_scraper.is_fb_connected(),
        "enabled": user_settings.get("fb_marketplace_enabled"),
    }


@app.post("/api/fb/connect")
async def fb_connect():
    """Launch a visible browser for the user to log into Facebook."""
    result = await fb_scraper.fb_login()
    return result


@app.post("/api/fb/disconnect")
async def fb_disconnect():
    result = await fb_scraper.fb_disconnect()
    return result


# ── Static files & frontend ─────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
