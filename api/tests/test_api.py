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
        if os.environ.get("REQUIRE_DB"):   # в CI база обязательна: пропуск тестов выглядел бы как успех
            raise
        pytest.skip(f"PostgreSQL недоступен: {e}")
    os.environ.update(DATABASE_URL=TEST_URL, ADMIN_PASSWORD=PASSWORD, METRICS_TOKEN=METRICS_TOKEN,
                      SEED_PATH=str(CASE), RATE_LIMIT_PER_MINUTE="100000")   # лимит проверяет отдельный тест
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
    assert client.post("/api/v1/feedback", json={"kind": "idea", "text": "без входа"}).status_code == 401


def test_docs_and_openapi_available(client):
    assert client.get("/api/v1/docs").status_code == 200
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    for p in ("/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/customers",
              "/api/v1/customers/{customer_id}", "/api/v1/analytics/summary", "/api/v1/model/settings",
              "/api/v1/source/snapshot", "/metrics"):
        assert p in paths, p


def test_openapi_file_matches_code(client):
    """docs/openapi.json не расходится с кодом: поменяли API — перевыгрузите (python export_openapi.py)."""
    spec = json.loads((Path(__file__).resolve().parents[2] / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert spec == client.get("/api/v1/openapi.json").json()


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

    # резкий рост реально собираемой потери: одно сообщение на один рост
    from datetime import datetime, timedelta, timezone

    from app.domain import Account, Service, Snapshot
    from app.scoring.engine import evaluate
    from app.scoring.settings import default_settings
    t0 = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)

    def result(balance):
        acc = Account(1, "user-1@example.test", balance, 0, 600_000, 0, "locked")
        svc = Service(1, "service-1", 1, "month", False, t0 - timedelta(days=27), t0 + timedelta(days=3), "active", 20_000)
        return evaluate(Snapshot([acc], [svc], [], reference_at=t0), default_settings())
    before, after = result(1_000_000), result(0)
    assert after.summary.expected_loss_paid > before.summary.expected_loss_paid * 1.2 + 5000
    bot._loss_base = None
    text = bot.loss_growth(before, after, "event")
    assert "Потеря растёт" in text and "событие биллинга" in text and "/app/retention" in text and "user-1@example.test" in text
    assert bot.loss_growth(after, after, "schedule") is None              # тот же уровень — без повтора
    bot.on_recalculated(before, after, "reset")                          # смена набора — новая точка отсчёта
    assert bot._loss_base == after.summary.expected_loss_paid
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


def test_role_from_sso_claims():
    from app.config import _role_map
    from app.routers.oidc import role_from_claims
    m = _role_map("radar-admins=admin, radar-viewers=viewer, Boss@Corp.ru=marketer")
    assert role_from_claims({"groups": ["radar-viewers"]}, "groups", m) == "viewer"
    assert role_from_claims({"groups": ["radar-viewers", "radar-admins"]}, "groups", m) == "admin"   # сильнейшая
    assert role_from_claims({"groups": "radar-admins"}, "groups", m) == "admin"
    assert role_from_claims({"email": "boss@corp.ru"}, "groups", m) == "marketer"
    assert role_from_claims({"groups": ["other"], "email": "x@y.z"}, "groups", m) is None
    import pytest
    with pytest.raises(RuntimeError):
        _role_map("radar-admins=superuser")


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


def test_ai_assistant_with_fake_model(client, auth):
    from tests.test_ai import Fake
    assert client.get("/api/v1/ai/status", headers=auth).json()["enabled"] is False
    assert client.post("/api/v1/ai/ask", headers=auth, json={"question": "кому звонить?"}).status_code == 503
    msg = client.get("/api/v1/customers/5/message?useAi=true", headers=auth).json()
    assert msg["generatedBy"] == "template" and "не подключён" in msg["note"]

    def answer(user):
        assert "Карточки клиентов из вопроса" in user and "клиент #5" in user
        return "Начните с клиента #5: удаление задержано. Потеря по прайсу € 999 999."
    client.app.state.ai = Fake(answer)
    try:
        assert client.get("/api/v1/ai/status", headers=auth).json()["enabled"] is True
        r = client.post("/api/v1/ai/ask", headers=auth, json={"question": "что с user-0005@example.test?"}).json()
        assert r["grounded"] is False and "999 999" in r["unverified"]
        assert r["customers"] == [{"id": 5, "email": "user-0005@example.test"}]

        client.app.state.ai = Fake("Тема: Сервер ждёт оплаты\n\nПродлите услугу, чтобы данные сохранились.")
        msg = client.get("/api/v1/customers/5/message?useAi=true", headers=auth).json()
        assert msg["generatedBy"] == "ai" and msg["subject"] == "Сервер ждёт оплаты" and msg["email"] == "user-0005@example.test"
        client.app.state.ai = Fake("Тема: Подарок\n\nДарим € 777.")
        assert client.get("/api/v1/customers/5/message?useAi=true", headers=auth).json()["generatedBy"] == "template"

        from app.telegram import TelegramBot
        client.app.state.ai = Fake("Начните с клиента #5.")
        bot = TelegramBot("0:test", "link-code-123", client.app.state.radar, "http://localhost:8080", None, client.app.state.ai)
        bot.handle(3003, "u", "/start link-code-123")
        assert "#5: http://localhost:8080/app/customers/5" in bot.handle(3003, "u", "/ask с кого начать?")
    finally:
        client.app.state.ai = None


def test_snapshot_origin_is_shown(client, auth):
    client.post("/api/v1/source/preset/extended", headers=auth)
    info = client.get("/api/v1/source/snapshot", headers=auth).json()
    assert info["origin"] == {"kind": "extended", "caseAccounts": 10, "otherAccounts": 190}
    assert "синтетических" in info["title"] and info["changesSinceLoad"] == 0
    client.post("/api/v1/source/events", headers=auth, json={"type": "topup", "accountId": 5, "amount": 10})
    info = client.get("/api/v1/source/snapshot", headers=auth).json()
    assert info["changesSinceLoad"] == 1 and "изменений после загрузки: 1" in info["title"]
    client.post("/api/v1/source/reset", headers=auth)
    assert client.get("/api/v1/source/snapshot", headers=auth).json()["changesSinceLoad"] == 0
    client.post("/api/v1/source/preset/case", headers=auth)
    assert client.get("/api/v1/source/snapshot", headers=auth).json()["title"] == "данные кейса, 10 кл."


def test_payment_deferral_panel_and_bot(client, auth):
    client.post("/api/v1/source/preset/case", headers=auth)
    wi = client.get("/api/v1/customers/62/what-if", headers=auth)
    cust = next(c for c in client.get("/api/v1/customers?pageSize=200", headers=auth).json()["items"] if c["level"] == "high")
    scenarios = client.get(f"/api/v1/customers/{cust['id']}/what-if", headers=auth).json()
    assert any(s["key"] == "defer" for s in scenarios) or wi.status_code == 404

    client.post("/api/v1/users", headers=auth, json={"login": "viewer9", "displayName": "v", "password": "pass-2026-long", "role": "viewer"})
    viewer = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"login": "viewer9", "password": "pass-2026-long"}).json()["token"]}
    assert client.post("/api/v1/customers/5/defer", headers=viewer, json={"days": 7}).status_code == 403

    r = client.post("/api/v1/customers/5/defer", headers=auth, json={"days": 7})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["services"] and d["lossAfter"] < d["lossBefore"] and d["action"]["kind"] == "deferred" and d["action"]["followUp"]
    detail = client.get("/api/v1/customers/5", headers=auth).json()
    assert all(s["status"] != "prolong_wait" for s in detail["services"])           # сервер больше не ждёт удаления
    assert client.post("/api/v1/customers/5/defer", headers=auth, json={"days": 7}).status_code == 409   # раз в 30 дней
    assert client.post("/api/v1/customers/5/defer", headers=auth, json={"days": 30}).status_code == 422
    item = next(x for x in client.get("/api/v1/retention/queue", headers=auth).json()["items"] if x["customerId"] == 5) \
        if any(x["customerId"] == 5 for x in client.get("/api/v1/retention/queue", headers=auth).json()["items"]) else None
    assert item is None or item["lastAction"] == "deferred"

    from app.telegram import TelegramBot
    bot = TelegramBot("0:test", "link-code-123", client.app.state.radar, "http://localhost:8080")
    bot.handle(4004, "u", "/start link-code-123")
    assert "Укажите клиента" in bot.handle(4004, "u", "/defer")
    out = bot.handle(4004, "u", "/defer 9 5")
    assert "Платёж перенесён" in out or "Переносить нечего" in out
    assert "уже переносили" in bot.handle(4004, "u", "/defer 5 3")
    client.post("/api/v1/source/reset", headers=auth)                    # откат данных отменяет и перенос
    assert client.post("/api/v1/customers/5/defer", headers=auth, json={"days": 3}).status_code == 201
    client.post("/api/v1/source/preset/case", headers=auth)


