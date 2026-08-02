from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import main


SAMPLE_REPORT = """# Rule-Based Institutional Market Update

Generated at: 21 July 2026, 12:11:19 (HKT)

## Executive Dashboard

- Regime score: **61 / 100**
- US equity strength: **70 / 100**

## Macro Snapshot

| Asset | Level | Change | Signal |
|---|---|---|---|
| S&P 500 | 6400 | 0.5% | Up |
| VIX | 18 | -2.0% | Calmer |

## Official Sector Strength

| Rank | Sector | Score |
|---|---|---|
| 1 | Technology | 81 |
| 2 | Healthcare | 74 |
| 3 | Utilities | 34 |

## Positioning

- Equities: Selective
- Credit: Neutral
- Duration: Hold

## ETF Flow Monitor

| ETF | Flow | Trend |
|---|---|---|
| IVV | 1.2bn | Inflow |
| HYG | -250mn | Outflow |

- ETF flows show selective equity demand.
- Credit flows are mixed.

## Market News

| Headline | Summary | Market Impact | Related |
|---|---|---|---|
| Fed comments drive rates | Policy uncertainty remains active | Mixed | Bonds |
| AI earnings beat | Large-cap technology sentiment improves | Positive | Technology |
| Oil supply risk rises | Energy risk premium increases | Mixed | Oil |
| Retail sales soften | Growth data cools | Negative | Consumer |
| Gold demand rises | Hedge demand improves | Mixed | Gold |
| Dollar weakens | FX pressure eases | Positive | EM |

## Highest Conviction Opportunities

- Cybersecurity leadership remains constructive.
- Healthcare has defensive growth support.

## Three-Month Outperformance Setup

| Rank | Theme | Score | Classification |
|---|---|---|---|
| 1 | Semiconductors | 78 | strong setup |

## Volatility and Risk Signals

- VIX is elevated.

## Signal Changes

- New ETF flow reversal in high-yield credit.
"""


@pytest.fixture()
def client(tmp_path):
    main.REPORT_CACHE["expires_at"] = 0.0
    main.REPORT_CACHE["report"] = None
    main.settings = main.Settings(
        tg_token="token",
        tg_chat_id="123",
        tg_username="Ai_market_monitor_bot",
        webhook_secret="shared-secret",
        render_base_url="https://market-intelligence-telegram-bot.onrender.com",
        market_api_base_url="https://api.example.com",
        report_url="https://reports.example.com/latest.md",
        dashboard_url="https://dashboard.example.com/#overview",
        request_timeout_seconds=1,
        report_hash_state_path=tmp_path / "report_hash.json",
    )
    return TestClient(main.app)


