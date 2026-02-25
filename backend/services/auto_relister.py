"""
Auto-relist pipeline.

When a user marks an opportunity as purchased:
1. Creates an inventory record
2. Generates an AI-optimized listing
3. Optionally publishes to eBay via Inventory API (if user OAuth is configured)
4. Falls back to returning the listing for manual copy-paste
"""

import logging
from datetime import datetime, timezone

from backend.services import inventory
from backend.services.listing_generator import generate_listing
from backend.services.ebay_auth import get_user_token, has_seller_access
from backend.services.ebay_seller import publish_listing

logger = logging.getLogger("thrifter.relister")


async def purchase_and_relist(
    opportunity_id: str,
    purchase_price: float,
    purchase_location: str = "eBay Flip",
) -> dict:
    """
    Full pipeline: mark purchased -> add to inventory -> generate listing -> optionally publish.
    Returns the generated listing and inventory item.
    """
    opp = await inventory.get_opportunity(opportunity_id)
    if not opp:
        raise ValueError("Opportunity not found")

    now = datetime.now(timezone.utc).isoformat()

    # 1. Add to inventory
    inv_item = await inventory.add_item({
        "title": opp["title"],
        "purchase_price": purchase_price,
        "purchase_date": now[:10],
        "purchase_location": purchase_location,
        "status": "unlisted",
        "image_url": opp.get("image_url"),
        "notes": f"Auto-acquired from deal scanner. Original listing: {opp.get('item_url', '')}",
        "search_query": opp["title"],
    })

    # 2. Mark opportunity as purchased
    await inventory.update_opportunity_status(
        opportunity_id, "purchased", inventory_item_id=inv_item["id"]
    )

    # 3. Generate AI listing
    identification = {
        "title": opp["title"],
        "search_query": opp["title"],
        "category": None,
        "brand": None,
        "condition_notes": opp.get("condition", ""),
    }
    pricing_context = {
        "sold_price": {"average": opp.get("estimated_sell_price"), "median": opp.get("estimated_sell_price")},
        "recommendation": {"estimated_sell_price": opp.get("estimated_sell_price")},
    }

    try:
        listing = await generate_listing(identification, pricing_context)
    except Exception as e:
        logger.error("Listing generation failed: %s", e)
        listing = None

    # 4. Try auto-publish if eBay seller access is configured
    published = False
    ebay_listing_url = None
    if listing and has_seller_access():
        try:
            result = await publish_listing(listing, inv_item["id"])
            published = True
            ebay_listing_url = result.get("listing_url")
            await inventory.update_item(inv_item["id"], {
                "status": "listed",
                "listed_price": listing.get("suggested_price"),
                "listed_date": now[:10],
                "listed_platform": "eBay",
            })
            logger.info("Auto-published listing for %s", opp["title"])
        except Exception as e:
            logger.warning("Auto-publish failed (will return for manual listing): %s", e)

    return {
        "inventory_item": inv_item,
        "listing": listing,
        "published": published,
        "ebay_listing_url": ebay_listing_url,
        "opportunity": opp,
    }
