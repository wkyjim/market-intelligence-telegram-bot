from __future__ import annotations

import hashlib
import html
import hmac
import json
import logging
import os
import re
import time
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTERNAL_ENV_DIR = (
    PROJECT_ROOT.parent.parent / "DB_builder_env"
    if PROJECT_ROOT.parent.name.casefold() == "hermes_pm"
    else PROJECT_ROOT.parent / "DB_builder_env"
)
EXTERNAL_ENV_DIR = Path(
    os.getenv("DB_BUILDER_ENV_DIR", str(DEFAULT_EXTERNAL_ENV_DIR))
).expanduser()
if os.getenv("DB_BUILDER_AUDIT_MODE", "").strip().lower() not in {"1", "true", "yes", "on"}:
    load_dotenv(EXTERNAL_ENV_DIR / "market-intelligence-telegram-bot" / ".env", override=False)


TELEGRAM_LIMIT = 3900
DEFAULT_DASHBOARD_URL = "https://wkyjim.github.io/market-dashboard/#overview"
DEFAULT_REPORT_URL = "https://wkyjim.github.io/market-dashboard/data/latest-report.md"
HKT = ZoneInfo("Asia/Hong_Kong")

logger = logging.getLogger("market_intelligence_telegram_bot")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


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
        render_base_url=(os.getenv("TG_RENDER_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/"),
        market_api_base_url=(os.getenv("MARKET_API_BASE_URL") or "").rstrip("/"),
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


@dataclass
class ParsedReport:
    title: str
    timestamp: str | None
    executive_summary: str | None
    market_snapshot: dict[str, Any]
    positioning: dict[str, Any]
    sector_signals: list[dict[str, str]]
    etf_flows: dict[str, Any]
    news: list[dict[str, str]]
    opportunities: list[str]
    risks: list[str]
    signal_changes: list[str]
    raw_sections: dict[str, str]
    markdown: str


@dataclass
class TelegramReply:
    messages: list[str]
    parse_mode: str | None = "HTML"
    reply_markup: dict[str, Any] | None = None


REPORT_CACHE: dict[str, Any] = {"expires_at": 0.0, "report": None}


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


def telegram_failure_detail(response: httpx.Response) -> str:
    description = ""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict):
        description = str(body.get("description") or "").strip()
    if description:
        description = re.sub(r"\d{6,}:[A-Za-z0-9_-]+", "[redacted-token]", description)
        return f"Telegram delivery failed: {description[:300]}"
    return "Telegram delivery failed"


