"""
Investment Council — one-shot setup script.
Run this once:  python setup.py
It creates every file, then prints how to start the server.
"""
import os, sys, textwrap

def w(path, content):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))
    print(f"  created  {path}")

# ── config.py ─────────────────────────────────────────────────────────────────
w("config.py", """
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
        ANTHROPIC_API_KEY: str
        MODEL: str = "claude-sonnet-4-6"
        DATABASE_PATH: str = "investment_council.db"
        MAX_TOKENS: int = 1500
        HOST: str = "0.0.0.0"
        PORT: int = 8000

    settings = Settings()
""")

# ── main.py ───────────────────────────────────────────────────────────────────
w("main.py", """
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
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
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
""")

# ── agents/__init__.py ────────────────────────────────────────────────────────
w("agents/__init__.py", "")

# ── agents/council.py ─────────────────────────────────────────────────────────
w("agents/council.py", r'''
    from dataclasses import dataclass
    from datetime import date
    from typing import Literal

    TODAY = date.today().strftime("%B %d, %Y")
    DATE_CONTEXT = (
        f"Today is {TODAY}. You have live web search access via tools. "
        "Always reference current conditions. When you cite data (prices, rates, earnings, news), "
        "make clear it reflects current conditions. Never say 'as of my training' — reason from "
        "current market context and flag uncertainty explicitly."
    )
    DEBATE_EXTRA = (
        "\n\nYou are in a structured debate. Take one clear position and argue it with conviction. "
        "Do not hedge."
    )

    @dataclass
    class Agent:
        id: str
        name: str
        role: str
        emoji: str
        color: str
        bg: str
        tag: str
        qtype: Literal["QUANT", "QUAL", "SYNTH"]
        system: str

        def to_dict(self) -> dict:
            return {k: getattr(self, k) for k in ("id","name","role","emoji","color","bg","tag","qtype")}

    INVESTORS: list[Agent] = [
        Agent(id="banker", name="David Kessler", role="Investment Banker", emoji="🎩",
              color="#d4a843", bg="rgba(212,168,67,.15)", tag="IB", qtype="QUANT",
              system=(f"{DATE_CONTEXT}\n\nYou are David Kessler, former M&A banker at bulge bracket firms, "
                      "25 years on Wall Street. You lead every response with valuation: P/E vs sector, "
                      "EV/EBITDA, FCF yield, revenue growth, net margins. Give specific numbers with context — "
                      "'P/E of 23x vs sector at 18x is a 28% premium, justified only if growth exceeds X.' "
                      "Independent view only. Use ALL CAPS section headers.")),
        Agent(id="macro", name="Rachel Stern", role="Macro Strategist", emoji="📊",
              color="#5294e0", bg="rgba(82,148,224,.15)", tag="MACRO", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are Rachel Stern, former Fed researcher turned macro hedge fund PM. "
                      "You interpret the current rate environment, credit spreads, dollar trend, inflation trajectory, "
                      "and how they affect asset pricing. Draw historical parallels but anchor firmly to current conditions. "
                      "Independent view. ALL CAPS section headers.")),
        Agent(id="geo", name="Tom Callahan", role="Geopolitical Analyst", emoji="🌍",
              color="#9b6ee0", bg="rgba(155,110,224,.15)", tag="GEO", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are Tom Callahan, former CIA analyst now running geopolitical risk consulting. "
                      "You translate current geopolitical events — tariffs, trade wars, Middle East, China-Taiwan, "
                      "EU politics — into specific market implications: commodity flows, supply chains, capital movement, "
                      "sector effects. Pragmatic. No ideology. ALL CAPS headers.")),
        Agent(id="quant", name="Arjun Mehta", role="Quantitative Analyst", emoji="⚡",
              color="#52c48a", bg="rgba(82,196,138,.15)", tag="QUANT", qtype="QUANT",
              system=(f"{DATE_CONTEXT}\n\nYou are Arjun Mehta, systematic multi-strat fund PM. "
                      "You think in probabilities and statistics: vol regimes, beta, correlation, Sharpe, "
                      "max drawdown, factor exposure, z-scores. Give concrete numbers and ranges. "
                      "Distinguish signal from noise statistically. Independent analysis. ALL CAPS headers.")),
        Agent(id="growth", name="Sarah Park", role="Tech & Growth Investor", emoji="🚀",
              color="#e05252", bg="rgba(224,82,82,.15)", tag="GROWTH", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are Sarah Park, 14 years in tech and growth investing. "
                      "You think 3-5 years ahead: which companies win the AI/energy/biotech transition, "
                      "who actually captures value vs who just rides narrative. Opinionated on current tech landscape. "
                      "Direct. ALL CAPS headers.")),
        Agent(id="risk", name="Mark Osei", role="Risk Manager", emoji="🛡️",
              color="#e0834a", bg="rgba(224,131,74,.15)", tag="RISK", qtype="QUANT",
              system=(f"{DATE_CONTEXT}\n\nYou are Mark Osei, institutional risk management. "
                      "For any question: concentration risk, correlation breakdown scenarios, max drawdown under stress, "
                      "position sizing math. Give numbers — what % of book is at risk, what a -20% move does in dollar terms, "
                      "what proper sizing looks like. ALL CAPS headers.")),
        Agent(id="chair", name="The Chair", role="Portfolio Manager", emoji="🏦",
              color="#52c48a", bg="rgba(82,196,138,.12)", tag="PM", qtype="SYNTH",
              system=(f"{DATE_CONTEXT}\n\nYou are the portfolio committee chair as of {TODAY}. "
                      "You synthesize views from David (IB/valuation), Rachel (macro), Tom (geopolitics), "
                      "Arjun (quant), Sarah (growth/tech), and Mark (risk). Be honest about disagreements. "
                      "Respond in exactly THREE sections with ALL CAPS headers: "
                      "WHERE THEY AGREE, WHERE THEY DIFFER, VERDICT. "
                      "Verdict must be specific, actionable, and include sizing/timing guidance.")),
    ]

    RESEARCHERS: list[Agent] = [
        Agent(id="fund", name="Nina Kovac", role="Fundamental Analyst", emoji="🔬",
              color="#4ab8c4", bg="rgba(74,184,196,.15)", tag="FUND", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are Nina Kovac, fundamental equity research. "
                      "You analyze business quality: moats, unit economics, management, capital allocation, balance sheet. "
                      "You find companies trading below intrinsic value. When recommending a stock: thesis in 2-3 sentences, "
                      "key risks, rough intrinsic value range, and a clear BUY/WATCH/AVOID. ALL CAPS section headers.")),
        Agent(id="tech", name="Rex Huang", role="Technical Analyst", emoji="📈",
              color="#d4a843", bg="rgba(212,168,67,.15)", tag="TECH", qtype="QUANT",
              system=(f"{DATE_CONTEXT}\n\nYou are Rex Huang, technical analysis and market microstructure. "
                      "You identify momentum setups, support/resistance, breakout patterns, volume confirmation. "
                      "Give specific price levels: current support, resistance, stop, and entry target. "
                      "Assign probabilities: '65% probability of breakout above X if volume holds.' ALL CAPS headers.")),
        Agent(id="screen", name="Zoe Chan", role="Quantitative Screener", emoji="🧮",
              color="#52c48a", bg="rgba(82,196,138,.15)", tag="SCREEN", qtype="QUANT",
              system=(f"{DATE_CONTEXT}\n\nYou are Zoe Chan, quantitative factor screening. "
                      "You look for statistical anomalies: value-growth combinations, momentum-reversion traps, "
                      "unusual volume patterns. Screen across sectors and market caps. "
                      "Give specific screening criteria and factor scores. ALL CAPS headers.")),
        Agent(id="theme", name="Dev Patel", role="Thematic Researcher", emoji="🌐",
              color="#9b6ee0", bg="rgba(155,110,224,.15)", tag="THEME", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are Dev Patel, thematic and structural research. "
                      "You identify multi-year trends before they become consensus: AI infrastructure buildout, "
                      "energy transition, reshoring, demographic shifts, emerging market realignment. "
                      "You find specific names and sectors positioned for each theme. ALL CAPS headers per theme.")),
        Agent(id="contra", name="Cass Rivera", role="Contrarian Analyst", emoji="🎯",
              color="#e05252", bg="rgba(224,82,82,.15)", tag="CONTRA", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are Cass Rivera, contrarian analyst. "
                      "You find what the market is systematically wrong about: hated stocks that are actually fine, "
                      "overcrowded longs ready to unwind, or ignored sectors. You need a specific reason why consensus "
                      "is mispriced — not just 'everyone else is wrong.' Provocative but rigorous. ALL CAPS headers.")),
    ]

    NEWS_DESK: list[Agent] = [
        Agent(id="mkts", name="Elena Marsh", role="Markets Reporter", emoji="📰",
              color="#e0834a", bg="rgba(224,131,74,.15)", tag="MKTS", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are Elena Marsh, financial markets reporter. "
                      "When given a headline or news item: what happened, immediate market reaction, "
                      "which sectors and assets are most affected, and what to watch over the next 48 hours. "
                      "Write like a sharp markets journalist — fast, specific, no fluff. ALL CAPS section headers.")),
        Agent(id="policy", name="James Wei", role="Macro & Policy Reporter", emoji="🏛️",
              color="#5294e0", bg="rgba(82,148,224,.15)", tag="POLICY", qtype="QUAL",
              system=(f"{DATE_CONTEXT}\n\nYou are James Wei, macro and policy reporter. "
                      "You cover the Fed, Treasury, global central banks, government fiscal policy, "
                      "and geopolitical developments. When given a headline, explain the policy context, "
                      "what policymakers are signaling, and the second and third-order market effects. ALL CAPS headers.")),
        Agent(id="corp", name="Priya Das", role="Corporate Reporter", emoji="🏢",
              color="#52c48a", bg="rgba(82,196,138,.15)", tag="CORP", qtype="QUANT",
              system=(f"{DATE_CONTEXT}\n\nYou are Priya Das, corporate reporter. "
                      "You cover earnings, M&A, management changes, and company-specific news. "
                      "When given a headline: earnings vs expectations, implied valuation impact, "
                      "competitive read-across to other companies, and a clear buy/sell/hold signal with reasoning. "
                      "ALL CAPS headers.")),
    ]

    ALL_AGENTS: dict[str, Agent] = {a.id: a for a in [*INVESTORS, *RESEARCHERS, *NEWS_DESK]}

    def get_agent(agent_id: str) -> Agent | None:
        return ALL_AGENTS.get(agent_id)
''')

