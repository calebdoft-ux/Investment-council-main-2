from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db.database import (
    delete_position,
    get_cash,
    get_portfolio,
    set_cash,
    upsert_position,
    update_position_price,
    get_briefing,
    set_briefing,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class Position(BaseModel):
    ticker: str
    size: float
    entry: float | None = None
    price: float | None = None
    name: str | None = None


class CashUpdate(BaseModel):
    amount: float


class BriefingUpdate(BaseModel):
    text: str


@router.get("")
async def list_portfolio():
    portfolio = await get_portfolio()
    cash = await get_cash()
    briefing = await get_briefing()
    return {"portfolio": portfolio, "cash": cash, "briefing": briefing}


@router.put("/position")
async def add_or_update_position(pos: Position):
    await upsert_position(pos.ticker.upper(), pos.size, pos.entry, pos.price, pos.name)
    return {"ok": True}


@router.patch("/position/{ticker}/price")
async def refresh_price(ticker: str, price: float):
    await update_position_price(ticker.upper(), price)
    return {"ok": True}


@router.delete("/position/{ticker}")
async def remove_position(ticker: str):
    await delete_position(ticker.upper())
    return {"ok": True}


@router.put("/cash")
async def update_cash(body: CashUpdate):
    if body.amount < 0:
        raise HTTPException(status_code=400, detail="Cash cannot be negative")
    await set_cash(body.amount)
    return {"ok": True, "cash": body.amount}


@router.put("/briefing")
async def update_briefing(body: BriefingUpdate):
    await set_briefing(body.text)
    return {"ok": True}
