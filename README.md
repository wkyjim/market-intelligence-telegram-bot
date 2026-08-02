# Market Intelligence Telegram Bot

Standalone FastAPI webhook service for the Market Intelligence Telegram bot.

It connects:

1. FastAPI webhook app
2. GitHub repository
3. Render Web Service
4. Telegram Bot webhook

The bot does not store secrets in code. Configure credentials in Render environment variables.

## Files

```text
market-intelligence-telegram-bot/
├── main.py
├── requirements.txt
├── .env
├── .gitignore
└── README.md
```

## Local Setup

1. Create a Telegram bot using BotFather.
2. Copy `.env` and fill local values.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run locally:

```bash
uvicorn main:app --reload
```

5. Check health:

```bash
curl http://127.0.0.1:8000/
```

Local Telegram webhook testing requires a public HTTPS tunnel such as ngrok or Cloudflare Tunnel.

## Required Environment Variables

Set these in Render:

```text
TG_token=<Telegram BotFather token>
TG_chat_id=<your Telegram chat id>
TG_username=<bot username, optional>
TG_webhook_secret=<long random path secret>
TG_RENDER_BASE_URL=https://<your-render-service>.onrender.com
MARKET_API_BASE_URL=https://postgresql-us-equities-api.onrender.com
TG_REPORT_URL=https://wkyjim.github.io/market-dashboard/data/latest-report.md
```

Recommended extra security:

```text
TG_webhook_secret_token=<long random Telegram secret token>
```

Do not commit real values.

## GitHub Setup

From inside this folder:

```bash
git init
git add main.py requirements.txt .gitignore README.md
git commit -m "Initial Telegram webhook bot"
git branch -M main
git remote add origin https://github.com/wkyjim/market-intelligence-telegram-bot.git
git push -u origin main
```

`.env` is ignored and should not be pushed.

## Render Setup

1. Create a new Render Web Service.
2. Connect the GitHub repository.
3. Runtime: Python.
4. Build command:

```bash
pip install -r requirements.txt
```

5. Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

6. Add the environment variables listed above.
7. Deploy.

## Register Telegram Webhook

After Render is live, call:

```bash
curl -X POST https://<your-render-service>.onrender.com/telegram/set-webhook/<TG_webhook_secret>
```

If using `TG_webhook_secret_token`, call:

```bash
curl -X POST \
  -H "X-Telegram-Bot-Api-Secret-Token: <TG_webhook_secret_token>" \
  https://<your-render-service>.onrender.com/telegram/set-webhook/<TG_webhook_secret>
```

Telegram will send updates to:

```text
https://<your-render-service>.onrender.com/telegram/webhook/<TG_webhook_secret>
```

## Telegram Commands

```text
/start          Show available commands and features
/market         Show the latest market snapshot
/dashboard      Show the latest executive dashboard
/report         Show the latest full market report
/signals        Show latest deterministic setup signals
/sectors        Show top and bottom sector scores
/equity AAPL    Look up latest stock data and signals
/macro          Show latest macro summary
/risk           Show current market risk alerts
/status         Show database, API, and data update status
```

## Scheduled Reports on Render

Use Render Cron Jobs to call protected task endpoints.

Morning Positioning Report, daily 06:00 HKT:

```bash
curl -X POST https://<your-render-service>.onrender.com/tasks/send/morning/<TG_webhook_secret>
```

End-of-Day Market Report, daily 18:00 HKT:

```bash
curl -X POST https://<your-render-service>.onrender.com/tasks/send/eod/<TG_webhook_secret>
```

Report update notification:

```bash
curl -X POST https://<your-render-service>.onrender.com/tasks/send/report-update/<TG_webhook_secret>
```

System alert:

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"Macro data update failed\"}" \
  https://<your-render-service>.onrender.com/tasks/alert/<TG_webhook_secret>
```

If using `TG_webhook_secret_token`, add:

```bash
-H "X-Telegram-Bot-Api-Secret-Token: <TG_webhook_secret_token>"
```

## Notes

- `/market`, `/macro`, and `/equity` use `MARKET_API_BASE_URL`.
- `/dashboard`, `/report`, `/signals`, `/sectors`, and `/risk` use `TG_REPORT_URL`.
- If `TG_REPORT_URL` is missing, `/dashboard` falls back to market tape and `/report` explains that report markdown is unavailable.
- Long Telegram responses are split into safe message chunks.
