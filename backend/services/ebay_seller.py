"""
eBay Inventory API integration for auto-listing.

Uses user-level OAuth tokens to create listings on behalf of the seller.
This is optional — the app works without it via copy-paste listings.
"""

import httpx
import logging
from backend.services.ebay_auth import get_user_token

logger = logging.getLogger("thrifter.seller")

BASE_URL = "https://api.ebay.com/sell/inventory/v1"


async def publish_listing(listing: dict, sku: str) -> dict:
    """
    Create an eBay listing via the Inventory API.

    Steps:
    1. createOrReplaceInventoryItem (SKU-based)
    2. createOffer
    3. publishOffer

    Returns dict with listing_id and listing_url on success.
    """
    token = await get_user_token()
    if not token:
        raise RuntimeError("No eBay seller access configured. Complete OAuth at /api/ebay/auth.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
    }

    # 1. Create inventory item
    item_body = {
        "product": {
            "title": listing.get("title", ""),
            "description": listing.get("description", ""),
            "aspects": _build_aspects(listing.get("item_specifics", {})),
        },
        "condition": _map_condition(listing.get("condition", "Used - Good")),
        "availability": {
            "shipToLocationAvailability": {"quantity": 1}
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{BASE_URL}/inventory_item/{sku}",
            headers=headers,
            json=item_body,
        )
        if resp.status_code not in (200, 201, 204):
            logger.error("Failed to create inventory item: %s %s", resp.status_code, resp.text)
            raise RuntimeError(f"Inventory item creation failed: {resp.status_code}")

        # 2. Create offer
        offer_body = {
            "sku": sku,
            "marketplaceId": "EBAY_US",
            "format": "FIXED_PRICE",
            "pricingSummary": {
                "price": {
                    "value": str(listing.get("suggested_price", "19.99")),
                    "currency": "USD",
                }
            },
            "listingDescription": listing.get("description", ""),
            "availableQuantity": 1,
        }

        resp = await client.post(
            f"{BASE_URL}/offer",
            headers=headers,
            json=offer_body,
        )
        if resp.status_code not in (200, 201):
            logger.error("Failed to create offer: %s %s", resp.status_code, resp.text)
            raise RuntimeError(f"Offer creation failed: {resp.status_code}")

        offer_data = resp.json()
        offer_id = offer_data.get("offerId")

        # 3. Publish offer
        resp = await client.post(
            f"{BASE_URL}/offer/{offer_id}/publish",
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            logger.error("Failed to publish offer: %s %s", resp.status_code, resp.text)
            raise RuntimeError(f"Publish failed: {resp.status_code}")

        publish_data = resp.json()
        listing_id = publish_data.get("listingId", "")
        listing_url = f"https://www.ebay.com/itm/{listing_id}" if listing_id else ""

        return {
            "listing_id": listing_id,
            "listing_url": listing_url,
            "offer_id": offer_id,
        }


def _build_aspects(specifics: dict) -> dict:
    return {k: [str(v)] for k, v in specifics.items() if v}


def _map_condition(condition_str: str) -> str:
    mapping = {
        "new": "NEW",
        "open box": "LIKE_NEW",
        "used - like new": "LIKE_NEW",
        "used - good": "GOOD",
        "used - fair": "ACCEPTABLE",
        "for parts": "FOR_PARTS_OR_NOT_WORKING",
    }
    return mapping.get(condition_str.lower().strip(), "GOOD")
