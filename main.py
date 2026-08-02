from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator


load_dotenv()


TELEGRAM_LIMIT = 3900
DEFAULT_MARKET_API_BASE_URL = "https://postgresql-us-equities-api.onrender.com"
DEFAULT_SERVICE_URL = "https://market-intelligence-telegram-bot.onrender.com"
DEFAULT_DASHBOARD_URL = "https://wkyjim.github.io/market-dashboard/#overview"
DEFAULT_REPORT_URL = "https://wkyjim.github.io/market-dashboard/data/latest-report.md"
HKT = ZoneInfo("Asia/Hong_Kong")

logger = logging.getLogger("market_intelligence_telegram_bot")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


@dataclass(frozen=True)
class Settings:
    tg_token: str | None
    tg_chat_id: str | None
    tg_username: str | None
    webhook_secret: str | None
    render_base_url: str
    market_api_base_url: str
    report_url: str
    dashboard_url: str
    request_timeout_seconds: float
    report_hash_state_path: Path


def load_settings() -> Settings:
    return Settings(
        tg_token=os.getenv("TG_TOKEN") or os.getenv("TG_token"),
        tg_chat_id=os.getenv("TG_CHAT_ID") or os.getenv("TG_chat_id"),
        tg_username=os.getenv("TG_USERNAME") or os.getenv("TG_username"),
        webhook_secret=os.getenv("TG_WEBHOOK_SECRET") or os.getenv("TG_webhook_secret"),
        render_base_url=(os.getenv("TG_RENDER_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or DEFAULT_SERVICE_URL).rstrip("/"),
        market_api_base_url=os.getenv("MARKET_API_BASE_URL", DEFAULT_MARKET_API_BASE_URL).rstrip("/"),
        report_url=os.getenv("TG_REPORT_URL", DEFAULT_REPORT_URL),
        dashboard_url=os.getenv("TG_DASHBOARD_URL", DEFAULT_DASHBOARD_URL),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
        report_hash_state_path=Path(
            os.getenv("REPORT_HASH_STATE_PATH", "/tmp/market-intelligence-telegram-bot-report-hash.json")
        ),
    )


settings = load_settings()
app = FastAPI(title="Market Intelligence Telegram Bot", version="1.1.0")


class HealthResponse(BaseModel):
    status: str
    telegram_configured: bool
    report_url_configured: bool
    dashboard_url_configured: bool
    market_api_base_url: str


class BotResponse(BaseModel):
    ok: bool
    handled: bool = False
    message: str | None = None


class AlertRequest(BaseModel):
    message: str = Field(min_length=1)
    source: str | None = None
    severity: Literal["info", "warning", "critical"] = "warning"

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank")
        return value.strip()


def require_env(value: str | None, name: str) -> str:
    if not value:
        raise HTTPException(status_code=500, detail=f"Required environment variable is missing: {name}")
    return value


def validate_shared_secret(path_secret: str, header_secret: str | None) -> None:
    expected = require_env(settings.webhook_secret, "TG_WEBHOOK_SECRET")
    if not hmac.compare_digest(path_secret, expected):
        raise HTTPException(status_code=403, detail="Invalid shared secret")
    if header_secret is None or not hmac.compare_digest(header_secret, expected):
        raise HTTPException(status_code=403, detail="Invalid shared secret header")


def hkt_now() -> datetime:
    return datetime.now(tz=HKT)


def hkt_timestamp() -> str:
    return hkt_now().strftime("%Y-%m-%d %H:%M HKT")


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    text = text.strip() or "No message content."
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < int(limit * 0.6):
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return chunks


async def send_telegram_message(chat_id: str, text: str, parse_mode: str | None = None) -> int:
    token = require_env(settings.tg_token, "TG_TOKEN")
    sent = 0
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        for chunk in split_message(text):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            response = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
            logger.info("Telegram sendMessage status=%s", response.status_code)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning("Telegram delivery failed with status=%s", response.status_code)
                raise HTTPException(status_code=502, detail="Telegram delivery failed") from exc
            sent += 1
    return sent


async def send_to_default_chat(text: str, parse_mode: str | None = None) -> int:
    chat_id = require_env(settings.tg_chat_id, "TG_CHAT_ID")
    return await send_telegram_message(chat_id, text, parse_mode=parse_mode)


def register_telegram_webhook() -> dict[str, Any]:
    token = require_env(settings.tg_token, "TG_TOKEN")
    secret = require_env(settings.webhook_secret, "TG_WEBHOOK_SECRET")
    webhook_url = f"{settings.render_base_url}/telegram/webhook/{secret}"
    payload = {
        "url": webhook_url,
        "secret_token": secret,
        "drop_pending_updates": True,
    }
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json=payload,
            timeout=settings.request_timeout_seconds,
        )
        logger.info("Telegram setWebhook status=%s", response.status_code)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Telegram setWebhook failed")
        raise HTTPException(status_code=502, detail="Telegram webhook registration failed") from exc
    return response.json()


def fetch_text_url(url: str) -> str:
    try:
        response = requests.get(url, timeout=settings.request_timeout_seconds)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        logger.exception("Report service unavailable")
        raise HTTPException(status_code=502, detail="Report service unavailable") from exc


def fetch_json(path: str) -> dict[str, Any]:
    url = f"{settings.market_api_base_url}{path}"
    try:
        response = requests.get(url, timeout=settings.request_timeout_seconds)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.exception("Market data service unavailable")
        raise HTTPException(status_code=502, detail="Market data service unavailable") from exc


def fetch_report_markdown() -> str:
    return fetch_text_url(settings.report_url)


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


def first_non_empty_sections(markdown: str, headings: list[str], *, limit: int = 5) -> list[str]:
    sections: list[str] = []
    for heading in headings:
        section = strip_markdown(extract_section(markdown, heading))
        if section:
            lines = [line.strip() for line in section.splitlines() if line.strip()]
            if lines:
                sections.append(f"{heading}\n" + "\n".join(lines[:limit]))
    return sections


def report_preview(markdown: str, *, max_lines: int = 12) -> str:
    lines = [strip_markdown(line) for line in markdown.splitlines() if strip_markdown(line)]
    return "\n".join(lines[:max_lines])


def sector_extremes(markdown: str) -> tuple[str | None, str | None]:
    rows = markdown_table_rows(extract_section(markdown, "Official Sector Strength"))
    if not rows:
        rows = markdown_table_rows(extract_section(markdown, "Sector Strength Ranking"))
    if not rows:
        return None, None
    sector_key = "Sector" if "Sector" in rows[0] else next(iter(rows[0]))
    score_key = "Score" if "Score" in rows[0] else None
    top = rows[0].get(sector_key, "n/a")
    bottom = rows[-1].get(sector_key, "n/a")
    if score_key:
        top = f"{top} ({rows[0].get(score_key, 'n/a')})"
        bottom = f"{bottom} ({rows[-1].get(score_key, 'n/a')})"
    return top, bottom


def links_block() -> str:
    return f"Dashboard: {settings.dashboard_url}\nReport: {settings.report_url}"


def build_morning_report(markdown: str) -> str:
    parts = ["Morning Positioning Report", f"Time: {hkt_timestamp()}"]
    sections = first_non_empty_sections(
        markdown,
        [
            "Executive Dashboard",
            "Executive Summary",
            "Tactical Positioning",
            "Highest Conviction Opportunities",
            "Volatility and Risk Signals",
        ],
    )
    top, bottom = sector_extremes(markdown)
    if top:
        sections.append(f"Top buy/overweight sector\n{top}")
    if bottom:
        sections.append(f"Top sell/underweight sector\n{bottom}")
    if not sections:
        sections.append("Latest report preview\n" + report_preview(markdown))
    parts.extend(sections)
    parts.append(links_block())
    return "\n\n".join(part for part in parts if part.strip())


def build_eod_report(markdown: str) -> str:
    parts = ["End-of-Day Market Report", f"Time: {hkt_timestamp()}"]
    sections = first_non_empty_sections(
        markdown,
        [
            "Executive Dashboard",
            "Executive Summary",
            "Market Regime Score",
            "Three-Month Outperformance Setup",
            "Volatility and Risk Signals",
            "Contradiction / Audit Flags",
        ],
    )
    top, bottom = sector_extremes(markdown)
    if top:
        sections.append(f"Top buy/overweight sector\n{top}")
    if bottom:
        sections.append(f"Top sell/underweight sector\n{bottom}")
    if not sections:
        sections.append("Latest report preview\n" + report_preview(markdown))
    parts.extend(sections)
    parts.append(links_block())
    return "\n\n".join(part for part in parts if part.strip())


def build_report_update_message(markdown: str) -> str:
    return "\n\n".join(
        [
            "Latest Report Updated",
            f"Time: {hkt_timestamp()}",
            dashboard_from_report(markdown) or report_preview(markdown, max_lines=8),
            links_block(),
        ]
    )


def dashboard_from_report(markdown: str) -> str:
    section = extract_section(markdown, "Executive Dashboard") or extract_section(markdown, "Executive Summary")
    if not section:
        return ""
    lines = [strip_markdown(line) for line in section.splitlines() if strip_markdown(line)]
    return "Executive Summary\n" + "\n".join(lines[:10])


def fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.2f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def market_snapshot_message() -> str:
    data = fetch_json("/market-tape")
    lines = ["Latest Market Snapshot"]
    for group, rows in data.get("groups", {}).items():
        if not rows:
            continue
        lines.append("")
        lines.append(group.replace("_", " ").title())
        for row in rows[:8]:
            change = fmt(row.get("change_bps"), " bps") if group == "ust_yields" else fmt(row.get("pct_chg"), "%")
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
        ]
    )