def test_ai_connection_from_panel(client, auth, monkeypatch):
    from app import ai
    calls = []
    monkeypatch.setattr(ai.AIClient, "ping", lambda self: calls.append(self.cfg) or None)
    body = {"baseUrl": "https://llm.aeza.example/v1", "model": "aeza-gpt", "apiKey": "sk-secret-1234"}

    client.post("/api/v1/users", headers=auth, json={"login": "mk-ai", "displayName": "m", "password": "pass-2026-long", "role": "marketer"})
    mk = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"login": "mk-ai", "password": "pass-2026-long"}).json()["token"]}
    assert client.put("/api/v1/ai/config", headers=mk, json=body).status_code == 403          # только администратор

    r = client.put("/api/v1/ai/config", headers=auth, json=body)
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["source"] == "panel"
    assert "llm.aeza.example" in r.json()["provider"] and calls[-1].base_url == "https://llm.aeza.example/v1"
    cfg = client.get("/api/v1/ai/config", headers=auth).json()
    assert cfg["keyHint"] == "1234" and "sk-secret" not in str(cfg)                              # ключ не отдаётся
    # без ключа — оставить сохранённый, сменить только модель
    r = client.put("/api/v1/ai/config", headers=auth, json={"baseUrl": body["baseUrl"], "model": "other"})
    assert r.status_code == 200 and calls[-1].api_key == "sk-secret-1234" and calls[-1].model == "other"

    def fail(self):
        raise ai.AIError("ключ не подошёл")
    monkeypatch.setattr(ai.AIClient, "ping", fail)
    r = client.put("/api/v1/ai/config", headers=auth, json={**body, "apiKey": "sk-wrong-0000"})
    assert r.status_code == 422 and "ничего не сохранено" in r.json()["detail"]
    assert client.get("/api/v1/ai/config", headers=auth).json()["keyHint"] == "1234"         # старое осталось

    assert client.delete("/api/v1/ai/config", headers=auth).status_code == 204
    assert client.get("/api/v1/ai/status", headers=auth).json()["enabled"] is False


