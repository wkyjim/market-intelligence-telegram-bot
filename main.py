from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel


load_dotenv()


TELEGRAM_LIMIT = 3900
DEFAULT_MARKET_API_BASE_URL = "https://postgresql-us-equities-api.onrender.com"


@dataclass(frozen=True)
class Settings:
    tg_token: str | None = os.getenv("TG_token") or os.getenv("TG_TOKEN")
    tg_chat_id: str | None = os.getenv("TG_chat_id") or os.getenv("TG_CHAT_ID")
    tg_username: str | None = os.getenv("TG_username") or os.getenv("TG_USERNAME")
    webhook_secret: str | None = os.getenv("TG_webhook_secret") or os.getenv("TG_WEBHOOK_SECRET")
    webhook_secret_token: str | None = os.getenv("TG_webhook_secret_token") or os.getenv("TG_WEBHOOK_SECRET_TOKEN")
    render_base_url: str | None = os.getenv("TG_RENDER_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    market_api_base_url: str = os.getenv("MARKET_API_BASE_URL", DEFAULT_MARKET_API_BASE_URL).rstrip("/")
    report_url: str | None = os.getenv("TG_REPORT_URL") or os.getenv("LATEST_REPORT_URL")


settings = Settings()
app = FastAPI(title="Market Intelligence Telegram Bot", version="1.0.0")


class HealthResponse(BaseModel):
    status: str
    telegram_configured: bool
    report_url_configured: bool
    market_api_base_url: str


class BotResponse(BaseModel):
    ok: bool
    handled: bool = False
    message: str | None = None


def require_tg_token() -> str:
    if not settings.tg_token:
        raise HTTPException(status_code=503, detail="TG_token is not configured")
    return settings.tg_token


def require_chat_id(chat_id: str | None = None) -> str:
    target = chat_id or settings.tg_chat_id
    if not target:
        raise HTTPException(status_code=503, detail="TG_chat_id is not configured")
    return str(target)


def verify_secret(path_secret: str, header_secret: str | None = None) -> None:
    if not settings.webhook_secret:
        raise HTTPException(status_code=503, detail="TG_webhook_secret is not configured")
    if path_secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    if settings.webhook_secret_token and header_secret != settings.webhook_secret_token:
        raise HTTPException(status_code=403, detail="Invalid Telegram secret token")


def split_message(text: str) -> list[str]:
    text = text.strip() or "No message content."
    if len(text) <= TELEGRAM_LIMIT:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_LIMIT:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, TELEGRAM_LIMIT)
        if cut < int(TELEGRAM_LIMIT * 0.6):
            cut = TELEGRAM_LIMIT
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return chunks


def send_telegram_message(text: str, *, chat_id: str | None = None) -> int:
    token = require_tg_token()
    target_chat_id = require_chat_id(chat_id)
    sent = 0
    for chunk in split_message(text):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": target_chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        sent += 1
    return sent


def register_telegram_webhook() -> dict[str, Any]:
    token = require_tg_token()
    if not settings.render_base_url:
        raise HTTPException(status_code=503, detail="TG_RENDER_BASE_URL is not configured")
    if not settings.webhook_secret:
        raise HTTPException(status_code=503, detail="TG_webhook_secret is not configured")

    webhook_url = f"{settings.render_base_url.rstrip('/')}/telegram/webhook/{settings.webhook_secret}"
    payload: dict[str, Any] = {"url": webhook_url, "drop_pending_updates": True}
    if settings.webhook_secret_token:
        payload["secret_token"] = settings.webhook_secret_token

    response = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_json(path: str) -> dict[str, Any]:
    response = requests.get(f"{settings.market_api_base_url}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_report_markdown() -> str:
    if not settings.report_url:
        return ""
    try:
        response = requests.get(settings.report_url, timeout=20)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        return ""


def strip_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


def normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def extract_section(markdown: str, heading: str) -> str:
    wanted = normalize_heading(heading)
    lines = markdown.splitlines()
    start: int | None = None
    start_level = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        if normalize_heading(match.group(2)) == wanted:
            start = index + 1
            start_level = len(match.group(1))
            break
    if start is None:
        return ""

    end = len(lines)
    for index in range(start, len(lines)):
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", lines[index])
        if match and len(match.group(1)) <= start_level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def markdown_table_rows(section: str) -> list[dict[str, str]]:
    rows = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    if len(rows) < 3:
        return []
    headers = [cell.strip() for cell in rows[0].strip("|").split("|")]
    parsed: list[dict[str, str]] = []
    for row in rows[2:]:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) == len(headers):
            parsed.append(dict(zip(headers, cells)))
    return parsed


def fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.2f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def dashboard_from_report(markdown: str) -> str:
    section = extract_section(markdown, "Executive Dashboard")
    if not section:
        return ""
    rows = [strip_markdown(line) for line in section.splitlines() if line.strip()]
    return "Latest Executive Dashboard\n" + "\n".join(rows[:10])


def sectors_from_report(markdown: str) -> str:
    rows = markdown_table_rows(extract_section(markdown, "Official Sector Strength"))
    if not rows:
        return ""
    top = rows[0]
    bottom = rows[-1]
    lines = [
        "Top Sector Signals",
        f"- Highest score: {top.get('Sector', 'n/a')} ({top.get('Score', 'n/a')})",
        f"- Lowest score: {bottom.get('Sector', 'n/a')} ({bottom.get('Score', 'n/a')})",
        "",
        "Top 5 sectors:",
    ]
    for index, row in enumerate(rows[:5], start=1):
        lines.append(f"- {index}. {row.get('Sector', 'n/a')}: {row.get('Score', 'n/a')}")
    return "\n".join(lines)


def market_snapshot_message() -> str:
    data = fetch_json("/market-tape")
    lines = ["Latest Market Snapshot"]
    for group, rows in data.get("groups", {}).items():
        if not rows:
            continue
        lines.append("")
        lines.append(group.replace("_", " ").title())
        for row in rows[:8]:
            if group == "ust_yields":
                change = fmt(row.get("change_bps"), " bps")
            else:
                change = fmt(row.get("pct_chg"), "%")
            lines.append(f"- {row.get('display_name')}: {fmt(row.get('close'))} ({change}), {row.get('data_source', 'n/a')}")
    return "\n".join(lines)


def macro_message() -> str:
    symbols = "^GSPC,^NDX,^DJI,^VIX,US2YT=X,US10YT=X,US30YT=X,GC=F,CL=F,BTC-USD"
    data = fetch_json(f"/macro/batch/latest?symbols={symbols}")
    lines = ["Latest Macro Summary"]
    for row in data.get("data", []):
        lines.append(f"- {row.get('symbol')}: {fmt(row.get('close'))} ({fmt(row.get('pct_chg'), '%')}), {row.get('data_source', 'n/a')}")
    return "\n".join(lines)