def auth_headers(secret: str = "shared-secret") -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def test_correct_secret_accepted(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_to_default_chat", sender)

    response = client.post("/tasks/send/morning/shared-secret", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["message"] == "Morning report sent"


def test_wrong_path_secret_rejected(client):
    response = client.post("/tasks/send/morning/wrong-secret", headers=auth_headers())

    assert response.status_code == 403


def test_missing_header_secret_rejected(client):
    response = client.post("/tasks/send/morning/shared-secret")

    assert response.status_code == 403


def test_wrong_header_secret_rejected(client):
    response = client.post("/tasks/send/morning/shared-secret", headers=auth_headers("wrong-secret"))

    assert response.status_code == 403


def test_unknown_report_kind_rejected(client):
    response = client.post("/tasks/send/weekly/shared-secret", headers=auth_headers())

    assert response.status_code == 400


def test_blank_alert_message_rejected(client):
    response = client.post(
        "/tasks/alert/shared-secret",
        headers=auth_headers(),
        json={"message": "   ", "severity": "warning"},
    )

    assert response.status_code == 422


def test_invalid_alert_severity_rejected(client):
    response = client.post(
        "/tasks/alert/shared-secret",
        headers=auth_headers(),
        json={"message": "Macro failed", "severity": "urgent"},
    )

    assert response.status_code == 422


def test_morning_endpoint_calls_telegram_sender(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_to_default_chat", sender)

    response = client.post("/tasks/send/morning/shared-secret", headers=auth_headers())

    assert response.status_code == 200
    sender.assert_awaited_once()
    assert "Morning Positioning Report" in sender.await_args.args[0]


def test_eod_endpoint_calls_telegram_sender(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_to_default_chat", sender)

    response = client.post("/tasks/send/eod/shared-secret", headers=auth_headers())

    assert response.status_code == 200
    sender.assert_awaited_once()
    assert "End-of-Day Market Report" in sender.await_args.args[0]


def test_report_update_sends_when_changed(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_to_default_chat", sender)

    response = client.post("/tasks/send/report-update/shared-secret", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["handled"] is True
    assert response.json()["message"] == "Report update notification sent"
    sender.assert_awaited_once()


def test_report_update_does_not_send_when_unchanged(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_to_default_chat", sender)

    client.post("/tasks/send/report-update/shared-secret", headers=auth_headers())
    response = client.post("/tasks/send/report-update/shared-secret", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["handled"] is False
    assert response.json()["message"] == "Report has not changed"
    assert sender.await_count == 1


def test_report_update_force_sends_even_when_unchanged(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_to_default_chat", sender)

    client.post("/tasks/send/report-update/shared-secret", headers=auth_headers())
    response = client.post("/tasks/send/report-update/shared-secret?force=true", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["handled"] is True
    assert sender.await_count == 2


def test_telegram_webhook_recognizes_start(client, monkeypatch):
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_telegram_message", sender)

    response = client.post(
        "/telegram/webhook/shared-secret",
        headers=auth_headers(),
        json={"message": {"text": "/start", "chat": {"id": "123"}}},
    )

    assert response.status_code == 200
    assert response.json()["handled"] is True
    sender.assert_awaited_once()
    assert "Market Intelligence Bot" in sender.await_args.args[1]


def test_telegram_webhook_recognizes_addressed_start(client, monkeypatch):
    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "send_telegram_message", sender)

    response = client.post(
        "/telegram/webhook/shared-secret",
        headers=auth_headers(),
        json={"message": {"text": "/start@Ai_market_monitor_bot", "chat": {"id": "123"}}},
    )

    assert response.status_code == 200
    assert response.json()["handled"] is True
    assert "Market Intelligence Bot" in sender.await_args.args[1]


REQUIRED_COMMANDS = [
    "/start",
    "/market",
    "/dashboard",
    "/report",
    "/report_executive",
    "/report_market",
    "/report_positioning",
    "/report_sectors",
    "/report_etf",
    "/report_news",
    "/report_news_10",
    "/report_opportunities",
    "/report_risks",
    "/report_changes",
    "/report_full",
    "/report_help",
    "/signals",
    "/sectors",
    "/equity AAPL",
    "/risk",
    "/status",
]


def test_start_includes_every_required_command(client):
    response = main.command_response("/start")

    for command in REQUIRED_COMMANDS:
        assert command in response


def test_start_does_not_include_macro(client):
    response = main.command_response("/start")

    assert "/macro" not in response


def test_report_returns_menu_and_summary(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report")

    assert "Available Report Sections" in response
    assert "/report_etf" in response
    assert "/report_news_10" in response
    assert "Top buy/overweight sector" in response


def test_report_help_returns_subsection_menu(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_help")

    assert "ETF Flows" in response
    assert "Market News" in response


def test_report_executive_selects_formatter(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_executive")

    assert "EXECUTIVE SUMMARY" in response
    assert "Top buy/overweight sector" in response


def test_report_market_selects_market_formatter(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_market")

    assert "MARKET SNAPSHOT" in response
    assert "<pre>" in response
    assert "S&amp;P 500" in response


def test_report_positioning_selects_formatter(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_positioning")

    assert "POSITIONING" in response
    assert "Equities: Selective" in response


def test_report_sectors_selects_formatter(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_sectors")

    assert "SECTOR SIGNALS" in response
    assert "Technology" in response


def test_report_etf_selects_etf_flow_formatter(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_etf")

    assert "ETF FLOW MONITOR" in response
    assert "IVV" in response


def test_etf_flow_aliases_are_recognized(client):
    report = main.parse_latest_report("# Test\n\n## Fund Flows\n\n- SPY inflow\n")

    assert report.etf_flows["section"]
    assert "SPY inflow" in main.build_etf_flow_message(report).messages[0]


def test_missing_etf_data_fallback(client):
    report = main.parse_latest_report("# Test\n\n## Executive Dashboard\n\n- ok\n")

    response = main.build_etf_flow_message(report).messages[0]

    assert "ETF flow data is not available" in response


def test_report_news_defaults_to_five_items(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_news")

    assert "MARKET NEWS" in response
    assert response.count("<b>") >= 7
    assert "6. Dollar" not in response


def test_report_news_10_returns_at_most_ten(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_news_10")

    assert "6. Dollar" in response
    assert "11." not in response


def test_space_based_news_count_is_not_supported(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report news 10")

    assert "Unknown report section" in response


def test_news_heading_aliases_are_recognized(client):
    report = main.parse_latest_report("# Test\n\n## Key Headlines\n\n- CPI data moves rates\n")

    assert report.news[0]["headline"] == "CPI data moves rates"


def test_missing_news_data_fallback(client):
    report = main.parse_latest_report("# Test\n\n## Executive Dashboard\n\n- ok\n")

    response = main.build_news_message(report).messages[0]

    assert "No market-news section was found" in response


def test_report_opportunities_works(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_opportunities")

    assert "OPPORTUNITIES" in response
    assert "Cybersecurity" in response


def test_report_risks_works(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_risks")

    assert "RISKS" in response
    assert "VIX" in response


def test_report_changes_works(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_changes")

    assert "SIGNAL CHANGES" in response
    assert "high-yield" in response


def test_report_full_returns_multiple_safe_chunks(client):
    long_report = "# Test\n\n" + "\n\n".join(f"## Section {idx}\n\n" + ("content\n" * 80) for idx in range(8))
    report = main.parse_latest_report(long_report)

    reply = main.build_full_report_messages(report)

    assert len(reply.messages) > 1
    assert all(len(message) <= main.TELEGRAM_LIMIT for message in reply.messages)
    assert "Full Report - 1/" in reply.messages[0]


def test_report_group_command_syntax_is_recognized(client, monkeypatch):
    monkeypatch.setattr(main, "fetch_report_markdown", lambda: SAMPLE_REPORT)

    response = main.command_response("/report_etf@Ai_market_monitor_bot")

    assert "ETF FLOW MONITOR" in response


def test_equity_with_ticker_works(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "fetch_json",
        lambda path: {
            "found": True,
            "data": {
                "name": "Apple Inc.",
                "date": "2026-07-10",
                "close": 200.0,
                "pct_chg": 1.2,
                "rsi_14": 55.0,
                "return_20d": 3.0,
                "return_60d": 8.0,
            },
        },
    )

    response = main.command_response("/equity AAPL")

    assert "Equity Snapshot: AAPL" in response
    assert "Apple Inc." in response


def test_equity_without_ticker_returns_usage(client):
    response = main.command_response("/equity")

    assert response == "Usage: /equity AAPL"


def test_equity_group_command_suffix_works(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "fetch_json",
        lambda path: {"found": True, "data": {"name": "Apple Inc.", "close": 200.0}},
    )

    response = main.command_response("/equity@Ai_market_monitor_bot AAPL")

    assert "Equity Snapshot: AAPL" in response


def test_macro_is_unknown_command(client):
    response = main.command_response("/macro")

    assert response == "Unknown command. Use /start to view available commands."


def test_unknown_command_returns_required_fallback(client):
    response = main.command_response("/unknown")

    assert response == "Unknown command. Use /start to view available commands."


def test_dynamic_html_is_escaped(client):
    report = main.parse_latest_report("# Test\n\n## ETF Flow\n\n- <script>alert(1)</script>\n")

    response = main.build_etf_flow_message(report).messages[0]

    assert "&lt;script&gt;" in response
    assert "<script>" not in response


def test_external_report_fetch_failure_produces_user_friendly_reply(client, monkeypatch):
    def raise_report_failure():
        raise main.HTTPException(status_code=502, detail="Report service unavailable")

    monkeypatch.setattr(main, "fetch_report_markdown", raise_report_failure)

    response = main.command_response("/report")

    assert response == "The latest report is temporarily unavailable."


def test_webhook_returns_200_after_handled_command_error(client, monkeypatch):
    def raise_report_failure():
        raise main.HTTPException(status_code=502, detail="Report service unavailable")

    sender = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "fetch_report_markdown", raise_report_failure)
    monkeypatch.setattr(main, "send_telegram_message", sender)

    response = client.post(
        "/telegram/webhook/shared-secret",
        headers=auth_headers(),
        json={"message": {"text": "/report", "chat": {"id": "123"}}},
    )

    assert response.status_code == 200
    assert response.json()["handled"] is True
    assert "temporarily unavailable" in sender.await_args.args[1]