# ── db/__init__.py ────────────────────────────────────────────────────────────
w("db/__init__.py", "")

# ── db/database.py ────────────────────────────────────────────────────────────
w("db/database.py", '''
    import json
    from contextlib import asynccontextmanager
    from datetime import datetime
    import aiosqlite
    from config import settings

    DB_PATH = settings.DATABASE_PATH

    CREATE_TABLES = """
    CREATE TABLE IF NOT EXISTS portfolio (
        ticker TEXT PRIMARY KEY, size REAL NOT NULL, entry REAL, price REAL, name TEXT, added TEXT
    );
    CREATE TABLE IF NOT EXISTS journal (
        id TEXT PRIMARY KEY, ticker TEXT NOT NULL, type TEXT NOT NULL,
        price REAL, size REAL, date TEXT, thesis TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, preview TEXT, team TEXT, date TEXT, messages TEXT
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
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

    async def get_portfolio() -> dict:
        async with get_db() as db:
            cur = await db.execute("SELECT * FROM portfolio")
            return {r["ticker"]: dict(r) for r in await cur.fetchall()}

    async def upsert_position(ticker, size, entry, price, name):
        async with get_db() as db:
            await db.execute(
                """INSERT INTO portfolio (ticker,size,entry,price,name,added) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET size=excluded.size,entry=excluded.entry,
                   price=excluded.price,name=excluded.name""",
                (ticker, size, entry, price, name, datetime.now().isoformat()))
            await db.commit()

    async def update_position_price(ticker, price):
        async with get_db() as db:
            await db.execute("UPDATE portfolio SET price=? WHERE ticker=?", (price, ticker))
            await db.commit()

    async def delete_position(ticker):
        async with get_db() as db:
            await db.execute("DELETE FROM portfolio WHERE ticker=?", (ticker,))
            await db.commit()

    async def get_journal() -> list[dict]:
        async with get_db() as db:
            cur = await db.execute("SELECT * FROM journal ORDER BY created_at DESC")
            return [dict(r) for r in await cur.fetchall()]

    async def add_journal_entry(entry: dict):
        async with get_db() as db:
            await db.execute(
                "INSERT INTO journal (id,ticker,type,price,size,date,thesis,created_at) "
                "VALUES (:id,:ticker,:type,:price,:size,:date,:thesis,:created_at)", entry)
            await db.commit()

    async def delete_journal_entry(entry_id: str):
        async with get_db() as db:
            await db.execute("DELETE FROM journal WHERE id=?", (entry_id,))
            await db.commit()

    async def get_sessions() -> list[dict]:
        async with get_db() as db:
            cur = await db.execute("SELECT * FROM sessions ORDER BY date DESC LIMIT 50")
            result = []
            for r in await cur.fetchall():
                d = dict(r); d["messages"] = json.loads(d["messages"] or "[]"); result.append(d)
            return result

    async def upsert_session(session_id, preview, team, messages):
        async with get_db() as db:
            await db.execute(
                """INSERT INTO sessions (id,preview,team,date,messages) VALUES (?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET preview=excluded.preview,team=excluded.team,
                   date=excluded.date,messages=excluded.messages""",
                (session_id, preview, team, datetime.now().isoformat(), json.dumps(messages)))
            await db.commit()

    async def delete_session(session_id: str):
        async with get_db() as db:
            await db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            await db.commit()

    async def get_setting(key: str) -> str | None:
        async with get_db() as db:
            cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cur.fetchone()
            return row["value"] if row else None

    async def set_setting(key: str, value: str):
        async with get_db() as db:
            await db.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
            await db.commit()

    async def get_cash() -> float:
        val = await get_setting("cash"); return float(val) if val else 0.0
    async def set_cash(amount: float): await set_setting("cash", str(amount))
    async def get_briefing() -> str: return (await get_setting("briefing")) or ""
    async def set_briefing(text: str): await set_setting("briefing", text)
''')