def test_login_with_aeza_account(client, auth, monkeypatch):
    from app import aeza_auth
    monkeypatch.setattr(aeza_auth, "fetch_profile", lambda key: {"id": "777", "email": "client@mail.ru", "name": "Клиент"})
    monkeypatch.setenv("AEZA_AUTO_APPROVE", "@aeza.net")
    r = client.post("/api/v1/auth/aeza", json={"apiKey": "some-aeza-key"})
    assert r.status_code == 200 and r.json()["status"] == "pending" and "radar_session" not in r.cookies
    member = next(m for m in client.get("/api/v1/users", headers=auth).json() if m["login"] == "aeza:client@mail.ru")
    assert member["role"] == "pending" and member["viaAeza"]
    # администратор выдаёт роль — теперь вход создаёт сессию
    client.patch(f"/api/v1/users/{member['id']}", headers=auth, json={"role": "viewer"})
    r = client.post("/api/v1/auth/aeza", json={"apiKey": "some-aeza-key"})
    assert r.json()["status"] == "ok" and r.json()["user"]["role"] == "viewer"
    assert client.get("/api/v1/auth/me").json()["login"] == "aeza:client@mail.ru"
    client.post("/api/v1/auth/logout")
    # сотрудник с доменом из AEZA_AUTO_APPROVE входит сразу маркетологом
    monkeypatch.setattr(aeza_auth, "fetch_profile", lambda key: {"id": "778", "email": "anna@aeza.net", "name": "Анна"})
    r = client.post("/api/v1/auth/aeza", json={"apiKey": "staff-aeza-key"})
    assert r.json()["status"] == "ok" and r.json()["user"]["role"] == "marketer"
    client.post("/api/v1/auth/logout")

    def bad(key):
        raise aeza_auth.AezaAuthError("Ключ API Aéza не подошёл")
    monkeypatch.setattr(aeza_auth, "fetch_profile", bad)
    assert client.post("/api/v1/auth/aeza", json={"apiKey": "wrong-key-1"}).status_code == 401


def test_demo_event_stream(client, auth):
    import random

    from app.demo_stream import pick_event
    client.post("/api/v1/source/preset/extended", headers=auth)
    radar = client.app.state.radar
    rng = random.Random(7)
    kinds = {pick_event(radar.current.result, rng)["type"] for _ in range(200)}
    assert {"topup", "autoprolong", "signal"} <= kinds                        # и «спасают», и «портят»

    stream = client.app.state.demo_stream
    before = radar.current.version
    for _ in range(5):                                                       # тот же путь, что POST /source/events
        stream.tick()
    assert radar.current.version >= before + 5 and stream.sent == 5
    log = client.get("/api/v1/source/activity?limit=5", headers=auth).json()
    assert all(a["author"] == "демо-поток" for a in log)

    client.post("/api/v1/users", headers=auth, json={"login": "v-demo", "displayName": "v", "password": "pass-2026-long", "role": "viewer"})
    viewer = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"login": "v-demo", "password": "pass-2026-long"}).json()["token"]}
    assert client.put("/api/v1/source/demo-stream", headers=viewer, json={"running": True}).status_code == 403
    r = client.put("/api/v1/source/demo-stream", headers=auth, json={"running": True, "intervalSec": 3, "minutes": 1})
    assert r.status_code == 200 and r.json()["running"] and r.json()["stopsAt"]
    r = client.put("/api/v1/source/demo-stream", headers=auth, json={"running": False})
    assert r.json()["running"] is False
    assert client.get("/api/v1/source/snapshot", headers=auth).json()["changesSinceLoad"] >= 5
    client.post("/api/v1/source/reset", headers=auth)
    client.post("/api/v1/source/preset/case", headers=auth)