async def send_telegram_message(
    chat_id: str,
    text: str,
    parse_mode: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> int:
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
            if reply_markup and sent == 0:
                payload["reply_markup"] = reply_markup
            try:
                response = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
            except httpx.RequestError as exc:
                logger.warning("Telegram delivery request failed: %s", exc.__class__.__name__)
                raise HTTPException(status_code=502, detail="Telegram delivery failed: request error") from None
            logger.info("Telegram sendMessage status=%s", response.status_code)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = telegram_failure_detail(response)
                logger.warning("%s", detail)
                raise HTTPException(status_code=502, detail=detail) from None
            sent += 1
    return sent


async def send_to_default_chat(text: str, parse_mode: str | None = None) -> int:
    chat_id = require_env(settings.tg_chat_id, "TG_CHAT_ID")
    return await send_telegram_message(chat_id, text, parse_mode=parse_mode)


def register_telegram_webhook() -> dict[str, Any]:
    token = require_env(settings.tg_token, "TG_TOKEN")
    secret = require_env(settings.webhook_secret, "TG_WEBHOOK_SECRET")
    service_url = require_env(settings.render_base_url, "TG_RENDER_BASE_URL")
    webhook_url = f"{service_url}/telegram/webhook/{secret}"
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
    except requests.RequestException:
        logger.warning("Telegram setWebhook request failed")
        raise HTTPException(status_code=502, detail="Telegram webhook registration failed") from None
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
    base_url = require_env(settings.market_api_base_url, "MARKET_API_BASE_URL")
    url = f"{base_url}{path}"
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


def escape_html(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def normalize_section_key(value: str) -> str:
    value = re.sub(r"^#+\s*", "", value.strip().lower())
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\bflows\b", "flow", value)
    value = re.sub(r"\bheadlines\b", "headline", value)
    value = re.sub(r"\brisks\b", "risk", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_markdown_sections(markdown: str) -> dict[str, str]:
    lines = markdown.splitlines()
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = normalize_section_key(match.group(2))
            current_lines = []
            continue
        if current_key:
            current_lines.append(line)
    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()
    return sections


def find_section(raw_sections: dict[str, str], aliases: list[str]) -> str:
    alias_keys = [normalize_section_key(alias) for alias in aliases]
    for alias in alias_keys:
        if alias in raw_sections and raw_sections[alias].strip():
            return raw_sections[alias]
    for key, section in raw_sections.items():
        if any(alias in key or key in alias for alias in alias_keys):
            if section.strip():
                return section
    return ""


ETF_FLOW_ALIASES = [
    "ETF Flow",
    "ETF Flows",
    "Fund Flow",
    "Fund Flows",
    "ETF Fund Flow",
    "ETF Flow Monitor",
    "ETF Flows Analysis",
    "Sector ETF Flow",
    "Thematic ETF Flow",
    "Core Flow Signals",
    "Sector Flow Signals",
    "Subsector PFV Signals",
]


NEWS_ALIASES = [
    "News",
    "Market News",
    "Latest News",
    "Headlines",
    "Key Headlines",
    "Catalysts",
    "Market Catalysts",
    "Macro News",
    "Geopolitics",
    "Earnings",
    "Company News",
    "News Analytics",
    "Top Market-Moving Headlines",
]


def extract_executive_summary(report: ParsedReport) -> str | None:
    return strip_markdown(
        find_section(report.raw_sections, ["Executive Dashboard", "Executive Summary"])
    ) or None


def extract_market_snapshot(report: ParsedReport) -> dict[str, Any]:
    section = find_section(report.raw_sections, ["Macro Snapshot", "Cross-Asset Confirmation", "Market Snapshot"])
    return {"section": strip_markdown(section), "tables": markdown_table_rows(section)}


def extract_positioning(report: ParsedReport) -> dict[str, Any]:
    section = find_section(
        report.raw_sections,
        ["Tactical Positioning", "Positioning", "Risk Management", "Market Regime Score"],
    )
    return {"section": strip_markdown(section), "tables": markdown_table_rows(section)}


def extract_sector_signals(report: ParsedReport) -> list[dict[str, str]]:
    section = find_section(report.raw_sections, ["Official Sector Strength", "Sector Strength Ranking", "Sector Signals"])
    return markdown_table_rows(section)


def extract_etf_flows(report: ParsedReport) -> dict[str, Any]:
    section = find_section(report.raw_sections, ETF_FLOW_ALIASES)
    return {"section": strip_markdown(section), "tables": markdown_table_rows(section)}


def section_items(section: str, *, limit: int = 10) -> list[str]:
    clean = strip_markdown(section)
    if not clean:
        return []
    lines = [line.strip(" -\t") for line in clean.splitlines() if line.strip()]
    items: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if re.match(r"^(\d+[\.\)]\s+|[-*]\s+)", line) or (line and not line.startswith("|") and len(buffer) >= 3):
            if buffer:
                items.append(" ".join(buffer).strip())
            buffer = [re.sub(r"^(\d+[\.\)]\s+|[-*]\s+)", "", line)]
        else:
            buffer.append(line)
    if buffer:
        items.append(" ".join(buffer).strip())
    return [item for item in items if item][:limit]


def extract_news(report: ParsedReport) -> list[dict[str, str]]:
    section = find_section(report.raw_sections, NEWS_ALIASES)
    table_rows = markdown_table_rows(section)
    if table_rows:
        items = []
        for row in table_rows[:10]:
            title = row.get("Headline") or row.get("Title") or row.get("News") or next(iter(row.values()), "")
            summary = row.get("Why it matters") or row.get("Summary") or row.get("Market Impact") or ""
            related = row.get("Affected Assets") or row.get("Related") or row.get("Tickers") or ""
            items.append({"headline": title, "summary": summary, "impact": row.get("Impact", ""), "related": related})
        return items
    return [{"headline": item, "summary": "", "impact": "", "related": ""} for item in section_items(section, limit=10)]


def extract_opportunities(report: ParsedReport) -> list[str]:
    section = find_section(report.raw_sections, ["Highest Conviction Opportunities", "Opportunities", "Tactical Opportunities", "Three-Month Outperformance Setup"])
    rows = markdown_table_rows(section)
    if rows:
        return [" | ".join(f"{key}: {value}" for key, value in row.items() if value) for row in rows[:8]]
    return section_items(section, limit=8)


def extract_risks(report: ParsedReport) -> list[str]:
    section = find_section(report.raw_sections, ["Volatility and Risk Signals", "Key Risks", "Risk Register", "Contradiction / Audit Flags"])
    rows = markdown_table_rows(section)
    if rows:
        return [" | ".join(f"{key}: {value}" for key, value in row.items() if value) for row in rows[:8]]
    return section_items(section, limit=8)


def extract_signal_changes(report: ParsedReport) -> list[str]:
    section = find_section(report.raw_sections, ["Signal Changes", "What Changed", "Material Divergences", "Contradiction / Audit Flags", "Leadership Rotation"])
    return section_items(section, limit=10)


def parse_latest_report(markdown: str) -> ParsedReport:
    raw_sections = parse_markdown_sections(markdown)
    title_match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    generated_match = re.search(r"Generated at:\s*(.+)", markdown)
    report = ParsedReport(
        title=title_match.group(1).strip() if title_match else "Market Intelligence Report",
        timestamp=generated_match.group(1).strip() if generated_match else None,
        executive_summary=None,
        market_snapshot={},
        positioning={},
        sector_signals=[],
        etf_flows={},
        news=[],
        opportunities=[],
        risks=[],
        signal_changes=[],
        raw_sections=raw_sections,
        markdown=markdown,
    )
    report.executive_summary = extract_executive_summary(report)
    report.market_snapshot = extract_market_snapshot(report)
    report.positioning = extract_positioning(report)
    report.sector_signals = extract_sector_signals(report)
    report.etf_flows = extract_etf_flows(report)
    report.news = extract_news(report)
    report.opportunities = extract_opportunities(report)
    report.risks = extract_risks(report)
    report.signal_changes = extract_signal_changes(report)
    return report


def fetch_latest_report() -> ParsedReport:
    now = time.monotonic()
    if REPORT_CACHE["report"] is not None and now < float(REPORT_CACHE["expires_at"]):
        return REPORT_CACHE["report"]
    report = parse_latest_report(fetch_report_markdown())
    REPORT_CACHE["report"] = report
    REPORT_CACHE["expires_at"] = now + 60
    return report


def html_bullet_lines(lines: list[str], *, limit: int = 6) -> str:
    return "\n".join(f"• {escape_html(line)}" for line in lines[:limit] if line)


def html_table(rows: list[dict[str, str]], columns: list[str], *, limit: int = 8) -> str:
    selected = rows[:limit]
    if not selected:
        return ""
    widths = {col: min(max(len(col), *(len(str(row.get(col, ""))) for row in selected)), 18) for col in columns}
    lines = [" ".join(col[: widths[col]].ljust(widths[col]) for col in columns)]
    for row in selected:
        lines.append(" ".join(str(row.get(col, ""))[: widths[col]].ljust(widths[col]) for col in columns))
    return "<pre>" + escape_html("\n".join(lines)) + "</pre>"


def report_links_markup() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Executive Dashboard", "url": settings.dashboard_url},
                {"text": "Full Report", "url": settings.report_url},
            ]
        ]
    }