def signals_message(markdown: str) -> str:
    rows = markdown_table_rows(extract_section(markdown, "Three-Month Outperformance Setup"))[:8]
    if not rows:
        return "Signals are unavailable in the latest report."
    lines = ["Latest Rule-Based Setup Signals"]
    for row in rows:
        lines.append(f"- {row.get('Theme', 'n/a')}: {row.get('Classification', 'n/a')} ({row.get('Score', 'n/a')})")
    return "\n".join(lines)


def sectors_message(markdown: str) -> str:
    top, bottom = sector_extremes(markdown)
    if not top and not bottom:
        return "Sector ranking is unavailable in the latest report."
    return f"Top Sector Signals\n- Highest score: {top or 'n/a'}\n- Lowest score: {bottom or 'n/a'}"


def risk_message(markdown: str) -> str:
    risk = extract_section(markdown, "Volatility and Risk Signals") or extract_section(markdown, "Contradiction / Audit Flags")
    return "Current Market Risk Alerts\n" + (strip_markdown(risk) if risk else "No risk section found in latest report.")


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


def normalize_command(text: str) -> tuple[str, str]:
    command_text = text.strip()
    command, _sep, arg_text = command_text.partition(" ")
    return command.lower().split("@")[0], arg_text


def command_response(text: str) -> str:
    command, arg_text = normalize_command(text)
    if command == "/start":
        return START_TEXT.strip()
    try:
        markdown = fetch_report_markdown()
        if command == "/market":
            return market_snapshot_message()
        if command == "/dashboard":
            return dashboard_from_report(markdown) or market_snapshot_message()
        if command == "/report":
            return strip_markdown(markdown)
        if command == "/signals":
            return signals_message(markdown)
        if command == "/sectors":
            return sectors_message(markdown)
        if command == "/equity":
            return equity_message(arg_text)
        if command == "/macro":
            return macro_message()
        if command == "/risk":
            return risk_message(markdown)
        if command == "/status":
            return status_message()
    except HTTPException as exc:
        logger.warning("Command %s failed safely: %s", command, exc.detail)
        return f"{command} is temporarily unavailable: {exc.detail}"
    return "Unknown command. Use /start for available commands."