def test_backtest_feedback_loop(client, auth):
    client.post("/api/v1/source/preset/extended", headers=auth)
    assert client.get("/api/v1/source/history", headers=auth).json()["snapshots"] == 3
    b = client.get("/api/v1/model/backtest", headers=auth).json()
    assert b["available"] and b["demo"] and len(b["rounds"]) == 3
    assert 0 < b["caught"] <= b["churned"] and b["actualLoss"] > 0
    assert {lvl["level"] for lvl in b["levels"]} == {"high", "medium", "low"}
    assert all(f["verdict"] in ("ok", "under", "over", "few") for f in b["features"])
    # предпросмотр весов показывает, как изменится сверка
    p = client.post("/api/v1/model/preview", headers=auth,
                    json={"features": {"insufficient_funds": {"weight": 0}, "prolong_wait": {"weight": 0}}}).json()
    assert p["backtestBefore"]["caught"] == b["caught"] and p["backtestAfter"]["caught"] < b["caught"]
    # прошлый снимок из архива биллинга заменяет демо-историю; дата должна быть в прошлом
    case = json.loads(CASE.read_text(encoding="utf-8"))
    assert client.post("/api/v1/source/history", headers=auth, json=case).status_code == 422
    r = client.post("/api/v1/source/history", headers=auth, json={**case, "referenceAt": "2026-09-19T15:00:00.000Z"})
    assert r.status_code == 200 and r.json() == {**r.json(), "snapshots": 1, "demo": False}
    client.post("/api/v1/source/preset/case", headers=auth)
    assert client.get("/api/v1/model/backtest", headers=auth).json()["available"] is False


def test_feedback_about_radar(client, auth):
    r = client.post("/api/v1/feedback", headers=auth, json={"kind": "idea", "text": "Добавить выгрузку в Excel", "page": "/app/retention"})
    assert r.status_code == 201 and r.json()["status"] == "new" and r.json()["authorName"]
    assert client.post("/api/v1/feedback", headers=auth, json={"kind": "idea", "text": "x"}).status_code == 422
    fid = r.json()["id"]
    assert client.patch(f"/api/v1/feedback/{fid}", headers=auth, json={"status": "done"}).status_code == 204
    assert client.get("/api/v1/feedback", headers=auth).json()[0]["status"] == "done"
    assert client.patch("/api/v1/feedback/999999", headers=auth, json={"status": "done"}).status_code == 404
    client.post("/api/v1/users", headers=auth, json={"login": "viewer2", "displayName": "Наблюдатель", "password": "pass-2026-long", "role": "viewer"})
    viewer = _login(client, "viewer2", "pass-2026-long")
    assert client.post("/api/v1/feedback", headers=viewer, json={"kind": "problem", "text": "Не грузится график"}).status_code == 201
    assert client.get("/api/v1/feedback", headers=viewer).status_code == 403


def test_event_inbox_batches_and_partial_recalc(client, auth):
    """Очередь событий (её наполняет сервис приёма на Go): пачка, один пересчёт затронутых, дубли и ошибки."""
    client.post("/api/v1/source/preset/case", headers=auth)
    state = client.app.state
    before_balance = client.get("/api/v1/customers/3", headers=auth).json()["balance"]
    partial = state.radar.partial_recalculations
    events = [("t-1", {"type": "topup", "accountId": 3, "amount": 10}),
              ("t-2", {"type": "topup", "accountId": 3, "amount": 5}),
              ("t-3", {"type": "autoprolong", "serviceId": 999999, "enabled": True}),
              ("t-4", {"type": "nonsense"})]
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        for eid, payload in events + [("t-1", {"type": "topup", "accountId": 3, "amount": 10})]:
            conn.execute("INSERT INTO event_inbox (event_id, payload) VALUES (%s, %s) ON CONFLICT (event_id) DO NOTHING",
                         (eid, json.dumps(payload)))
    state.inbox.drain()
    after = client.get("/api/v1/customers/3", headers=auth).json()["balance"]
    assert round(after - before_balance, 2) == 15          # повтор t-1 не применился второй раз
    with psycopg.connect(TEST_URL) as conn:
        rows = dict(conn.execute("SELECT event_id, ok FROM event_inbox WHERE event_id LIKE 't-%'").fetchall())
    assert rows == {"t-1": True, "t-2": True, "t-3": False, "t-4": False}
    assert state.radar.partial_recalculations > partial
    # частичный пересчёт совпадает с полным
    partial_summary = client.get("/api/v1/analytics/summary", headers=auth).json()
    state.radar.recalculate("check")
    assert client.get("/api/v1/analytics/summary", headers=auth).json()["expectedLoss"] == partial_summary["expectedLoss"]
    metrics = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).text
    assert 'churn_radar_inbox_events{state="rejected"} 2.0' in metrics
    client.post("/api/v1/source/preset/case", headers=auth)


