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
    welcome = bot.handle(1001, "u", "/start")
    assert "Как подключиться" in welcome and "Пример уведомления" in welcome   # гайд новичку без кода
    assert "Как пользоваться" in bot.handle(1001, "u", "/guide")
    assert "после подключения" in bot.handle(1001, "u", "/top")             # данных клиентов без кода нет
    assert "user-0005" not in bot.handle(1001, "u", "/client 5")
    assert "Код не подошёл" in bot.handle(1001, "u", "/start wrong-code")
    connected = bot.handle(1001, "u", "/start link-code-123")
    assert "Готово" in connected and "Под угрозой" in connected
    assert "Под угрозой" in bot.handle(1001, "u", "/summary")
    assert "score" in bot.handle(1001, "u", "/client 5")
    today = bot.handle(1001, "u", "/today")
    assert "Связаться" in today and "/app/retention" in today and "сохраним" in today
    assert "после подключения" in bot.handle(2002, "u", "/today")

    from datetime import datetime
    from app.telegram import MSK
    bot.digest_hour = 9
    assert bot.digest(datetime(2026, 10, 24, 8, 59, tzinfo=MSK)) is None
    assert "План на сегодня" in bot.digest(datetime(2026, 10, 24, 9, 1, tzinfo=MSK))
    assert bot.digest(datetime(2026, 10, 24, 9, 30, tzinfo=MSK)) is None      # раз в день
    assert bot.digest(datetime(2026, 10, 25, 9, 0, tzinfo=MSK)) is not None

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


def test_new_feature_is_picked_up_everywhere(client, auth):
    """Расширяемость: признак из README добавляется одним классом и строкой в FEATURES."""
    from app.scoring.features import FEATURES, Feature, ServiceContext, Signal

    class WithdrawableBalance(Feature):
        key = "withdrawable_balance"
        title = "Деньги можно вывести"
        description = "Большая часть баланса — реферальные средства."
        default_weight = 10

        def evaluate(self, ctx: ServiceContext) -> Signal | None:
            a = ctx.account
            if a.balance == 0 or a.withdrawBalance / a.balance < 0.5:
                return None
            return Signal(a.withdrawBalance / a.balance, "Больше половины баланса можно вывести")

    FEATURES.append(WithdrawableBalance())
    try:
        client.app.state.radar.recalculate("test")
        settings = client.get("/api/v1/model/settings", headers=auth).json()
        assert settings["settings"]["features"]["withdrawable_balance"] == {"weight": 10, "enabled": True}
        assert any(f["key"] == "withdrawable_balance" for f in settings["features"])
        hit = [c for c in client.app.state.radar.current.result.customers
               if any(x.feature == "withdrawable_balance" for x in c.contributions)]
        assert hit, "признак должен сработать хотя бы у одного клиента"
        detail = client.get(f"/api/v1/customers/{hit[0].account.id}", headers=auth).json()
        assert any(x["feature"] == "withdrawable_balance" for x in detail["contributions"])
        r = client.patch("/api/v1/model/settings", headers=auth, json={"features": {"withdrawable_balance": {"weight": 25}}})
        assert r.status_code == 200
    finally:
        FEATURES.pop()
        # признак убрали из кода — сохранённый вес не мешает загрузке настроек и пересчёту
        client.app.state.radar.recalculate("test")
        assert "withdrawable_balance" not in client.get("/api/v1/model/settings", headers=auth).json()["settings"]["features"]


def test_cause_filter_and_csv(client, auth):
    summary = client.get("/api/v1/analytics/summary", headers=auth).json()
    assert summary["causes"], "у данных кейса есть клиенты под риском"
    top = summary["causes"][0]
    page = client.get(f"/api/v1/customers?cause={top['feature']}&pageSize=200", headers=auth).json()
    assert page["total"] == top["customers"]
    csv = client.get(f"/api/v1/customers/export.csv?cause={top['feature']}", headers=auth)
    assert csv.status_code == 200 and csv.text.count("\n") == top["customers"] + 1
    assert "churn_radar_expected_loss_by_cause_eur" in client.get("/metrics", headers=auth).text


def test_own_snapshot_changes_results(client, auth):
    """Как будет проверять жюри: свой снимок в формате кейса через PUT — всё пересчитано из него."""
    own = {
        "accounts": [{"id": 1, "email": "jury@example.test", "balance": 0, "withdrawBalance": 0,
                      "totalReplenished": 5000, "bonusBalance": 0, "bonusState": "locked"}],
        "services": [{"id": 1, "name": "jury-vps", "ownerId": 1, "paymentTerm": "month", "autoProlong": False,
                      "createdAt": "2026-10-01T00:00:00.000Z", "expiresAt": "2026-10-20T00:00:00.000Z",
                      "status": "prolong_wait", "price": 900}],
        "transactions": [{"id": 1, "amount": 900, "status": "not_performed", "performedAt": None,
                          "createdAt": "2026-10-01T00:00:00.000Z", "type": "order", "serviceId": 1}],
    }
    assert client.put("/api/v1/source/snapshot", headers=auth, json=own).status_code == 200
    s = client.get("/api/v1/analytics/summary", headers=auth).json()
    assert s["customersTotal"] == 1 and s["levels"]["high"] == 1 and s["expectedLoss"] > 0 and s["ltvAtRisk"] > s["expectedLoss"]
    items = client.get("/api/v1/customers", headers=auth).json()["items"]
    assert items[0]["email"] == "jury@example.test"
    metrics = client.get("/metrics", headers=auth).text
    assert 'churn_radar_customers{level="high"} 1.0' in metrics
    client.post("/api/v1/source/preset/case", headers=auth)


