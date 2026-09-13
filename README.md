# Market Intelligence Telegram Bot

Standalone FastAPI webhook service for market intelligence Telegram commands, scheduled reports, report-update notifications, and system alerts.

Production URL:

```text
https://bot.138.2.69.165.sslip.io
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

## Required Service Environment Variables

```text
TG_TOKEN=
TG_CHAT_ID=
TG_USERNAME=Ai_market_monitor_bot
TG_WEBHOOK_SECRET=
TG_RENDER_BASE_URL=https://bot.138.2.69.165.sslip.io
TG_REPORT_URL=https://wkyjim.github.io/market-dashboard/data/latest-report.md
TG_DASHBOARD_URL=https://wkyjim.github.io/market-dashboard/#overview
MARKET_API_BASE_URL=https://api.138.2.69.165.sslip.io
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

## Service Setup

Deploy this repository as the Telegram bot service. The current production deployment runs on Oracle Cloud.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

If deployed on a service that sleeps when idle, Telegram and scheduled report requests can take longer during a cold start.

## Register Telegram Webhook

PowerShell one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://bot.138.2.69.165.sslip.io/telegram/set-webhook/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

PowerShell multiline:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://bot.138.2.69.165.sslip.io/telegram/set-webhook/<TG_WEBHOOK_SECRET>" `
  -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

Telegram will call:

```text
https://bot.138.2.69.165.sslip.io/telegram/webhook/<TG_WEBHOOK_SECRET>
```

## Telegram Commands

```text
/start
/market
/dashboard
/report
/report_executive
/report_market
/report_positioning
/report_sectors
/report_etf
/report_news
/report_news_10
/report_opportunities
/report_risks
/report_changes
/report_full
/report_help
/signals
/sectors
/equity AAPL
/risk
/status
```

Commands addressed to the bot, such as `/report_etf@Ai_market_monitor_bot` or `/equity@Ai_market_monitor_bot AAPL`, are normalized by stripping the bot username suffix before matching the command.

## BotFather Command List

Paste this block into BotFather:

```text
start - Show available commands and bot features
market - Show the latest market snapshot
dashboard - Open the latest executive dashboard
report - Browse the latest report by section
report_executive - Show the executive summary and market regime
report_market - Show the cross-asset market snapshot
report_positioning - Show current asset and regional positioning
report_sectors - Show sector rankings, top buy, and top sell signals
report_etf - Show ETF inflows, outflows, and flow implications
report_news - Show the five most material market news items and catalysts
report_news_10 - Show up to ten market news items and catalysts
report_opportunities - Show the highest-conviction investment opportunities
report_risks - Show the main market risks and warning signals
report_changes - Show recent signal, positioning, flow, and risk changes
report_full - Show the complete report in structured messages
report_help - Show all available report subsections
signals - Show the latest buy and sell signals
sectors - Show the top buy sector and top sell sector
equity - Look up the latest data and signals for a stock
risk - Show current market and system risk alerts
status - Show bot, API, database, report, and data-update status
```

## Structured Report Commands

`/report` is a section browser for the latest published markdown report at `TG_REPORT_URL`.

```text
/report                         Report menu and concise summary
/report_executive               Executive summary
/report_market                  Market snapshot
/report_positioning             Positioning summary
/report_sectors                 Sector signals
/report_etf                     ETF flow monitor
/report_news                    Market news and catalysts, default 5 items
/report_news_10                 Market news and catalysts, maximum 10 items
/report_opportunities           Opportunity summary
/report_risks                   Risk summary
/report_changes                 Signal changes
/report_full                    Full report split into Telegram-safe parts
/report_help                    Report command menu only
```

Group syntax is supported:

```text
/report_etf@Ai_market_monitor_bot
/report_news@Ai_market_monitor_bot
/equity@Ai_market_monitor_bot AAPL
```

The bot uses Telegram HTML parse mode for report messages. Dynamic report content is escaped before sending.

Example `/report` output:

```text
Rule-Based Institutional Market Update
2026-07-21 12:11 HKT

? Regime score: 61 / 100
? US equity strength: 70 / 100

Top buy/overweight sector: Technology (81)
Top sell/underweight sector: Utilities (34)

Available Report Sections
Executive Summary      /report_executive
ETF Flows              /report_etf
Market News            /report_news
```

Example `/report_etf` output:

```text
MARKET INTELLIGENCE
ETF FLOW MONITOR

FLOW TABLE
ETF  Flow    Trend
IVV  1.2bn   Inflow
HYG  -250mn  Outflow

KEY IMPLICATIONS
? ETF flows show selective equity demand.
```

Example `/report_news` output:

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

The bot does not invent missing report data. If ETF flow or news sections are absent, it returns a clear fallback message. `/report_news` uses content already present in `TG_REPORT_URL`; it does not browse the internet.

To extend heading aliases, update these lists in `main.py`:

```python
ETF_FLOW_ALIASES
NEWS_ALIASES
```

## Test Task Endpoints in PowerShell

