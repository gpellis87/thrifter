import aiosqlite
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "inventory.db"


async def _get_db() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            brand TEXT,
            category TEXT,
            purchase_price REAL,
            purchase_date TEXT,
            purchase_location TEXT,
            storage_location TEXT,
            status TEXT DEFAULT 'unlisted',
            listed_price REAL,
            listed_date TEXT,
            listed_platform TEXT,
            sold_price REAL,
            sold_date TEXT,
            sold_platform TEXT,
            shipping_cost REAL,
            platform_fees REAL,
            image_url TEXT,
            notes TEXT,
            search_query TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS watch_queries (
            id TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            category TEXT,
            max_buy_price REAL,
            min_profit REAL DEFAULT 5.0,
            min_deal_score INTEGER DEFAULT 50,
            enabled INTEGER DEFAULT 1,
            last_scanned TEXT,
            scan_count INTEGER DEFAULT 0,
            opportunities_found INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            watch_query_id TEXT,
            ebay_item_id TEXT,
            title TEXT NOT NULL,
            current_price REAL,
            estimated_sell_price REAL,
            estimated_profit REAL,
            deal_score INTEGER,
            deal_verdict TEXT,
            item_url TEXT,
            image_url TEXT,
            condition TEXT,
            seller TEXT,
            found_at TEXT,
            status TEXT DEFAULT 'new',
            inventory_item_id TEXT,
            FOREIGN KEY (watch_query_id) REFERENCES watch_queries(id)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status)"
    )
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_opp_ebay_id ON opportunities(ebay_item_id)"
    )
    await db.commit()
    return db


async def add_item(data: dict) -> dict:
    db = await _get_db()
    try:
        item_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO items
               (id, title, brand, category, purchase_price, purchase_date,
                purchase_location, storage_location, status, listed_price,
                listed_platform, image_url, notes, search_query, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                data.get("title", ""),
                data.get("brand"),
                data.get("category"),
                data.get("purchase_price"),
                data.get("purchase_date", now[:10]),
                data.get("purchase_location"),
                data.get("storage_location"),
                data.get("status", "unlisted"),
                data.get("listed_price"),
                data.get("listed_platform"),
                data.get("image_url"),
                data.get("notes"),
                data.get("search_query"),
                now,
                now,
            ),
        )
        await db.commit()
        return await get_item(item_id)
    finally:
        await db.close()


async def update_item(item_id: str, data: dict) -> dict | None:
    db = await _get_db()
    try:
        allowed = {
            "title", "brand", "category", "purchase_price", "purchase_date",
            "purchase_location", "storage_location", "status", "listed_price",
            "listed_date", "listed_platform", "sold_price", "sold_date",
            "sold_platform", "shipping_cost", "platform_fees", "image_url",
            "notes", "search_query",
        }
        fields = {k: v for k, v in data.items() if k in allowed}
        if not fields:
            return await get_item(item_id)

        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [item_id]

        await db.execute(f"UPDATE items SET {set_clause} WHERE id = ?", values)
        await db.commit()
        return await get_item(item_id)
    finally:
        await db.close()


async def delete_item(item_id: str) -> bool:
    db = await _get_db()
    try:
        cursor = await db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_item(item_id: str) -> dict | None:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT * FROM items WHERE id = ?", (item_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_items(
    status: str | None = None,
    sort_by: str = "created_at",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    db = await _get_db()
    try:
        safe_sort = sort_by if sort_by in {
            "created_at", "purchase_date", "purchase_price",
            "listed_price", "sold_price", "sold_date", "title", "status",
        } else "created_at"
        safe_order = "ASC" if order.lower() == "asc" else "DESC"

        query = "SELECT * FROM items"
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += f" ORDER BY {safe_sort} {safe_order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_dashboard_stats() -> dict:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM items")
        total = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM items WHERE status = 'unlisted'")
        unlisted = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM items WHERE status = 'listed'")
        listed = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM items WHERE status = 'sold'")
        sold = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT COALESCE(SUM(purchase_price), 0) as total FROM items"
        )
        total_invested = (await cursor.fetchone())["total"]

        cursor = await db.execute(
            "SELECT COALESCE(SUM(sold_price), 0) as total FROM items WHERE status = 'sold'"
        )
        total_revenue = (await cursor.fetchone())["total"]

        cursor = await db.execute(
            "SELECT COALESCE(SUM(purchase_price), 0) as total FROM items WHERE status = 'sold'"
        )
        cost_of_sold = (await cursor.fetchone())["total"]

        cursor = await db.execute(
            "SELECT COALESCE(SUM(shipping_cost), 0) + COALESCE(SUM(platform_fees), 0) as total FROM items WHERE status = 'sold'"
        )
        total_fees = (await cursor.fetchone())["total"]

        total_profit = total_revenue - cost_of_sold - total_fees

        cursor = await db.execute(
            "SELECT COALESCE(SUM(purchase_price), 0) as total FROM items WHERE status IN ('unlisted', 'listed')"
        )
        unsold_investment = (await cursor.fetchone())["total"]

        return {
            "total_items": total,
            "unlisted": unlisted,
            "listed": listed,
            "sold": sold,
            "total_invested": round(total_invested, 2),
            "total_revenue": round(total_revenue, 2),
            "total_profit": round(total_profit, 2),
            "total_fees": round(total_fees, 2),
            "unsold_investment": round(unsold_investment, 2),
            "roi_percent": round((total_profit / cost_of_sold) * 100, 1) if cost_of_sold > 0 else 0,
        }
    finally:
        await db.close()


# ── Watch Queries ────────────────────────────────────────────────

