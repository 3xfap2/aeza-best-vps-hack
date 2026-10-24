import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain import Account, Service, Snapshot, Transaction
from app.scoring.engine import evaluate, renewal_cost, revenue_over_horizon, timeline
from app.scoring.settings import ModelSettings, default_settings, merge_patch
from app.source import SnapshotIn

CASE = Path(__file__).resolve().parents[2] / "data" / "case" / "data.json"
T0 = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)


def account(id=1, balance=10_000, bonus=0, bonus_state="locked"):
    return Account(id, f"user-{id}@example.test", balance, 0, balance, bonus, bonus_state)


def service(id=1, owner=1, term="month", status="active", autoprolong=True, expires_in_days=10.0, price=1000):
    expires = T0 + timedelta(days=expires_in_days)
    return Service(id, f"service-{id}", owner, term, autoprolong, expires - timedelta(days=30), expires, status, price)


def snap(accounts, services, transactions=()):
    return Snapshot(list(accounts), list(services), list(transactions), reference_at=T0)


def only(result):
    return result.customers[0]


def test_case_data_levels_and_issues():
    data = SnapshotIn.model_validate(json.loads(CASE.read_text(encoding="utf-8"))).to_snapshot()
    r = evaluate(data, default_settings())
    assert r.summary.levels == {"high": 3, "medium": 2, "low": 1, "no_score": 2, "churned": 2}
    assert r.summary.quality_issues == 8
    assert all(c.score is None for c in r.customers if c.level in ("no_score", "churned"))


def test_healthy_customer_is_low():
    c = only(evaluate(snap([account()], [service(expires_in_days=200)]), default_settings()))
    assert c.level == "low" and c.score == 0 and c.contributions == []


def test_expiring_without_autoprolong_grows_as_deadline_nears():
    far = only(evaluate(snap([account()], [service(autoprolong=False, expires_in_days=25)]), default_settings()))
    near = only(evaluate(snap([account()], [service(autoprolong=False, expires_in_days=2)]), default_settings()))
    assert 0 < far.score < near.score <= 35


def test_prolong_wait_overdue_gets_full_strength_and_quality_flag():
    c = only(evaluate(snap([account()], [service(status="prolong_wait", expires_in_days=-30)]), default_settings()))
    pw = next(x for x in c.contributions if x.feature == "prolong_wait")
    assert pw.strength == 1.0 and pw.points == 60
    assert c.quality_issues and "удаление задержано" in c.quality_issues[0]


def test_insufficient_funds_counts_unlocked_bonus_only():
    services = [service(price=5000, expires_in_days=3)]
    locked = only(evaluate(snap([account(balance=3000, bonus=3000, bonus_state="locked")], services), default_settings()))
    unlocked = only(evaluate(snap([account(balance=3000, bonus=3000, bonus_state="unlocked")], services), default_settings()))
    assert any(x.feature == "insufficient_funds" for x in locked.contributions)
    assert not any(x.feature == "insufficient_funds" for x in unlocked.contributions)


def test_unpaid_transactions_share():
    txs = [Transaction(i, 100, "performed" if i % 2 else "not_performed", T0 if i % 2 else None, T0, "prolong", 1)
           for i in range(1, 5)]
    c = only(evaluate(snap([account()], [service(expires_in_days=200)], txs), default_settings()))
    unpaid = next(x for x in c.contributions if x.feature == "unpaid_transactions")
    assert unpaid.strength == 0.5 and unpaid.points == 7.5


def test_no_score_and_churned():
    blocked = only(evaluate(snap([account()], [service(status="blocked"), service(2, status="deleted")]), default_settings()))
    churned = only(evaluate(snap([account()], [service(status="deleted")]), default_settings()))
    empty = only(evaluate(snap([account()], []), default_settings()))
    assert (blocked.level, churned.level, empty.level) == ("no_score", "churned", "no_score")
    assert "заблокировано 1 из 2" in blocked.note