# ── routers/__init__.py ───────────────────────────────────────────────────────
w("routers/__init__.py", "")

# ── routers/chat.py ───────────────────────────────────────────────────────────
w("routers/chat.py", '''
    import asyncio, json
    from typing import AsyncIterator
    import anthropic
    from fastapi import APIRouter
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel
    from agents.council import INVESTORS, NEWS_DESK, RESEARCHERS, Agent, get_agent, DEBATE_EXTRA
    from config import settings
    from db.database import get_briefing, get_cash, get_portfolio

    router = APIRouter(prefix="/api/chat", tags=["chat"])
    WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}
    WEB_SEARCH_BETA = "web-search-2025-03-05"

    class ChatRequest(BaseModel):
        team: str
        mode: str
        advisor_id: str | None = None
        debate_members: list[str] = []
        messages: list[dict]

    async def build_portfolio_context() -> str:
        portfolio = await get_portfolio()
        cash = await get_cash()
        briefing = await get_briefing()
        ctx = f"\\n\\nINVESTOR PROFILE:\\n{briefing.strip()}" if briefing.strip() else ""
        tickers = list(portfolio.keys())
        if not tickers and not cash:
            return ctx
        total = sum(p["size"] for p in portfolio.values())
        lines, total_pnl, has_pnl = [], 0.0, False
        for tk, p in portfolio.items():
            pct = f"{(p[\'size\']/total*100):.1f}" if total > 0 else "?"
            line = f"  {tk}: ${p[\'size\']:,.0f} @ ${p[\'entry\'] or \'?\'} ({pct}% of book)"
            if p.get("entry") and p.get("price"):
                pnl = (p["size"]/p["entry"])*p["price"]-p["size"]
                total_pnl += pnl; has_pnl = True
                line += f"  |  ~${p[\'price\']:.2f}, P&L {\'+\' if pnl>=0 else \'\'}${pnl:.0f}"
            lines.append(line)
        from agents.council import TODAY
        ctx += f"\\n\\nPORTFOLIO ({TODAY}):\\nInvested: ${total:,.0f}  |  Cash: ${cash:,.2f}"
        if has_pnl: ctx += f"  |  P&L: {\'+\' if total_pnl>=0 else \'\'}${total_pnl:.0f}"
        if lines: ctx += "\\nPositions:\\n" + "\\n".join(lines)
        ctx += "\\n\\nAlways factor in cash and positions when advising."
        return ctx

    def make_client(): return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    def sse(event: dict) -> str: return f"data: {json.dumps(event)}\\n\\n"

    async def stream_solo(client, agent: Agent, messages, pf_ctx) -> AsyncIterator[str]:
        yield sse({"type": "typing", "advisor": agent.to_dict()})
        full_text = []
        async with client.messages.stream(
            model=settings.MODEL, max_tokens=settings.MAX_TOKENS,
            system=agent.system + pf_ctx, messages=messages,
            tools=[WEB_SEARCH_TOOL], extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
        ) as stream:
            async for text in stream.text_stream:
                full_text.append(text)
                yield sse({"type": "token", "advisor_id": agent.id, "text": text})
        yield sse({"type": "advisor_complete", "advisor": agent.to_dict(), "full_text": "".join(full_text)})

    async def _run_advisor_task(client, agent, user_msg, pf_ctx, queue, system_extra=""):
        try:
            response = await client.messages.create(
                model=settings.MODEL, max_tokens=settings.MAX_TOKENS,
                system=agent.system + system_extra + pf_ctx,
                messages=[{"role": "user", "content": user_msg}],
                tools=[WEB_SEARCH_TOOL], extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
            )
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            await queue.put(("ok", agent, text))
        except Exception as e:
            await queue.put(("err", agent, str(e)))

    async def stream_concurrent(client, agents, user_msg, pf_ctx,
                                block_title, block_color, block_bg, system_extra="") -> AsyncIterator[str]:
        yield sse({"type": "block_start", "title": block_title, "color": block_color,
                   "bg": block_bg, "advisor_ids": [a.id for a in agents]})
        for agent in agents:
            yield sse({"type": "typing_in_block", "advisor": agent.to_dict()})
        queue: asyncio.Queue = asyncio.Queue()
        tasks = [asyncio.create_task(_run_advisor_task(client, a, user_msg, pf_ctx, queue, system_extra)) for a in agents]
        for _ in agents:
            status, agent, text = await queue.get()
            if status == "ok":
                yield sse({"type": "block_entry", "advisor": agent.to_dict(), "text": text})
            else:
                yield sse({"type": "block_error", "advisor": agent.to_dict(), "message": text})
        await asyncio.gather(*tasks, return_exceptions=True)
        yield sse({"type": "block_end"})

    async def stream_pm(client, user_msg, pf_ctx) -> AsyncIterator[str]:
        council = [a for a in INVESTORS if a.id != "chair"]
        chair = get_agent("chair")
        async for event in stream_concurrent(client, council, user_msg, pf_ctx,
                                             "⚡ COUNCIL DELIBERATION", "var(--gold)", "rgba(212,168,67,.12)"):
            yield event
        council_queue: asyncio.Queue = asyncio.Queue()
        await asyncio.gather(*[_run_advisor_task(client, a, user_msg, pf_ctx, council_queue) for a in council])
        summaries = []
        while not council_queue.empty():
            status, a, text = council_queue.get_nowait()
            if status == "ok": summaries.append(f"{a.name} ({a.tag}):\\n{text}")
        chair_prompt = (f\'Question: "{user_msg}"\\n\\nCouncil views:\\n\\n\' +
                        "\\n\\n---\\n\\n".join(summaries) + "\\n\\nNow deliver your PM synthesis.")
        yield sse({"type": "synth_start"})
        yield sse({"type": "typing", "advisor": chair.to_dict()})
        full_text = []
        async with client.messages.stream(
            model=settings.MODEL, max_tokens=settings.MAX_TOKENS,
            system=chair.system + pf_ctx, messages=[{"role": "user", "content": chair_prompt}],
            tools=[WEB_SEARCH_TOOL], extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
        ) as stream:
            async for text in stream.text_stream:
                full_text.append(text); yield sse({"type": "token", "advisor_id": "chair", "text": text})
        yield sse({"type": "synth_complete", "advisor": chair.to_dict(), "full_text": "".join(full_text)})

    async def stream_research_brief(client, user_msg, pf_ctx) -> AsyncIterator[str]:
        res_queue: asyncio.Queue = asyncio.Queue()
        async for event in stream_concurrent(client, RESEARCHERS, user_msg, pf_ctx,
                                             "🔬 RESEARCH ANALYSIS", "var(--teal)", "rgba(74,184,196,.12)"):
            yield event
        await asyncio.gather(*[_run_advisor_task(client, a, user_msg, pf_ctx, res_queue) for a in RESEARCHERS])
        res_texts = []
        while not res_queue.empty():
            status, a, text = res_queue.get_nowait()
            if status == "ok": res_texts.append(f"{a.name} ({a.tag}):\\n{text}")
        investor_prompt = (f\'Research analyzed: "{user_msg}"\\n\\n\' + "\\n\\n---\\n\\n".join(res_texts) +
                           "\\n\\nAs an investor, react. Should we act? Buy, watch, or pass?")
        async for event in stream_concurrent(client, [a for a in INVESTORS if a.id != "chair"],
                                             investor_prompt, pf_ctx, "📤 INVESTOR REACTION",
                                             "var(--gold)", "rgba(212,168,67,.12)"):
            yield event

    async def stream_news_brief(client, user_msg, pf_ctx) -> AsyncIterator[str]:
        news_queue: asyncio.Queue = asyncio.Queue()
        async for event in stream_concurrent(client, NEWS_DESK, user_msg, pf_ctx,
                                             "📡 NEWS DESK ANALYSIS", "var(--orange)", "rgba(224,131,74,.12)"):
            yield event
        await asyncio.gather(*[_run_advisor_task(client, a, user_msg, pf_ctx, news_queue) for a in NEWS_DESK])
        news_texts = []
        while not news_queue.empty():
            status, a, text = news_queue.get_nowait()
            if status == "ok": news_texts.append(f"{a.name}:\\n{text}")
        summary = "\\n\\n---\\n\\n".join(news_texts)
        async for event in stream_concurrent(client, INVESTORS[:3],
                                             f"NEWS:\\n{summary}\\n\\nHeadline: \\"{user_msg}\\"\\nHow does this affect our portfolio?",
                                             pf_ctx, "⚡ INVESTOR REACTION", "var(--gold)", "rgba(212,168,67,.12)"):
            yield event
        async for event in stream_concurrent(client, [RESEARCHERS[0], RESEARCHERS[3]],
                                             f"NEWS:\\n{summary}\\n\\nNews: \\"{user_msg}\\"\\nAny research opportunities?",
                                             pf_ctx, "🔬 RESEARCH REACTION", "var(--teal)", "rgba(74,184,196,.12)"):
            yield event

    async def generate_stream(request: ChatRequest) -> AsyncIterator[str]:
        client = make_client()
        pf_ctx = await build_portfolio_context()
        user_msg = next((m["content"] for m in reversed(request.messages) if m["role"] == "user"), "")
        try:
            if request.team == "inv":
                if request.mode == "solo" and request.advisor_id:
                    agent = get_agent(request.advisor_id)
                    if agent:
                        async for ev in stream_solo(client, agent, request.messages, pf_ctx): yield ev
                elif request.mode == "all":
                    async for ev in stream_concurrent(client, [a for a in INVESTORS if a.id != "chair"],
                                                      user_msg, pf_ctx, "⚡ INVESTMENT COUNCIL",
                                                      "var(--gold)", "rgba(212,168,67,.12)"): yield ev
                elif request.mode == "pm":
                    async for ev in stream_pm(client, user_msg, pf_ctx): yield ev
                elif request.mode == "debate" and request.debate_members:
                    agents = [a for a in (get_agent(i) for i in request.debate_members) if a]
                    label = " vs ".join(a.name.split()[0].upper() for a in agents)
                    async for ev in stream_concurrent(client, agents, user_msg, pf_ctx,
                                                      f"⚔️ DEBATE — {label}", "var(--purple)",
                                                      "rgba(155,110,224,.12)", DEBATE_EXTRA): yield ev
            elif request.team == "res":
                if request.mode == "solo" and request.advisor_id:
                    agent = get_agent(request.advisor_id)
                    if agent:
                        async for ev in stream_solo(client, agent, request.messages, pf_ctx): yield ev
                elif request.mode == "all":
                    async for ev in stream_concurrent(client, RESEARCHERS, user_msg, pf_ctx,
                                                      "🔬 RESEARCH TEAM", "var(--teal)", "rgba(74,184,196,.12)"): yield ev
                elif request.mode == "brief":
                    async for ev in stream_research_brief(client, user_msg, pf_ctx): yield ev
            elif request.team == "news":
                if request.mode == "solo" and request.advisor_id:
                    agent = get_agent(request.advisor_id)
                    if agent:
                        async for ev in stream_solo(client, agent, request.messages, pf_ctx): yield ev
                elif request.mode == "all":
                    async for ev in stream_concurrent(client, NEWS_DESK, user_msg, pf_ctx,
                                                      "📡 NEWS DESK", "var(--orange)", "rgba(224,131,74,.12)"): yield ev
                elif request.mode == "brief":
                    async for ev in stream_news_brief(client, user_msg, pf_ctx): yield ev
        except Exception as e:
            yield sse({"type": "error", "message": str(e)})
        yield sse({"type": "done"})

    @router.post("")
    async def chat(request: ChatRequest):
        return StreamingResponse(generate_stream(request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
''')