def test_csrf_origin_and_settings_version(client, auth):
    evil = client.patch("/api/v1/model/settings", headers={**auth, "Origin": "https://evil.example"},
                        json={"horizonDays": 31})
    assert evil.status_code == 403
    same = client.patch("/api/v1/model/settings", headers={**auth, "Origin": "http://testserver"}, json={"horizonDays": 30})
    assert same.status_code == 200
    version = client.get("/api/v1/model/settings", headers=auth).json()["version"]
    stale = client.patch(f"/api/v1/model/settings?expectedVersion={version - 1}", headers=auth, json={"horizonDays": 31})
    assert stale.status_code == 409


def test_what_if_and_renewals(client, auth):
    client.post("/api/v1/source/preset/extended", headers=auth)
    scenarios = client.get("/api/v1/customers/62/what-if", headers=auth).json()
    assert scenarios and all(s["saved"] > 0 for s in scenarios)
    assert scenarios[0]["score"] < client.get("/api/v1/customers/62", headers=auth).json()["score"]
    assert client.get("/api/v1/customers/9999/what-if", headers=auth).status_code == 404
    cal = client.get("/api/v1/analytics/renewals?days=14", headers=auth).json()
    assert cal and {"no_money", "no_autoprolong"} & {x["problem"] for x in cal}
    detail = client.get("/api/v1/customers/62", headers=auth).json()
    assert detail["history"] and detail["history"][-1]["score"] == detail["score"]
    client.post("/api/v1/source/preset/case", headers=auth)


def test_retention_actions_message_and_privacy(client, auth):
    assert client.post("/api/v1/customers/5/actions", headers=auth, json={"kind": "churned"}).status_code == 422
    r = client.post("/api/v1/customers/5/actions", headers=auth, json={"kind": "called", "note": "обещал оплатить"})
    assert r.status_code == 201 and r.json()["author"] == "admin"
    client.post("/api/v1/customers/9/actions", headers=auth, json={"kind": "churned", "reason": "Дорого"})
    assert client.get("/api/v1/customers/5/actions", headers=auth).json()[0]["note"] == "обещал оплатить"
    items = {i["id"]: i for i in client.get("/api/v1/customers?pageSize=200", headers=auth).json()["items"]}
    assert items[5]["lastAction"] == "called"
    ret = client.get("/api/v1/analytics/summary", headers=auth).json()["retention"]
    assert ret["actions"]["called"] >= 1 and ret["reasons"]["Дорого"] >= 1 and ret["churned"] >= 1
    msg = client.get("/api/v1/customers/5/message", headers=auth).json()
    assert msg["subject"] and "Здравствуйте" in msg["text"] and msg["generatedBy"] == "template"
    plain = client.get("/api/v1/customers/export.csv", headers=auth).text
    assert "@example.test" not in plain and "работа" in plain.splitlines()[0]
    assert "@example.test" in client.get("/api/v1/customers/export.csv?includeEmail=true", headers=auth).text
    assert 'churn_radar_retention_actions{kind="called"}' in client.get("/metrics", headers=auth).text


def test_external_signals(client, auth):
    before = client.get("/api/v1/customers/1", headers=auth).json()["score"]
    r = client.post("/api/v1/source/events", headers=auth,
                    json={"type": "signal", "name": "loadDrop", "targetId": 21, "value": 0.9})
    assert r.status_code == 200
    d = client.get("/api/v1/customers/1", headers=auth).json()
    assert any(c["feature"] == "usage_drop" for c in d["contributions"]) and d["score"] >= before
    client.post("/api/v1/source/events", headers=auth, json={"type": "signal", "name": "supportTickets30d", "targetId": 1, "value": 3})
    d = client.get("/api/v1/customers/1", headers=auth).json()
    assert any(c["feature"] == "support_tickets" for c in d["contributions"])
    assert client.post("/api/v1/source/events", headers=auth,
                       json={"type": "signal", "name": "loadDrop", "targetId": 99999, "value": 0.5}).status_code == 404
    client.post("/api/v1/source/preset/case", headers=auth)


