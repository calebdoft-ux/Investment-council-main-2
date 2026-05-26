"""
Chat router — SSE streaming endpoint.

Solo mode:   real per-token streaming via Anthropic streaming API.
Multi mode:  concurrent asyncio tasks; results stream in completion order.
"""

from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

import anthropic
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.council import (
    INVESTORS,
    NEWS_DESK,
    RESEARCHERS,
    Agent,
    get_agent,
    DEBATE_EXTRA,
)
from config import settings
from db.database import get_briefing, get_cash, get_portfolio

router = APIRouter(prefix="/api/chat", tags=["chat"])

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}
WEB_SEARCH_BETA = "web-search-2025-03-05"


# ─── Request model ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    team: str  # 'inv' | 'res' | 'news'
    mode: str  # 'solo' | 'all' | 'pm' | 'debate' | 'brief'
    advisor_id: str | None = None
    debate_members: list[str] = []
    messages: list[dict]  # [{role, content}]


# ─── Portfolio context builder ────────────────────────────────────────────────

async def build_portfolio_context() -> str:
    portfolio = await get_portfolio()
    cash = await get_cash()
    briefing = await get_briefing()

    ctx = ""
    if briefing.strip():
        ctx += f"\n\nINVESTOR PROFILE:\n{briefing.strip()}"

    tickers = list(portfolio.keys())
    if not tickers and not cash:
        return ctx

    total = sum(p["size"] for p in portfolio.values())
    lines = []
    total_pnl = 0.0
    has_pnl = False
    for tk, p in portfolio.items():
        pct = f"{(p['size'] / total * 100):.1f}" if total > 0 else "?"
        line = f"  {tk}: ${p['size']:,.0f} invested @ ${p['entry'] or '?'} entry ({pct}% of book)"
        if p.get("entry") and p.get("price"):
            pnl = (p["size"] / p["entry"]) * p["price"] - p["size"]
            pp = pnl / p["size"] * 100
            total_pnl += pnl
            has_pnl = True
            line += f"  |  current ~${p['price']:.2f},  P&L {'+' if pnl >= 0 else ''}${pnl:.0f} ({'+' if pnl >= 0 else ''}{pp:.1f}%)"
        lines.append(line)

    from agents.council import TODAY
    ctx += f"\n\nPORTFOLIO ({TODAY}):\nTotal invested: ${total:,.0f}  |  Cash: ${cash:,.2f}"
    if has_pnl:
        ctx += f"  |  Unrealized P&L: {'+' if total_pnl >= 0 else ''}${total_pnl:.0f}"
    if lines:
        ctx += "\nPositions:\n" + "\n".join(lines)
    ctx += "\n\nAlways factor in cash availability and existing positions when advising."
    return ctx


# ─── Anthropic client factory ─────────────────────────────────────────────────

def make_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


# ─── SSE helpers ─────────────────────────────────────────────────────────────

def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


# ─── Single-advisor streaming (per-token) ─────────────────────────────────────

async def stream_solo(
    client: anthropic.AsyncAnthropic,
    agent: Agent,
    messages: list[dict],
    pf_ctx: str,
) -> AsyncIterator[str]:
    yield sse({"type": "typing", "advisor": agent.to_dict()})

    full_text = []
    async with client.messages.stream(
        model=settings.MODEL,
        max_tokens=settings.MAX_TOKENS,
        system=agent.system + pf_ctx,
        messages=messages,
        tools=[WEB_SEARCH_TOOL],
        extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
    ) as stream:
        async for text in stream.text_stream:
            full_text.append(text)
            yield sse({"type": "token", "advisor_id": agent.id, "text": text})

    yield sse({
        "type": "advisor_complete",
        "advisor": agent.to_dict(),
        "full_text": "".join(full_text),
    })


# ─── Multi-advisor (concurrent, results in completion order) ──────────────────

async def _run_advisor_task(
    client: anthropic.AsyncAnthropic,
    agent: Agent,
    user_msg: str,
    pf_ctx: str,
    queue: asyncio.Queue,
    system_extra: str = "",
):
    try:
        response = await client.messages.create(
            model=settings.MODEL,
            max_tokens=settings.MAX_TOKENS,
            system=agent.system + system_extra + pf_ctx,
            messages=[{"role": "user", "content": user_msg}],
            tools=[WEB_SEARCH_TOOL],
            extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
        )
        text = "".join(
            b.text for b in response.content if hasattr(b, "text")
        )
        await queue.put(("ok", agent, text))
    except Exception as e:
        await queue.put(("err", agent, str(e)))