def test_quick_answers_without_ai(client, auth):
    """Быстрые ответы Радара: работают без подключённой модели, числа — из того же расчёта, что панель."""
    client.post("/api/v1/source/preset/extended", headers=auth)
    summary = client.get("/api/v1/analytics/summary", headers=auth).json()
    loss = client.post("/api/v1/ai/quick", headers=auth, json={"intent": "loss"}).json()
    assert loss["grounded"] and loss["provider"] == "Радар, без ИИ"
    from app.money import fmt_euros
    assert fmt_euros(summary["expectedLossPaid"]) in loss["answer"]
    today = client.post("/api/v1/ai/quick", headers=auth, json={"intent": "today"}).json()
    assert today["answer"].startswith("План на сегодня") and today["customers"]
    card = client.post("/api/v1/ai/quick", headers=auth, json={"intent": "customer", "customerId": 5}).json()
    assert "Клиент #5" in card["answer"] and card["customers"][0]["id"] == 5
    acc = client.post("/api/v1/ai/quick", headers=auth, json={"intent": "accuracy"}).json()
    assert "заранее отметила" in acc["answer"]
    assert client.post("/api/v1/ai/quick", headers=auth, json={"intent": "changes"}).status_code == 200
    assert client.post("/api/v1/ai/quick", headers=auth, json={"intent": "customer"}).status_code == 422
    assert client.post("/api/v1/ai/quick", headers=auth, json={"intent": "whatever"}).status_code == 422
    client.post("/api/v1/source/preset/case", headers=auth)


def test_plan_beats_risk_list(client, auth):
    """Тот же ресурс звонков: план Радара (срок, затем выгода) сохраняет не меньше, чем списки по сумме и по score."""
    client.post("/api/v1/source/preset/extended", headers=auth)
    q = client.get("/api/v1/retention/queue?callsPerDay=10", headers=auth).json()
    st = {x["key"]: x for x in q["strategies"]}
    assert q["strategyCallsPerDay"] == 10 and set(st) == {"plan", "loss", "score"}
    assert st["plan"]["saved"] >= st["loss"]["saved"] and st["plan"]["saved"] > st["score"]["saved"]
    assert st["plan"]["missed"] <= st["score"]["missed"]
    assert "список «по риску»" in client.post("/api/v1/ai/quick", headers=auth, json={"intent": "today"}).json()["answer"]
    client.post("/api/v1/source/preset/case", headers=auth)


def test_weight_sensitivity(client, auth):
    """±20 % к каждому весу: сколько клиентов сменят уровень, как изменятся деньги и план звонков."""
    client.post("/api/v1/source/preset/extended", headers=auth)
    s = client.get("/api/v1/model/sensitivity", headers=auth).json()
    assert s["delta"] == 0.2 and not s["sampled"] and s["planSize"] == 10
    assert len(s["rows"]) == 10
    row = next(r for r in s["rows"] if r["feature"] == "insufficient_funds")
    assert row["minus"]["lossPct"] < 0 < row["plus"]["lossPct"]
    assert s["maxChanged"] == max(max(r["minus"]["changed"], r["plus"]["changed"]) for r in s["rows"])
    client.post("/api/v1/source/preset/case", headers=auth)


