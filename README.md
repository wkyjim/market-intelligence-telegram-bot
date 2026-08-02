# Market Intelligence Telegram Bot

Standalone FastAPI webhook service for market intelligence Telegram commands, scheduled reports, report-update notifications, and system alerts.

Production URL:

```text
https://market-intelligence-telegram-bot.onrender.com
```

## Security Model

The project uses one shared secret:

```text
TG_WEBHOOK_SECRET
```

The same secret is required in both places for every protected endpoint:

- URL path: `/.../{secret}`
- Header: `X-Telegram-Bot-Api-Secret-Token`

Do not commit `.env`, Telegram tokens, chat IDs, or shared secrets.

## Required Render Environment Variables

```text
TG_TOKEN=
TG_CHAT_ID=
TG_USERNAME=Ai_market_monitor_bot
TG_WEBHOOK_SECRET=
TG_RENDER_BASE_URL=https://market-intelligence-telegram-bot.onrender.com
TG_REPORT_URL=https://wkyjim.github.io/market-dashboard/data/latest-report.md
TG_DASHBOARD_URL=https://wkyjim.github.io/market-dashboard/#overview
MARKET_API_BASE_URL=https://postgresql-us-equities-api.onrender.com
REQUEST_TIMEOUT_SECONDS=30
LOG_LEVEL=INFO
```

`TG_CHAT_ID` must be the numeric Telegram chat ID.

## Local Setup

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Render Setup

Create a Render Web Service from this repository.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Render free-tier warning: the service may cold start. Telegram and scheduled report requests can take longer immediately after idle periods.

## Register Telegram Webhook

PowerShell one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://market-intelligence-telegram-bot.onrender.com/telegram/set-webhook/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

PowerShell multiline:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://market-intelligence-telegram-bot.onrender.com/telegram/set-webhook/<TG_WEBHOOK_SECRET>" `
  -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

Telegram will call:

```text
https://market-intelligence-telegram-bot.onrender.com/telegram/webhook/<TG_WEBHOOK_SECRET>
```

## Telegram Commands

```text
/start
/market
/dashboard
/report
/signals
/sectors
/equity AAPL
/macro
/risk
/status
```

Commands addressed to the bot, such as `/start@Ai_market_monitor_bot`, are normalized to `/start`.

## Structured Report Commands

`/report` is a section browser for the latest published markdown report at `TG_REPORT_URL`.

```text
/report                         Report menu and concise summary
/report executive               Executive summary
/report market                  Market snapshot
/report positioning             Positioning summary
/report sectors                 Sector signals
/report etf                     ETF flow monitor
/report news                    Market news and catalysts, default 5 items
/report news 10                 Market news and catalysts, maximum 10 items
/report opportunities           Opportunity summary
/report risks                   Risk summary
/report changes                 Signal changes
/report full                    Full report split into Telegram-safe parts
/report help                    Report command menu only
```

Group syntax is supported:

```text
/report@Ai_market_monitor_bot etf
/report@Ai_market_monitor_bot news 10
```

The bot uses Telegram HTML parse mode for report messages. Dynamic report content is escaped before sending.

Example `/report` output:

```text
Rule-Based Institutional Market Update
2026-07-21 12:11 HKT

• Regime score: 61 / 100
• US equity strength: 70 / 100

Top buy/overweight sector: Technology (81)
Top sell/underweight sector: Utilities (34)

Available Report Sections
Executive Summary      /report executive
ETF Flows              /report etf
Market News            /report news
```

Example `/report etf` output:

```text
MARKET INTELLIGENCE
ETF FLOW MONITOR

FLOW TABLE
ETF  Flow    Trend
IVV  1.2bn   Inflow
HYG  -250mn  Outflow

KEY IMPLICATIONS
• ETF flows show selective equity demand.
```

Example `/report news` output:

```text
MARKET INTELLIGENCE
MARKET NEWS & CATALYSTS

1. Fed comments drive rates
Summary: Policy uncertainty remains active
Market impact: Mixed
Related assets: Bonds
```

ETF flow extraction looks for report headings such as:

```text
ETF Flow
ETF Flows
Fund Flow
Fund Flows
ETF Fund Flow
ETF Flow Monitor
ETF Flows Analysis
Sector ETF Flow
Thematic ETF Flow
Core Flow Signals
Sector Flow Signals
Subsector PFV Signals
```

News extraction looks for report headings such as:

