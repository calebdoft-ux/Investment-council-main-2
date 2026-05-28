from fastapi import APIRouter
from pydantic import BaseModel

from db.database import delete_session, get_sessions, upsert_session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionSave(BaseModel):
    id: str
    preview: str
    team: str
    messages: list[dict]


@router.get("")
async def list_sessions():
    return await get_sessions()


@router.put("")
async def save_session(body: SessionSave):
    await upsert_session(body.id, body.preview, body.team, body.messages)
    return {"ok": True}


@router.delete("/{session_id}")
async def remove_session(session_id: str):
    await delete_session(session_id)
    return {"ok": True}
