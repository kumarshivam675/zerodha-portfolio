# Portfolio

Self-hosted Zerodha portfolio tracker with an AI analyst. It syncs your holdings through Kite
Connect, saves daily snapshots to Postgres, refreshes end-of-day prices on a schedule, computes
XIRR from your tradebook, and adds a Claude-powered analyst that can query your data, research any
holding on the web, and write a daily brief.

> **Not investment advice.** This is a personal, educational tool. The analyst's output is
> AI-generated, can be wrong or out of date, and is not a recommendation to buy or sell any
> security. Verify anything important yourself, and consult a SEBI-registered adviser for advice.

## Requirements

- Docker with Docker Compose
- A Zerodha account and a free **Kite Connect Personal** app ([developers.kite.trade](https://developers.kite.trade))
- An **Anthropic API key** ([console.anthropic.com](https://console.anthropic.com)) for the analyst and daily brief.
  Everything else works without it.

```
apps/
  api/   FastAPI + SQLAlchemy (Python 3.12) — Kite sync, prices, snapshots, agent, scheduler
  web/   Next.js 16 (App Router) — dashboard, snapshots, analyst chat; proxies /api/* to the API
infra/postgres/  init script (creates the agent's read-only DB role)
docker-compose.yml  db · api · worker · web
```

## Setup

1. **Kite Connect app.** At <https://developers.kite.trade> create a *Personal* app (free) and set its
   **Redirect URL** to `http://localhost:3300/api/kite/callback`.
2. **Configure.** `cp .env.example .env` and fill in `KITE_API_KEY`, `KITE_API_SECRET`,
   `ANTHROPIC_API_KEY`. Change the Postgres passwords *before* the first start (they're only read
   when the database volume is created).
3. **Run.** `docker compose up -d --build`, then open <http://localhost:3300> and click
   **Connect Zerodha**.

Port 3300 taken? Set `WEB_PORT` and `WEB_URL` in `.env` and update the Kite redirect URL to match.

## How it works

| Piece | What it does |
|---|---|
| Zerodha login | `/api/kite/login` → Kite → `/api/kite/callback` stores the access token and syncs holdings (equity + mutual funds). Kite tokens expire every day at ~6 AM IST, so log in again to refresh holdings. |
| Snapshots | **Save today's snapshot** freezes the current holdings for today's date (saving again the same day replaces it). The daily job also saves one automatically (`AUTO_SNAPSHOT`). |
| Daily job (`worker`) | Runs at `DAILY_JOB_CRON` (default 16:30 IST, Mon–Fri): re-syncs from Zerodha if today's session is valid, downloads EOD candles from Yahoo Finance for every stock, reprices holdings, saves a snapshot, then has the analyst write a **daily brief** (`AUTO_BRIEF`). Trigger it manually from the dashboard. |
| Privacy mode | Position-sized amounts (values, invested, P&L, quantities) show as `***` by default. Any eye button reveals them on every screen; the choice is remembered per browser. Percentages, market prices, average cost and today's movers stay visible. In the analyst's replies, only figures it tags as yours (`{{…}}`) are hidden, so prices, targets and recommendations stay readable. Handy for screenshots and screen shares. |
| XIRR | Kite's API has no XIRR or trade history (only today's trades), so import your **Console → Reports → Tradebook** CSVs (one per year) from the dashboard. XIRR is calculated per holding and for the portfolio from those cash flows plus today's value. New trades are picked up on every Zerodha sync. Holdings the tradebook only partly explains (older trades, bonus/split, transfer-in) are marked ≈. Dividends aren't included. |
| Analyse (holdings table) | Each holding has an **Analyse** button. It opens a side panel where the analyst researches that instrument: 3- and 6-month price action, technicals (computed server-side), fundamentals, company news, and macro/geopolitical drivers, ending with a verdict for your position. You can ask follow-ups in the panel, and each analysis is saved in the Analyst history. |
| Slack notifications | Set `SLACK_WEBHOOK_URL` (a Slack Incoming Webhook) to get a message for every analyst answer (Analyse runs, Analyst-page questions, follow-ups) and every **daily brief**. Long reports arrive as a one-minute digest (verdict and confidence, key figures, 3–4 short sections, what to watch) written by `SLACK_DIGEST_MODEL`, with a link to the full report in the app; short answers arrive as-is. Your own figures are masked (`SLACK_HIDE_AMOUNTS`). Test with `curl -X POST localhost:3300/api/slack/test`. |
| Analyst | Claude (`AGENT_MODEL`, default `claude-opus-5-5`) with tools for portfolio, snapshots, price history, stock fundamentals, read-only SQL, plus web search/fetch. Conversations are stored in Postgres. |

### Data notes and limits

- **Prices come from Yahoo Finance**, not Kite: the free Kite Personal API has no market-data
  endpoints. To use Kite quotes and history instead, you'd need the paid ₹500/month Connect plan.
- Mutual fund NAVs only update when you sync from Zerodha.
- The agent's SQL tool connects as a separate `agent_ro` Postgres role. That role has SELECT access to
  the portfolio tables only, so it can't read the stored Kite token or write anything.
- There's no login on the web app. All ports bind to `127.0.0.1` only, so keep it local or put auth
  in front before exposing it.
- Tables are created on startup (`create_all`); there are no migrations yet.

## Costs

- **Zerodha:** the Kite Connect Personal API is free. Holdings, orders and portfolio data are
  included; market data isn't, which is why prices come from Yahoo Finance.
- **Anthropic:** you pay per token on your own API key. A chat question is usually cheap. A deep-dive
  **Analyse** run is the expensive part: it makes 20–40 tool calls, including many web searches (billed
  separately), and runs on Claude Opus at high effort. The daily brief runs once per weekday. To cut
  costs, set `AGENT_MODEL=claude-sonnet-5-5` and/or `AGENT_EFFORT=medium` in `.env`, or `AUTO_BRIEF=false`.
  Check actual spend in the Anthropic Console.

## Security and privacy

- Everything runs on your machine. Your holdings, trades and conversations live in a local Postgres
  volume. Anthropic receives what the analyst sends it (portfolio figures and your questions) to
  answer you.
- Keep `.env` out of version control (it is git-ignored). If a key leaks, regenerate it in the
  Kite developer console or Anthropic Console.
- The app has **no login**. Ports bind to `127.0.0.1` only. Don't expose it to a network without
  putting authentication in front.
- Yahoo Finance data is for personal use; don't redistribute it, and don't redistribute Kite API data
  (exchange data-vending rules).

## Useful commands

```bash
docker compose logs -f api worker        # backend + scheduler logs
docker compose exec db psql -U portfolio # SQL shell (also on 127.0.0.1:5433)
curl -X POST localhost:3300/api/jobs/daily/run   # run the daily job now
```

## License

[MIT](LICENSE)
