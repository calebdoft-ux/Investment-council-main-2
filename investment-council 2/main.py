import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from db.database import init_db
from routers.chat import router as chat_router
from routers.portfolio import router as portfolio_router
from routers.journal import router as journal_router
from routers.quotes import router as quotes_router
from routers.sessions import router as sessions_router
from config import settings

app = FastAPI(title="Investment Council", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(portfolio_router)
app.include_router(journal_router)
app.include_router(quotes_router)
app.include_router(sessions_router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