Morning report one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://bot.138.2.69.165.sslip.io/tasks/send/morning/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

End-of-day report one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://bot.138.2.69.165.sslip.io/tasks/send/eod/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

Report update one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://bot.138.2.69.165.sslip.io/tasks/send/report-update/<TG_WEBHOOK_SECRET>?force=true" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>" }
```

System alert one-line:

```powershell
Invoke-RestMethod -Method Post -Uri "https://bot.138.2.69.165.sslip.io/tasks/alert/<TG_WEBHOOK_SECRET>" -Headers @{ "X-Telegram-Bot-Api-Secret-Token" = "<TG_WEBHOOK_SECRET>"; "Content-Type" = "application/json" } -Body '{"message":"Macro data update failed","source":"Macro ingestion","severity":"warning"}'
```

## Troubleshooting Telegram Delivery

If GitHub Actions reaches the bot service but fails with:

```text
{"detail":"Telegram delivery failed: Forbidden: the bot can't send messages to the bot"}
```

then `TG_CHAT_ID` is wrong. It is likely set to the bot username/id instead of the target user or group chat. Telegram bots cannot send messages to themselves.

Fix in the bot service environment variables:

```text
TG_CHAT_ID=<numeric user, group, or channel chat id>
```

Requirements:

- Start a chat with the bot first, or add the bot to the target group/channel.
- Use the numeric chat ID, not `TG_USERNAME` and not the bot username.
- Keep `TG_TOKEN`, `TG_WEBHOOK_SECRET`, and `TG_CHAT_ID` out of Git.

A quick local diagnostic can use Telegram `getMe` and `sendMessage`, but it must never print the token or chat ID.

## Troubleshooting Scheduled Reports

If the scheduled report workflow fails with:

```text
env:
  TG_WEBHOOK_SECRET:
curl: (22) The requested URL returned error: 404
{"detail":"Not Found"}
```

then the GitHub Actions secret is missing or empty. The request path becomes `/tasks/send/morning/`, which does not match the protected FastAPI route.

Fix it in the `wkyjim/market-intelligence-telegram-bot` GitHub repository:

```text
Settings -> Secrets and variables -> Actions -> New repository secret
Name: TG_WEBHOOK_SECRET
Value: the same value configured for the bot service as `TG_WEBHOOK_SECRET`
```

Do not print or commit the secret. The workflow now fails before curl with a clear error when this secret is absent.

## GitHub Actions Scheduling

Workflow:

```text
.github/workflows/telegram-reports.yml
```

GitHub Actions secret required:

```text
TG_WEBHOOK_SECRET
```

GitHub Actions repository variable required:

```text
TG_RENDER_BASE_URL=https://bot.138.2.69.165.sslip.io
```

Configure it under `Settings > Secrets and variables > Actions > Variables`. The workflow reads the service URL from this variable and fails before delivery if it is missing.

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
    TG_RENDER_BASE_URL: ${{ vars.TG_RENDER_BASE_URL }}
  run: |
    curl --fail-with-body --silent --show-error \
      --retry 2 --retry-delay 5 --max-time 120 \
      -X POST \
      -H "X-Telegram-Bot-Api-Secret-Token: ${TG_WEBHOOK_SECRET}" \
      "${TG_RENDER_BASE_URL%/}/tasks/send/report-update/${TG_WEBHOOK_SECRET}"
```

Do not trigger this before the published `TG_REPORT_URL` contains the new report.

Report-update state is stored in a small local JSON file by default. On an ephemeral container filesystem, this state may reset on redeployment or restart, so the next report-update call may send once again.

## System-Alert Integration Examples

Python:

```python
import os
import requests

secret = os.environ["TG_WEBHOOK_SECRET"]
service_url = os.environ["TG_RENDER_BASE_URL"].rstrip("/")
url = f"{service_url}/tasks/alert/{secret}"

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
    TG_RENDER_BASE_URL: ${{ vars.TG_RENDER_BASE_URL }}
  run: |
    curl --fail-with-body --silent --show-error \
      --retry 2 --retry-delay 5 --max-time 120 \
      -X POST \
      -H "X-Telegram-Bot-Api-Secret-Token: ${TG_WEBHOOK_SECRET}" \
      -H "Content-Type: application/json" \
      --data '{"message":"Macro data update failed","source":"Macro ingestion","severity":"warning"}' \
      "${TG_RENDER_BASE_URL%/}/tasks/alert/${TG_WEBHOOK_SECRET}"
```

## Development Tests

```bash
pytest
```

## Manual Test Checklist

After deploy:

1. Open `https://bot.138.2.69.165.sslip.io/health`.
2. Register webhook with the same `TG_WEBHOOK_SECRET` in the path and header.
3. Send `/start` in Telegram.
4. Send `/report`.
5. Send `/report_etf`.
6. Send `/report_news`.
7. Send `/report_news_10`.
8. Send `/report_full`.
9. Trigger the morning task endpoint.
10. Trigger the alert endpoint with a test warning.