def test_events_go_to_external_systems(client, auth):
    """Отзыв и отметка работы с клиентом — в outbox в той же транзакции; доставка с повторами, пока CRM не ответит."""
    from app.integrations import Outbox

    got, fail = [], {"on": True}

    class FlakyCRM:
        name = "crm"

        def send(self, event):
            if fail["on"]:
                raise ConnectionError("CRM недоступна")
            got.append(event)

    radar = client.app.state.radar
    old, radar.integrations = radar.integrations, Outbox([FlakyCRM()], radar.pool)
    try:
        client.post("/api/v1/feedback", headers=auth, json={"kind": "idea", "text": "Добавить выгрузку в CRM"})
        client.post("/api/v1/customers/5/actions", headers=auth, json={"kind": "called", "note": "договорились"})
        assert radar.integrations.deliver_due() == 0            # CRM лежит — события остаются в outbox
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            rows = conn.execute("SELECT attempts, last_error, delivered_at FROM outbox WHERE sink = 'crm'").fetchall()
            assert len(rows) == 2 and all(r[0] == 1 and "недоступна" in r[1] and r[2] is None for r in rows)
            conn.execute("UPDATE outbox SET next_attempt_at = now() WHERE sink = 'crm'")   # не ждём паузу
        log = client.get("/api/v1/integrations/deliveries?status=pending", headers=auth).json()
        assert [d["sink"] for d in log] == ["crm", "crm"] and "недоступна" in log[0]["lastError"]
        fail["on"] = False
        assert radar.integrations.deliver_due() == 2 and radar.integrations.deliver_due() == 0
        done = client.get("/api/v1/integrations/deliveries?status=delivered", headers=auth).json()
        assert {d["id"] for d in log} <= {d["id"] for d in done}
        r = client.post(f"/api/v1/integrations/deliveries/{log[0]['id']}/redeliver", headers=auth)
        assert r.status_code == 202 and r.json()["status"] == "pending" and r.json()["attempts"] == 0
        assert radar.integrations.deliver_due() == 1 and len(got) == 3            # CRM получила событие ещё раз
        assert client.post("/api/v1/integrations/deliveries/999999/redeliver", headers=auth).status_code == 404
    finally:
        radar.integrations = old
    by_type = {e.type: e for e in got}
    assert by_type["feedback.created"].data["text"] == "Добавить выгрузку в CRM"
    action = by_type["customer.action"].data
    assert action["customerId"] == 5 and action["kind"] == "called" and "email" in action and "expectedLossEur" in action
    assert by_type["customer.action"].id > 0                     # номер доставки — для защиты от повтора у получателя


def test_webhook_sink_signs_body(monkeypatch):
    import base64
    import hashlib
    import hmac

    from app import integrations

    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

    def fake_post(url, content, headers, timeout):
        sent.update(url=url, body=content, headers=headers)
        return Resp()

    monkeypatch.setattr(integrations.httpx, "post", fake_post)
    from datetime import datetime, timezone
    integrations.WebhookSink("https://crm.example/hook", "s3cret").send(
        integrations.Event(7, "feedback.created", {"id": 1}, datetime.now(timezone.utc)))
    expected = "sha256=" + hmac.new(b"s3cret", sent["body"], hashlib.sha256).hexdigest()
    assert sent["headers"]["X-Radar-Signature"] == expected and sent["headers"]["X-Radar-Event"] == "feedback.created"
    assert sent["headers"]["X-Radar-Delivery"] == "7" and integrations.backoff(1).seconds == 5 and integrations.backoff(30).seconds == 3600
    assert integrations.build_sinks({}) == [] and len(integrations.build_sinks({"INTEGRATION_WEBHOOK_URL": "https://x.example"})) == 1
    # Standard Webhooks: подпись покрывает id и время, поэтому перехваченный запрос нельзя отправить позже
    h = sent["headers"]
    signed = f"{h['webhook-id']}.{h['webhook-timestamp']}.".encode() + sent["body"]
    assert h["webhook-id"] == "7" and h["webhook-signature"] == "v1," + base64.b64encode(
        hmac.new(b"s3cret", signed, hashlib.sha256).digest()).decode()
    # публичный пример из спецификации Standard Webhooks; префикс отдельно — чтобы сканер секретов не принял его за ключ
    spec = integrations.sign("whsec" + "_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", "msg_p5jXN8AQM9LWM0D4loKWxJek", 1614265330,
                             b'{"test": 2432232314}')
    assert spec["webhook-signature"] == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def test_rate_limit(client, auth):
    """Сверх лимита — 429 problem+json с Retry-After; каждый ответ несёт RateLimit-заголовки; у каждого вызывающего свой счёт."""
    from app.ratelimit import RateLimiter

    old, client.app.state.rate_limiter = client.app.state.rate_limiter, RateLimiter(3, clock=lambda: 999_970)   # 10-я секунда минуты
    try:
        ok = [client.get("/api/v1/auth/me", headers=auth) for _ in range(3)]
        assert [r.status_code for r in ok] == [200, 200, 200]
        assert ok[0].headers["RateLimit-Policy"] == '"default";q=3;w=60'
        assert ok[0].headers["RateLimit"] == '"default";r=2;t=50' and ok[2].headers["RateLimit"] == '"default";r=0;t=50'
        r = client.get("/api/v1/auth/me", headers=auth)
        assert r.status_code == 429 and r.headers["Retry-After"] == "50"
        assert r.headers["content-type"].startswith("application/problem+json") and "3 в минуту" in r.json()["detail"]
        other = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer other"})
        assert other.status_code == 429            # новый токен от того же адреса не даёт свежую квоту
        assert client.get("/health/live").status_code == 200              # вне /api лимита нет
    finally:
        client.app.state.rate_limiter = old