def report_menu_text() -> str:
    return "\n".join(
        [
            "<b>Available Report Sections</b>",
            "Executive Summary      /report_executive",
            "Market Snapshot        /report_market",
            "Positioning            /report_positioning",
            "Sector Signals         /report_sectors",
            "ETF Flows              /report_etf",
            "Market News            /report_news",
            "Market News 10         /report_news_10",
            "Opportunities          /report_opportunities",
            "Risks                  /report_risks",
            "Signal Changes         /report_changes",
            "Full Report            /report_full",
            "Report Help            /report_help",
        ]
    )


def build_report_menu_message(report: ParsedReport) -> TelegramReply:
    top, bottom = sector_extremes(report.markdown)
    key_risk = report.risks[0] if report.risks else "Not available in latest report."
    summary_lines = (report.executive_summary or report_preview(report.markdown, max_lines=5)).splitlines()[:5]
    text = "\n\n".join(
        [
            f"<b>{escape_html(report.title)}</b>",
            f"<i>{escape_html(report.timestamp or hkt_timestamp())}</i>",
            html_bullet_lines(summary_lines, limit=5),
            f"<b>Top buy/overweight sector:</b> {escape_html(top or 'Not available')}",
            f"<b>Top sell/underweight sector:</b> {escape_html(bottom or 'Not available')}",
            f"<b>Key risk:</b> {escape_html(key_risk)}",
            f'<a href="{escape_html(settings.dashboard_url)}">Executive Dashboard</a> | <a href="{escape_html(settings.report_url)}">Full Report</a>',
            report_menu_text(),
        ]
    )
    return TelegramReply([text], reply_markup=report_links_markup())


