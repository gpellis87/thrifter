import os
import asyncio
import base64
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID", "")

_token_cache: dict = {"token": None, "expires_at": 0}


async def _get_oauth_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = time.time() + data["expires_in"]
        return data["access_token"]


async def search_active_listings(query: str, limit: int = 40) -> list[dict]:
    """Search eBay Browse API for currently active listings."""
    token = await _get_oauth_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={"q": query, "limit": min(limit, 50), "sort": "price"},
        )
        resp.raise_for_status()
        data = resp.json()

    total_active = data.get("total", 0)
    items = []
    for item in data.get("itemSummaries", []):
        price_val = item.get("price", {}).get("value")
        img = ""
        if item.get("thumbnailImages"):
            img = item["thumbnailImages"][0].get("imageUrl", "")
        elif item.get("image"):
            img = item["image"].get("imageUrl", "")
        items.append({
            "title": item.get("title", ""),
            "price": float(price_val) if price_val else None,
            "currency": item.get("price", {}).get("currency", "USD"),
            "condition": item.get("condition", ""),
            "image_url": img,
            "item_url": item.get("itemWebUrl", ""),
            "seller": item.get("seller", {}).get("username", ""),
            "source": "ebay",
            "listing_type": "active",
        })
    return items, total_active


def _parse_finding_items(data: dict) -> tuple[list[dict], int]:
    """Parse Finding API response into item list + total count."""
    response_key = None
    for k in data:
        if k.endswith("Response"):
            response_key = k
            break
    if not response_key:
        return [], 0

    resp_body = data[response_key][0]
    total = int(
        resp_body.get("paginationOutput", [{}])[0]
        .get("totalEntries", ["0"])[0]
    )
    results = resp_body.get("searchResult", [{}])[0].get("item", [])

    items = []
    for item in results:
        price_info = (
            item.get("sellingStatus", [{}])[0]
            .get("currentPrice", [{}])[0]
        )
        price_val = price_info.get("__value__")

        title = item.get("title", [""])[0] if isinstance(item.get("title"), list) else item.get("title", "")
        gallery = item.get("galleryURL", [""])[0] if isinstance(item.get("galleryURL"), list) else item.get("galleryURL", "")
        view_url = item.get("viewItemURL", [""])[0] if isinstance(item.get("viewItemURL"), list) else item.get("viewItemURL", "")
        cond = ""
        if item.get("condition"):
            cond_list = item["condition"][0].get("conditionDisplayName", [""])
            cond = cond_list[0] if isinstance(cond_list, list) else cond_list

        end_time = ""
        if item.get("listingInfo"):
            et = item["listingInfo"][0].get("endTime", [""])
            end_time = et[0] if isinstance(et, list) else et

        items.append({
            "title": title,
            "price": float(price_val) if price_val else None,
            "currency": price_info.get("@currencyId", "USD"),
            "condition": cond,
            "image_url": gallery,
            "item_url": view_url,
            "source": "ebay",
            "listing_type": "sold",
            "sold_date": end_time,
        })
    return items, total


async def _finding_api_search(query: str, sold_only: bool, limit: int = 100) -> tuple[list[dict], int]:
    """Search eBay Finding API for completed items."""
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": query,
        "paginationInput.entriesPerPage": str(min(limit, 100)),
        "sortOrder": "EndTimeSoonest",
    }
    if sold_only:
        params["itemFilter(0).name"] = "SoldItemsOnly"
        params["itemFilter(0).value"] = "true"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://svcs.ebay.com/services/search/FindingService/v1",
            params=params,
        )
        resp.raise_for_status()
        return _parse_finding_items(resp.json())


async def search_sold_listings(query: str, limit: int = 40) -> tuple[list[dict], int]:
    return await _finding_api_search(query, sold_only=True, limit=limit)


async def search_completed_listings(query: str, limit: int = 100) -> tuple[list[dict], int]:
    """All completed listings (sold + unsold) for sell-through calculation."""
    return await _finding_api_search(query, sold_only=False, limit=limit)


async def search_by_upc(upc: str) -> dict:
    """Search eBay by UPC/barcode."""
    token = await _get_oauth_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={"gtin": upc, "limit": 40},
        )
        if resp.status_code == 200:
            data = resp.json()
            total_active = data.get("total", 0)
            items = []
            for item in data.get("itemSummaries", []):
                price_val = item.get("price", {}).get("value")
                img = ""
                if item.get("thumbnailImages"):
                    img = item["thumbnailImages"][0].get("imageUrl", "")
                elif item.get("image"):
                    img = item["image"].get("imageUrl", "")
                items.append({
                    "title": item.get("title", ""),
                    "price": float(price_val) if price_val else None,
                    "currency": item.get("price", {}).get("currency", "USD"),
                    "condition": item.get("condition", ""),
                    "image_url": img,
                    "item_url": item.get("itemWebUrl", ""),
                    "source": "ebay",
                    "listing_type": "active",
                })
            return items, total_active

    # Fallback: search by UPC as keywords
    return await search_active_listings(upc, limit=40)


def _api_keys_configured() -> bool:
    return bool(EBAY_APP_ID) and EBAY_APP_ID != "your-ebay-app-id"


async def search_all(query: str) -> dict:
    """Run active, sold, and total-completed searches in parallel.

    Routes through API or web scraper based on user settings.
    Mode "auto" tries the API first, falls back to scraping if keys are missing.
    """
    from backend.services import settings as _settings
    from backend.services import ebay_scraper

    mode = _settings.get("ebay_mode")
    use_api = (mode == "api") or (mode == "auto" and _api_keys_configured())
    use_scrape = (mode == "scrape") or (mode == "auto" and not _api_keys_configured())

    if use_api:
        active_task = asyncio.create_task(search_active_listings(query))
        sold_task = asyncio.create_task(search_sold_listings(query))
        completed_task = asyncio.create_task(search_completed_listings(query))

        active_r, sold_r, completed_r = await asyncio.gather(
            active_task, sold_task, completed_task, return_exceptions=True
        )

        active, total_active = ([], 0) if isinstance(active_r, Exception) else active_r
        sold, total_sold = ([], 0) if isinstance(sold_r, Exception) else sold_r
        _, total_completed = ([], 0) if isinstance(completed_r, Exception) else completed_r

        # If API returned results, use them
        if active or sold:
            return {
                "active": active,
                "sold": sold,
                "total_active": total_active,
                "total_sold": total_sold,
                "total_completed": total_completed,
                "source_mode": "api",
            }

        # API failed or returned nothing — fall back to scrape in auto mode
        if mode != "api":
            use_scrape = True

    if use_scrape:
        result = await ebay_scraper.scrape_all(query)
        result["source_mode"] = "scrape"
        return result

    return {
        "active": [],
        "sold": [],
        "total_active": 0,
        "total_sold": 0,
        "total_completed": 0,
        "source_mode": "none",
    }
