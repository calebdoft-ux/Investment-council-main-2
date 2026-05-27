"""
Investment Council — Streamlit frontend.
Replaces static/index.html + static/app.js entirely.
Run with:  streamlit run streamlit_app.py
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import uuid
from datetime import date, datetime

import anthropic
import streamlit as st

from agents.council import (
    ALL_AGENTS, INVESTORS, NEWS_DESK, RESEARCHERS,
    DEBATE_EXTRA, get_agent,
)
from config import settings
from db.database import (
    add_journal_entry, delete_journal_entry, delete_position, delete_session,
    get_briefing, get_cash, get_journal, get_portfolio, get_sessions,
    init_db, set_briefing, set_cash, upsert_position, upsert_session,
    update_position_price,
)
from routers.quotes import fetch_quote

# ─── Constants ────────────────────────────────────────────────────────────────

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}
WEB_SEARCH_BETA = "web-search-2025-03-05"
MAX_HISTORY_PAIRS = 8
PRIOR_RESP_MAX_CHARS = 1200

# ─── Async bridge ─────────────────────────────────────────────────────────────

def arun(coro):
    """Run an async coroutine from sync Streamlit context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── One-time DB init ─────────────────────────────────────────────────────────

@st.cache_resource
def _init_db():
    arun(init_db())

_init_db()

# ─── Page config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Investment Council",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Session state ────────────────────────────────────────────────────────────