def status_message() -> str:
    lines = [
        "System Status",
        "- FastAPI app: running",
        f"- Market API: {settings.market_api_base_url}",
        f"- TG token: {'configured' if settings.tg_token else 'missing'}",
        f"- TG chat id: {'configured' if settings.tg_chat_id else 'missing'}",
        f"- Webhook secret: {'configured' if settings.webhook_secret else 'missing'}",
        f"- Report URL: {'configured' if settings.report_url else 'missing'}",
        f"- Dashboard URL: {'configured' if settings.dashboard_url else 'missing'}",
    ]
    try:
        tape = fetch_json("/market-tape")
        lines.append(f"- Market tape rows: {tape.get('count', 0)}")
    except HTTPException:
        lines.append("- Market API check failed")
    return "\n".join(lines)


def read_report_hash_state() -> dict[str, Any]:
    try:
        if settings.report_hash_state_path.exists():
            return json.loads(settings.report_hash_state_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed reading report hash state")
    return {}


def write_report_hash_state(report_hash: str) -> None:
    payload = {"report_hash": report_hash, "notified_at_hkt": hkt_timestamp()}
    try:
        settings.report_hash_state_path.parent.mkdir(parents=True, exist_ok=True)
        settings.report_hash_state_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        logger.exception("Failed writing report hash state")


def report_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def scheduled_message(kind: str, markdown: str) -> str:
    if kind == "morning":
        return build_morning_report(markdown)
    if kind == "eod":
        return build_eod_report(markdown)
    if kind == "report-update":
        return build_report_update_message(markdown)
    raise HTTPException(status_code=400, detail=f"Unsupported scheduled message: {kind}")


async def send_system_alert(message: str, *, source: str | None = None, severity: str = "warning") -> None:
    try:
        source_line = f"\nSource: {source}" if source else ""
        text = f"SYSTEM ALERT - {severity.upper()}\n\n{message}{source_line}\nTime: {hkt_timestamp()}"
        await send_to_default_chat(text)
    except HTTPException:
        logger.warning("System alert delivery failed")


@app.get("/", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="running",
        telegram_configured=bool(settings.tg_token and settings.tg_chat_id),
        report_url_configured=bool(settings.report_url),
        dashboard_url_configured=bool(settings.dashboard_url),
        market_api_base_url=settings.market_api_base_url,
    )


@app.get("/health", response_model=HealthResponse)
def health_alias() -> HealthResponse:
    return health()


@app.get("/debug/config", response_model=dict[str, Any])
def debug_config() -> dict[str, Any]:
    return {
        "tg_token_configured": bool(settings.tg_token),
        "tg_chat_id_configured": bool(settings.tg_chat_id),
        "tg_chat_id_looks_numeric": bool(settings.tg_chat_id and str(settings.tg_chat_id).lstrip("-").isdigit()),
        "tg_username_configured": bool(settings.tg_username),
        "webhook_secret_configured": bool(settings.webhook_secret),
        "render_base_url_configured": bool(settings.render_base_url),
        "market_api_base_url": settings.market_api_base_url,
        "report_url_configured": bool(settings.report_url),
        "dashboard_url_configured": bool(settings.dashboard_url),
    }


@app.post("/telegram/set-webhook/{secret}", response_model=BotResponse)
def set_webhook(
    secret: str,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> BotResponse:
    validate_shared_secret(secret, x_telegram_bot_api_secret_token)
    result = register_telegram_webhook()
    return BotResponse(ok=bool(result.get("ok")), handled=True, message="Telegram webhook registered")


@app.post("/telegram/webhook/{secret}", response_model=BotResponse)
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> BotResponse:
    validate_shared_secret(secret, x_telegram_bot_api_secret_token)
    update = await request.json()
    logger.info("Telegram update keys=%s", list(update.keys()))

    message = update.get("message") or update.get("edited_message")
    if not message:
        return BotResponse(ok=True, handled=False, message="No message found")

    text = str(message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id")) if chat.get("id") is not None else None
    logger.info("Incoming Telegram command chat_id_present=%s text=%r", bool(chat_id), text)

    if not text or not chat_id:
        return BotResponse(ok=True, handled=False, message="No text command found")

    reply = command_response(text)
    try:
        await send_telegram_message(chat_id, reply)
    except HTTPException:
        logger.warning("Webhook command handled but Telegram reply delivery failed")
        return BotResponse(ok=True, handled=True, message="Command handled; reply delivery failed")
    return BotResponse(ok=True, handled=True, message="Command handled")


@app.post("/tasks/send/{kind}/{secret}", response_model=BotResponse)
async def send_scheduled(
    kind: str,
    secret: str,
    force: bool = Query(False),
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> BotResponse:
    validate_shared_secret(secret, x_telegram_bot_api_secret_token)
    if kind not in {"morning", "eod", "report-update"}:
        raise HTTPException(status_code=400, detail=f"Unsupported scheduled message: {kind}")

    markdown = fetch_report_markdown()
    if kind == "report-update":
        current_hash = report_hash(markdown)
        previous_hash = read_report_hash_state().get("report_hash")
        if previous_hash == current_hash and not force:
            return BotResponse(ok=True, handled=False, message="Report has not changed")
        await send_to_default_chat(build_report_update_message(markdown))
        write_report_hash_state(current_hash)
        return BotResponse(ok=True, handled=True, message="Report update notification sent")

    await send_to_default_chat(scheduled_message(kind, markdown))
    message = "Morning report sent" if kind == "morning" else "End-of-day report sent"
    return BotResponse(ok=True, handled=True, message=message)


@app.post("/tasks/alert/{secret}", response_model=BotResponse)
async def send_alert(
    secret: str,
    payload: AlertRequest,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> BotResponse:
    validate_shared_secret(secret, x_telegram_bot_api_secret_token)
    source_line = f"\nSource: {payload.source}" if payload.source else ""
    text = f"SYSTEM ALERT - {payload.severity.upper()}\n\n{payload.message}{source_line}\nTime: {hkt_timestamp()}"
    await send_to_default_chat(text)
    return BotResponse(ok=True, handled=True, message="Alert sent")