# ── routers/portfolio.py ──────────────────────────────────────────────────────
w("routers/portfolio.py", '''
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel
    from db.database import (delete_position, get_cash, get_portfolio, set_cash,
                              upsert_position, update_position_price, get_briefing, set_briefing)

    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

    class Position(BaseModel):
        ticker: str; size: float; entry: float|None=None; price: float|None=None; name: str|None=None

    class CashUpdate(BaseModel):
        amount: float

    class BriefingUpdate(BaseModel):
        text: str

    @router.get("")
    async def list_portfolio():
        return {"portfolio": await get_portfolio(), "cash": await get_cash(), "briefing": await get_briefing()}

    @router.put("/position")
    async def add_or_update_position(pos: Position):
        await upsert_position(pos.ticker.upper(), pos.size, pos.entry, pos.price, pos.name)
        return {"ok": True}

    @router.patch("/position/{ticker}/price")
    async def refresh_price(ticker: str, price: float):
        await update_position_price(ticker.upper(), price); return {"ok": True}

    @router.delete("/position/{ticker}")
    async def remove_position(ticker: str):
        await delete_position(ticker.upper()); return {"ok": True}

    @router.put("/cash")
    async def update_cash(body: CashUpdate):
        if body.amount < 0: raise HTTPException(400, "Cash cannot be negative")
        await set_cash(body.amount); return {"ok": True, "cash": body.amount}

    @router.put("/briefing")
    async def update_briefing(body: BriefingUpdate):
        await set_briefing(body.text); return {"ok": True}
''')