def test_score_capped_and_points_add_up():
    s = service(status="prolong_wait", expires_in_days=-10, price=100_000)  # 60 + 40 + 15 > 100
    unpaid = [Transaction(1, 100, "not_performed", None, T0, "prolong", 1)]
    c = only(evaluate(snap([account(balance=0)], [s], unpaid), default_settings()))
    assert sum(x.points for x in c.contributions) > 100
    assert c.score == 100
    services = [service(autoprolong=False, expires_in_days=15)]
    c2 = only(evaluate(snap([account()], services), default_settings()))
    assert c2.score == round(sum(x.points for x in c2.contributions))


def test_weights_change_result_and_disabled_feature_is_ignored():
    data = snap([account()], [service(autoprolong=False, expires_in_days=2)])
    base = only(evaluate(data, default_settings()))
    heavier = default_settings().model_copy(deep=True)
    heavier.features["expiring_no_autoprolong"].weight = 90
    off = default_settings().model_copy(deep=True)
    off.features["expiring_no_autoprolong"].enabled = False
    assert only(evaluate(data, heavier)).score > base.score
    assert only(evaluate(data, off)).score == 0


def test_money_is_revenue_times_probability():
    s = service(autoprolong=False, expires_in_days=5, price=3000)
    r = evaluate(snap([account()], [s]), default_settings())
    c = only(r)
    expected = revenue_over_horizon(s, 30) * c.services[0].score / 100
    assert c.expected_loss == pytest.approx(expected)
    assert r.summary.expected_loss == pytest.approx(expected)


