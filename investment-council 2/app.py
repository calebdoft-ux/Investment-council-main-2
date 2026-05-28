from __future__ import annotations
import asyncio
import json
import queue
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from time import time

import anthropic
import pandas as pd
import streamlit as st
import yfinance as yf

from agents.council import (
    INVESTORS, RESEARCHERS, NEWS_DESK,
    get_agent, DEBATE_EXTRA,
)
from config import settings
from db.database import (
    init_db,
    get_portfolio, upsert_position, delete_position, update_position_price,
    get_journal, add_journal_entry, delete_journal_entry,
    get_sessions, upsert_session, delete_session,
    get_cash, set_cash, get_briefing, set_briefing,
    get_watchlist, set_watchlist,
)

# ── Constants ──────────────────────────────────────────────────────────────────

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}
WEB_SEARCH_BETA = "web-search-2025-03-05"
MAX_HISTORY_PAIRS = 8
PRIOR_RESP_MAX_CHARS = 1200
CACHE_TTL = 60

_quote_cache: dict[str, tuple[float, dict]] = {}

TICKER_EXCLUDE = {
    'THE','AND','FOR','NOT','BUT','ALL','ARE','CAN','GET','HAS','ITS','MAY','NEW',
    'NOW','OUR','OUT','SEE','SET','TOP','USE','CEO','CFO','EPS','ETF','FCF','GDP',
    'IPO','FED','SEC','YOY','USD','EUR','GBP','BUY','SELL','HOLD','WATCH','AVOID',
    'NEWS','RATE','RISK','FUND','HIGH','FLOW','TECH','DATA','CORP','COST','CASH',
    'DEBT','LOSS','GAIN','LONG','TERM','GOOD','REAL','FULL','NEXT','LAST','BEST',
}

# ── Async bridge ───────────────────────────────────────────────────────────────

def arun(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# ── Quote fetching ─────────────────────────────────────────────────────────────

def fetch_quote_sync(symbol: str) -> dict | None:
    symbol = symbol.upper()
    now = time()
    if symbol in _quote_cache:
        ts, data = _quote_cache[symbol]
        if now - ts < CACHE_TTL:
            return data
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)
        if price is None:
            return None
        change = (price - prev_close) if prev_close else 0
        chg_pct = (change / prev_close * 100) if prev_close else 0
        try:
            full = ticker.info
            name = full.get("longName") or full.get("shortName") or symbol
            pe = full.get("trailingPE") or full.get("forwardPE")
        except Exception:
            name = symbol
            pe = None
        data = {
            "symbol": symbol,
            "name": name,
            "price": round(price, 4),
            "change": round(change, 4),
            "change_pct": round(chg_pct, 4),
            "pe": round(pe, 2) if pe else None,
            "day_high": round(getattr(info, "day_high", 0) or 0, 4) or None,
            "day_low": round(getattr(info, "day_low", 0) or 0, 4) or None,
        }
        _quote_cache[symbol] = (now, data)
        return data
    except Exception:
        return None

# ── Portfolio context ──────────────────────────────────────────────────────────

def build_pf_context() -> str:
    portfolio = arun(get_portfolio())
    cash = arun(get_cash())
    briefing = arun(get_briefing())

    ctx = ""
    if briefing.strip():
        ctx += f"\n\nINVESTOR PROFILE:\n{briefing.strip()}"

    if not portfolio and not cash:
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

# ── Agent calls ────────────────────────────────────────────────────────────────

def stream_agent(agent, messages_for_api, pf_ctx, system_extra=""):
    """Sync generator yielding text chunks from a streaming agent call."""
    chunks: queue.Queue = queue.Queue()

    async def _run():
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        try:
            async with client.messages.stream(
                model=settings.MODEL,
                max_tokens=settings.MAX_TOKENS,
                system=agent.system + system_extra + pf_ctx,
                messages=messages_for_api,
                tools=[WEB_SEARCH_TOOL],
                extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
            ) as stream:
                async for text in stream.text_stream:
                    chunks.put(text)
        except Exception as e:
            chunks.put(f"\n\n**[Error: {e}]**")
        finally:
            chunks.put(None)

    threading.Thread(target=lambda: asyncio.run(_run()), daemon=True).start()
    while True:
        chunk = chunks.get()
        if chunk is None:
            break
        yield chunk


def call_agent(agent, user_msg, pf_ctx, system_extra="") -> str:
    """Non-streaming agent call, returns full response text."""
    async def _run():
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=settings.MODEL,
            max_tokens=settings.MAX_TOKENS,
            system=agent.system + system_extra + pf_ctx,
            messages=[{"role": "user", "content": user_msg}],
            tools=[WEB_SEARCH_TOOL],
            extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
        )
        return "".join(b.text for b in response.content if hasattr(b, "text"))
    return asyncio.run(_run())

# ── Message trimming ───────────────────────────────────────────────────────────

def trim_messages(messages: list[dict]) -> list[dict]:
    clean = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return clean[-(MAX_HISTORY_PAIRS * 2):]

# ── Ticker extraction ──────────────────────────────────────────────────────────

