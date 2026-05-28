"""
Chat router — SSE streaming endpoint.

Solo mode:     real per-token streaming via Anthropic streaming API.
Multi mode:    concurrent asyncio tasks; results stream in completion order.
Round mode:    sequential; each agent sees prior responses before answering.
PM mode:       council concurrent + chair synthesis (single run, no double cost).
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

MAX_HISTORY_PAIRS = 8       # trim solo-mode message history to last N exchanges
PRIOR_RESP_MAX_CHARS = 1200  # cap each prior response in sequential mode to avoid bloat


# ─── Request model ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    team: str                          # 'inv' | 'res' | 'news'
    mode: str                          # 'solo' | 'all' | 'round' | 'pm' | 'debate' | 'brief'
    advisor_id: str | None = None
    debate_members: list[str] = []
    messages: list[dict]               # [{role, content, ...}]
    enabled_agents: list[str] = []     # empty = all enabled


# ─── Helpers ─────────────────────────────────────────────────────────────────

def trim_messages(messages: list[dict]) -> list[dict]:
    """Strip non-standard fields and keep only the last MAX_HISTORY_PAIRS exchanges."""
    clean = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return clean[-(MAX_HISTORY_PAIRS * 2):]


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def make_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


# ─── Portfolio context ────────────────────────────────────────────────────────

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
            line += (
                f"  |  current ~${p['price']:.2f},"
                f"  P&L {'+' if pnl >= 0 else ''}${pnl:.0f}"
                f" ({'+' if pnl >= 0 else ''}{pp:.1f}%)"
            )
        lines.append(line)

    from agents.council import TODAY
    ctx += f"\n\nPORTFOLIO ({TODAY}):\nTotal invested: ${total:,.0f}  |  Cash: ${cash:,.2f}"
    if has_pnl:
        ctx += f"  |  Unrealized P&L: {'+' if total_pnl >= 0 else ''}${total_pnl:.0f}"
    if lines:
        ctx += "\nPositions:\n" + "\n".join(lines)
    ctx += "\n\nAlways factor in cash availability and existing positions when advising."
    return ctx


# ─── Single-advisor streaming (per-token) ─────────────────────────────────────

async def stream_solo(
    client: anthropic.AsyncAnthropic,
    agent: Agent,
    messages: list[dict],
    pf_ctx: str,
) -> AsyncIterator[str]:
    yield sse({"type": "typing", "advisor": agent.to_dict()})

    full_text: list[str] = []
    async with client.messages.stream(
        model=settings.MODEL,
        max_tokens=settings.MAX_TOKENS,
        system=agent.system + pf_ctx,
        messages=trim_messages(messages),
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


# ─── Non-streaming advisor task (for concurrent/PM modes) ────────────────────

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
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        await queue.put(("ok", agent, text))
    except Exception as e:
        await queue.put(("err", agent, str(e)))


# ─── Concurrent multi-advisor (all fire at once, results in completion order) ─

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
            yield sse({"type": "block_entry", "advisor": agent.to_dict(), "text": text})
        else:
            yield sse({"type": "block_error", "advisor": agent.to_dict(), "message": text})

    await asyncio.gather(*tasks, return_exceptions=True)
    yield sse({"type": "block_end"})


# ─── Sequential multi-advisor (each sees prior responses) ────────────────────

async def stream_sequential(
    client: anthropic.AsyncAnthropic,
    agents: list[Agent],
    user_msg: str,
    pf_ctx: str,
    block_title: str,
    block_color: str,
    block_bg: str,
) -> AsyncIterator[str]:
    """Agents respond one by one; each receives prior responses as context."""
    yield sse({
        "type": "block_start",
        "title": block_title,
        "color": block_color,
        "bg": block_bg,
        "advisor_ids": [a.id for a in agents],
    })

    prior: list[str] = []

    for agent in agents:
        yield sse({"type": "typing_in_block", "advisor": agent.to_dict()})

        if prior:
            capped = [
                t if len(t) <= PRIOR_RESP_MAX_CHARS else t[:PRIOR_RESP_MAX_CHARS] + "… [see above]"
                for t in prior
            ]
            context_block = "\n\n".join(capped)
            msg = (
                f"{user_msg}\n\n"
                "---\n"
                f"Your colleagues have already weighed in:\n\n{context_block}\n\n"
                "---\n"
                "Now add YOUR analysis. Engage with what was said — "
                "agree, disagree, or bring a perspective that was missed. Be direct and specific."
            )
        else:
            msg = user_msg

        full_text: list[str] = []
        try:
            yield sse({"type": "seq_token_start", "advisor": agent.to_dict()})
            async with client.messages.stream(
                model=settings.MODEL,
                max_tokens=settings.MAX_TOKENS,
                system=agent.system + pf_ctx,
                messages=[{"role": "user", "content": msg}],
                tools=[WEB_SEARCH_TOOL],
                extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
            ) as stream:
                async for text in stream.text_stream:
                    full_text.append(text)
                    yield sse({"type": "seq_token", "advisor_id": agent.id, "text": text})
        except Exception as e:
            yield sse({"type": "block_error", "advisor": agent.to_dict(), "message": str(e)})
            continue

        complete = "".join(full_text)
        prior.append(f"{agent.name} ({agent.tag}):\n{complete}")
        yield sse({"type": "seq_entry_done", "advisor": agent.to_dict(), "text": complete})

    yield sse({"type": "block_end"})


# ─── PM Synthesis (council once → chair synthesizes) ─────────────────────────

async def stream_pm(
    client: anthropic.AsyncAnthropic,
    user_msg: str,
    pf_ctx: str,
) -> AsyncIterator[str]:
    """Run council once, collect responses, then stream chair synthesis — no double-run."""
    council = [a for a in INVESTORS if a.id != "chair"]
    chair = get_agent("chair")

    yield sse({
        "type": "block_start",
        "title": "⚡ COUNCIL DELIBERATION",
        "color": "var(--gold)",
        "bg": "rgba(212,168,67,.12)",
        "advisor_ids": [a.id for a in council],
    })
    for a in council:
        yield sse({"type": "typing_in_block", "advisor": a.to_dict()})

    queue: asyncio.Queue = asyncio.Queue()
    tasks = [
        asyncio.create_task(_run_advisor_task(client, a, user_msg, pf_ctx, queue))
        for a in council
    ]

    summaries: list[str] = []
    for _ in council:
        status, agent, text = await queue.get()
        if status == "ok":
            yield sse({"type": "block_entry", "advisor": agent.to_dict(), "text": text})
            summaries.append(f"{agent.name} ({agent.tag}):\n{text}")
        else:
            yield sse({"type": "block_error", "advisor": agent.to_dict(), "message": text})

    await asyncio.gather(*tasks, return_exceptions=True)
    yield sse({"type": "block_end"})

    if not summaries:
        return

    summary_text = "\n\n---\n\n".join(summaries)
    chair_prompt = (
        f'Question posed to council: "{user_msg}"\n\n'
        f"Council views:\n\n{summary_text}\n\n"
        "Now deliver your PM synthesis."
    )

    yield sse({"type": "synth_start"})
    yield sse({"type": "typing", "advisor": chair.to_dict()})

    full_text: list[str] = []
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


# ─── Research → Investors brief (single run) ─────────────────────────────────

async def stream_research_brief(
    client: anthropic.AsyncAnthropic,
    user_msg: str,
    pf_ctx: str,
) -> AsyncIterator[str]:
    """Research team runs once; collected results brief investors — no double-run."""
    yield sse({
        "type": "block_start",
        "title": "🔬 RESEARCH ANALYSIS",
        "color": "var(--teal)",
        "bg": "rgba(74,184,196,.12)",
        "advisor_ids": [a.id for a in RESEARCHERS],
    })
    for a in RESEARCHERS:
        yield sse({"type": "typing_in_block", "advisor": a.to_dict()})

    queue: asyncio.Queue = asyncio.Queue()
    tasks = [
        asyncio.create_task(_run_advisor_task(client, a, user_msg, pf_ctx, queue))
        for a in RESEARCHERS
    ]

    res_texts: list[str] = []
    for _ in RESEARCHERS:
        status, a, text = await queue.get()
        if status == "ok":
            yield sse({"type": "block_entry", "advisor": a.to_dict(), "text": text})
            res_texts.append(f"{a.name} ({a.tag}):\n{text}")
        else:
            yield sse({"type": "block_error", "advisor": a.to_dict(), "message": text})

    await asyncio.gather(*tasks, return_exceptions=True)
    yield sse({"type": "block_end"})

    if not res_texts:
        return

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


# ─── News → All Teams brief (single run) ─────────────────────────────────────

async def stream_news_brief(
    client: anthropic.AsyncAnthropic,
    user_msg: str,
    pf_ctx: str,
) -> AsyncIterator[str]:
    """News desk runs, then The Chair synthesizes a portfolio brief."""
    yield sse({
        "type": "block_start",
        "title": "📡 NEWS DESK ANALYSIS",
        "color": "var(--orange)",
        "bg": "rgba(224,131,74,.12)",
        "advisor_ids": [a.id for a in NEWS_DESK],
    })
    for a in NEWS_DESK:
        yield sse({"type": "typing_in_block", "advisor": a.to_dict()})

    queue: asyncio.Queue = asyncio.Queue()
    tasks = [
        asyncio.create_task(_run_advisor_task(client, a, user_msg, pf_ctx, queue))
        for a in NEWS_DESK
    ]

    news_texts: list[str] = []
    for _ in NEWS_DESK:
        status, a, text = await queue.get()
        if status == "ok":
            yield sse({"type": "block_entry", "advisor": a.to_dict(), "text": text})
            news_texts.append(f"{a.name} ({a.tag}):\n{text}")
        else:
            yield sse({"type": "block_error", "advisor": a.to_dict(), "message": text})

    await asyncio.gather(*tasks, return_exceptions=True)
    yield sse({"type": "block_end"})

    if not news_texts:
        return

    chair = get_agent("chair")
    news_summary = "\n\n---\n\n".join(news_texts)
    chair_prompt = (
        f'News desk briefing on: "{user_msg}"\n\n'
        f"Reporter findings:\n\n{news_summary}\n\n"
        "Write a concise investment brief in exactly THREE sections:\n\n"
        "REPORTER SUMMARIES — 1-2 sentences per reporter capturing their key point.\n\n"
        "PORTFOLIO IMPACT — how this news specifically affects our current positions "
        "(reference actual tickers and dollar amounts from the portfolio).\n\n"
        "RECOMMENDED ACTION — clear, specific next step: what to buy, sell, watch, or hold, "
        "with sizing guidance. Be direct."
    )

    yield sse({"type": "synth_start"})
    yield sse({"type": "typing", "advisor": chair.to_dict()})

    full_text: list[str] = []
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


# ─── Main router ──────────────────────────────────────────────────────────────

async def generate_stream(request: ChatRequest) -> AsyncIterator[str]:
    client = make_client()
    pf_ctx = await build_portfolio_context()
    user_msg = next(
        (m["content"] for m in reversed(request.messages) if m["role"] == "user"),
        "",
    )

    enabled: set[str] | None = set(request.enabled_agents) if request.enabled_agents else None

    def filter_agents(agents: list[Agent]) -> list[Agent]:
        if enabled is None:
            return agents
        return [a for a in agents if a.id in enabled]

    try:
        if request.team == "inv":
            if request.mode == "solo" and request.advisor_id:
                agent = get_agent(request.advisor_id)
                if agent:
                    async for ev in stream_solo(client, agent, request.messages, pf_ctx):
                        yield ev

            elif request.mode == "all":
                agents = filter_agents([a for a in INVESTORS if a.id != "chair"])
                if agents:
                    async for ev in stream_concurrent(
                        client, agents, user_msg, pf_ctx,
                        "⚡ INVESTMENT COUNCIL", "var(--gold)", "rgba(212,168,67,.12)",
                    ):
                        yield ev

            elif request.mode == "round":
                agents = filter_agents([a for a in INVESTORS if a.id != "chair"])
                if agents:
                    async for ev in stream_sequential(
                        client, agents, user_msg, pf_ctx,
                        "🔄 ROUND TABLE", "var(--gold)", "rgba(212,168,67,.12)",
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
                agents = filter_agents(RESEARCHERS)
                if agents:
                    async for ev in stream_concurrent(
                        client, agents, user_msg, pf_ctx,
                        "🔬 RESEARCH TEAM", "var(--teal)", "rgba(74,184,196,.12)",
                    ):
                        yield ev

            elif request.mode == "round":
                agents = filter_agents(RESEARCHERS)
                if agents:
                    async for ev in stream_sequential(
                        client, agents, user_msg, pf_ctx,
                        "🔄 RESEARCH DIALOGUE", "var(--teal)", "rgba(74,184,196,.12)",
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
                agents = filter_agents(NEWS_DESK)
                if agents:
                    async for ev in stream_concurrent(
                        client, agents, user_msg, pf_ctx,
                        "📡 NEWS DESK", "var(--orange)", "rgba(224,131,74,.12)",
                    ):
                        yield ev

            elif request.mode == "round":
                agents = filter_agents(NEWS_DESK)
                if agents:
                    async for ev in stream_sequential(
                        client, agents, user_msg, pf_ctx,
                        "🔄 NEWS DIALOGUE", "var(--orange)", "rgba(224,131,74,.12)",
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
