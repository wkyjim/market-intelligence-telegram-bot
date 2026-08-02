from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import main


SAMPLE_REPORT = """# Rule-Based Institutional Market Update

## Executive Dashboard

- Regime score: **61 / 100**
- US equity strength: **70 / 100**

## Official Sector Strength

| Rank | Sector | Score |
|---|---|---|
| 1 | Technology | 81 |
| 2 | Healthcare | 74 |
| 3 | Utilities | 34 |

## Three-Month Outperformance Setup

| Rank | Theme | Score | Classification |
|---|---|---|---|
| 1 | Semiconductors | 78 | strong setup |

## Volatility and Risk Signals

- VIX is elevated.
"""


@pytest.fixture()
def client(tmp_path):
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