def build_executive_message(report: ParsedReport) -> TelegramReply:
    top, bottom = sector_extremes(report.markdown)
    parts = [
        "<b>MARKET INTELLIGENCE</b>",
        "<b>EXECUTIVE SUMMARY</b>",
        f"<i>{escape_html(report.timestamp or hkt_timestamp())}</i>",
        html_bullet_lines((report.executive_summary or "").splitlines(), limit=8),
        f"<b>Top buy/overweight sector:</b> {escape_html(top or 'Not available')}",
        f"<b>Top sell/underweight sector:</b> {escape_html(bottom or 'Not available')}",
    ]
    if report.risks:
        parts.append(f"<b>Main near-term risk:</b> {escape_html(report.risks[0])}")
    return TelegramReply(["\n\n".join(part for part in parts if part.strip())], reply_markup=report_links_markup())


def build_market_message(report: ParsedReport) -> TelegramReply:
    rows = report.market_snapshot.get("tables") or []
    table = html_table(rows, list(rows[0].keys())[:4], limit=12) if rows else ""
    body = table or "<i>Market snapshot table is not available in the latest report.</i>"
    return TelegramReply([f"<b>MARKET INTELLIGENCE</b>\n<b>MARKET SNAPSHOT</b>\n<i>{hkt_timestamp()}</i>\n\n{body}"])


def build_positioning_message(report: ParsedReport) -> TelegramReply:
    section = report.positioning.get("section") or "Positioning data is not available in the latest report."
    return TelegramReply([f"<b>MARKET INTELLIGENCE</b>\n<b>POSITIONING</b>\n<i>{hkt_timestamp()}</i>\n\n{html_bullet_lines(section.splitlines(), limit=12)}"])


def build_sector_message(report: ParsedReport) -> TelegramReply:
    rows = report.sector_signals
    top, bottom = sector_extremes(report.markdown)
    table = html_table(rows, list(rows[0].keys())[:4], limit=12) if rows else ""
    text = f"<b>MARKET INTELLIGENCE</b>\n<b>SECTOR SIGNALS</b>\n<i>{hkt_timestamp()}</i>\n\n"
    text += f"<b>Top buy/overweight sector:</b> {escape_html(top or 'Not available')}\n"
    text += f"<b>Top sell/underweight sector:</b> {escape_html(bottom or 'Not available')}\n\n"
    text += table or "<i>Ranked sector table is not available.</i>"
    return TelegramReply([text])