async def add_watch_query(data: dict) -> dict:
    db = await _get_db()
    try:
        wq_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO watch_queries
               (id, query, category, max_buy_price, min_profit, min_deal_score, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                wq_id,
                data["query"],
                data.get("category"),
                data.get("max_buy_price"),
                data.get("min_profit", 5.0),
                data.get("min_deal_score", 50),
                1 if data.get("enabled", True) else 0,
                now,
            ),
        )
        await db.commit()
        return await get_watch_query(wq_id)
    finally:
        await db.close()


async def get_watch_query(wq_id: str) -> dict | None:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT * FROM watch_queries WHERE id = ?", (wq_id,))
        row = await cursor.fetchone()
        return _wq_to_dict(row) if row else None
    finally:
        await db.close()


def _wq_to_dict(row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled", 0))
    return d


async def list_watch_queries(enabled_only: bool = False) -> list[dict]:
    db = await _get_db()
    try:
        q = "SELECT * FROM watch_queries"
        params = []
        if enabled_only:
            q += " WHERE enabled = 1"
        q += " ORDER BY created_at DESC"
        cursor = await db.execute(q, params)
        return [_wq_to_dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def update_watch_query(wq_id: str, data: dict) -> dict | None:
    db = await _get_db()
    try:
        allowed = {"query", "category", "max_buy_price", "min_profit", "min_deal_score", "enabled"}
        fields = {}
        for k, v in data.items():
            if k in allowed:
                fields[k] = (1 if v else 0) if k == "enabled" else v
        if not fields:
            return await get_watch_query(wq_id)
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [wq_id]
        await db.execute(f"UPDATE watch_queries SET {set_clause} WHERE id = ?", values)
        await db.commit()
        return await get_watch_query(wq_id)
    finally:
        await db.close()


async def delete_watch_query(wq_id: str) -> bool:
    db = await _get_db()
    try:
        cursor = await db.execute("DELETE FROM watch_queries WHERE id = ?", (wq_id,))
        await db.execute("DELETE FROM opportunities WHERE watch_query_id = ?", (wq_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def mark_watch_scanned(wq_id: str, new_opps: int) -> None:
    db = await _get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE watch_queries SET last_scanned = ?, scan_count = scan_count + 1, "
            "opportunities_found = opportunities_found + ? WHERE id = ?",
            (now, new_opps, wq_id),
        )
        await db.commit()
    finally:
        await db.close()


# ── Opportunities ────────────────────────────────────────────────

async def add_opportunity(data: dict) -> dict | None:
    """Insert an opportunity, ignoring duplicates by ebay_item_id."""
    db = await _get_db()
    try:
        opp_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT OR IGNORE INTO opportunities
               (id, watch_query_id, ebay_item_id, title, current_price,
                estimated_sell_price, estimated_profit, deal_score, deal_verdict,
                item_url, image_url, condition, seller, found_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
            (
                opp_id,
                data.get("watch_query_id"),
                data.get("ebay_item_id"),
                data.get("title", ""),
                data.get("current_price"),
                data.get("estimated_sell_price"),
                data.get("estimated_profit"),
                data.get("deal_score"),
                data.get("deal_verdict"),
                data.get("item_url"),
                data.get("image_url"),
                data.get("condition"),
                data.get("seller"),
                now,
            ),
        )
        await db.commit()
        if db.total_changes > 0:
            return await get_opportunity(opp_id)
        return None
    finally:
        await db.close()


async def get_opportunity(opp_id: str) -> dict | None:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_opportunities(
    status: str | None = None,
    watch_query_id: str | None = None,
    min_score: int | None = None,
    min_profit: float | None = None,
    sort_by: str = "found_at",
    order: str = "desc",
    limit: int = 100,
) -> list[dict]:
    db = await _get_db()
    try:
        q = "SELECT * FROM opportunities WHERE 1=1"
        params: list = []
        if status:
            q += " AND status = ?"
            params.append(status)
        else:
            q += " AND status IN ('new', 'viewed')"
        if watch_query_id:
            q += " AND watch_query_id = ?"
            params.append(watch_query_id)
        if min_score is not None:
            q += " AND deal_score >= ?"
            params.append(min_score)
        if min_profit is not None:
            q += " AND estimated_profit >= ?"
            params.append(min_profit)

        safe_sort = sort_by if sort_by in {"found_at", "deal_score", "estimated_profit", "current_price"} else "found_at"
        safe_order = "ASC" if order.lower() == "asc" else "DESC"
        q += f" ORDER BY {safe_sort} {safe_order} LIMIT ?"
        params.append(limit)

        cursor = await db.execute(q, params)
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def update_opportunity_status(opp_id: str, status: str, inventory_item_id: str | None = None) -> dict | None:
    db = await _get_db()
    try:
        if inventory_item_id:
            await db.execute(
                "UPDATE opportunities SET status = ?, inventory_item_id = ? WHERE id = ?",
                (status, inventory_item_id, opp_id),
            )
        else:
            await db.execute(
                "UPDATE opportunities SET status = ? WHERE id = ?", (status, opp_id)
            )
        await db.commit()
        return await get_opportunity(opp_id)
    finally:
        await db.close()


async def get_scanner_stats() -> dict:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM watch_queries WHERE enabled = 1")
        active_watches = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM opportunities WHERE status = 'new'")
        new_opps = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM opportunities WHERE status = 'purchased'")
        purchased = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM opportunities")
        total_opps = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT MIN(last_scanned) as oldest, MAX(last_scanned) as newest FROM watch_queries WHERE enabled = 1"
        )
        row = await cursor.fetchone()

        return {
            "active_watches": active_watches,
            "new_opportunities": new_opps,
            "purchased": purchased,
            "total_opportunities_found": total_opps,
            "last_scan": row["newest"] if row else None,
        }
    finally:
        await db.close()