def _login(client, login, password):
    r = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"]
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_roles_and_team(client, auth):
    me = client.get("/api/v1/auth/me", headers=auth).json()
    assert me["role"] == "admin"
    for login, role in (("viewer1", "viewer"), ("market1", "marketer")):
        r = client.post("/api/v1/users", headers=auth, json={"login": login, "displayName": login, "password": "pass-2026-long", "role": role})
        assert r.status_code == 201 and r.json()["role"] == role
    assert client.post("/api/v1/users", headers=auth, json={"login": "viewer1", "displayName": "x", "password": "pass-2026-long"}).status_code == 409
    viewer, marketer = _login(client, "viewer1", "pass-2026-long"), _login(client, "market1", "pass-2026-long")

    assert client.get("/api/v1/customers", headers=viewer).status_code == 200
    assert client.get("/api/v1/retention/queue", headers=viewer).status_code == 200
    assert client.patch("/api/v1/model/settings", headers=viewer, json={"horizonDays": 30}).status_code == 403
    assert client.post("/api/v1/customers/5/actions", headers=viewer, json={"kind": "called"}).status_code == 403
    assert client.post("/api/v1/customers/5/actions", headers=marketer, json={"kind": "called"}).status_code == 201
    assert client.patch("/api/v1/model/settings", headers=marketer, json={"horizonDays": 30}).status_code == 200
    assert client.post("/api/v1/source/preset/case", headers=marketer).status_code == 403
    assert client.post("/api/v1/users", headers=marketer, json={"login": "x1", "displayName": "x", "password": "pass-2026-long"}).status_code == 403

    team = {m["login"]: m for m in client.get("/api/v1/users", headers=viewer).json()}
    assert team["market1"]["actions"] >= 1 and team["admin"]["role"] == "admin"
    admin_id = team["admin"]["id"]
    assert client.delete(f"/api/v1/users/{admin_id}", headers=auth).status_code == 409
    assert client.patch(f"/api/v1/users/{admin_id}", headers=auth, json={"role": "viewer"}).status_code == 409
    assert client.patch(f"/api/v1/users/{team['viewer1']['id']}", headers=auth, json={"role": "marketer"}).json()["role"] == "marketer"
    assert client.delete(f"/api/v1/users/{team['viewer1']['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/customers", headers=viewer).status_code == 401


def test_retention_queue_and_paid_loss(client, auth):
    q = client.get("/api/v1/retention/queue?saveRate=30&callCost=3", headers=auth).json()
    s = q["summary"]
    assert s["customers"] == len(q["items"]) == sum(s["buckets"].values()) > 0
    assert all(x["level"] in ("high", "medium") for x in q["items"])
    order = ["today", "week", "later"]
    assert [order.index(x["bucket"]) for x in q["items"]] == sorted(order.index(x["bucket"]) for x in q["items"])
    assert all((x["channel"] == "call") == (x["expectedSaved"] > 3) for x in q["items"])
    item5 = next(x for x in q["items"] if x["customerId"] == 5)
    assert item5["deadlineKind"] == "overdue" and item5["bucket"] == "today"
    assert item5["expectedLossPaid"] < item5["expectedLoss"] / 5  # почасовой сервер: клиент столько не платит

    summary = client.get("/api/v1/analytics/summary", headers=auth).json()
    assert 0 < summary["expectedLossPaid"] < summary["expectedLoss"]
    assert client.get("/api/v1/customers/5", headers=auth).json()["finances"]["paidMonthly"] > 0

    before = s["savedFact"]
    client.post("/api/v1/customers/5/actions", headers=auth, json={"kind": "renewed"})
    after = client.get("/api/v1/retention/queue", headers=auth).json()
    assert after["summary"]["savedFact"] > before
    assert next(x for x in after["items"] if x["customerId"] == 5)["status"] == "done"
    # «не дозвонились, перезвонить завтра» → после «+1 день» клиент в «Сегодня» с пометкой
    target = next(x for x in after["items"] if x["bucket"] != "today" and x["status"] == "new")
    r = client.post(f"/api/v1/customers/{target['customerId']}/actions", headers=auth, json={"kind": "no_answer", "followUpDays": 1})
    assert r.status_code == 201 and r.json()["followUp"]
    item = next(x for x in client.get("/api/v1/retention/queue", headers=auth).json()["items"] if x["customerId"] == target["customerId"])
    assert item["followUp"] and not item["followUpDue"] and item["riskAtContact"] is not None
    client.post("/api/v1/source/simulate", headers=auth, json={"days": 1})
    moved = client.get("/api/v1/retention/queue?callsPerDay=3", headers=auth).json()
    item = next((x for x in moved["items"] if x["customerId"] == target["customerId"]), None)
    assert item is None or (item["followUpDue"] and item["bucket"] == "today")
    calls = sum(1 for x in moved["items"] if x["channel"] == "call" and x["status"] != "done")
    assert moved["summary"]["planned"] == min(3, calls) == sum(x["planned"] for x in moved["items"]) > 0
    eff = client.get("/api/v1/retention/effect", headers=auth).json()
    assert any(x["customerId"] == 5 for x in eff) and all(x["reduced"] >= 0 for x in eff)
    client.post("/api/v1/source/reset", headers=auth)

    csv_plain = client.get("/api/v1/retention/queue.csv", headers=auth).text
    assert "@example.test" not in csv_plain and "канал" in csv_plain.splitlines()[0]
    assert "@example.test" in client.get("/api/v1/retention/queue.csv?includeEmail=true", headers=auth).text
    metrics = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).text
    assert "churn_radar_expected_loss_paid_eur" in metrics and "churn_radar_retention_saved_eur" in metrics