def build_etf_flow_message(report: ParsedReport) -> TelegramReply:
    section = report.etf_flows.get("section", "")
    rows = report.etf_flows.get("tables") or []
    if not section and not rows:
        return TelegramReply(["<b>MARKET INTELLIGENCE</b>\n<b>ETF FLOW MONITOR</b>\n\nETF flow data is not available in the latest report."])
    parts = ["<b>MARKET INTELLIGENCE</b>", "<b>ETF FLOW MONITOR</b>", f"<i>{hkt_timestamp()}</i>"]
    if rows:
        parts.append("<b>FLOW TABLE</b>\n" + html_table(rows, list(rows[0].keys())[:4], limit=10))
    if section:
        parts.append("<b>KEY IMPLICATIONS</b>\n" + html_bullet_lines(section.splitlines(), limit=10))
    return TelegramReply(["\n\n".join(parts)])


def build_news_message(report: ParsedReport, limit: int = 5) -> TelegramReply:
    limit = max(1, min(limit, 10))
    if not report.news:
        return TelegramReply(["<b>MARKET INTELLIGENCE</b>\n<b>MARKET NEWS &amp; CATALYSTS</b>\n\nNo market-news section was found in the latest report."])
    lines = ["<b>MARKET INTELLIGENCE</b>", "<b>MARKET NEWS &amp; CATALYSTS</b>", f"<i>{hkt_timestamp()}</i>"]
    for idx, item in enumerate(report.news[:limit], start=1):
        lines.append(
            f"<b>{idx}. {escape_html(item.get('headline', 'Headline'))}</b>\n"
            f"Summary: {escape_html(item.get('summary') or 'Not specified')}\n"
            f"Market impact: {escape_html(item.get('impact') or 'Not specified')}\n"
            f"Related assets: {escape_html(item.get('related') or 'Not specified')}"
        )
    return TelegramReply(["\n\n".join(lines)])


def build_opportunities_message(report: ParsedReport) -> TelegramReply:
    body = html_bullet_lines(report.opportunities, limit=8) or "<i>Opportunities section is not available in the latest report.</i>"
    return TelegramReply([f"<b>MARKET INTELLIGENCE</b>\n<b>OPPORTUNITIES</b>\n<i>{hkt_timestamp()}</i>\n\n{body}"])


def build_risks_message(report: ParsedReport) -> TelegramReply:
    body = html_bullet_lines(report.risks, limit=8) or "<i>Risk section is not available in the latest report.</i>"
    return TelegramReply([f"<b>MARKET INTELLIGENCE</b>\n<b>RISKS</b>\n<i>{hkt_timestamp()}</i>\n\n{body}"])


def build_changes_message(report: ParsedReport) -> TelegramReply:
    body = html_bullet_lines(report.signal_changes, limit=10) or "<i>No explicit signal-change section was found in the latest report.</i>"
    return TelegramReply([f"<b>MARKET INTELLIGENCE</b>\n<b>SIGNAL CHANGES</b>\n<i>{hkt_timestamp()}</i>\n\n{body}"])


def build_full_report_messages(report: ParsedReport) -> TelegramReply:
    messages: list[str] = []
    for key, section in report.raw_sections.items():
        if section.strip():
            title = key.title()
            content = strip_markdown(section)
            for chunk in split_message(content, limit=3000):
                messages.append(f"<b>{escape_html(title)}</b>\n<pre>{escape_html(chunk)}</pre>")
    if not messages:
        for chunk in split_message(strip_markdown(report.markdown), limit=3000):
            messages.append(f"<b>{escape_html(report.title)}</b>\n<pre>{escape_html(chunk)}</pre>")
    total = len(messages)
    labelled = [f"<b>Full Report - {idx}/{total}</b>\n\n{message}" for idx, message in enumerate(messages, start=1)]
    labelled[-1] += f'\n\n<a href="{escape_html(settings.dashboard_url)}">Executive Dashboard</a> | <a href="{escape_html(settings.report_url)}">Original Full Report</a>'
    return TelegramReply(labelled, reply_markup=report_links_markup())


REPORT_COMMANDS: dict[str, Any] = {
    "/report_executive": build_executive_message,
    "/report_market": build_market_message,
    "/report_positioning": build_positioning_message,
    "/report_sectors": build_sector_message,
    "/report_etf": build_etf_flow_message,
    "/report_news": lambda report: build_news_message(report, limit=5),
    "/report_news_10": lambda report: build_news_message(report, limit=10),
    "/report_opportunities": build_opportunities_message,
    "/report_risks": build_risks_message,
    "/report_changes": build_changes_message,
    "/report_full": build_full_report_messages,
    "/report_help": lambda report: TelegramReply([report_menu_text()], reply_markup=report_links_markup()),
}


