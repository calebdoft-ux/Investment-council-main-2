# The Investment Council

A Python/FastAPI application that gives you a private AI advisory firm: 14 specialized advisors across three teams who think in **real time**, run **concurrently**, and brief each other.

## What's different from a simple chatbot

| Feature | This app |
|---|---|
| **Streaming** | Characters appear as they're generated (SSE) |
| **Concurrent advisors** | All 6 investors start simultaneously — fastest appears first |
| **Server-side key** | API key stays in `.env`, never in the browser |
| **SQLite persistence** | Portfolio and journal survive restarts |
| **Real stock data** | yfinance — no fragile CORS hacks |
| **Dynamic date** | Always uses today's date, not a hardcoded one |

## Teams

**Investment Council** (6 advisors)
- David Kessler — Investment Banker (valuation, M&A)
- Rachel Stern — Macro Strategist (rates, credit, dollar)
- Tom Callahan — Geopolitical Analyst (tariffs, risk flows)
- Arjun Mehta — Quantitative Analyst (vol, factor, stats)
- Sarah Park — Tech & Growth Investor (AI, disruption)
- Mark Osei — Risk Manager (sizing, drawdown, stress)
- The Chair — PM Synthesis (structured verdict)

**Research Team** (5 analysts)
- Nina Kovac — Fundamental (moats, intrinsic value)
- Rex Huang — Technical (levels, momentum, volume)
- Zoe Chan — Quantitative Screener (factor anomalies)
- Dev Patel — Thematic (AI, energy, reshoring)
- Cass Rivera — Contrarian (mispriced consensus)

**News Desk** (3 reporters)
- Elena Marsh — Markets Reporter
- James Wei — Macro & Policy
- Priya Das — Corporate (earnings, M&A)

## Modes

- **1-on-1**: Solo advisor with per-token streaming
- **Full Council/Team/Desk**: All advisors run concurrently
- **PM Synthesis**: Council deliberates → Chair delivers structured verdict
- **Debate**: Pick 2–6 members to argue opposite sides
- **Brief Investors**: Research team analyzes → briefs investment council
- **Brief All Teams**: News desk → investors + researchers

## Quick start

```bash
git clone <your-repo>
cd investment-council

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run
python main.py
```

Open [http://localhost:8000](http://localhost:8000)

## Slash commands

| Command | Action |
|---|---|
| `/add AAPL 5000` | Add position ($5000 in AAPL) |
| `/sell AAPL` | Remove position |
| `/watch TSLA` | Add to watchlist |
| `/pm [question]` | PM Synthesis mode |
| `/council` | Full Council mode |
| `/debate` | Open debate picker |
| `/news [headline]` | News desk (all) |
| `/brief [topic]` | News → All Teams |
| `/banker`, `/macro`, `/geo`… | Jump to specific advisor |

## Project structure

```
investment-council/
├── main.py               # FastAPI app entry point
├── config.py             # Environment settings
├── agents/
│   └── council.py        # All 14 agent definitions & system prompts
├── routers/
│   ├── chat.py           # SSE streaming endpoint
│   ├── portfolio.py      # Portfolio REST API
│   ├── journal.py        # Trade journal REST API
│   ├── quotes.py         # Stock quotes (yfinance)
│   └── sessions.py       # Chat session storage
├── db/
│   └── database.py       # Async SQLite operations
└── static/
    ├── index.html        # Frontend (HTML + CSS)
    └── app.js            # Frontend JS (SSE streaming client)
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Your Anthropic API key |
| `MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `MAX_TOKENS` | `1500` | Max tokens per advisor response |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DATABASE_PATH` | `investment_council.db` | SQLite database file |

## Docker

```bash
docker build -t investment-council .
docker run -p 8000:8000 --env-file .env investment-council
```