# ── routers/journal.py ────────────────────────────────────────────────────────
w("routers/journal.py", '''
    from datetime import datetime
    from fastapi import APIRouter
    from pydantic import BaseModel
    from db.database import add_journal_entry, delete_journal_entry, get_journal

    router = APIRouter(prefix="/api/journal", tags=["journal"])

    class JournalEntry(BaseModel):
        ticker: str; type: str; price: float|None=None; size: float|None=None
        date: str|None=None; thesis: str=""

    @router.get("")
    async def list_journal(): return await get_journal()

    @router.post("")
    async def create_entry(entry: JournalEntry):
        now = datetime.now()
        record = {"id": str(int(now.timestamp()*1000)), "ticker": entry.ticker.upper(),
                  "type": entry.type, "price": entry.price, "size": entry.size,
                  "date": entry.date or now.strftime("%-m/%-d/%Y"),
                  "thesis": entry.thesis, "created_at": now.isoformat()}
        await add_journal_entry(record); return record

    @router.delete("/{entry_id}")
    async def delete_entry(entry_id: str):
        await delete_journal_entry(entry_id); return {"ok": True}
''')

# ── routers/quotes.py ─────────────────────────────────────────────────────────
w("routers/quotes.py", '''
    import asyncio
    from time import time
    import yfinance as yf
    from fastapi import APIRouter, HTTPException

    router = APIRouter(prefix="/api/quotes", tags=["quotes"])
    _cache: dict[str, tuple[float, dict]] = {}
    CACHE_TTL = 60

    def _fetch_quote_sync(symbol: str) -> dict | None:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)
            if price is None: return None
            change = (price - prev) if prev else 0
            chg_pct = (change / prev * 100) if prev else 0
            pe = name = exch = None
            try:
                full = ticker.info
                pe = full.get("trailingPE") or full.get("forwardPE")
                name = full.get("longName") or full.get("shortName") or symbol
                exch = full.get("exchange") or ""
            except Exception:
                name = symbol; exch = ""
            def s(v): return round(v, 4) if v else None
            return {"symbol": symbol.upper(), "name": name, "exchange": exch,
                    "price": round(price,4), "prev_close": s(prev),
                    "change": round(change,4), "change_pct": round(chg_pct,4),
                    "day_high": s(getattr(info,"day_high",None)),
                    "day_low": s(getattr(info,"day_low",None)),
                    "volume": int(getattr(info,"three_month_average_volume",None) or 0) or None,
                    "market_cap": int(getattr(info,"market_cap",None) or 0) or None,
                    "pe": round(pe,2) if pe else None,
                    "high_52": s(getattr(info,"year_high",None)),
                    "low_52": s(getattr(info,"year_low",None))}
        except Exception: return None

    async def fetch_quote(symbol: str) -> dict | None:
        symbol = symbol.upper(); now = time()
        if symbol in _cache:
            ts, data = _cache[symbol]
            if now - ts < CACHE_TTL: return data
        data = await asyncio.to_thread(_fetch_quote_sync, symbol)
        if data: _cache[symbol] = (now, data)
        return data

    @router.get("/{symbol}")
    async def get_quote(symbol: str):
        data = await fetch_quote(symbol.upper())
        if not data: raise HTTPException(404, f"Could not fetch {symbol.upper()}")
        return data

    @router.post("/batch")
    async def get_batch_quotes(symbols: list[str]):
        results = await asyncio.gather(*[fetch_quote(s) for s in symbols])
        return {s.upper(): r for s, r in zip(symbols, results)}
''')