def equity_message(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not ticker:
        return "Usage: /equity AAPL"
    data = fetch_json(f"/equities/latest/{ticker}")
    if not data.get("found"):
        return data.get("message") or f"No data found for {ticker}."
    row = data.get("data") or {}
    return "\n".join(
        [
            f"Equity Snapshot: {ticker}",
            f"- Name: {row.get('name') or 'n/a'}",
            f"- Date: {row.get('date') or 'n/a'}",
            f"- Close: {fmt(row.get('close'))}",
            f"- 1D pct change: {fmt(row.get('pct_chg'), '%')}",
            f"- RSI 14: {fmt(row.get('rsi_14'))}",
            f"- 20D return: {fmt(row.get('return_20d'), '%')}",
            f"- 60D return: {fmt(row.get('return_60d'), '%')}",
            f"- Volume ratio 20D: {fmt(row.get('volume_ratio_20'))}",
        ]
    )


def risk_message(markdown: str) -> str:
    risk = extract_section(markdown, "Volatility and Risk Signals")
    audit = extract_section(markdown, "Contradiction / Audit Flags")
    body = strip_markdown(risk or audit)
    return "Current Market Risk Alerts\n" + (body if body else "No risk section found in latest report.")


def signals_message(markdown: str) -> str:
    rows = markdown_table_rows(extract_section(markdown, "Three-Month Outperformance Setup"))[:8]
    if not rows:
        return "Signals are unavailable in the latest report."
    lines = ["Latest Rule-Based Setup Signals"]
    for row in rows:
        lines.append(f"- {row.get('Theme', 'n/a')}: {row.get('Classification', 'n/a')} ({row.get('Score', 'n/a')})")
    return "\n".join(lines)


def status_message() -> str:
    lines = [
        "System Status",
        "- FastAPI app: running",
        f"- Market API: {settings.market_api_base_url}",
        f"- TG token: {'configured' if settings.tg_token else 'missing'}",
        f"- TG chat id: {'configured' if settings.tg_chat_id else 'missing'}",
        f"- Webhook secret: {'configured' if settings.webhook_secret else 'missing'}",
        f"- Report URL: {'configured' if settings.report_url else 'missing'}",
    ]
    try:
        tape = fetch_json("/market-tape")
        lines.append(f"- Market tape rows: {tape.get('count', 0)}")
    except requests.RequestException as exc:
        lines.append(f"- Market API check failed: {type(exc).__name__}")
    return "\n".join(lines)


START_TEXT = """Market Intelligence Bot

Commands:
/market - Show the latest market snapshot
/dashboard - Show the latest executive dashboard
/report - Show the latest full market report
/signals - Show latest deterministic setup signals
/sectors - Show top and bottom sector scores
/equity AAPL - Look up latest stock data and signals
/macro - Show latest macro summary
/risk - Show current market risk alerts
/status - Show database, API, and data update status
"""


def command_response(text: str) -> str:
    command_text = text.strip()
    command, _sep, arg_text = command_text.partition(" ")
    command = command.lower()
    markdown = fetch_report_markdown()

    if command == "/start":
        return START_TEXT.strip()
    if command == "/market":
        return market_snapshot_message()
    if command == "/dashboard":
        return dashboard_from_report(markdown) or market_snapshot_message()
    if command == "/report":
        return strip_markdown(markdown) if markdown else "Latest report is unavailable. Configure TG_REPORT_URL."
    if command == "/signals":
        return signals_message(markdown)
    if command == "/sectors":
        return sectors_from_report(markdown) or "Sector ranking is unavailable in the latest report."
    if command == "/equity":
        return equity_message(arg_text)
    if command == "/macro":
        return macro_message()
    if command == "/risk":
        return risk_message(markdown)
    if command == "/status":
        return status_message()
    return "Unknown command. Use /start for available commands."


def scheduled_message(kind: str) -> str:
    markdown = fetch_report_markdown()
    if kind == "morning":
        return "Morning Positioning Report\n\n" + (dashboard_from_report(markdown) or market_snapshot_message()) + "\n\n" + (sectors_from_report(markdown) or "")
    if kind == "eod":
        return "End-of-Day Market Report\n\n" + (dashboard_from_report(markdown) or market_snapshot_message()) + "\n\n" + risk_message(markdown)
    if kind == "report-update":
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        return "Report Update Notification\n" + timestamp + "\n\n" + (dashboard_from_report(markdown) or market_snapshot_message())
    raise HTTPException(status_code=400, detail=f"Unsupported scheduled message: {kind}")


@app.get("/", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="running",
        telegram_configured=bool(settings.tg_token and settings.tg_chat_id),
        report_url_configured=bool(settings.report_url),
        market_api_base_url=settings.market_api_base_url,
    )


@app.post("/telegram/set-webhook/{secret}", response_model=BotResponse)
def set_webhook(secret: str, x_telegram_bot_api_secret_token: str | None = Header(None)) -> BotResponse:
    verify_secret(secret, x_telegram_bot_api_secret_token)
    result = register_telegram_webhook()
    return BotResponse(ok=bool(result.get("ok")), handled=True, message=str(result))


@app.post("/telegram/webhook/{secret}", response_model=BotResponse)
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> BotResponse:
    verify_secret(secret, x_telegram_bot_api_secret_token)
    update = await request.json()
    message = update.get("message") or update.get("edited_message") or {}
    text = message.get("text")
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id")) if chat.get("id") is not None else None

    if not text or not chat_id:
        return BotResponse(ok=True, handled=False, message="No text command found.")

    if settings.tg_chat_id and chat_id != str(settings.tg_chat_id):
        return BotResponse(ok=True, handled=False, message="Ignored unauthorized chat.")

    send_telegram_message(command_response(text), chat_id=chat_id)
    return BotResponse(ok=True, handled=True, message="Command handled.")


@app.post("/tasks/send/{kind}/{secret}", response_model=BotResponse)
def send_scheduled(kind: str, secret: str, x_telegram_bot_api_secret_token: str | None = Header(None)) -> BotResponse:
    verify_secret(secret, x_telegram_bot_api_secret_token)
    send_telegram_message(scheduled_message(kind))
    return BotResponse(ok=True, handled=True, message=f"Sent {kind}.")


@app.post("/tasks/alert/{secret}", response_model=BotResponse)
async def send_alert(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> BotResponse:
    verify_secret(secret, x_telegram_bot_api_secret_token)
    payload = await request.json()
    message = str(payload.get("message") or "System alert: market data/API update failure.")
    send_telegram_message("System Alert\n" + message)
    return BotResponse(ok=True, handled=True, message="Alert sent.")
