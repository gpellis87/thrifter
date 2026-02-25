"""
eBay user-level OAuth (authorization code grant).

Required for Sell APIs (Inventory API, etc.) which act on behalf of a user.
The app-level tokens we already have only support Browse/Finding (read-only).
"""

import os
import base64
import time
import httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID", "")
EBAY_REDIRECT_URI = os.getenv(
    "EBAY_REDIRECT_URI",
    os.getenv("EBAY_RU_NAME", ""),
)

SELL_SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]

TOKEN_FILE = Path(__file__).resolve().parent.parent.parent / "data" / ".ebay_user_token.json"

_user_token_cache: dict = {"access_token": None, "refresh_token": None, "expires_at": 0}


def _load_stored_token():
    if TOKEN_FILE.exists():
        import json
        try:
            data = json.loads(TOKEN_FILE.read_text())
            _user_token_cache.update(data)
        except Exception:
            pass


def _save_token():
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    import json
    TOKEN_FILE.write_text(json.dumps(_user_token_cache))


_load_stored_token()


def get_consent_url() -> str | None:
    if not EBAY_APP_ID or not EBAY_REDIRECT_URI:
        return None
    scope = " ".join(SELL_SCOPES)
    return (
        f"https://auth.ebay.com/oauth2/authorize"
        f"?client_id={EBAY_APP_ID}"
        f"&response_type=code"
        f"&redirect_uri={EBAY_REDIRECT_URI}"
        f"&scope={scope}"
    )


async def exchange_code(auth_code: str) -> dict:
    """Exchange the authorization code from the OAuth callback for tokens."""
    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": EBAY_REDIRECT_URI,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _user_token_cache["access_token"] = data["access_token"]
    _user_token_cache["refresh_token"] = data.get("refresh_token")
    _user_token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
    _save_token()
    return {"ok": True}


async def _refresh_token() -> str:
    rt = _user_token_cache.get("refresh_token")
    if not rt:
        raise RuntimeError("No refresh token available. Re-authorize via /api/ebay/auth.")
    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    scope = " ".join(SELL_SCOPES)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "scope": scope,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _user_token_cache["access_token"] = data["access_token"]
    _user_token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
    _save_token()
    return data["access_token"]


async def get_user_token() -> str | None:
    """Get a valid user access token, refreshing if needed."""
    if not _user_token_cache.get("refresh_token"):
        return None
    if _user_token_cache.get("access_token") and time.time() < _user_token_cache["expires_at"] - 120:
        return _user_token_cache["access_token"]
    try:
        return await _refresh_token()
    except Exception:
        return None


def has_seller_access() -> bool:
    return bool(_user_token_cache.get("refresh_token"))