_DEFAULTS: dict = {
    "messages": [],        # list of message dicts (see add_to_history / add_block)
    "team": "inv",
    "mode": "all",
    "advisor_id": "banker",
    "debate_members": [],
    "session_id": str(uuid.uuid4()),
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─── Anthropic helpers ────────────────────────────────────────────────────────

def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def trim_messages(messages: list[dict]) -> list[dict]:
    clean = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return clean[-(MAX_HISTORY_PAIRS * 2):]


def run_agent(agent, prompt: str, pf_ctx: str, system_extra: str = "") -> str:
    """Blocking single-agent call. Used for concurrent/PM/brief modes."""
    client = make_client()
    response = client.messages.create(
        model=settings.MODEL,
        max_tokens=settings.MAX_TOKENS,
        system=agent.system + system_extra + pf_ctx,
        messages=[{"role": "user", "content": prompt}],
        tools=[WEB_SEARCH_TOOL],
        extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
    )
    return "".join(b.text for b in response.content if hasattr(b, "text"))


def stream_agent_tokens(agent, messages_or_prompt, pf_ctx: str, system_extra: str = ""):
    """Generator yielding text tokens. Used for solo and round-table modes."""
    client = make_client()
    if isinstance(messages_or_prompt, str):
        msgs = [{"role": "user", "content": messages_or_prompt}]
    else:
        msgs = trim_messages(messages_or_prompt)
    with client.messages.stream(
        model=settings.MODEL,
        max_tokens=settings.MAX_TOKENS,
        system=agent.system + system_extra + pf_ctx,
        messages=msgs,
        tools=[WEB_SEARCH_TOOL],
        extra_headers={"anthropic-beta": WEB_SEARCH_BETA},
    ) as stream:
        yield from stream.text_stream


# ─── Portfolio context ────────────────────────────────────────────────────────

def build_portfolio_context() -> str:
    from agents.council import TODAY
    portfolio = arun(get_portfolio())
    cash = arun(get_cash())
    briefing = arun(get_briefing())

    ctx = f"\n\nINVESTOR PROFILE:\n{briefing.strip()}" if briefing.strip() else ""
    if not portfolio and not cash:
        return ctx

    total = sum(p["size"] for p in portfolio.values())
    lines = []
    total_pnl = 0.0
    has_pnl = False
    for tk, p in portfolio.items():
        pct = f"{(p['size'] / total * 100):.1f}" if total else "?"
        line = f"  {tk}: ${p['size']:,.0f} invested @ ${p['entry'] or '?'} entry ({pct}% of book)"
        if p.get("entry") and p.get("price"):
            pnl = (p["size"] / p["entry"]) * p["price"] - p["size"]
            pp = pnl / p["size"] * 100
            total_pnl += pnl
            has_pnl = True
            line += (f"  |  current ~${p['price']:.2f},"
                     f"  P&L {'+' if pnl >= 0 else ''}${pnl:.0f} ({'+' if pnl >= 0 else ''}{pp:.1f}%)")
        lines.append(line)

    ctx += f"\n\nPORTFOLIO ({TODAY}):\nTotal invested: ${total:,.0f}  |  Cash: ${cash:,.2f}"
    if has_pnl:
        ctx += f"  |  Unrealized P&L: {'+' if total_pnl >= 0 else ''}${total_pnl:.0f}"
    if lines:
        ctx += "\nPositions:\n" + "\n".join(lines)
    ctx += "\n\nAlways factor in cash availability and existing positions when advising."
    return ctx


# ─── Message history ──────────────────────────────────────────────────────────

def add_to_history(role: str, content: str, agent=None):
    entry: dict = {"role": role, "content": content}
    if agent:
        entry.update({"agent_id": agent.id, "agent_name": agent.name,
                      "agent_emoji": agent.emoji, "agent_role": agent.role})
    st.session_state.messages.append(entry)


def add_block(label: str, entries: list[tuple]):
    """Store a council/concurrent block for replay. entries = [(agent, text), ...]"""
    st.session_state.messages.append({
        "role": "council_block",
        "label": label,
        "entries": [
            {"agent_id": a.id, "agent_name": a.name,
             "agent_emoji": a.emoji, "agent_role": a.role, "content": t}
            for a, t in entries
        ],
    })


def display_history():
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            with st.chat_message("user"):
                st.markdown(msg["content"])

        elif msg["role"] == "assistant":
            emoji = msg.get("agent_emoji", "🤖")
            name = msg.get("agent_name", "Advisor")
            with st.chat_message(name, avatar=emoji):
                st.caption(msg.get("agent_role", ""))
                st.markdown(msg["content"])

        elif msg["role"] == "council_block":
            with st.expander(msg["label"], expanded=False):
                for i, entry in enumerate(msg["entries"]):
                    st.markdown(f"**{entry['agent_emoji']} {entry['agent_name']}** · *{entry['agent_role']}*")
                    st.markdown(entry["content"])
                    if i < len(msg["entries"]) - 1:
                        st.divider()


# ─── Chat mode implementations ────────────────────────────────────────────────

def do_solo(prompt: str, pf_ctx: str):
    agent = get_agent(st.session_state.advisor_id)
    if not agent:
        st.error("No advisor selected.")
        return
    history = trim_messages(st.session_state.messages)
    with st.chat_message(agent.name, avatar=agent.emoji):
        st.caption(agent.role)
        text = st.write_stream(stream_agent_tokens(agent, history, pf_ctx))
    add_to_history("assistant", text, agent)


def do_concurrent(agents: list, prompt: str, pf_ctx: str, label: str,
                  system_extra: str = "") -> list[tuple]:
    """Run agents in parallel threads; display placeholders as each finishes."""
    pairs: list[tuple] = []

    with st.expander(label, expanded=True):
        agent_slots = []
        for agent in agents:
            st.markdown(f"**{agent.emoji} {agent.name}** · *{agent.role}*")
            slot = st.empty()
            slot.markdown("*Thinking…*")
            agent_slots.append((agent, slot))
            st.divider()

    def _run(agent):
        return run_agent(agent, prompt, pf_ctx, system_extra)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as pool:
        future_map = {pool.submit(_run, agent): (agent, slot)
                      for agent, slot in agent_slots}
        for future in concurrent.futures.as_completed(future_map):
            agent, slot = future_map[future]
            try:
                text = future.result()
                slot.markdown(text)
                pairs.append((agent, text))
            except Exception as exc:
                slot.error(f"Error: {exc}")

    return pairs


def do_round_table(agents: list, prompt: str, pf_ctx: str, label: str):
    prior: list[str] = []
    pairs: list[tuple] = []

    with st.expander(label, expanded=True):
        for i, agent in enumerate(agents):
            st.markdown(f"**{agent.emoji} {agent.name}** · *{agent.role}*")
            if prior:
                capped = [
                    t if len(t) <= PRIOR_RESP_MAX_CHARS else t[:PRIOR_RESP_MAX_CHARS] + "… [see above]"
                    for t in prior
                ]
                msg = (
                    f"{prompt}\n\n---\n"
                    f"Your colleagues have already weighed in:\n\n{chr(10).join(capped)}\n\n---\n"
                    "Now add YOUR analysis. Engage with what was said — "
                    "agree, disagree, or bring a new perspective. Be direct and specific."
                )
            else:
                msg = prompt

            text = st.write_stream(stream_agent_tokens(agent, msg, pf_ctx))
            prior.append(f"{agent.name} ({agent.tag}):\n{text}")
            pairs.append((agent, text))
            if i < len(agents) - 1:
                st.divider()

    add_block(label, pairs)


def do_pm(prompt: str, pf_ctx: str):
    council = [a for a in INVESTORS if a.id != "chair"]
    chair = get_agent("chair")

    pairs = do_concurrent(council, prompt, pf_ctx, "⚡ COUNCIL DELIBERATION")
    add_block("⚡ COUNCIL DELIBERATION", pairs)

    if not pairs:
        return

    summaries = [f"{a.name} ({a.tag}):\n{t}" for a, t in pairs]
    chair_prompt = (
        f'Question posed to council: "{prompt}"\n\n'
        f"Council views:\n\n{chr(10).join('---'.join(['', s, '']) for s in summaries)}\n\n"
        "Now deliver your PM synthesis."
    )

    with st.chat_message(chair.name, avatar=chair.emoji):
        st.caption(chair.role)
        text = st.write_stream(stream_agent_tokens(chair, chair_prompt, pf_ctx))
    add_to_history("assistant", text, chair)


def do_research_brief(prompt: str, pf_ctx: str):
    pairs = do_concurrent(RESEARCHERS, prompt, pf_ctx, "🔬 RESEARCH ANALYSIS")
    add_block("🔬 RESEARCH ANALYSIS", pairs)

    if not pairs:
        return

    brief = "\n\n---\n\n".join(f"{a.name} ({a.tag}):\n{t}" for a, t in pairs)
    investor_prompt = (
        f'The research team analyzed: "{prompt}"\n\nFindings:\n\n{brief}\n\n'
        "As an investor, react to this briefing. Should we act? Buy, watch, or pass?"
    )
    investors = [a for a in INVESTORS if a.id != "chair"]
    inv_pairs = do_concurrent(investors, investor_prompt, pf_ctx, "📤 INVESTOR REACTION")
    add_block("📤 INVESTOR REACTION", inv_pairs)


def do_news_brief(prompt: str, pf_ctx: str):
    pairs = do_concurrent(NEWS_DESK, prompt, pf_ctx, "📡 NEWS DESK ANALYSIS")
    add_block("📡 NEWS DESK ANALYSIS", pairs)

    if not pairs:
        return

    news_summary = "\n\n---\n\n".join(f"{a.name}:\n{t}" for a, t in pairs)
    inv_prompt = (
        f"NEWS DESK BRIEFING:\n\n{news_summary}\n\nHeadline: \"{prompt}\"\n\n"
        "How does this affect our portfolio and investment outlook?"
    )
    res_prompt = (
        f"NEWS DESK BRIEFING:\n\n{news_summary}\n\nNews: \"{prompt}\"\n\n"
        "Does this create research opportunities or change sector outlook?"
    )
    inv_pairs = do_concurrent(INVESTORS[:3], inv_prompt, pf_ctx, "⚡ INVESTOR REACTION")
    add_block("⚡ INVESTOR REACTION", inv_pairs)

    res_pairs = do_concurrent([RESEARCHERS[0], RESEARCHERS[3]], res_prompt, pf_ctx,
                               "🔬 RESEARCH REACTION")
    add_block("🔬 RESEARCH REACTION", res_pairs)


def do_debate(prompt: str, pf_ctx: str):
    members = st.session_state.debate_members
    if len(members) < 2:
        st.error("Select at least 2 members to start a debate.")
        return
    agents = [a for a in (get_agent(m) for m in members) if a]
    label = "⚔️ DEBATE — " + " vs ".join(a.name.split()[0].upper() for a in agents)
    pairs = do_concurrent(agents, prompt, pf_ctx, label, DEBATE_EXTRA)
    add_block(label, pairs)


def save_session(prompt: str):
    if st.session_state.messages:
        arun(upsert_session(
            st.session_state.session_id,
            prompt[:60],
            st.session_state.team,
            st.session_state.messages,
        ))


# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🏦 Investment Council")
    st.divider()

    team = st.radio(
        "**Team**",
        ["inv", "res", "news"],
        format_func=lambda x: {"inv": "🎩 Investors", "res": "🔬 Research", "news": "📡 News Desk"}[x],
        key="team",
    )

    if team == "inv":
        mode_opts = {"all": "⚡ Full Council", "round": "🔄 Round Table",
                     "pm": "🏦 PM Mode", "solo": "👤 Solo", "debate": "⚔️ Debate"}
    elif team == "res":
        mode_opts = {"all": "🔬 Full Research", "round": "🔄 Research Dialogue",
                     "brief": "📤 Brief Investors", "solo": "👤 Solo"}
    else:
        mode_opts = {"all": "📡 Full Desk", "round": "🔄 News Dialogue",
                     "brief": "📰 Brief All Teams", "solo": "👤 Solo"}

    # Reset mode if not valid for new team
    if st.session_state.mode not in mode_opts:
        st.session_state.mode = "all"

    mode = st.radio(
        "**Mode**",
        list(mode_opts.keys()),
        format_func=lambda x: mode_opts[x],
        key="mode",
    )

    if mode == "solo":
        pool = {"inv": INVESTORS, "res": RESEARCHERS, "news": NEWS_DESK}[team]
        advisor_opts = {a.id: f"{a.emoji} {a.name}" for a in pool}
        if st.session_state.advisor_id not in advisor_opts:
            st.session_state.advisor_id = list(advisor_opts.keys())[0]
        st.selectbox("**Advisor**", list(advisor_opts.keys()),
                     format_func=lambda x: advisor_opts[x], key="advisor_id")

    if mode == "debate" and team == "inv":
        debate_pool = [a for a in INVESTORS if a.id != "chair"]
        debate_opts = {a.id: f"{a.emoji} {a.name}" for a in debate_pool}
        st.multiselect("**Debaters** (pick 2+)", list(debate_opts.keys()),
                       format_func=lambda x: debate_opts[x], key="debate_members")

    st.divider()

    if st.button("＋ New Chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

    sessions = arun(get_sessions())
    if sessions:
        st.markdown("**History**")
        for sess in sessions[:15]:
            c1, c2 = st.columns([5, 1])
            preview = (sess.get("preview") or "Chat")[:28]
            with c1:
                if st.button(preview, key=f"s_{sess['id']}", use_container_width=True):
                    st.session_state.messages = sess.get("messages", [])
                    st.session_state.session_id = sess["id"]
                    st.rerun()
            with c2:
                if st.button("✕", key=f"d_{sess['id']}"):
                    arun(delete_session(sess["id"]))
                    st.rerun()


# ─── Main tabs ────────────────────────────────────────────────────────────────

tab_chat, tab_pf, tab_journal, tab_setup = st.tabs(
    ["💬 Chat", "📊 Portfolio", "📓 Journal", "⚙️ Setup"]
)

# ══════════════════════════════════════════════════════════════════════════════
# CHAT TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_chat:
    display_history()

    if not st.session_state.messages:
        st.markdown(
            "### 🏦 Investment Council\n"
            "Three specialized teams of AI advisors — Investors, Research, and News Desk. "
            "Select your team and mode in the sidebar, then ask anything.\n\n"
            "**Try:** *What's the macro outlook right now?* · "
            "*Find me an undervalued stock* · "
            "*Break down today's market news*"
        )

    if prompt := st.chat_input("Ask the council…"):
        with st.chat_message("user"):
            st.markdown(prompt)
        add_to_history("user", prompt)

        pf_ctx = build_portfolio_context()
        _team = st.session_state.team
        _mode = st.session_state.mode

        if _mode == "solo":
            do_solo(prompt, pf_ctx)

        elif _team == "inv":
            if _mode == "all":
                agents = [a for a in INVESTORS if a.id != "chair"]
                pairs = do_concurrent(agents, prompt, pf_ctx, "⚡ INVESTMENT COUNCIL")
                add_block("⚡ INVESTMENT COUNCIL", pairs)
            elif _mode == "round":
                agents = [a for a in INVESTORS if a.id != "chair"]
                do_round_table(agents, prompt, pf_ctx, "🔄 ROUND TABLE")
            elif _mode == "pm":
                do_pm(prompt, pf_ctx)
            elif _mode == "debate":
                do_debate(prompt, pf_ctx)

        elif _team == "res":
            if _mode == "all":
                pairs = do_concurrent(RESEARCHERS, prompt, pf_ctx, "🔬 RESEARCH TEAM")
                add_block("🔬 RESEARCH TEAM", pairs)
            elif _mode == "round":
                do_round_table(RESEARCHERS, prompt, pf_ctx, "🔄 RESEARCH DIALOGUE")
            elif _mode == "brief":
                do_research_brief(prompt, pf_ctx)

        elif _team == "news":
            if _mode == "all":
                pairs = do_concurrent(NEWS_DESK, prompt, pf_ctx, "📡 NEWS DESK")
                add_block("📡 NEWS DESK", pairs)
            elif _mode == "round":
                do_round_table(NEWS_DESK, prompt, pf_ctx, "🔄 NEWS DIALOGUE")
            elif _mode == "brief":
                do_news_brief(prompt, pf_ctx)

        save_session(prompt)

# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_pf:
    import pandas as pd

    st.subheader("Portfolio")

    portfolio = arun(get_portfolio())
    cash = arun(get_cash())

    total_inv = sum(p["size"] for p in portfolio.values())
    has_pnl_data = any(p.get("price") and p.get("entry") for p in portfolio.values())
    total_pnl = sum(
        (p["size"] / p["entry"]) * p["price"] - p["size"]
        for p in portfolio.values()
        if p.get("price") and p.get("entry")
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Invested", f"${total_inv:,.0f}")
    c2.metric("Cash", f"${cash:,.2f}")
    c3.metric("Positions", len(portfolio))
    if has_pnl_data:
        c4.metric("Unrealized P&L", f"${total_pnl:+,.0f}",
                  delta_color="normal" if total_pnl >= 0 else "inverse")
    else:
        c4.metric("Unrealized P&L", "—")

    st.divider()

    if portfolio:
        rows = []
        for tk, p in portfolio.items():
            pnl = pnl_pct = None
            if p.get("entry") and p.get("price"):
                pnl = (p["size"] / p["entry"]) * p["price"] - p["size"]
                pnl_pct = pnl / p["size"] * 100
            rows.append({
                "Ticker": tk,
                "Invested": f"${p['size']:,.0f}",
                "Entry": f"${p['entry']:,.2f}" if p.get("entry") else "—",
                "Current": f"${p['price']:,.2f}" if p.get("price") else "—",
                "P&L": f"${pnl:+,.0f}" if pnl is not None else "—",
                "P&L %": f"{pnl_pct:+.1f}%" if pnl_pct is not None else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.info("No positions yet.")

    col_left, col_right = st.columns(2)

    with col_left:
        with st.expander("➕ Add / Update Position"):
            with st.form("add_pos"):
                r1c1, r1c2, r1c3 = st.columns(3)
                ticker_in = r1c1.text_input("Ticker").strip().upper()
                size_in = r1c2.number_input("Amount ($)", min_value=0.0, step=100.0)
                entry_in = r1c3.number_input("Entry Price ($)", min_value=0.0)
                if st.form_submit_button("Add Position", use_container_width=True):
                    if not ticker_in:
                        st.error("Enter a ticker.")
                    elif size_in <= 0:
                        st.error("Enter an amount.")
                    else:
                        arun(upsert_position(ticker_in, size_in, entry_in or None, None, None))
                        st.success(f"{ticker_in} added.")
                        st.rerun()

        if portfolio:
            with st.expander("🗑 Remove Position"):
                ticker_del = st.selectbox("Ticker", list(portfolio.keys()), key="ticker_del")
                if st.button("Confirm Remove", type="secondary"):
                    arun(delete_position(ticker_del))
                    st.rerun()

        with st.expander("💵 Update Cash"):
            with st.form("cash_form"):
                new_cash = st.number_input("Cash ($)", min_value=0.0,
                                           value=float(cash), step=100.0)
                if st.form_submit_button("Save", use_container_width=True):
                    arun(set_cash(new_cash))
                    st.success("Cash updated.")
                    st.rerun()

    with col_right:
        with st.expander("🔍 Stock Screener", expanded=True):
            search_sym = st.text_input("Ticker", placeholder="AAPL", key="screener_sym")
            if st.button("Search", key="screener_btn") and search_sym:
                with st.spinner("Fetching quote…"):
                    quote = arun(fetch_quote(search_sym.strip().upper()))
                if quote:
                    q1, q2, q3 = st.columns(3)
                    q1.metric("Price", f"${quote['price']:.2f}",
                              f"{quote['change_pct']:+.2f}%")
                    q2.metric("Day High",
                              f"${quote['day_high']:.2f}" if quote.get("day_high") else "—")
                    q3.metric("Day Low",
                              f"${quote['day_low']:.2f}" if quote.get("day_low") else "—")
                    q4, q5, q6 = st.columns(3)
                    q4.metric("P/E", f"{quote['pe']:.1f}" if quote.get("pe") else "—")
                    q5.metric("52W High",
                              f"${quote['high_52']:.2f}" if quote.get("high_52") else "—")
                    q6.metric("52W Low",
                              f"${quote['low_52']:.2f}" if quote.get("low_52") else "—")
                    st.caption(f"**{quote.get('name', search_sym)}** · {quote.get('exchange', '')}")
                else:
                    st.error("Could not fetch quote.")

        if portfolio:
            with st.expander("↺ Refresh Prices"):
                if st.button("Fetch Live Prices", use_container_width=True):
                    tickers = list(portfolio.keys())
                    with st.spinner(f"Fetching {len(tickers)} quotes…"):
                        quotes = arun(
                            __import__("asyncio").gather(*[fetch_quote(t) for t in tickers])
                        )
                    updated = 0
                    for tk, q in zip(tickers, quotes):
                        if q and q.get("price"):
                            arun(update_position_price(tk, q["price"]))
                            updated += 1
                    st.success(f"Updated {updated} prices.")
                    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# JOURNAL TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_journal:
    st.subheader("Trade Journal")

    with st.expander("📝 Log a Trade", expanded=True):
        with st.form("log_trade"):
            jc1, jc2, jc3, jc4 = st.columns(4)
            j_ticker = jc1.text_input("Ticker").strip().upper()
            j_type = jc2.selectbox("Type", ["buy", "sell", "watch"])
            j_price = jc3.number_input("Price ($)", min_value=0.0)
            j_size = jc4.number_input("Size ($)", min_value=0.0)
            j_date = st.date_input("Date", value=date.today())
            j_thesis = st.text_area(
                "Thesis",
                placeholder="Why this trade? What's the catalyst? What would make you wrong?",
            )
            if st.form_submit_button("Log Trade", use_container_width=True):
                if not j_ticker:
                    st.error("Enter a ticker.")
                else:
                    now = datetime.now()
                    arun(add_journal_entry({
                        "id": str(int(now.timestamp() * 1000)),
                        "ticker": j_ticker,
                        "type": j_type,
                        "price": j_price or None,
                        "size": j_size or None,
                        "date": j_date.strftime("%-m/%-d/%Y"),
                        "thesis": j_thesis,
                        "created_at": now.isoformat(),
                    }))
                    st.success("Trade logged.")
                    st.rerun()

    entries = arun(get_journal())
    if entries:
        for entry in entries:
            type_colors = {"buy": "green", "sell": "red", "watch": "orange"}
            color = type_colors.get(entry["type"], "gray")
            with st.container(border=True):
                hc1, hc2 = st.columns([8, 1])
                with hc1:
                    price_str = f"${entry['price']:,.2f}" if entry.get("price") else "—"
                    size_str = f"${entry['size']:,.0f}" if entry.get("size") else "—"
                    st.markdown(
                        f"**{entry['ticker']}** :{color}[{entry['type'].upper()}]"
                        f" · {entry.get('date', '')} · {price_str} · {size_str}"
                    )
                    if entry.get("thesis"):
                        st.caption(entry["thesis"])
                with hc2:
                    if st.button("Delete", key=f"jdel_{entry['id']}", type="secondary"):
                        arun(delete_journal_entry(entry["id"]))
                        st.rerun()
    else:
        st.info("No trades logged yet.")

# ══════════════════════════════════════════════════════════════════════════════
# SETUP TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_setup:
    st.subheader("Investor Profile")
    st.markdown(
        "Tell the advisors who you are and what you're trying to achieve. "
        "This context is included in every conversation."
    )

    current_briefing = arun(get_briefing())

    with st.form("setup"):
        profile = st.text_area(
            "Your profile",
            value=current_briefing,
            height=220,
            placeholder=(
                "Example: I'm a 35-year-old software engineer with $250K invested. "
                "Time horizon is 10+ years. I can tolerate high volatility. "
                "I'm focused on tech and AI infrastructure. "
                "I don't want to hold more than 8 positions…"
            ),
        )
        if st.form_submit_button("Save Profile", use_container_width=True):
            arun(set_briefing(profile))
            st.success("Profile saved. Advisors will use this in all future conversations.")