# ── routers/sessions.py ───────────────────────────────────────────────────────
w("routers/sessions.py", '''
    from fastapi import APIRouter
    from pydantic import BaseModel
    from db.database import delete_session, get_sessions, upsert_session

    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    class SessionSave(BaseModel):
        id: str; preview: str; team: str; messages: list[dict]

    @router.get("")
    async def list_sessions(): return await get_sessions()

    @router.put("")
    async def save_session(body: SessionSave):
        await upsert_session(body.id, body.preview, body.team, body.messages); return {"ok": True}

    @router.delete("/{session_id}")
    async def remove_session(session_id: str):
        await delete_session(session_id); return {"ok": True}
''')

# ── requirements.txt ──────────────────────────────────────────────────────────
w("requirements.txt", """
    anthropic>=0.40.0
    fastapi>=0.115.0
    uvicorn[standard]>=0.30.0
    aiosqlite>=0.20.0
    pydantic-settings>=2.0.0
    yfinance>=0.2.40
    httpx>=0.27.0
""")

# ── .env.example ──────────────────────────────────────────────────────────────
w(".env.example", """
    ANTHROPIC_API_KEY=sk-ant-api03-...
    MODEL=claude-sonnet-4-6
    MAX_TOKENS=1500
    HOST=0.0.0.0
    PORT=8000
    DATABASE_PATH=investment_council.db
""")

