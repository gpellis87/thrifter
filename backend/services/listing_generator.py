import json
from backend.services.image_analyzer import _get_client

LISTING_PROMPT = """You are an expert eBay listing copywriter for resellers.
Given item details and market context, generate a complete, optimized eBay listing.

Return a JSON object with:
- "title": eBay-optimized title, max 80 chars, front-loaded with keywords buyers search for.
  Include brand, model, key attributes (size, color, material). No filler words.
- "subtitle": Optional subtitle for extra visibility (max 55 chars), or null
- "description": HTML-formatted item description (3-5 short paragraphs). Professional tone,
  highlight condition, features, specs. Include a short "What's included" section.
- "category_suggestion": Suggested eBay category name
- "item_specifics": Object with key-value pairs for eBay item specifics
  (Brand, Model, Color, Size, Material, Type, Style, etc. — whatever applies)
- "condition": One of "New", "Open Box", "Used - Like New", "Used - Good", "Used - Fair", "For Parts"
- "suggested_price": Recommended listing price (number)
- "pricing_strategy": "auction" or "buy_it_now" with brief rationale
- "keywords": Array of 5-8 search keywords/phrases buyers would use
- "shipping_notes": Recommended shipping method and estimated weight category

Return ONLY valid JSON, no markdown fencing."""


async def generate_listing(
    identification: dict,
    pricing_data: dict | None = None,
    image_data: bytes | None = None,
    mime_type: str = "image/jpeg",
) -> dict:
    context_parts = [f"Item: {json.dumps(identification)}"]

    if pricing_data:
        sold = pricing_data.get("sold_price", {})
        rec = pricing_data.get("recommendation", {})
        context_parts.append(
            f"Market data: avg sold ${sold.get('average', 'N/A')}, "
            f"median sold ${sold.get('median', 'N/A')}, "
            f"recommended sell price ${rec.get('estimated_sell_price', 'N/A')}"
        )

    messages = [
        {"role": "system", "content": LISTING_PROMPT},
    ]

    user_content = []
    user_content.append({
        "type": "text",
        "text": f"Generate an optimized eBay listing for this item:\n\n{chr(10).join(context_parts)}",
    })

    if image_data:
        import base64
        b64 = base64.b64encode(image_data).decode("utf-8")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
        })

    messages.append({"role": "user", "content": user_content})

    response = await _get_client().chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=1200,
        temperature=0.3,
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)
