from __future__ import annotations
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from db.database import add_journal_entry, delete_journal_entry, get_journal

router = APIRouter(prefix="/api/journal", tags=["journal"])


class JournalEntry(BaseModel):
    ticker: str
    type: str  # buy | sell | watch
    price: float | None = None
    size: float | None = None
    date: str | None = None
    thesis: str = ""


@router.get("")
async def list_journal():
    return await get_journal()


@router.post("")
async def create_entry(entry: JournalEntry):
    now = datetime.now()
    record = {
        "id": str(int(now.timestamp() * 1000)),
        "ticker": entry.ticker.upper(),
        "type": entry.type,
        "price": entry.price,
        "size": entry.size,
        "date": entry.date or now.strftime("%-m/%-d/%Y"),
        "thesis": entry.thesis,
        "created_at": now.isoformat(),
    }
    await add_journal_entry(record)
    return record


@router.delete("/{entry_id}")
async def delete_entry(entry_id: str):
    await delete_journal_entry(entry_id)
    return {"ok": True}
