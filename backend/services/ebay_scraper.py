"""
eBay web scraper — zero-API-key fallback.

Scrapes eBay search result pages for active and sold/completed listings.
Uses httpx + BeautifulSoup.  Intended as a fallback when eBay API keys
are not configured; the API path is preferred when keys are available.
"""

import asyncio
import re
import logging
from urllib.parse import quote_plus, urlencode
from functools import partial

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

try:
    from curl_cffi.requests import AsyncSession as _CurlSession
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

_BASE = "https://www.ebay.com/sch/i.html"

_curl_session: object | None = None
_httpx_client: httpx.AsyncClient | None = None


def _parse_price(text: str) -> float | None:
    """Extract first numeric dollar amount from a price string."""
    if not text:
        return None
    text = text.replace(",", "")
    m = re.search(r"\$\s*([\d.]+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"([\d.]+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _parse_total(soup: BeautifulSoup) -> int:
    """Extract the total result count from the page heading."""
    heading = soup.select_one(".srp-controls__count-heading")
    if heading:
        m = re.search(r"([\d,]+)", heading.get_text())
        if m:
            return int(m.group(1).replace(",", ""))
    h1 = soup.select_one("h1.srp-controls__count-heading, h2.srp-controls__count-heading")
    if h1:
        m = re.search(r"([\d,]+)", h1.get_text())
        if m:
            return int(m.group(1).replace(",", ""))
    return 0


def _parse_items(soup: BeautifulSoup, listing_type: str = "active") -> list[dict]:
    """Parse search result items from the page."""
    items = []
    for li in soup.select("li.s-item"):
        title_el = li.select_one(".s-item__title span[role='heading']") or li.select_one(".s-item__title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if title.lower().startswith("shop on ebay"):
            continue

        link_el = li.select_one("a.s-item__link")
        url = link_el["href"] if link_el and link_el.has_attr("href") else ""

        img_el = li.select_one(".s-item__image-img")
        image = ""
        if img_el:
            image = img_el.get("src", "") or img_el.get("data-src", "")

        price_el = li.select_one(".s-item__price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_price(price_text)

        cond_el = li.select_one(".SECONDARY_INFO")
        condition = cond_el.get_text(strip=True) if cond_el else ""

        sold_date = ""
        if listing_type == "sold":
            date_el = li.select_one(".s-item__title--tag .POSITIVE, .s-item__ended-date, .s-item__endedDate")
            if date_el:
                sold_date = date_el.get_text(strip=True)
            if not sold_date:
                for span in li.select("span.s-item__detail"):
                    txt = span.get_text(strip=True).lower()
                    if "sold" in txt:
                        sold_date = span.get_text(strip=True)
                        break

        items.append({
            "title": title,
            "price": price,
            "currency": "USD",
            "condition": condition,
            "image_url": image,
            "item_url": url,
            "source": "ebay",
            "listing_type": listing_type,
            "sold_date": sold_date if listing_type == "sold" else "",
        })

    return items


async def _fetch_page(url: str, params: dict) -> BeautifulSoup | None:
    """Fetch an eBay page, preferring curl_cffi for browser-like TLS."""
    full_url = f"{url}?{urlencode(params)}"

    if _HAS_CURL_CFFI:
        try:
            global _curl_session
            if _curl_session is None:
                _curl_session = _CurlSession(impersonate="chrome")
            resp = await _curl_session.get(full_url, headers=_HEADERS)
            if resp.status_code != 200:
                log.warning("eBay scrape (curl) returned %s for %s", resp.status_code, params.get("_nkw"))
            else:
                return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            log.warning("eBay scrape (curl) error: %s — falling back to httpx", e)

    try:
        global _httpx_client
        if _httpx_client is None or _httpx_client.is_closed:
            _httpx_client = httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_HEADERS)
            try:
                await _httpx_client.get("https://www.ebay.com/")
            except Exception:
                pass
        resp = await _httpx_client.get(url, params=params, headers={
            **_HEADERS, "Referer": "https://www.ebay.com/",
        })
        if resp.status_code != 200:
            log.warning("eBay scrape (httpx) returned %s for %s", resp.status_code, params.get("_nkw"))
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        log.warning("eBay scrape error: %s", e)
        return None


async def scrape_active_listings(query: str, limit: int = 48) -> tuple[list[dict], int]:
    """Scrape eBay search results for active Buy-It-Now listings."""
    params = {
        "_nkw": query,
        "_ipg": str(min(limit, 240)),
        "LH_BIN": "1",
        "_sop": "12",
    }
    soup = await _fetch_page(_BASE, params)
    if soup is None:
        return [], 0
    total = _parse_total(soup)
    items = _parse_items(soup, "active")
    log.info("eBay scrape active: %d items (total %d) for '%s'", len(items), total, query)
    return items[:limit], total


async def scrape_sold_listings(query: str, limit: int = 48) -> tuple[list[dict], int]:
    """Scrape eBay sold/completed listings."""
    params = {
        "_nkw": query,
        "_ipg": str(min(limit, 240)),
        "LH_Complete": "1",
        "LH_Sold": "1",
        "_sop": "13",
    }
    soup = await _fetch_page(_BASE, params)
    if soup is None:
        return [], 0
    total = _parse_total(soup)
    items = _parse_items(soup, "sold")
    log.info("eBay scrape sold: %d items (total %d) for '%s'", len(items), total, query)
    return items[:limit], total


async def scrape_completed_listings(query: str, limit: int = 100) -> tuple[list[dict], int]:
    """Scrape all completed (sold+unsold) for sell-through calculation."""
    params = {
        "_nkw": query,
        "_ipg": str(min(limit, 240)),
        "LH_Complete": "1",
        "_sop": "13",
    }
    soup = await _fetch_page(_BASE, params)
    if soup is None:
        return [], 0
    total = _parse_total(soup)
    items = _parse_items(soup, "sold")
    return items[:limit], total


async def scrape_all(query: str) -> dict:
    """Scrape active, sold, and completed in parallel — mirrors ebay_service.search_all."""
    active_t = asyncio.create_task(scrape_active_listings(query))
    sold_t = asyncio.create_task(scrape_sold_listings(query))
    completed_t = asyncio.create_task(scrape_completed_listings(query))

    active_r, sold_r, completed_r = await asyncio.gather(
        active_t, sold_t, completed_t, return_exceptions=True,
    )

    active, total_active = ([], 0) if isinstance(active_r, Exception) else active_r
    sold, total_sold = ([], 0) if isinstance(sold_r, Exception) else sold_r
    _, total_completed = ([], 0) if isinstance(completed_r, Exception) else completed_r

    return {
        "active": active,
        "sold": sold,
        "total_active": total_active,
        "total_sold": total_sold,
        "total_completed": total_completed,
    }
