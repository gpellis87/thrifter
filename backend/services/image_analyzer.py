import base64
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client

SYSTEM_PROMPT = """You are a product identification expert for resellers and thrifters.
Given an image of an item, identify it with maximum specificity. Return a JSON object with:
- "title": A concise product title (e.g. "Nike Air Max 90 White/Black Men's Size 10")
- "search_query": The best eBay search query to find this exact item (short, keyword-focused)
- "category": General category (clothing, electronics, furniture, collectibles, toys, books, etc.)
- "brand": Brand name if identifiable, otherwise null
- "condition_notes": Any visible condition details

Return ONLY valid JSON, no markdown fencing."""


async def analyze_image(image_data: bytes, mime_type: str = "image/jpeg") -> dict:
    b64 = base64.b64encode(image_data).decode("utf-8")

    response = await _get_client().chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Identify this item for resale research:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                ],
            },
        ],
        max_tokens=300,
        temperature=0.2,
    )

    import json
    raw = response.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


async def refine_text_query(description: str) -> dict:
    """Turn a freeform text description into structured search parameters."""
    response = await _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Based on this description, generate the JSON identification object: {description}",
            },
        ],
        max_tokens=300,
        temperature=0.2,
    )

    import json
    raw = response.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)