def extract_tickers(text: str) -> list[str]:
    found = set()
    for m in re.findall(r'\$([A-Z]{1,5})\b', text):
        found.add(m)
    for m in re.findall(r'(?:BUY|WATCH|AVOID)[:\s]+([A-Z]{2,5})\b', text):
        found.add(m)
    return [t for t in found if len(t) >= 2 and t not in TICKER_EXCLUDE]


def auto_add_tickers(text: str) -> list[str]:
    tickers = extract_tickers(text)
    if not tickers:
        return []
    wl = st.session_state.watchlist
    existing = {w["t"] for w in wl}
    new_ones = [t for t in tickers if t not in existing]
    if new_ones:
        for t in new_ones:
            wl.append({"t": t, "added": datetime.now().strftime("%Y-%m-%d")})
        st.session_state.watchlist = wl
        arun(set_watchlist(wl))
    return new_ones

# ── Agent card renderer ────────────────────────────────────────────────────────

def agent_card(agent_dict, text, container=None):
    target = container or st
    color = agent_dict.get("color", "#888")
    emoji = agent_dict.get("emoji", "")
    name = agent_dict.get("name", "")
    tag = agent_dict.get("tag", "")
    with target.container(border=True):
        st.markdown(
            f'<span style="color:{color}">**{emoji} {name}**</span> `{tag}`',
            unsafe_allow_html=True,
        )
        st.markdown(text)

# ── Slash command parser ───────────────────────────────────────────────────────

ADVISOR_SHORTCUTS = {
    "david": "banker", "rachel": "macro", "tom": "geo",
    "arjun": "quant", "sarah": "growth", "mark": "risk",
    "nina": "fund", "rex": "tech", "zoe": "screen",
    "dev": "theme", "cass": "contra",
    "elena": "mkts", "james": "policy", "priya": "corp",
}


def parse_slash(text: str):
    m = re.match(r'^/(\w+)\s*(.*)', text.strip(), re.DOTALL)
    if not m:
        return None
    cmd = m.group(1).lower()
    args = m.group(2).strip()
    if cmd in ADVISOR_SHORTCUTS:
        return ("advisor", ADVISOR_SHORTCUTS[cmd], args)
    if cmd in ("add", "sell", "watch", "pm", "round", "council", "news", "brief"):
        return (cmd, args)
    return None

# ── Session state init ─────────────────────────────────────────────────────────

