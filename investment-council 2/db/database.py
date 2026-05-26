from __future__ import annotations
import json
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite

from config import settings

DB_PATH = settings.DATABASE_PATH

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS portfolio (
    ticker   TEXT PRIMARY KEY,
    size     REAL NOT NULL,
    entry    REAL,
    price    REAL,
    name     TEXT,
    added    TEXT
);

CREATE TABLE IF NOT EXISTS journal (
    id         TEXT PRIMARY KEY,
    ticker     TEXT NOT NULL,
    type       TEXT NOT NULL,
    price      REAL,
    size       REAL,
    date       TEXT,
    thesis     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    preview    TEXT,
    team       TEXT,
    date       TEXT,
    messages   TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES)
        await db.commit()


# ─── Portfolio ────────────────────────────────────────────────────────────────

async def get_portfolio() -> dict:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM portfolio")
        rows = await cursor.fetchall()
        return {r["ticker"]: dict(r) for r in rows}


async def upsert_position(ticker: str, size: float, entry: float | None,
                          price: float | None, name: str | None):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO portfolio (ticker, size, entry, price, name, added)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 size=excluded.size, entry=excluded.entry,
                 price=excluded.price, name=excluded.name""",
            (ticker, size, entry, price, name, datetime.now().isoformat()),
        )
        await db.commit()


async def update_position_price(ticker: str, price: float):
    async with get_db() as db:
        await db.execute("UPDATE portfolio SET price=? WHERE ticker=?", (price, ticker))
        await db.commit()


async def delete_position(ticker: str):
    async with get_db() as db:
        await db.execute("DELETE FROM portfolio WHERE ticker=?", (ticker,))
        await db.commit()


# ─── Journal ─────────────────────────────────────────────────────────────────

async def get_journal() -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM journal ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_journal_entry(entry: dict):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal (id, ticker, type, price, size, date, thesis, created_at)
               VALUES (:id, :ticker, :type, :price, :size, :date, :thesis, :created_at)""",
            entry,
        )
        await db.commit()


async def delete_journal_entry(entry_id: str):
    async with get_db() as db:
        await db.execute("DELETE FROM journal WHERE id=?", (entry_id,))
        await db.commit()


# ─── Sessions ────────────────────────────────────────────────────────────────

async def get_sessions() -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM sessions ORDER BY date DESC LIMIT 50")
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["messages"] = json.loads(d["messages"] or "[]")
            result.append(d)
        return result


async def upsert_session(session_id: str, preview: str, team: str, messages: list):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO sessions (id, preview, team, date, messages)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 preview=excluded.preview, team=excluded.team,
                 date=excluded.date, messages=excluded.messages""",
            (session_id, preview, team, datetime.now().isoformat(), json.dumps(messages)),
        )
        await db.commit()


async def delete_session(session_id: str):
    async with get_db() as db:
        await db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        await db.commit()


# ─── Settings ─────────────────────────────────────────────────────────────────

async def get_setting(key: str) -> str | None:
    async with get_db() as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else None


async def set_setting(key: str, value: str):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def get_cash() -> float:
    val = await get_setting("cash")
    return float(val) if val else 0.0


async def set_cash(amount: float):
    await set_setting("cash", str(amount))


async def get_briefing() -> str:
    return (await get_setting("briefing")) or ""


async def set_briefing(text: str):
    await set_setting("briefing", text)
