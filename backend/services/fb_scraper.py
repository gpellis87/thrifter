"""
Facebook Marketplace scraper using Playwright.

Flow:
  1. User clicks "Connect Facebook" → we open a visible browser to FB login
  2. User logs in manually → we save the browser session to disk
  3. Future searches use the saved session headlessly
  4. We intercept Facebook's GraphQL network responses for clean JSON data
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
FB_STATE_DIR = DATA_DIR / ".fb_browser_state"
_login_in_progress = False


def is_fb_connected() -> bool:
    """Check whether a saved Facebook session exists."""
    cookie_file = FB_STATE_DIR / "Default" / "Cookies"
    state_file = FB_STATE_DIR / "playwright_state.json"
    return cookie_file.exists() or state_file.exists()


async def fb_login() -> dict:
    """
    Launch a visible browser so the user can log into Facebook.
    Returns when the browser is closed or the user reaches marketplace.
    """
    global _login_in_progress
    if _login_in_progress:
        return {"status": "already_in_progress"}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"status": "error", "message": "Playwright not installed. Run: pip install playwright && playwright install chromium"}

    _login_in_progress = True
    try:
        FB_STATE_DIR.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(FB_STATE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.facebook.com/marketplace/")

            try:
                await page.wait_for_url("**/marketplace/**", timeout=300_000)
                await page.wait_for_timeout(3000)
            except Exception:
                pass

            await context.close()

        return {"status": "connected" if is_fb_connected() else "failed"}
    except Exception as e:
        log.error("FB login error: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        _login_in_progress = False


async def fb_disconnect() -> dict:
    """Remove saved Facebook session."""
    import shutil
    try:
        if FB_STATE_DIR.exists():
            shutil.rmtree(FB_STATE_DIR)
        return {"status": "disconnected"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _extract_marketplace_items(data: dict | list, results: list, depth: int = 0):
    """Recursively walk a GraphQL JSON response to find marketplace listings."""
    if depth > 15:
        return
    if isinstance(data, list):
        for item in data:
            _extract_marketplace_items(item, results, depth + 1)
        return
    if not isinstance(data, dict):
        return

    if "marketplace_listing_title" in data or ("listing" in data and isinstance(data.get("listing"), dict)):
        listing = data.get("listing", data)
        title = (
            listing.get("marketplace_listing_title", "")
            or listing.get("name", "")
        )
        price_obj = listing.get("listing_price", {}) or {}
        price_text = price_obj.get("formatted_amount", "") or price_obj.get("amount", "")
        price = None
        if price_text:
            cleaned = re.sub(r"[^\d.]", "", str(price_text))
            try:
                price = float(cleaned) if cleaned else None
            except ValueError:
                pass

        image = ""
        primary_photo = listing.get("primary_listing_photo", {}) or {}
        if primary_photo.get("image", {}).get("uri"):
            image = primary_photo["image"]["uri"]
        elif listing.get("primaryListingPhoto", {}).get("listing_image", {}).get("uri"):
            image = listing["primaryListingPhoto"]["listing_image"]["uri"]

        listing_id = listing.get("id", "") or listing.get("listing_id", "")
        location = ""
        loc_obj = listing.get("location", {}) or listing.get("marketplace_listing_seller", {}).get("location", {})
        if isinstance(loc_obj, dict):
            location = loc_obj.get("reverse_geocode", {}).get("city", "") or loc_obj.get("name", "")

        if title and (price is not None or image):
            results.append({
                "title": title,
                "price": price,
                "currency": "USD",
                "condition": listing.get("condition", {}).get("condition_text", "") if isinstance(listing.get("condition"), dict) else "",
                "image_url": image,
                "item_url": f"https://www.facebook.com/marketplace/item/{listing_id}" if listing_id else "",
                "source": "facebook",
                "listing_type": "active",
                "location": location,
            })
        return

    for val in data.values():
        if isinstance(val, (dict, list)):
            _extract_marketplace_items(val, results, depth + 1)


async def search_fb_marketplace(query: str, limit: int = 30) -> list[dict]:
    """Search Facebook Marketplace using a saved browser session."""
    if not is_fb_connected():
        return []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright not installed — cannot scrape FB Marketplace")
        return []

    results: list[dict] = []

    async with async_playwright() as pw:
        try:
            context = await pw.chromium.launch_persistent_context(
                str(FB_STATE_DIR),
                headless=True,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            log.error("Could not launch FB browser context: %s", e)
            return []

        page = context.pages[0] if context.pages else await context.new_page()

        captured_responses: list[dict] = []

        async def _on_response(response):
            if "api/graphql" not in response.url:
                return
            try:
                body = await response.json()
                captured_responses.append(body)
            except Exception:
                try:
                    text = await response.text()
                    for line in text.strip().split("\n"):
                        line = line.strip()
                        if line:
                            try:
                                captured_responses.append(json.loads(line))
                            except Exception:
                                pass
                except Exception:
                    pass

        page.on("response", _on_response)

        search_url = f"https://www.facebook.com/marketplace/search/?query={quote(query)}"
        try:
            await page.goto(search_url, wait_until="networkidle", timeout=20_000)
        except Exception:
            try:
                await page.goto(search_url, timeout=20_000)
                await page.wait_for_timeout(5000)
            except Exception as e:
                log.warning("FB navigation error: %s", e)
                await context.close()
                return []

        await page.wait_for_timeout(3000)

        try:
            for _ in range(2):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1500)
        except Exception:
            pass

        await context.close()

    for resp in captured_responses:
        _extract_marketplace_items(resp, results)

    seen_titles = set()
    deduped = []
    for item in results:
        key = (item["title"], item["price"])
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(item)

    log.info("FB Marketplace scrape: %d items for '%s'", len(deduped), query)
    return deduped[:limit]