def build_report_reply(args: str = "") -> TelegramReply:
    report = fetch_latest_report()
    if args.strip():
        return TelegramReply(["Unknown report section. Use /report_help to view available sections."])
    return build_report_menu_message(report)


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
/start - Show available commands and bot features
/market - Show the latest market snapshot
/dashboard - Open the latest executive dashboard
/report - Browse the latest report by section
/report_executive - Show the executive summary and market regime
/report_market - Show the cross-asset market snapshot
/report_positioning - Show current asset and regional positioning
/report_sectors - Show sector rankings, top buy, and top sell signals
/report_etf - Show ETF inflows, outflows, and flow implications
/report_news - Show the five most material market news items and catalysts
/report_news_10 - Show up to ten market news items and catalysts
/report_opportunities - Show the highest-conviction investment opportunities
/report_risks - Show the main market risks and warning signals
/report_changes - Show recent signal, positioning, flow, and risk changes
/report_full - Show the complete report in structured messages
/report_help - Show all available report subsections
/signals - Show the latest buy and sell signals
/sectors - Show the top buy sector and top sell sector
/equity AAPL - Look up the latest data and signals for a stock
/risk - Show current market and system risk alerts
/status - Show bot, API, database, report, and data-update status
"""


def normalize_command(text: str) -> tuple[str, str]:
    command_text = text.strip()
    command, _sep, arg_text = command_text.partition(" ")
    return command.lower().split("@")[0], arg_text


def command_reply(text: str) -> TelegramReply:
    command, arg_text = normalize_command(text)
    report_commands = {"/dashboard", "/report", "/signals", "/sectors", "/risk", *REPORT_COMMANDS.keys()}
    if command == "/start":
        return TelegramReply([f"<pre>{escape_html(START_TEXT.strip())}</pre>"])
    if command not in {"/market", "/equity", "/status", *report_commands}:
        return TelegramReply(["Unknown command. Use /start to view available commands."])
    try:
        if command == "/market":
            return TelegramReply([f"<pre>{escape_html(market_snapshot_message())}</pre>"])
        if command == "/equity":
            if not arg_text.strip():
                return TelegramReply(["Usage: /equity AAPL"])
            return TelegramReply([f"<pre>{escape_html(equity_message(arg_text))}</pre>"])
        if command == "/status":
            return TelegramReply([f"<pre>{escape_html(status_message())}</pre>"])
        report = fetch_latest_report()
        if command == "/dashboard":
            return build_executive_message(report)
        if command == "/report":
            return build_report_reply(arg_text)
        if command in REPORT_COMMANDS:
            return REPORT_COMMANDS[command](report)
        if command == "/signals":
            return TelegramReply([f"<pre>{escape_html(signals_message(report.markdown))}</pre>"])
        if command == "/sectors":
            return build_sector_message(report)
        if command == "/risk":
            return build_risks_message(report)
    except HTTPException as exc:
        logger.warning("Command %s failed safely: %s", command, exc.detail)
        if command in report_commands:
            return TelegramReply(["The latest report is temporarily unavailable."])
        return TelegramReply([f"{escape_html(command)} is temporarily unavailable: {escape_html(exc.detail)}"])
    return TelegramReply(["Unknown command. Use /start to view available commands."])


def command_response(text: str) -> str:
    return "\n\n".join(command_reply(text).messages)


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
    # Temporary diagnostic: remove the numeric chat ID after TG_CHAT_ID is confirmed.
    logger.info("Incoming Telegram command chat_id=%s text=%r", chat_id, text)

    if not text or not chat_id:
        return BotResponse(ok=True, handled=False, message="No text command found")

    reply = command_reply(text)
    try:
        for idx, message_text in enumerate(reply.messages):
            await send_telegram_message(
                chat_id,
                message_text,
                parse_mode=reply.parse_mode,
                reply_markup=reply.reply_markup if idx == len(reply.messages) - 1 else None,
            )
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
