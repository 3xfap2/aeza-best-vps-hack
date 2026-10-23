"""Сквозные проверки API на настоящем PostgreSQL.

База берётся из TEST_DATABASE_URL (по умолчанию локальная radar_test) и пересоздаётся.
Если сервер недоступен, тесты пропускаются.
"""
import json
import os
from pathlib import Path

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://radar@127.0.0.1:5434/postgres")
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://radar@127.0.0.1:5434/radar_test")
CASE = Path(__file__).resolve().parents[2] / "data" / "case" / "data.json"
PASSWORD = "test-password-2026"
METRICS_TOKEN = "test-metrics-token-2026"


@pytest.fixture(scope="module")
def client():
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as conn:
            conn.execute("DROP DATABASE IF EXISTS radar_test WITH (FORCE)")
            conn.execute("CREATE DATABASE radar_test")
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL недоступен: {e}")
    os.environ.update(DATABASE_URL=TEST_URL, ADMIN_PASSWORD=PASSWORD, METRICS_TOKEN=METRICS_TOKEN,
                      SEED_PATH=str(CASE))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    r = client.post("/api/v1/auth/login", json={"login": "admin", "password": PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_everything_requires_auth(client):
    for path in ("/api/v1/customers", "/api/v1/customers/1", "/api/v1/analytics/summary",
                 "/api/v1/model/settings", "/api/v1/auth/me", "/metrics", "/api/v1/stream"):
        assert client.get(path).status_code == 401, path
    assert client.put("/api/v1/source/snapshot", json={}).status_code == 401
    assert client.patch("/api/v1/model/settings", json={}).status_code == 401


def test_docs_and_openapi_available(client):
    assert client.get("/api/v1/docs").status_code == 200
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    for p in ("/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/customers",
              "/api/v1/customers/{customer_id}", "/api/v1/analytics/summary", "/api/v1/model/settings",
              "/api/v1/source/snapshot", "/metrics"):
        assert p in paths, p


def test_cookie_login_me_logout(client):
    r = client.post("/api/v1/auth/login", json={"login": "ADMIN", "password": PASSWORD})
    assert r.status_code == 200 and "radar_session" in r.cookies
    assert client.get("/api/v1/auth/me").json()["login"] == "admin"
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("field,key", [("id", "id"), ("email", "email"), ("balance", "balance"),
                                       ("totalReplenished", "totalReplenished")])
def test_sorting_both_directions(client, auth, field, key):
    asc = client.get(f"/api/v1/customers?sort={field}&order=asc&pageSize=200", headers=auth).json()["items"]
    desc = client.get(f"/api/v1/customers?sort={field}&order=desc&pageSize=200", headers=auth).json()["items"]
    values = [i[key] for i in asc]
    assert values == sorted(values)
    assert [i[key] for i in desc] == sorted(values, reverse=True)


def test_score_sort_puts_unscored_last(client, auth):
    for order in ("asc", "desc"):
        items = client.get(f"/api/v1/customers?sort=score&order={order}", headers=auth).json()["items"]
        scores = [i["score"] for i in items]
        assert scores[-4:] == [None] * 4


def test_customer_detail_and_404(client, auth):
    d = client.get("/api/v1/customers/5", headers=auth).json()
    assert d["level"] == "high" and d["contributions"] and d["actions"] and d["issues"]
    assert len(d["services"]) == 3 and d["transactions"]
    assert client.get("/api/v1/customers/9999", headers=auth).status_code == 404


def test_patch_settings_recalculates_and_persists(client, auth):
    before = client.get("/api/v1/customers/1", headers=auth).json()["score"]
    r = client.patch("/api/v1/model/settings", headers=auth,
                     json={"features": {"expiring_no_autoprolong": {"weight": 70}}})
    assert r.status_code == 200
    after = client.get("/api/v1/customers/1", headers=auth).json()["score"]
    assert after > before
    from app import db
    with client.app.state.pool.connection() as conn:
        stored, _ = db.current_settings(conn)
    assert stored.features["expiring_no_autoprolong"].weight == 70
    bad = client.patch("/api/v1/model/settings", headers=auth, json={"thresholds": {"high": 10}})
    assert bad.status_code == 422


def test_metrics_match_summary(client, auth):
    summary = client.get("/api/v1/analytics/summary", headers=auth).json()
    text = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).text
    assert f'churn_radar_expected_loss_eur{{horizon_days="30"}} {summary["expectedLoss"]}' in text
    for level, count in summary["levels"].items():
        assert f'churn_radar_customers{{level="{level}"}} {float(count)}' in text


def test_snapshot_put_validates_and_replaces(client, auth):
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(raw))
    broken["transactions"][0]["amount"] = 5000
    assert client.put("/api/v1/source/snapshot", headers=auth, json=broken).status_code == 422

    raw["accounts"][4]["balance"] = 1_000_000  # клиент 5 пополнил счёт
    raw["services"][4]["status"] = "deleted"
    r = client.put("/api/v1/source/snapshot", headers=auth, json=raw)
    assert r.status_code == 200 and r.json()["statusChanges"] == 1
    d = client.get("/api/v1/customers/5", headers=auth).json()
    assert not any(c["feature"] == "insufficient_funds" for c in d["contributions"])


def test_event_and_simulation(client, auth):
    r = client.post("/api/v1/source/events", headers=auth, json={"type": "topup", "accountId": 1, "amount": 10})
    assert r.status_code == 200
    assert client.post("/api/v1/source/events", headers=auth,
                       json={"type": "pay_renewal", "serviceId": 3}).status_code == 409
    sim = client.post("/api/v1/source/simulate", headers=auth, json={"days": 1}).json()
    assert sim["notes"]
    timeline = client.get("/api/v1/analytics/churn-timeline", headers=auth).json()
    assert sum(p["factRecorded"] for p in timeline["points"]) > 0
    assert client.post("/api/v1/source/reset", headers=auth).status_code == 200


def test_login_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"login": "nobody", "password": "x"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"login": "nobody", "password": "x"}).status_code == 429


def test_telegram_bot_requires_link_code_and_alerts(client, auth):
    from app.telegram import TelegramBot
    bot = TelegramBot("0:test", "link-code-123", client.app.state.radar, "http://localhost:8080")
    assert "код доступа" in bot.handle(1001, "u", "/top")
    assert "код доступа" in bot.handle(1001, "u", "/start wrong-code")
    assert "Готово" in bot.handle(1001, "u", "/start link-code-123")
    assert "Под угрозой" in bot.handle(1001, "u", "/summary")
    assert "score" in bot.handle(1001, "u", "/client 5")

    radar = client.app.state.radar
    before = radar.current.result
    client.patch("/api/v1/model/settings", headers=auth, json={"thresholds": {"high": 31}})
    bot.on_recalculated(before, radar.current.result, "settings")
    chat_id, text = bot._outbox.get_nowait()
    assert chat_id == 1001 and "В высокий риск перешли" in text
    client.patch("/api/v1/model/settings", headers=auth, json={"thresholds": {"high": 60}})
    assert "отключены" in bot.handle(1001, "u", "/stop")


def test_presets(client, auth):
    r = client.post("/api/v1/source/preset/extended", headers=auth)
    assert r.status_code == 200 and r.json()["accounts"] == 200
    assert client.get("/api/v1/customers/1", headers=auth).json()["email"] == "user-0001@example.test"
    r = client.post("/api/v1/source/preset/case", headers=auth)
    assert r.status_code == 200 and r.json()["accounts"] == 10
    assert client.post("/api/v1/source/preset/other", headers=auth).status_code == 422
