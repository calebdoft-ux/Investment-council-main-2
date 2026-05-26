from __future__ import annotations
import asyncio
from functools import lru_cache
from time import time

from fastapi import APIRouter, HTTPException
import yfinance as yf

router = APIRouter(prefix="/api/quotes", tags=["quotes"])

# Simple in-memory cache: {symbol: (timestamp, data)}
_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 60  # seconds


def _fetch_quote_sync(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info

        price = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)
        market_cap = getattr(info, "market_cap", None)
        high_52 = getattr(info, "year_high", None)
        low_52 = getattr(info, "year_low", None)
        volume = getattr(info, "three_month_average_volume", None)
        day_high = getattr(info, "day_high", None)
        day_low = getattr(info, "day_low", None)

        if price is None:
            return None

        change = (price - prev_close) if prev_close else 0
        chg_pct = (change / prev_close * 100) if prev_close else 0

        # Get P/E from full info (slower, optional)
        pe = None
        try:
            full = ticker.info
            pe = full.get("trailingPE") or full.get("forwardPE")
            name = full.get("longName") or full.get("shortName") or symbol
            exch = full.get("exchange") or full.get("exchangeName") or ""
        except Exception:
            name = symbol
            exch = ""

        return {
            "symbol": symbol.upper(),
            "name": name,
            "exchange": exch,
            "price": round(price, 4),
            "prev_close": round(prev_close, 4) if prev_close else None,
            "change": round(change, 4),
            "change_pct": round(chg_pct, 4),
            "day_high": round(day_high, 4) if day_high else None,
            "day_low": round(day_low, 4) if day_low else None,
            "volume": int(volume) if volume else None,
            "market_cap": int(market_cap) if market_cap else None,
            "pe": round(pe, 2) if pe else None,
            "high_52": round(high_52, 4) if high_52 else None,
            "low_52": round(low_52, 4) if low_52 else None,
        }
    except Exception:
        return None


async def fetch_quote(symbol: str) -> dict | None:
    symbol = symbol.upper()
    now = time()
    if symbol in _cache:
        ts, data = _cache[symbol]
        if now - ts < CACHE_TTL:
            return data

    data = await asyncio.to_thread(_fetch_quote_sync, symbol)
    if data:
        _cache[symbol] = (now, data)
    return data


@router.get("/{symbol}")
async def get_quote(symbol: str):
    data = await fetch_quote(symbol.upper())
    if not data:
        raise HTTPException(status_code=404, detail=f"Could not fetch quote for {symbol.upper()}")
    return data


@router.post("/batch")
async def get_batch_quotes(symbols: list[str]):
    results = await asyncio.gather(*[fetch_quote(s) for s in symbols])
    return {s.upper(): r for s, r in zip(symbols, results)}