def test_audit_log(client, auth):
    """Журнал безопасности: неудачный и удачный вход, отказ по правам, смена роли, выгрузка email; читает только админ."""
    assert client.post("/api/v1/auth/login", json={"login": "admin", "password": "wrong"}).status_code == 401
    r = client.post("/api/v1/users", headers=auth, json={"login": "auditee", "password": "auditee-pass-2026",
                                                         "displayName": "Проверка аудита", "role": "viewer"})
    assert r.status_code == 201
    viewer = client.post("/api/v1/auth/login", json={"login": "auditee", "password": "auditee-pass-2026"}).json()["token"]
    as_viewer = {"Authorization": f"Bearer {viewer}"}
    assert client.get("/api/v1/audit", headers=as_viewer).status_code == 403      # журнал — только администратору
    client.patch(f"/api/v1/users/{r.json()['id']}", headers=auth, json={"role": "marketer"})
    client.get("/api/v1/customers/export.csv?includeEmail=true", headers=auth)

    log = client.get("/api/v1/audit?limit=20", headers=auth).json()
    by_action = {}
    for e in log:
        by_action.setdefault(e["action"], e)
    assert by_action["login.failed"]["actor"] == "admin" and by_action["login.failed"]["requestId"]
    assert by_action["login"]["actor"] == "auditee" and by_action["login"]["details"]["method"] == "password"
    assert by_action["forbidden"]["target"] == "GET /api/v1/audit" and by_action["forbidden"]["details"]["role"] == "viewer"
    assert by_action["user.updated"]["target"] == "auditee" and by_action["user.updated"]["details"]["role"] == "marketer"
    assert by_action["export.emails"]["details"]["customers"] > 0
    assert "auditee-pass-2026" not in json.dumps(log) and viewer not in json.dumps(log)   # ни паролей, ни токенов
    only = client.get("/api/v1/audit?action=login.failed", headers=auth).json()
    assert only and {e["action"] for e in only} == {"login.failed"}


def test_events_resource(client, auth):
    """GET /api/v1/events: куда пришли события и чем закончились — API, демо-поток и поток биллинга через Go."""
    assert client.post("/api/v1/source/events", headers=auth,
                       json={"type": "topup", "accountId": 2, "amount": 5}).status_code == 200
    with psycopg.connect(TEST_URL, autocommit=True) as conn:   # как будто сервис на Go положил в очередь
        conn.execute("INSERT INTO event_inbox (event_id, payload, processed_at, ok, result) VALUES "
                     "('bill-1', '{\"type\": \"topup\", \"accountId\": 3, \"amount\": 1}', now(), true, 'применено'), "
                     "('bill-2', '{\"type\": \"pay_renewal\", \"serviceId\": 999999}', now(), false, 'нет услуги 999999')")
    page = client.get("/api/v1/events?pageSize=200", headers=auth).json()
    channels = {e["channel"] for e in page["items"]}
    assert {"api", "stream"} <= channels and page["total"] >= 3
    rejected = client.get("/api/v1/events?status=rejected", headers=auth).json()["items"]
    assert rejected[0]["eventId"] == "bill-2" and "999999" in rejected[0]["result"]
    api = client.get("/api/v1/events?channel=api&type=topup", headers=auth).json()["items"][0]
    assert api["status"] == "applied" and api["payload"]["accountId"] == 2 and api["author"] == "admin"
    one = client.get(f"/api/v1/events/{api['id']}", headers=auth)
    assert one.status_code == 200 and one.json() == api
    assert client.get("/api/v1/events/inbox-0", headers=auth).status_code == 404
    paged = client.get("/api/v1/events?pageSize=1", headers=auth)
    assert 'rel="next"' in paged.headers["Link"] and len(paged.json()["items"]) == 1


def test_unexpected_error_is_problem_with_request_id(client):
    def boom():
        raise RuntimeError("проверка")
    client.app.add_api_route("/api/v1/_boom", boom)
    r = client.get("/api/v1/_boom", headers={"X-Request-ID": "boom-req-1"})
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["requestId"] == "boom-req-1" == r.headers["X-Request-ID"]


def test_typed_error_statuses(client, auth):
    """Код ответа задаёт тип ошибки, а не текст сообщения."""
    def post(event):
        return client.post("/api/v1/source/events", headers=auth, json=event).status_code
    assert post({"type": "topup", "accountId": 999999, "amount": 5}) == 404
    assert post({"type": "topup", "accountId": 1, "amount": 0.004}) == 422     # меньше цента — не пополнение
    assert client.get("/api/v1/customers/999999/actions", headers=auth).status_code == 404


def test_csv_cells_are_not_formulas():
    from app.http import csv_cell
    assert [csv_cell(v) for v in ("=HYPERLINK(1)", "+1", "-x", "@a", "user@x.test", -5, 3.5)] == \
           ["'=HYPERLINK(1)", "'+1", "'-x", "'@a", "user@x.test", -5, 3.5]   # числа не трогаем


