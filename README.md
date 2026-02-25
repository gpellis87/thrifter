# THRIFTER — Smart Resale Research Tool

Snap a photo, type a description, or scan a barcode to instantly see marketplace prices, sell-through rates, and what you should pay to make a profit. Works with **zero API keys** out of the box via web scraping, with optional API integrations for enhanced data.

![Python](https://img.shields.io/badge/Python-3.11+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### Search & Analysis
- **Photo lookup** — Upload an image and GPT-4o identifies the exact item
- **Text search** — Describe an item and get instant market data
- **Barcode/UPC scanner** — Enter a UPC for instant product lookup
- **Multi-platform results** — eBay, Facebook Marketplace, Poshmark, and Mercari

### Data Sources (Scrape vs API)
- **eBay scraping** — Works immediately, no API keys needed. Uses `curl_cffi` for browser-like TLS fingerprinting
- **eBay API** — Official Browse + Finding APIs for higher reliability and rate limits
- **Facebook Marketplace** — Playwright browser automation with saved login session
- **Poshmark / Mercari** — Internal endpoint scraping (best-effort)
- **Auto mode** — Tries API first, falls back to scraping automatically

### Market Intelligence
- **Sell-through rate** — What percentage of listings actually sell
- **Liquidity score** — HOT / STEADY / SLOW / DEAD rating
- **Average days to sell** — How long items sit before selling
- **Supply vs demand** — Whether the market is oversaturated

### Deal Score (0–100)
- **HOT DEAL / GOOD DEAL / OKAY / PASS** verdict for instant decisions
- Weighs profit (40%), demand (35%), confidence (15%), risk (10%)
- Designed for quick in-store decisions

### Deal Scanner & Auto-Relist
- **Watch queries** — Set up searches with target prices and minimum deal scores
- **Background scanner** — Continuously finds undervalued items on eBay
- **Opportunity feed** — Browse found deals sorted by score
- **Semi-auto relist** — Confirm a purchase, AI generates listing, optionally auto-publishes to eBay

### AI Listing Generator
- One-click optimized eBay listing from search results
- Title, description, item specifics, category, keywords, pricing strategy

### Inventory Tracker
- SQLite database, zero config
- Track purchases, storage, listing status, sale prices
- **P&L dashboard** — Total invested, revenue, profit, ROI

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/thrifter.git
cd thrifter
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. (Optional) Install Playwright for Facebook Marketplace

```bash
playwright install chromium
```

### 3. (Optional) Configure API keys

```bash
cp .env.example .env
```

Edit `.env` to add any keys you have. **The app works without any keys** — it will scrape eBay directly.

| Key | Required? | What it enables |
|-----|-----------|----------------|
| `EBAY_APP_ID` + `EBAY_CERT_ID` | No | Official eBay API (higher rate limits) |
| `OPENAI_API_KEY` | No | Photo analysis + AI listing generation |
| `EBAY_REDIRECT_URI` | No | Auto-publish listings to eBay (seller OAuth) |

### 4. Run

```bash
python run.py
```

Open **http://localhost:8080**

## Settings

The app has a **Settings** page where you can:
- Toggle eBay data source: **Auto** / **API Only** / **Scrape Only**
- Connect/disconnect Facebook Marketplace (opens browser for one-time login)
- View status of all API integrations and network connectivity

## Architecture

```
thrifter/
├── backend/
│   ├── main.py                    # FastAPI server + all routes
│   └── services/
│       ├── ebay_service.py        # eBay API client (Browse + Finding)
│       ├── ebay_scraper.py        # eBay web scraper fallback
│       ├── fb_scraper.py          # Facebook Marketplace (Playwright)
│       ├── marketplace.py         # Poshmark + Mercari + FB orchestrator
│       ├── image_analyzer.py      # OpenAI Vision for photo ID
│       ├── pricing.py             # Pricing, STR, and deal score engine
│       ├── listing_generator.py   # AI listing copywriter
│       ├── inventory.py           # SQLite inventory + watch queries + opportunities
│       ├── deal_scanner.py        # Background deal scanning engine
│       ├── auto_relister.py       # Post-purchase auto-relist pipeline
│       ├── ebay_auth.py           # eBay user OAuth (seller APIs)
│       ├── ebay_seller.py         # eBay Inventory API (publish listings)
│       ├── barcode.py             # UPC lookup
│       └── settings.py            # User preferences (JSON)
├── frontend/
│   └── index.html                 # Single-page app (vanilla HTML/CSS/JS)
├── data/                          # Auto-created at runtime
│   ├── inventory.db               # SQLite database
│   └── settings.json              # User preferences
├── run.py
├── .env.example
└── requirements.txt
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | System status + network check |
| GET | `/api/settings` | Get user settings |
| PUT | `/api/settings` | Update settings (ebay_mode, etc.) |
| POST | `/api/search/text` | Search by text description |
| POST | `/api/search/image` | Search by photo upload |
| POST | `/api/search/barcode` | Search by UPC/barcode |
| POST | `/api/listing/generate` | Generate AI listing copy |
| GET | `/api/inventory` | List inventory items |
| GET | `/api/inventory/dashboard` | P&L dashboard stats |
| POST | `/api/inventory` | Add inventory item |
| PUT | `/api/inventory/{id}` | Update item |
| DELETE | `/api/inventory/{id}` | Delete item |
| GET | `/api/watch` | List watch queries |
| POST | `/api/watch` | Create watch query |
| DELETE | `/api/watch/{id}` | Delete watch query |
| GET | `/api/opportunities` | List found deals |
| POST | `/api/opportunities/{id}/purchase` | Confirm purchase + auto-relist |
| POST | `/api/opportunities/{id}/dismiss` | Dismiss a deal |
| GET | `/api/scanner/status` | Scanner status + stats |
| POST | `/api/scanner/start` | Start background scanner |
| POST | `/api/scanner/stop` | Stop scanner |
| POST | `/api/scanner/scan-now` | Trigger immediate scan |
| GET | `/api/fb/status` | Facebook connection status |
| POST | `/api/fb/connect` | Launch Facebook login browser |
| POST | `/api/fb/disconnect` | Clear saved Facebook session |

## How the Deal Score Works

| Signal | Weight | What it measures |
|--------|--------|-----------------|
| Profit | 40% | ROI potential at recommended buy price |
| Demand | 35% | Sell-through rate — do these actually sell? |
| Confidence | 15% | How much sold data backs the estimate |
| Risk | 10% | Price variance and liquidity penalties |

**Verdicts:** 75+ = HOT DEAL, 55+ = GOOD DEAL, 35+ = OKAY, <35 = PASS

## Deployment Notes

This app is designed to **run locally** for the best experience:
- Web scraping requires a residential IP (cloud IPs get blocked)
- Facebook Marketplace requires Playwright (browser on your machine)
- Your inventory data stays on your machine

For cloud deployment, use eBay API keys instead of scraping and skip Facebook Marketplace.

## License

MIT