async def stream_concurrent(
    client: anthropic.AsyncAnthropic,
    agents: list[Agent],
    user_msg: str,
    pf_ctx: str,
    block_title: str,
    block_color: str,
    block_bg: str,
    system_extra: str = "",
) -> AsyncIterator[str]:
    yield sse({
        "type": "block_start",
        "title": block_title,
        "color": block_color,
        "bg": block_bg,
        "advisor_ids": [a.id for a in agents],
    })
    # show all typing indicators immediately
    for agent in agents:
        yield sse({"type": "typing_in_block", "advisor": agent.to_dict()})

    queue: asyncio.Queue = asyncio.Queue()
    tasks = [
        asyncio.create_task(
            _run_advisor_task(client, a, user_msg, pf_ctx, queue, system_extra)
        )
        for a in agents
    ]

    for _ in agents:
        status, agent, text = await queue.get()
        if status == "ok":
            yield sse({
                "type": "block_entry",
                "advisor": agent.to_dict(),
                "text": text,
            })
        else:
            yield sse({"type": "block_error", "advisor": agent.to_dict(), "message": text})

    await asyncio.gather(*tasks, return_exceptions=True)
    yield sse({"type": "block_end"})


# ─── PM Synthesis mode ────────────────────────────────────────────────────────

async def stream_pm(
    client: anthropic.AsyncAnthropic,
    user_msg: str,
    pf_ctx: str,
) -> AsyncIterator[str]:
    council = [a for a in INVESTORS if a.id != "chair"]
    chair = get_agent("chair")

    # Run council concurrently
    async for event in stream_concurrent(
        client, council, user_msg, pf_ctx,
        "⚡ COUNCIL DELIBERATION", "var(--gold)", "rgba(212,168,67,.12)",
    ):
        yield event

    # Collect council responses from the stream (we need to re-run to get text for chair)
    # In practice: run council again for synthesis, or store results above.
    # We use a separate gather here for the chair's input.
    council_queue: asyncio.Queue = asyncio.Queue()
    await asyncio.gather(*[
        _run_advisor_task(client, a, user_msg, pf_ctx, council_queue)
        for a in council
    ])

    summaries = []
    while not council_queue.empty():
        status, agent, text = council_queue.get_nowait()
        if status == "ok":
            summaries.append(f"{agent.name} ({agent.tag}):\n{text}")

    summary_text = "\n\n---\n\n".join(summaries)
    chair_prompt = (
        f'Question posed to council: "{user_msg}"\n\n'
        f"Council views:\n\n{summary_text}\n\n"
        "Now deliver your PM synthesis."
    )

    yield sse({"type": "synth_start"})
    yield sse({"type": "typing", "advisor": chair.to_dict()})

    full_text = []
    async with client.messages.stream(
        model=settings.MODEL,
        max_tokens=settings.MAX_TOKENS,
        system=chair.system + pf_ctx,
        messages=[{"role": "user", "content": chair_prompt}],
        tools=[WEB_SEARCH_TOOL],
        extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
    ) as stream:
        async for text in stream.text_stream:
            full_text.append(text)
            yield sse({"type": "token", "advisor_id": "chair", "text": text})

    yield sse({
        "type": "synth_complete",
        "advisor": chair.to_dict(),
        "full_text": "".join(full_text),
    })


# ─── Research → Investors brief ───────────────────────────────────────────────

async def stream_research_brief(
    client: anthropic.AsyncAnthropic,
    user_msg: str,
    pf_ctx: str,
) -> AsyncIterator[str]:
    # Research phase
    res_queue: asyncio.Queue = asyncio.Queue()
    async for event in stream_concurrent(
        client, RESEARCHERS, user_msg, pf_ctx,
        "🔬 RESEARCH ANALYSIS", "var(--teal)", "rgba(74,184,196,.12)",
    ):
        yield event

    # Re-collect for investor prompt
    await asyncio.gather(*[
        _run_advisor_task(client, a, user_msg, pf_ctx, res_queue)
        for a in RESEARCHERS
    ])
    res_texts = []
    while not res_queue.empty():
        status, a, text = res_queue.get_nowait()
        if status == "ok":
            res_texts.append(f"{a.name} ({a.tag}):\n{text}")

    brief_text = "\n\n---\n\n".join(res_texts)
    investor_prompt = (
        f'The research team analyzed: "{user_msg}"\n\nFindings:\n\n{brief_text}\n\n'
        "As an investor, react to this briefing. Should we act? Buy, watch, or pass?"
    )

    investors = [a for a in INVESTORS if a.id != "chair"]
    async for event in stream_concurrent(
        client, investors, investor_prompt, pf_ctx,
        "📤 INVESTOR REACTION", "var(--gold)", "rgba(212,168,67,.12)",
    ):
        yield event


# ─── News → All Teams brief ───────────────────────────────────────────────────