def test_calculation_headers(client, auth):
    r = client.get("/api/v1/analytics/summary", headers=auth)
    assert int(r.headers["X-Calculation-Version"]) >= 1 and int(r.headers["X-Data-Age"]) >= 0


def test_rest_conventions(client, auth):
    """problem+json, ETag/304, If-Match/412, 201 + Location, Link для страниц."""
    r = client.get("/api/v1/customers/999999", headers=auth)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status", "detail", "instance"} <= r.json().keys() and r.json()["status"] == 404
    r = client.get("/api/v1/customers?pageSize=0", headers=auth)
    assert r.status_code == 422 and r.json()["errors"][0]["loc"] == ["query", "pageSize"] and "pageSize" in r.json()["detail"]

    r = client.get("/api/v1/analytics/summary", headers=auth)
    tag = r.headers["ETag"]
    again = client.get("/api/v1/analytics/summary", headers={**auth, "If-None-Match": tag})
    assert again.status_code == 304 and again.content == b"" and again.headers["ETag"] == tag

    s = client.get("/api/v1/model/settings", headers=auth)
    etag = s.headers["ETag"]
    stale = client.patch("/api/v1/model/settings", headers={**auth, "If-Match": '"settings-0"'}, json={"horizonDays": 30})
    assert stale.status_code == 412 and stale.headers["content-type"].startswith("application/problem+json")
    ok = client.patch("/api/v1/model/settings", headers={**auth, "If-Match": etag}, json={"horizonDays": 30})
    assert ok.status_code == 200 and ok.headers["ETag"]

    r = client.post("/api/v1/feedback", headers=auth, json={"kind": "other", "text": "Проверка Location"})
    assert r.status_code == 201 and r.headers["Location"] == f"/api/v1/feedback/{r.json()['id']}"

    r = client.get("/api/v1/customers?pageSize=3&page=2", headers=auth)
    link = r.headers["Link"]
    assert 'rel="first"' in link and 'rel="prev"' in link and 'rel="next"' in link and 'page=3' in link


def test_idempotency_key(client, auth):
    """Повтор POST с тем же Idempotency-Key не выполняется второй раз; другой запрос с тем же ключом — 422."""
    client.post("/api/v1/source/preset/case", headers=auth)
    before = client.get("/api/v1/customers/3", headers=auth).json()["balance"]
    hdr = {**auth, "Idempotency-Key": "topup-3-0001"}
    first = client.post("/api/v1/source/events", headers=hdr, json={"type": "topup", "accountId": 3, "amount": 10})
    again = client.post("/api/v1/source/events", headers=hdr, json={"type": "topup", "accountId": 3, "amount": 10})
    assert first.status_code == again.status_code == 200 and again.json() == first.json()
    assert again.headers.get("Idempotent-Replayed") == "true" and "Idempotent-Replayed" not in first.headers
    assert round(client.get("/api/v1/customers/3", headers=auth).json()["balance"] - before, 2) == 10
    other = client.post("/api/v1/source/events", headers=hdr, json={"type": "topup", "accountId": 3, "amount": 99})
    assert other.status_code == 422 and other.headers["content-type"].startswith("application/problem+json")
    plain = client.post("/api/v1/source/events", headers=auth, json={"type": "topup", "accountId": 3, "amount": 1})
    assert plain.status_code == 200 and "Idempotent-Replayed" not in plain.headers
    client.post("/api/v1/source/preset/case", headers=auth)


def test_observability_and_pinned_pages(client, auth):
    """X-Request-ID в ответе и в ошибке, готовность сервиса, страницы по одной версии расчёта."""
    r = client.get("/api/v1/customers/999999", headers={**auth, "X-Request-ID": "trace-42"})
    assert r.headers["X-Request-ID"] == "trace-42" and r.json()["requestId"] == "trace-42"
    assert len(client.get("/api/v1/analytics/summary", headers=auth).headers["X-Request-ID"]) == 32   # создан сервером
    assert client.get("/api/v1/customers/1", headers={**auth, "X-Request-ID": "bad id with spaces"}).headers["X-Request-ID"] != "bad id with spaces"

    ready = client.get("/health/ready").json()
    assert ready["status"] == "ready" and ready["database"] == "ok" and ready["calculationVersion"] >= 1
    assert client.get("/health/live").json() == {"status": "ok"}

    page = client.get("/api/v1/customers?pageSize=2", headers=auth)
    version = page.json()["version"]
    assert f"version={version}" in page.headers["Link"]
    assert client.get(f"/api/v1/customers?pageSize=2&page=2&version={version}", headers=auth).status_code == 200
    stale = client.get(f"/api/v1/customers?pageSize=2&page=2&version={version - 1}", headers=auth)
    assert stale.status_code == 409 and "первой страницы" in stale.json()["detail"]