```text
News
Market News
Latest News
Headlines
Key Headlines
Catalysts
Market Catalysts
Macro News
Geopolitics
Earnings
Company News
News Analytics
Top Market-Moving Headlines
```

The bot does not invent missing report data. If ETF flow or news sections are absent, it returns a clear fallback message. `/report news` uses content already present in `TG_REPORT_URL`; it does not browse the internet.

To extend heading aliases, update these lists in `main.py`:

```python
ETF_FLOW_ALIASES
NEWS_ALIASES
```

## Test Task Endpoints in PowerShell

Morning report one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://market-intelligence-telegram-bot.onrender.com/tasks/send/morning/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

End-of-day report one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://market-intelligence-telegram-bot.onrender.com/tasks/send/eod/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

Report update one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://market-intelligence-telegram-bot.onrender.com/tasks/send/report-update/<TG_WEBHOOK_SECRET>?force=true" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

System alert one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://market-intelligence-telegram-bot.onrender.com/tasks/alert/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>"; "Content-Type" = "application/json" } -Body '{"message":"Macro data update failed","source":"Macro ingestion","severity":"warning"}'
```

## GitHub Actions Scheduling

Workflow:

```text
.github/workflows/telegram-reports.yml
```

GitHub Actions secret required:

```text
TG_WEBHOOK_SECRET
```

Schedule conversion:

- Morning report: `0 22 * * *` UTC = 06:00 HKT
- End-of-day report: `0 10 * * *` UTC = 18:00 HKT

Manual run:

1. Open GitHub Actions.
2. Select `Telegram Reports`.
3. Click `Run workflow`.
4. Choose `morning`, `eod`, or `report-update`.

## Report-Update Integration Example

Call this only after `latest-report.md` has been generated and published.

Reusable GitHub Actions step:

```yaml
- name: Notify Telegram report update
  env:
    TG_WEBHOOK_SECRET: ${{ secrets.TG_WEBHOOK_SECRET }}
    SERVICE_BASE_URL: https://market-intelligence-telegram-bot.onrender.com
  run: |
    curl --fail-with-body --silent --show-error \
      --retry 2 --retry-delay 5 --max-time 120 \
      -X POST \
      -H "X-Telegram-Bot-Api-Secret-Token: ${TG_WEBHOOK_SECRET}" \
      "${SERVICE_BASE_URL}/tasks/send/report-update/${TG_WEBHOOK_SECRET}"
```

Do not trigger this before the published `TG_REPORT_URL` contains the new report.

Report-update state is stored in a small local JSON file by default. On Render, this filesystem state may reset on redeployment or restart, so the next report-update call may send once again.

## System-Alert Integration Examples

Python:

```python
import os
import requests

secret = os.environ["TG_WEBHOOK_SECRET"]
url = f"https://market-intelligence-telegram-bot.onrender.com/tasks/alert/{secret}"

response = requests.post(
    url,
    headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    json={
        "message": "Macro data update failed",
        "source": "Macro ingestion",
        "severity": "warning",
    },
    timeout=30,
)
response.raise_for_status()
```

GitHub Actions:

```yaml
- name: Send Telegram system alert
  env:
    TG_WEBHOOK_SECRET: ${{ secrets.TG_WEBHOOK_SECRET }}
    SERVICE_BASE_URL: https://market-intelligence-telegram-bot.onrender.com
  run: |
    curl --fail-with-body --silent --show-error \
      --retry 2 --retry-delay 5 --max-time 120 \
      -X POST \
      -H "X-Telegram-Bot-Api-Secret-Token: ${TG_WEBHOOK_SECRET}" \
      -H "Content-Type: application/json" \
      --data '{"message":"Macro data update failed","source":"Macro ingestion","severity":"warning"}' \
      "${SERVICE_BASE_URL}/tasks/alert/${TG_WEBHOOK_SECRET}"
```

## Development Tests

```bash
pytest
```

## Manual Test Checklist

After deploy:

1. Open `https://market-intelligence-telegram-bot.onrender.com/health`.
2. Register webhook with the same `TG_WEBHOOK_SECRET` in the path and header.
3. Send `/start` in Telegram.
4. Send `/report`.
5. Send `/report etf`.
6. Send `/report news`.
7. Send `/report news 10`.
8. Send `/report full`.
9. Trigger the morning task endpoint.
10. Trigger the alert endpoint with a test warning.