def test_hourly_renewal_cost_covers_horizon():
    s = service(term="hour", expires_in_days=0.5, price=100)
    assert renewal_cost(s, 0.5, 30) == (1 + int((30 - 0.5) // (1 / 24))) * 100


def test_thresholds_must_be_ordered():
    with pytest.raises(ValidationError):
        ModelSettings.model_validate({"features": {}, "thresholds": {"high": 20, "medium": 30}})


def test_unknown_feature_rejected_and_new_feature_gets_default():
    with pytest.raises(ValidationError):
        ModelSettings.model_validate({"features": {"no_such": {"weight": 1}}})
    s = ModelSettings.model_validate({"features": {}})
    assert s.features["prolong_wait"].weight == 60


def test_merge_patch():
    assert merge_patch({"a": {"b": 1, "c": 2}, "d": 3}, {"a": {"b": 5}, "d": None}) == {"a": {"b": 5, "c": 2}}


def test_timeline_restores_deletion_date_and_forecasts():
    deleted = service(1, status="deleted", expires_in_days=-10)
    live = service(2, autoprolong=False, expires_in_days=4)
    data = snap([account()], [deleted, live])
    r = evaluate(data, default_settings())
    points = {p.date: p for p in timeline(r, data, [])}
    assert points[(T0 - timedelta(days=5)).date().isoformat()].fact_restored == 1
    assert points[(T0 + timedelta(days=4)).date().isoformat()].forecast > 0


def test_snapshot_validation_reports_field():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["services"][0]["ownerId"] = 999
    with pytest.raises(ValidationError, match=r"services\[0\]\.ownerId"):
        SnapshotIn.model_validate(raw)


def test_causes_split_involuntary_and_voluntary():
    accounts = [account(1, balance=0), account(2)]
    services = [service(1, owner=1, status="prolong_wait", expires_in_days=-6),  # удаление просрочено: 60 баллов
                service(2, owner=2, autoprolong=False, expires_in_days=1)]
    r = evaluate(snap(accounts, services), default_settings())
    causes = {g["feature"]: g for g in r.summary.causes}
    assert causes["prolong_wait"]["kind"] == "involuntary" and causes["prolong_wait"]["customers"] == 1
    assert causes["expiring_no_autoprolong"]["kind"] == "voluntary"
    assert sum(g["expected_loss"] for g in r.summary.causes) == pytest.approx(
        sum(c.expected_loss for c in r.customers if c.level in ("high", "medium")))


def test_features_are_discovered_automatically():
    from app.scoring.features import FEATURES
    keys = [f.key for f in FEATURES]
    assert {"no_autoprolong_no_funds", "partial_churn", "prolong_wait"} <= set(keys)
    assert keys[0] == "prolong_wait"  # порядок показа задаёт order


def test_no_autoprolong_and_no_money_combo():
    poor = only(evaluate(snap([account(balance=100)], [service(autoprolong=False, expires_in_days=200, price=1000)]), default_settings()))
    rich = only(evaluate(snap([account(balance=100_000)], [service(autoprolong=False, expires_in_days=200, price=1000)]), default_settings()))
    assert any(x.feature == "no_autoprolong_no_funds" for x in poor.contributions)
    assert not any(x.feature == "no_autoprolong_no_funds" for x in rich.contributions)


def test_partial_churn_counts_deleted_and_blocked():
    services = [service(1, expires_in_days=200), service(2, status="deleted"), service(3, status="blocked")]
    c = only(evaluate(snap([account()], services), default_settings()))
    pc = next(x for x in c.contributions if x.feature == "partial_churn")
    assert pc.strength == pytest.approx(2 / 3, abs=1e-3) and "2 из 3" in pc.reason


def test_ltv_is_monthly_revenue_over_churn_and_capped():
    from app.scoring.engine import LTV_CAP_MONTHS, ltv
    s = service(term="month", price=1000)
    assert ltv(s, 5) == pytest.approx(1000 * 20)                    # 1 / 5 % = 20 месяцев
    assert ltv(s, 1) == pytest.approx(1000 * LTV_CAP_MONTHS)        # не больше 36 месяцев
    c = only(evaluate(snap([account()], [service(autoprolong=False, expires_in_days=3, price=1000)]), default_settings()))
    assert c.ltv_at_risk == pytest.approx(ltv(c.services[0].service, 5) * c.services[0].probability)


def test_paid_loss_is_capped_by_what_customer_pays():
    # почасовой сервер: по прайсу € 7,2 тыс. за 30 дней (потеря — больше € 5 тыс.), а клиент платит € 100 в месяц и держит € 50 на счёте
    acc = Account(1, "user-1@example.test", 5_000, 0, 120_000, 0, "locked")
    hourly = Service(1, "service-1", 1, "hour", False, T0 - timedelta(days=360), T0 - timedelta(days=1), "prolong_wait", 1000)
    c = only(evaluate(snap([acc], [hourly]), default_settings()))
    assert c.expected_loss > 500_000
    assert c.paid_monthly == pytest.approx(10_000)
    assert c.expected_loss_paid == pytest.approx(c.expected_loss * 10_000 / c.revenue_horizon)
    # кто платит больше, чем стоит услуга, — без ограничения
    rich = Account(1, "user-1@example.test", 900_000, 0, 900_000, 0, "locked")
    c = only(evaluate(snap([rich], [service(expires_in_days=2, autoprolong=False)]), default_settings()))
    assert c.expected_loss_paid == pytest.approx(c.expected_loss)


def test_retention_queue_deadlines_and_channel():
    from app.retention import queue
    acc = account(balance=0)
    overdue = Service(1, "service-1", 1, "month", False, T0 - timedelta(days=60), T0 - timedelta(days=30), "prolong_wait", 5000)
    soon = Service(2, "service-2", 2, "month", False, T0 - timedelta(days=27), T0 + timedelta(days=3), "active", 5000)
    r = evaluate(snap([acc, account(2, balance=0)], [overdue, soon]), default_settings())
    items = {x["customerId"]: x for x in queue(r, {}, save_rate=30, call_cost=0)}
    assert items[1]["deadlineKind"] == "overdue" and items[1]["bucket"] == "today"
    assert items[2]["deadlineKind"] == "renewal" and items[2]["bucket"] == "week"
    assert [x["customerId"] for x in queue(r, {}, 30, 0)] == [1, 2]
    assert all(x["channel"] == "email" and x["net"] == x["expectedSaved"] for x in queue(r, {}, 30, 10_000))
    done = queue(r, {2: {"kind": "renewed", "author": "anna", "created_at": T0}}, 30, 0)
    assert next(x for x in done if x["customerId"] == 2)["status"] == "done"
    # перезвонить сегодня — клиент из «на неделе» поднимается в «Сегодня», и первым
    due = queue(r, {2: {"kind": "no_answer", "author": "anna", "created_at": T0, "follow_up": T0.date()}}, 30, 0)
    assert due[0]["customerId"] == 2 and due[0]["bucket"] == "today" and due[0]["followUpDue"]
    later = queue(r, {2: {"kind": "no_answer", "author": "anna", "created_at": T0, "follow_up": T0.date() + timedelta(days=3)}}, 30, 0)
    assert next(x for x in later if x["customerId"] == 2)["bucket"] == "week"
    # ёмкость: в план дня попадают первые N звонков
    payers = [Account(i, f"user-{i}@example.test", 0, 0, 1_000_000, 0, "locked") for i in (1, 2)]
    r2 = evaluate(snap(payers, [overdue, soon]), default_settings())
    assert all(x["channel"] == "call" for x in queue(r2, {}, 30, 1))
    planned = queue(r2, {}, 30, 1, calls_per_day=1)
    assert [x["customerId"] for x in planned if x["planned"]] == [1]


def test_effect_after_contact():
    from app.retention import effect
    acc = account(balance=0)
    s = Service(1, "service-1", 1, "month", False, T0 - timedelta(days=27), T0 + timedelta(days=3), "active", 5000)
    r = evaluate(snap([acc], [s]), default_settings())
    first = {1: {"author": "anna", "created_at": T0, "at_risk": round(r.by_id[1].expected_loss) + 1000, "score_at": 90}}
    e = effect(r, first)
    assert e[0]["reduced"] == 10.0 and e[0]["scoreBefore"] == 90


def test_backtest_compares_past_scores_with_outcomes():
    from app import backtest
    past_at = T0 - timedelta(days=30)

    def svc(id, owner, autoprolong, expires, status):
        return Service(id, f"service-{id}", owner, "month", autoprolong, expires - timedelta(days=30), expires, status, 1000)

    past = Snapshot([account(1, balance=0), account(2, balance=50_000)],
                    [svc(1, 1, False, past_at + timedelta(days=5), "active"),
                     svc(2, 2, True, past_at + timedelta(days=20), "active")], [], reference_at=past_at)
    now = snap([account(1, balance=0), account(2, balance=49_000)],
               [svc(1, 1, False, past_at + timedelta(days=5), "deleted"),
                svc(2, 2, True, past_at + timedelta(days=50), "active")])
    b = backtest.run([past], now, T0, default_settings())
    assert (len(b.rounds), b.customers, b.churned, b.caught, b.false_alarms) == (1, 2, 1, 1, 0)
    assert b.levels["high"].churned == 1 and b.levels["low"].churned == 0
    assert b.actual_loss == revenue_over_horizon(past.services[0], 30)
    # без весов модель никого не отмечает — сверка это видит
    zero = merge_patch(default_settings().model_dump(mode="json"),
                       {"features": {k: {"weight": 0} for k in default_settings().features}})
    assert backtest.run([past], now, T0, ModelSettings.model_validate(zero)).caught == 0
    # исхода ещё нет (прошло меньше горизонта) — раунд не считается
    assert backtest.run([past], now, past_at + timedelta(days=10), default_settings()).rounds == []


def test_demo_history_is_consistent_with_snapshot():
    from app import backtest
    root = Path(__file__).resolve().parents[2] / "data" / "demo"
    current = SnapshotIn.model_validate(json.loads((root / "data.json").read_text(encoding="utf-8"))).to_snapshot()
    history = [SnapshotIn.model_validate(s).to_snapshot()
               for s in json.loads((root / "history.json").read_text(encoding="utf-8"))["snapshots"]]
    b = backtest.run(history, current, current.reference_at, default_settings())
    assert len(b.rounds) == 3 and b.churned > 10
    assert b.caught / b.churned > 0.6
    rate = {k: v.churned / v.customers for k, v in b.levels.items()}
    assert rate["high"] > rate["medium"] > rate["low"]