# ── .gitignore ────────────────────────────────────────────────────────────────
w(".gitignore", """
    .env
    *.db
    __pycache__/
    .venv/
    venv/
    .DS_Store
""")

# ── static/index.html — read from embedded string ─────────────────────────────
os.makedirs("static", exist_ok=True)

# The HTML file is large — we write it from the file on disk if available,
# otherwise print a note.
HTML_SRC = os.path.join(os.path.dirname(__file__), "static", "index.html")
JS_SRC   = os.path.join(os.path.dirname(__file__), "static", "app.js")

if os.path.exists(HTML_SRC):
    print("  exists   static/index.html")
else:
    print("  WARNING  static/index.html not found — download it from the repo.")

if os.path.exists(JS_SRC):
    print("  exists   static/app.js")
else:
    print("  WARNING  static/app.js not found — download it from the repo.")

# ── Done ──────────────────────────────────────────────────────────────────────
print("""
╔══════════════════════════════════════════════╗
║  Investment Council — setup complete!        ║
╠══════════════════════════════════════════════╣
║  Next steps:                                 ║
║  1.  cp .env.example .env                    ║
║  2.  Edit .env — add your ANTHROPIC_API_KEY  ║
║  3.  pip install -r requirements.txt         ║
║  4.  python main.py                          ║
║  5.  Open http://localhost:8000              ║
╚══════════════════════════════════════════════╝
""")