def init_state():
    arun(init_db())
    defaults = {
        "chat_display": [],
        "api_messages": [],
        "team": "inv",
        "mode": "solo",
        "advisor_id": "banker",
        "enabled_agents": set(),
        "debate_members": [],
        "watchlist": [],
        "session_id": str(uuid.uuid4()),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "watchlist_loaded" not in st.session_state:
        st.session_state.watchlist = arun(get_watchlist()) or []
        st.session_state.watchlist_loaded = True

# ── Chat history renderer ──────────────────────────────────────────────────────

def render_chat_history():
    for item in st.session_state.chat_display:
        itype = item["type"]

        if itype == "user":
            with st.chat_message("user"):
                st.markdown(item["text"])

        elif itype == "solo":
            a = item["agent"]
            with st.chat_message("assistant"):
                st.markdown(
                    f'<span style="color:{a["color"]}">**{a["emoji"]} {a["name"]}**</span> `{a["tag"]}`',
                    unsafe_allow_html=True,
                )
                st.markdown(item["text"])

        elif itype == "block":
            st.markdown(f'### {item["title"]}')
            for agent_dict, text in item["entries"]:
                agent_card(agent_dict, text)

        elif itype == "pm":
            st.markdown("### ⚡ COUNCIL DELIBERATION")
            for agent_dict, text in item.get("council_entries", []):
                agent_card(agent_dict, text)
            chair = item.get("chair", {})
            st.markdown("---")
            st.markdown(f"### {chair.get('emoji','🏦')} THE CHAIR'S VERDICT")
            agent_card(chair, item.get("chair_text", ""))

        elif itype == "res_brief":
            st.markdown("### 🔬 RESEARCH ANALYSIS")
            for agent_dict, text in item.get("res_entries", []):
                agent_card(agent_dict, text)
            st.markdown("### 📤 INVESTOR REACTION")
            for agent_dict, text in item.get("inv_entries", []):
                agent_card(agent_dict, text)

        elif itype == "news_brief":
            st.markdown("### 📡 NEWS DESK ANALYSIS")
            for agent_dict, text in item.get("news_entries", []):
                agent_card(agent_dict, text)
            chair = item.get("chair", {})
            st.markdown("---")
            st.markdown("### 🏦 CHAIR'S BRIEFING")
            agent_card(chair, item.get("chair_text", ""))

        elif itype == "system":
            st.info(item["text"])

# ── Message processor ──────────────────────────────────────────────────────────

def process_message(user_text: str):
    slash = parse_slash(user_text)

    # Portfolio / watchlist slash commands — no LLM call
    if slash:
        cmd = slash[0]

        if cmd == "add":
            parts = slash[1].split()
            if len(parts) >= 2:
                try:
                    ticker = parts[0].upper()
                    size = float(parts[1])
                    entry = float(parts[2]) if len(parts) > 2 else None
                    arun(upsert_position(ticker, size, entry, None, None))
                    st.session_state.chat_display.append(
                        {"type": "system", "text": f"Added {ticker} (${size:,.0f}) to portfolio."}
                    )
                    return
                except ValueError:
                    pass

        elif cmd == "sell":
            ticker = slash[1].upper()
            if ticker:
                arun(delete_position(ticker))
                st.session_state.chat_display.append(
                    {"type": "system", "text": f"Removed {ticker} from portfolio."}
                )
                return

        elif cmd == "watch":
            ticker = slash[1].upper()
            if ticker:
                wl = st.session_state.watchlist
                if not any(w["t"] == ticker for w in wl):
                    wl.append({"t": ticker, "added": datetime.now().strftime("%Y-%m-%d")})
                    st.session_state.watchlist = wl
                    arun(set_watchlist(wl))
                st.session_state.chat_display.append(
                    {"type": "system", "text": f"Added {ticker} to watchlist."}
                )
                return

        # Mode overrides from slash
        elif cmd == "pm":
            st.session_state.team = "inv"
            st.session_state.mode = "pm"
            user_text = slash[1] or user_text
        elif cmd == "round":
            st.session_state.mode = "round"
            user_text = slash[1] or user_text
        elif cmd == "council":
            st.session_state.team = "inv"
            st.session_state.mode = "all"
            user_text = slash[1] or user_text
        elif cmd == "news":
            st.session_state.team = "news"
            st.session_state.mode = "all"
            user_text = slash[1] or user_text
        elif cmd == "brief":
            st.session_state.team = "news"
            st.session_state.mode = "brief"
            user_text = slash[1] or user_text
        elif cmd == "advisor":
            _, advisor_id, question = slash
            st.session_state.mode = "solo"
            st.session_state.advisor_id = advisor_id
            if question:
                user_text = question

    # Append user message
    st.session_state.chat_display.append({"type": "user", "text": user_text})
    st.session_state.api_messages.append({"role": "user", "content": user_text})

    pf_ctx = build_pf_context()
    team = st.session_state.team
    mode = st.session_state.mode
    enabled = st.session_state.enabled_agents

    def filter_agents(agents):
        if not enabled:
            return agents
        return [a for a in agents if a.id in enabled]

    # ── SOLO ──────────────────────────────────────────────────────────────────
    if mode == "solo":
        agent = get_agent(st.session_state.advisor_id)
        if not agent:
            return
        api_msgs = trim_messages(st.session_state.api_messages)
        with st.chat_message("assistant"):
            st.markdown(
                f'<span style="color:{agent.color}">**{agent.emoji} {agent.name}**</span> `{agent.tag}`',
                unsafe_allow_html=True,
            )
            full_text = st.write_stream(stream_agent(agent, api_msgs, pf_ctx))
        st.session_state.api_messages.append({"role": "assistant", "content": full_text})
        st.session_state.chat_display.append({"type": "solo", "agent": agent.to_dict(), "text": full_text})

    # ── ALL / CONCURRENT ──────────────────────────────────────────────────────
    elif mode == "all":
        if team == "inv":
            agents = filter_agents([a for a in INVESTORS if a.id != "chair"])
            title = "⚡ INVESTMENT COUNCIL"
        elif team == "res":
            agents = filter_agents(RESEARCHERS)
            title = "🔬 RESEARCH TEAM"
        else:
            agents = filter_agents(NEWS_DESK)
            title = "📡 NEWS DESK"

        if not agents:
            return

        st.markdown(f"### {title}")
        placeholders = {a.id: st.empty() for a in agents}
        entries = []

        with ThreadPoolExecutor(max_workers=len(agents)) as ex:
            futures = {ex.submit(call_agent, a, user_text, pf_ctx): a for a in agents}
            for future in as_completed(futures):
                a = futures[future]
                text = future.result() if not future.exception() else f"[Error: {future.exception()}]"
                with placeholders[a.id].container(border=True):
                    st.markdown(
                        f'<span style="color:{a.color}">**{a.emoji} {a.name}**</span> `{a.tag}`',
                        unsafe_allow_html=True,
                    )
                    st.markdown(text)
                entries.append((a.to_dict(), text))

        combined = "\n\n".join(t for _, t in entries)
        st.session_state.api_messages.append({"role": "assistant", "content": combined})
        st.session_state.chat_display.append({"type": "block", "title": title, "entries": entries})

        if team == "res":
            new_t = auto_add_tickers(combined)
            if new_t:
                st.info(f"Auto-added to watchlist: {', '.join(new_t)}")

    # ── ROUND TABLE / SEQUENTIAL ──────────────────────────────────────────────
    elif mode == "round":
        if team == "inv":
            agents = filter_agents([a for a in INVESTORS if a.id != "chair"])
            title = "🔄 ROUND TABLE"
        elif team == "res":
            agents = filter_agents(RESEARCHERS)
            title = "🔄 RESEARCH DIALOGUE"
        else:
            agents = filter_agents(NEWS_DESK)
            title = "🔄 NEWS DIALOGUE"

        if not agents:
            return

        st.markdown(f"### {title}")
        prior: list[str] = []
        entries = []

        for agent in agents:
            if prior:
                capped = [
                    t if len(t) <= PRIOR_RESP_MAX_CHARS else t[:PRIOR_RESP_MAX_CHARS] + "… [see above]"
                    for t in prior
                ]
                msg = (
                    f"{user_text}\n\n---\n"
                    f"Your colleagues have already weighed in:\n\n{chr(10).join(capped)}\n\n---\n"
                    "Now add YOUR analysis. Engage with what was said — "
                    "agree, disagree, or bring a perspective that was missed. Be direct and specific."
                )
            else:
                msg = user_text

            with st.container(border=True):
                st.markdown(
                    f'<span style="color:{agent.color}">**{agent.emoji} {agent.name}**</span> `{agent.tag}`',
                    unsafe_allow_html=True,
                )
                full_text = st.write_stream(
                    stream_agent(agent, [{"role": "user", "content": msg}], pf_ctx)
                )

            prior.append(f"{agent.name} ({agent.tag}):\n{full_text}")
            entries.append((agent.to_dict(), full_text))

        combined = "\n\n".join(t for _, t in entries)
        st.session_state.api_messages.append({"role": "assistant", "content": combined})
        st.session_state.chat_display.append({"type": "block", "title": title, "entries": entries})

        if team == "res":
            new_t = auto_add_tickers(combined)
            if new_t:
                st.info(f"Auto-added to watchlist: {', '.join(new_t)}")

    # ── PM MODE ───────────────────────────────────────────────────────────────
    elif mode == "pm":
        council = [a for a in INVESTORS if a.id != "chair"]
        chair = get_agent("chair")

        st.markdown("### ⚡ COUNCIL DELIBERATION")
        placeholders = {a.id: st.empty() for a in council}
        council_entries = []
        summaries: list[str] = []

        with ThreadPoolExecutor(max_workers=len(council)) as ex:
            futures = {ex.submit(call_agent, a, user_text, pf_ctx): a for a in council}
            for future in as_completed(futures):
                a = futures[future]
                text = future.result() if not future.exception() else f"[Error: {future.exception()}]"
                with placeholders[a.id].container(border=True):
                    st.markdown(
                        f'<span style="color:{a.color}">**{a.emoji} {a.name}**</span> `{a.tag}`',
                        unsafe_allow_html=True,
                    )
                    st.markdown(text)
                summaries.append(f"{a.name} ({a.tag}):\n{text}")
                council_entries.append((a.to_dict(), text))

        if not summaries:
            return

        chair_prompt = (
            f'Question posed to council: "{user_text}"\n\n'
            f"Council views:\n\n{chr(10).join(summaries)}\n\n"
            "Now deliver your PM synthesis."
        )

        st.markdown("---")
        st.markdown(f"### {chair.emoji} THE CHAIR'S VERDICT")
        with st.container(border=True):
            st.markdown(
                f'<span style="color:{chair.color}">**{chair.emoji} {chair.name}**</span> `{chair.tag}`',
                unsafe_allow_html=True,
            )
            full_chair = st.write_stream(
                stream_agent(chair, [{"role": "user", "content": chair_prompt}], pf_ctx)
            )

        all_text = "\n\n---\n\n".join([*summaries, f"CHAIR VERDICT:\n{full_chair}"])
        st.session_state.api_messages.append({"role": "assistant", "content": all_text})
        st.session_state.chat_display.append({
            "type": "pm",
            "council_entries": council_entries,
            "chair": chair.to_dict(),
            "chair_text": full_chair,
        })

    # ── DEBATE ────────────────────────────────────────────────────────────────
    elif mode == "debate":
        members = st.session_state.debate_members
        agents = [a for a in (get_agent(i) for i in members) if a and a.id != "chair"]
        if len(agents) < 2:
            st.warning("Select 2 or more debate members in the sidebar.")
            return

        label = " vs ".join(a.name.split()[0].upper() for a in agents)
        title = f"⚔️ DEBATE — {label}"
        st.markdown(f"### {title}")

        placeholders = {a.id: st.empty() for a in agents}
        entries = []

        with ThreadPoolExecutor(max_workers=len(agents)) as ex:
            futures = {ex.submit(call_agent, a, user_text, pf_ctx, DEBATE_EXTRA): a for a in agents}
            for future in as_completed(futures):
                a = futures[future]
                text = future.result() if not future.exception() else f"[Error: {future.exception()}]"
                with placeholders[a.id].container(border=True):
                    st.markdown(
                        f'<span style="color:{a.color}">**{a.emoji} {a.name}**</span> `{a.tag}`',
                        unsafe_allow_html=True,
                    )
                    st.markdown(text)
                entries.append((a.to_dict(), text))

        combined = "\n\n".join(t for _, t in entries)
        st.session_state.api_messages.append({"role": "assistant", "content": combined})
        st.session_state.chat_display.append({"type": "block", "title": title, "entries": entries})

    # ── RESEARCH BRIEF ────────────────────────────────────────────────────────
    elif mode == "brief" and team == "res":
        st.markdown("### 🔬 RESEARCH ANALYSIS")
        placeholders = {a.id: st.empty() for a in RESEARCHERS}
        res_entries = []
        res_summaries: list[str] = []

        with ThreadPoolExecutor(max_workers=len(RESEARCHERS)) as ex:
            futures = {ex.submit(call_agent, a, user_text, pf_ctx): a for a in RESEARCHERS}
            for future in as_completed(futures):
                a = futures[future]
                text = future.result() if not future.exception() else f"[Error: {future.exception()}]"
                with placeholders[a.id].container(border=True):
                    st.markdown(
                        f'<span style="color:{a.color}">**{a.emoji} {a.name}**</span> `{a.tag}`',
                        unsafe_allow_html=True,
                    )
                    st.markdown(text)
                res_entries.append((a.to_dict(), text))
                res_summaries.append(f"{a.name} ({a.tag}):\n{text}")

        if not res_summaries:
            return

        combined_res = "\n\n".join(res_summaries)
        new_t = auto_add_tickers(combined_res)
        if new_t:
            st.info(f"Auto-added to watchlist: {', '.join(new_t)}")

        investor_prompt = (
            f'The research team analyzed: "{user_text}"\n\nFindings:\n\n{combined_res}\n\n'
            "As an investor, react to this briefing. Should we act? Buy, watch, or pass?"
        )

        investors = [a for a in INVESTORS if a.id != "chair"]
        st.markdown("### 📤 INVESTOR REACTION")
        inv_placeholders = {a.id: st.empty() for a in investors}
        inv_entries = []

        with ThreadPoolExecutor(max_workers=len(investors)) as ex:
            futures = {ex.submit(call_agent, a, investor_prompt, pf_ctx): a for a in investors}
            for future in as_completed(futures):
                a = futures[future]
                text = future.result() if not future.exception() else f"[Error: {future.exception()}]"
                with inv_placeholders[a.id].container(border=True):
                    st.markdown(
                        f'<span style="color:{a.color}">**{a.emoji} {a.name}**</span> `{a.tag}`',
                        unsafe_allow_html=True,
                    )
                    st.markdown(text)
                inv_entries.append((a.to_dict(), text))

        all_text = combined_res + "\n\n" + "\n\n".join(t for _, t in inv_entries)
        st.session_state.api_messages.append({"role": "assistant", "content": all_text})
        st.session_state.chat_display.append({
            "type": "res_brief",
            "res_entries": res_entries,
            "inv_entries": inv_entries,
        })

    # ── NEWS BRIEF ────────────────────────────────────────────────────────────
    elif mode == "brief" and team == "news":
        st.markdown("### 📡 NEWS DESK ANALYSIS")
        placeholders = {a.id: st.empty() for a in NEWS_DESK}
        news_entries = []
        news_summaries: list[str] = []

        with ThreadPoolExecutor(max_workers=len(NEWS_DESK)) as ex:
            futures = {ex.submit(call_agent, a, user_text, pf_ctx): a for a in NEWS_DESK}
            for future in as_completed(futures):
                a = futures[future]
                text = future.result() if not future.exception() else f"[Error: {future.exception()}]"
                with placeholders[a.id].container(border=True):
                    st.markdown(
                        f'<span style="color:{a.color}">**{a.emoji} {a.name}**</span> `{a.tag}`',
                        unsafe_allow_html=True,
                    )
                    st.markdown(text)
                news_entries.append((a.to_dict(), text))
                news_summaries.append(f"{a.name} ({a.tag}):\n{text}")

        if not news_summaries:
            return

        chair = get_agent("chair")
        chair_prompt = (
            f'News desk briefing on: "{user_text}"\n\n'
            f"Reporter findings:\n\n{chr(10).join(news_summaries)}\n\n"
            "Write a concise investment brief in exactly THREE sections:\n\n"
            "REPORTER SUMMARIES — 1-2 sentences per reporter capturing their key point.\n\n"
            "PORTFOLIO IMPACT — how this news specifically affects our current positions "
            "(reference actual tickers and dollar amounts from the portfolio).\n\n"
            "RECOMMENDED ACTION — clear, specific next step: what to buy, sell, watch, or hold, "
            "with sizing guidance. Be direct."
        )

        st.markdown("---")
        st.markdown("### 🏦 CHAIR'S BRIEFING")
        with st.container(border=True):
            st.markdown(
                f'<span style="color:{chair.color}">**{chair.emoji} {chair.name}**</span> `{chair.tag}`',
                unsafe_allow_html=True,
            )
            full_chair = st.write_stream(
                stream_agent(chair, [{"role": "user", "content": chair_prompt}], pf_ctx)
            )

        all_text = "\n\n".join(news_summaries) + f"\n\nCHAIR:\n{full_chair}"
        st.session_state.api_messages.append({"role": "assistant", "content": all_text})
        st.session_state.chat_display.append({
            "type": "news_brief",
            "news_entries": news_entries,
            "chair": chair.to_dict(),
            "chair_text": full_chair,
        })

# ── Portfolio tab ──────────────────────────────────────────────────────────────

def portfolio_tab():
    portfolio = arun(get_portfolio())
    cash = arun(get_cash())
    total = sum(p["size"] for p in portfolio.values())

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Invested", f"${total:,.0f}")
    c2.metric("Cash", f"${cash:,.2f}")
    c3.metric("Positions", len(portfolio))

    if portfolio:
        rows = []
        for tk, p in portfolio.items():
            row = {
                "Ticker": tk,
                "Size ($)": f"${p['size']:,.0f}",
                "Entry": f"${p['entry']:.2f}" if p.get("entry") else "—",
                "Current": f"${p['price']:.2f}" if p.get("price") else "—",
                "P&L": "—",
                "Alloc": f"{p['size']/total*100:.1f}%" if total else "—",
            }
            if p.get("entry") and p.get("price"):
                pnl = (p["size"] / p["entry"]) * p["price"] - p["size"]
                pp = pnl / p["size"] * 100
                row["P&L"] = f"{'+' if pnl >= 0 else ''}${pnl:,.0f} ({'+' if pnl >= 0 else ''}{pp:.1f}%)"
            rows.append(row)

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        col1, col2, col3 = st.columns([3, 1, 1])
        del_ticker = col1.selectbox("Remove position", [""] + list(portfolio.keys()), key="del_ticker")
        col2.write("")
        col2.write("")
        if col2.button("Remove", key="btn_del") and del_ticker:
            arun(delete_position(del_ticker))
            st.rerun()
        if col3.button("Refresh Prices"):
            with st.spinner("Fetching..."):
                with ThreadPoolExecutor(max_workers=8) as ex:
                    fs = {ex.submit(fetch_quote_sync, tk): tk for tk in portfolio}
                    for f in as_completed(fs):
                        tk = fs[f]
                        q = f.result()
                        if q and q.get("price"):
                            arun(update_position_price(tk, q["price"]))
            st.rerun()

        csv = pd.DataFrame(rows).to_csv(index=False)
        st.download_button("Export CSV", csv, "portfolio.csv", "text/csv", key="csv_dl")
    else:
        st.info("No positions yet. Add one below.")

    st.divider()

    with st.expander("Add Position", expanded=not bool(portfolio)):
        with st.form("add_position"):
            c1, c2, c3 = st.columns(3)
            ticker_in = c1.text_input("Ticker", placeholder="AAPL").upper()
            size_in = c2.number_input("Size ($)", min_value=0.0, step=1000.0)
            entry_in = c3.number_input("Entry Price", min_value=0.0, step=0.01, value=0.0)
            if st.form_submit_button("Add to Portfolio") and ticker_in and size_in > 0:
                q = fetch_quote_sync(ticker_in)
                arun(upsert_position(
                    ticker_in, size_in,
                    entry_in if entry_in > 0 else None,
                    q.get("price") if q else None,
                    q.get("name") if q else None,
                ))
                st.success(f"Added {ticker_in}")
                st.rerun()

    with st.expander("Manage Cash"):
        with st.form("set_cash"):
            new_cash = st.number_input("Cash Balance ($)", min_value=0.0, value=float(cash), step=1000.0)
            if st.form_submit_button("Update Cash"):
                arun(set_cash(new_cash))
                st.success("Updated.")
                st.rerun()

# ── Watchlist tab ──────────────────────────────────────────────────────────────

def watchlist_tab():
    wl = st.session_state.watchlist

    if wl:
        with st.spinner("Fetching quotes..."):
            prices: dict[str, dict | None] = {}
            with ThreadPoolExecutor(max_workers=8) as ex:
                fs = {ex.submit(fetch_quote_sync, w["t"]): w["t"] for w in wl}
                for f in as_completed(fs):
                    sym = fs[f]
                    prices[sym] = f.result()

        rows = []
        for w in wl:
            q = prices.get(w["t"])
            chg = (q or {}).get("change_pct", 0) or 0
            rows.append({
                "Ticker": w["t"],
                "Added": w.get("added", "—"),
                "Price": f"${q['price']:.2f}" if q else "—",
                "Change": f"{'+' if chg >= 0 else ''}{chg:.2f}%" if q else "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        c1, c2, c3 = st.columns([2, 2, 1])
        rm = c1.selectbox("Remove", [""] + [w["t"] for w in wl], key="wl_rm")
        mv = c2.selectbox("Move to portfolio", [""] + [w["t"] for w in wl], key="wl_mv")
        c3.write("")
        c3.write("")
        if c3.button("Remove", key="wl_rm_btn") and rm:
            st.session_state.watchlist = [w for w in wl if w["t"] != rm]
            arun(set_watchlist(st.session_state.watchlist))
            st.rerun()

        if mv:
            with st.form("mv_to_portfolio"):
                q = prices.get(mv)
                sz = st.number_input("Size ($)", min_value=0.0, step=1000.0)
                ep = st.number_input("Entry Price", min_value=0.0,
                                     value=float(q["price"]) if q else 0.0, step=0.01)
                if st.form_submit_button(f"Add {mv} to Portfolio") and sz > 0:
                    arun(upsert_position(mv, sz, ep or None,
                                         q.get("price") if q else None,
                                         q.get("name") if q else None))
                    st.session_state.watchlist = [w for w in wl if w["t"] != mv]
                    arun(set_watchlist(st.session_state.watchlist))
                    st.success(f"Moved {mv} to portfolio.")
                    st.rerun()
    else:
        st.info("Watchlist is empty. Tickers auto-add from research, or add manually below.")

    st.divider()

    with st.form("add_watchlist"):
        c1, c2 = st.columns([4, 1])
        new_t = c1.text_input("Add ticker", placeholder="NVDA").upper()
        c2.write("")
        c2.write("")
        if st.form_submit_button("Add") and new_t:
            if not any(w["t"] == new_t for w in wl):
                wl.append({"t": new_t, "added": datetime.now().strftime("%Y-%m-%d")})
                st.session_state.watchlist = wl
                arun(set_watchlist(wl))
            st.rerun()

# ── Journal tab ────────────────────────────────────────────────────────────────

def journal_tab():
    entries = arun(get_journal())

    if entries:
        rows = [{
            "Date": e.get("date") or e.get("created_at", "")[:10],
            "Ticker": e["ticker"],
            "Type": e["type"],
            "Price": f"${e['price']:.2f}" if e.get("price") else "—",
            "Size ($)": f"${e['size']:,.0f}" if e.get("size") else "—",
            "Thesis": (e.get("thesis") or "")[:80],
        } for e in entries]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        labels = [f"{e.get('date','')[:10]} — {e['ticker']} {e['type']}" for e in entries]
        c1, c2 = st.columns([4, 1])
        del_idx = c1.selectbox("Delete entry", range(len(entries)), format_func=lambda i: labels[i], key="jrn_del")
        c2.write("")
        c2.write("")
        if c2.button("Delete", key="jrn_del_btn"):
            arun(delete_journal_entry(entries[del_idx]["id"]))
            st.rerun()

        if st.button("AI Review My Journal"):
            pf_ctx = build_pf_context()
            agent = get_agent("chair")
            summary = "\n".join(
                f"{e.get('date','')[:10]} {e['type']} {e['ticker']} @ ${e.get('price','?')} — {e.get('thesis','')}"
                for e in entries
            )
            prompt = f"Review this trading journal and identify patterns, mistakes, and lessons:\n\n{summary}"
            with st.container(border=True):
                st.markdown(f"**{agent.emoji} {agent.name}** — Journal Review")
                st.write_stream(stream_agent(agent, [{"role": "user", "content": prompt}], pf_ctx))
    else:
        st.info("No journal entries yet.")

    st.divider()

    with st.expander("Log Trade", expanded=not bool(entries)):
        with st.form("add_journal"):
            c1, c2, c3 = st.columns(3)
            jrn_ticker = c1.text_input("Ticker").upper()
            jrn_type = c2.selectbox("Type", ["BUY", "SELL", "WATCH", "NOTE"])
            jrn_date = c3.date_input("Date", value=datetime.now().date())

            c4, c5 = st.columns(2)
            jrn_price = c4.number_input("Price", min_value=0.0, step=0.01, value=0.0)
            jrn_size = c5.number_input("Size ($)", min_value=0.0, step=100.0, value=0.0)
            jrn_thesis = st.text_area("Thesis / Notes")

            if st.form_submit_button("Log Entry") and jrn_ticker:
                arun(add_journal_entry({
                    "id": str(uuid.uuid4()),
                    "ticker": jrn_ticker,
                    "type": jrn_type,
                    "price": jrn_price if jrn_price > 0 else None,
                    "size": jrn_size if jrn_size > 0 else None,
                    "date": str(jrn_date),
                    "thesis": jrn_thesis,
                    "created_at": datetime.now().isoformat(),
                }))
                st.success(f"Logged {jrn_type} {jrn_ticker}")
                st.rerun()

# ── Setup tab ──────────────────────────────────────────────────────────────────

def setup_tab():
    briefing = arun(get_briefing())

    st.subheader("Investor Profile")
    st.caption("Included in every advisor's context so they know your goals and risk tolerance.")

    with st.form("briefing_form"):
        new_briefing = st.text_area(
            "Your profile",
            value=briefing,
            height=200,
            placeholder="e.g. 35-year-old, 20-year horizon, moderate risk tolerance, focused on tech and clean energy.",
        )
        if st.form_submit_button("Save Profile"):
            arun(set_briefing(new_briefing))
            st.success("Profile saved.")

    st.divider()
    st.subheader("Sessions")

    sessions = arun(get_sessions())
    if sessions:
        for s in sessions:
            c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
            c1.markdown(f"**{(s.get('preview') or 'Session')[:60]}**")
            c2.caption(s.get("date", "")[:10])
            if c3.button("Load", key=f"load_{s['id']}"):
                msgs = s.get("messages", [])
                st.session_state.api_messages = msgs
                st.session_state.chat_display = []
                for m in msgs:
                    if m["role"] == "user":
                        st.session_state.chat_display.append({"type": "user", "text": m["content"]})
                    else:
                        ch = get_agent("chair")
                        st.session_state.chat_display.append(
                            {"type": "solo", "agent": ch.to_dict(), "text": m["content"]}
                        )
                st.rerun()
            if c4.button("Del", key=f"del_{s['id']}"):
                arun(delete_session(s["id"]))
                st.rerun()
    else:
        st.info("No saved sessions.")

    if st.session_state.api_messages:
        if st.button("Save Current Session"):
            preview = next(
                (m["content"][:60] for m in st.session_state.api_messages if m["role"] == "user"),
                "Session",
            )
            arun(upsert_session(
                st.session_state.session_id,
                preview,
                st.session_state.team,
                st.session_state.api_messages,
            ))
            st.success("Session saved.")

# ── Sidebar ────────────────────────────────────────────────────────────────────

def sidebar():
    with st.sidebar:
        st.title("🏦 Investment Council")

        st.subheader("Team")
        team_map = {"Investors": "inv", "Research": "res", "News Desk": "news"}
        team_label = st.radio("Team", list(team_map.keys()), key="team_radio", label_visibility="collapsed")
        st.session_state.team = team_map[team_label]
        team = st.session_state.team

        st.subheader("Mode")
        if team == "inv":
            mode_opts = {"Solo": "solo", "All (Parallel)": "all", "Round Table": "round", "PM Mode": "pm", "Debate": "debate"}
        elif team == "res":
            mode_opts = {"Solo": "solo", "All (Parallel)": "all", "Round Table": "round", "Research Brief": "brief"}
        else:
            mode_opts = {"Solo": "solo", "All (Parallel)": "all", "Round Table": "round", "News Brief": "brief"}

        mode_label = st.radio("Mode", list(mode_opts.keys()), key=f"mode_radio_{team}", label_visibility="collapsed")
        st.session_state.mode = mode_opts[mode_label]
        mode = st.session_state.mode

        if mode == "solo":
            st.subheader("Advisor")
            agents = INVESTORS if team == "inv" else (RESEARCHERS if team == "res" else NEWS_DESK)
            agent_map = {f"{a.emoji} {a.name}": a.id for a in agents}
            cur = st.session_state.advisor_id
            cur_label = next((l for l, i in agent_map.items() if i == cur), list(agent_map.keys())[0])
            sel = st.selectbox("Advisor", list(agent_map.keys()),
                               index=list(agent_map.keys()).index(cur_label),
                               key="agent_select", label_visibility="collapsed")
            st.session_state.advisor_id = agent_map[sel]

        elif mode == "debate":
            st.subheader("Debate Members")
            council = [a for a in INVESTORS if a.id != "chair"]
            d_map = {f"{a.emoji} {a.name}": a.id for a in council}
            sel = st.multiselect("Members", list(d_map.keys()), key="debate_ms")
            st.session_state.debate_members = [d_map[l] for l in sel]

        elif mode in ("all", "round"):
            st.subheader("Active Agents")
            pool = ([a for a in INVESTORS if a.id != "chair"] if team == "inv"
                    else (RESEARCHERS if team == "res" else NEWS_DESK))
            enabled = set()
            for a in pool:
                if st.checkbox(f"{a.emoji} {a.name}", value=True, key=f"mute_{a.id}_{team}"):
                    enabled.add(a.id)
            st.session_state.enabled_agents = enabled

        st.divider()

        with st.expander("Slash Commands"):
            st.markdown(
                "`/add TICKER SIZE ENTRY`  \n"
                "`/sell TICKER`  \n"
                "`/watch TICKER`  \n"
                "`/pm QUESTION`  \n"
                "`/round QUESTION`  \n"
                "`/council QUESTION`  \n"
                "`/news TOPIC`  \n"
                "`/brief TOPIC`  \n"
                "`/david` `/rachel` `/tom` `/arjun` `/sarah` `/mark`"
            )

        if st.button("Clear Chat", type="secondary", use_container_width=True):
            st.session_state.chat_display = []
            st.session_state.api_messages = []
            st.session_state.session_id = str(uuid.uuid4())
            st.rerun()

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Investment Council",
        page_icon="🏦",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    init_state()
    sidebar()

    tab_chat, tab_portfolio, tab_watchlist, tab_journal, tab_setup = st.tabs([
        "💬 Chat", "📊 Portfolio", "👁 Watchlist", "📓 Journal", "⚙️ Setup",
    ])

    with tab_chat:
        render_chat_history()
        if prompt := st.chat_input("Ask your council anything..."):
            with st.chat_message("user"):
                st.markdown(prompt)
            process_message(prompt)

    with tab_portfolio:
        portfolio_tab()

    with tab_watchlist:
        watchlist_tab()

    with tab_journal:
        journal_tab()

    with tab_setup:
        setup_tab()


if __name__ == "__main__":
    main()