async def stream_news_brief(
    client: anthropic.AsyncAnthropic,
    user_msg: str,
    pf_ctx: str,
) -> AsyncIterator[str]:
    # News desk phase
    news_queue: asyncio.Queue = asyncio.Queue()
    async for event in stream_concurrent(
        client, NEWS_DESK, user_msg, pf_ctx,
        "📡 NEWS DESK ANALYSIS", "var(--orange)", "rgba(224,131,74,.12)",
    ):
        yield event

    await asyncio.gather(*[
        _run_advisor_task(client, a, user_msg, pf_ctx, news_queue)
        for a in NEWS_DESK
    ])
    news_texts = []
    while not news_queue.empty():
        status, a, text = news_queue.get_nowait()
        if status == "ok":
            news_texts.append(f"{a.name}:\n{text}")

    news_summary = "\n\n---\n\n".join(news_texts)
    investor_prompt = (
        f"NEWS DESK BRIEFING:\n\n{news_summary}\n\nHeadline: \"{user_msg}\"\n\n"
        "How does this affect our portfolio and investment outlook?"
    )
    researcher_prompt = (
        f"NEWS DESK BRIEFING:\n\n{news_summary}\n\nNews: \"{user_msg}\"\n\n"
        "Does this create research opportunities or change sector outlook?"
    )

    key_investors = [a for a in INVESTORS[:3]]
    async for event in stream_concurrent(
        client, key_investors, investor_prompt, pf_ctx,
        "⚡ INVESTOR REACTION", "var(--gold)", "rgba(212,168,67,.12)",
    ):
        yield event

    key_researchers = [RESEARCHERS[0], RESEARCHERS[3]]
    async for event in stream_concurrent(
        client, key_researchers, researcher_prompt, pf_ctx,
        "🔬 RESEARCH REACTION", "var(--teal)", "rgba(74,184,196,.12)",
    ):
        yield event


# ─── Main router ──────────────────────────────────────────────────────────────

async def generate_stream(request: ChatRequest) -> AsyncIterator[str]:
    client = make_client()
    pf_ctx = await build_portfolio_context()
    user_msg = next(
        (m["content"] for m in reversed(request.messages) if m["role"] == "user"),
        "",
    )

    try:
        if request.team == "inv":
            if request.mode == "solo" and request.advisor_id:
                agent = get_agent(request.advisor_id)
                if agent:
                    async for ev in stream_solo(client, agent, request.messages, pf_ctx):
                        yield ev

            elif request.mode == "all":
                agents = [a for a in INVESTORS if a.id != "chair"]
                async for ev in stream_concurrent(
                    client, agents, user_msg, pf_ctx,
                    "⚡ INVESTMENT COUNCIL", "var(--gold)", "rgba(212,168,67,.12)",
                ):
                    yield ev

            elif request.mode == "pm":
                async for ev in stream_pm(client, user_msg, pf_ctx):
                    yield ev

            elif request.mode == "debate" and request.debate_members:
                agents = [a for a in (get_agent(i) for i in request.debate_members) if a]
                label = " vs ".join(a.name.split()[0].upper() for a in agents)
                async for ev in stream_concurrent(
                    client, agents, user_msg, pf_ctx,
                    f"⚔️ DEBATE — {label}", "var(--purple)", "rgba(155,110,224,.12)",
                    system_extra=DEBATE_EXTRA,
                ):
                    yield ev

        elif request.team == "res":
            if request.mode == "solo" and request.advisor_id:
                agent = get_agent(request.advisor_id)
                if agent:
                    async for ev in stream_solo(client, agent, request.messages, pf_ctx):
                        yield ev
            elif request.mode == "all":
                async for ev in stream_concurrent(
                    client, RESEARCHERS, user_msg, pf_ctx,
                    "🔬 RESEARCH TEAM", "var(--teal)", "rgba(74,184,196,.12)",
                ):
                    yield ev
            elif request.mode == "brief":
                async for ev in stream_research_brief(client, user_msg, pf_ctx):
                    yield ev

        elif request.team == "news":
            if request.mode == "solo" and request.advisor_id:
                agent = get_agent(request.advisor_id)
                if agent:
                    async for ev in stream_solo(client, agent, request.messages, pf_ctx):
                        yield ev
            elif request.mode == "all":
                async for ev in stream_concurrent(
                    client, NEWS_DESK, user_msg, pf_ctx,
                    "📡 NEWS DESK", "var(--orange)", "rgba(224,131,74,.12)",
                ):
                    yield ev
            elif request.mode == "brief":
                async for ev in stream_news_brief(client, user_msg, pf_ctx):
                    yield ev

    except Exception as e:
        yield sse({"type": "error", "message": str(e)})

    yield sse({"type": "done"})


@router.post("")
async def chat(request: ChatRequest):
    return StreamingResponse(
        generate_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
