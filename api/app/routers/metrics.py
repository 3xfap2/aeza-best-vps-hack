"""GET /metrics в формате Prometheus.

Значения берутся из того же результата расчёта, что и панель, поэтому сумма
churn_radar_expected_loss_eur совпадает с expectedLoss в /api/v1/analytics/summary.
Доступ: Bearer METRICS_TOKEN (для сборщика) или сессия пользователя.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily, HistogramMetricFamily

from app import db
from app.money import to_euros
from app.security import request_token, token_hash

router = APIRouter(tags=["Метрики"])

SCORE_BUCKETS = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)


class RadarCollector:
    def __init__(self, radar) -> None:
        self.radar = radar

    def collect(self):
        cur = self.radar.current
        s = cur.result.summary
        horizon = str(s.horizon_days)

        customers = GaugeMetricFamily("churn_radar_customers", "Клиенты по уровням риска", labels=["level"])
        for level, count in s.levels.items():
            customers.add_metric([level], count)
        yield customers

        loss = GaugeMetricFamily("churn_radar_expected_loss_eur",
                                 "Ожидаемая потеря выручки за горизонт, EUR", labels=["horizon_days"])
        loss.add_metric([horizon], to_euros(s.expected_loss))
        yield loss
        paid = GaugeMetricFamily("churn_radar_expected_loss_paid_eur",
                                 "Ожидаемая потеря в пределах платёжеспособности клиентов, EUR", labels=["horizon_days"])
        paid.add_metric([horizon], to_euros(s.expected_loss_paid))
        yield paid
        worst = GaugeMetricFamily("churn_radar_worst_case_loss_eur",
                                  "Выручка клиентов высокого риска за горизонт, EUR", labels=["horizon_days"])
        worst.add_metric([horizon], to_euros(s.worst_case))
        yield worst
        ltv_m = GaugeMetricFamily("churn_radar_ltv_at_risk_eur", "LTV клиентов × вероятность непродления, EUR")
        ltv_m.add_metric([], to_euros(s.ltv_at_risk))
        yield ltv_m
        at_risk = GaugeMetricFamily("churn_radar_revenue_at_risk_eur",
                                    "Выручка клиентов высокого и среднего риска за горизонт, EUR", labels=["horizon_days"])
        at_risk.add_metric([horizon], to_euros(s.revenue_at_risk))
        yield at_risk

        by_cause = GaugeMetricFamily("churn_radar_expected_loss_by_cause_eur",
                                     "Ожидаемая потеря клиентов высокого и среднего риска по главной причине, EUR",
                                     labels=["cause", "kind"])
        for g in s.causes:
            by_cause.add_metric([g["feature"], g["kind"]], to_euros(g["expected_loss"]))
        yield by_cause

        actions = GaugeMetricFamily("churn_radar_retention_actions", "Отметки работы с клиентами по видам", labels=["kind"])
        with self.radar.pool.connection() as conn:
            for kind, n in db.action_counts(conn).items():
                actions.add_metric([kind], n)
            saved = db.saved_by_renewals(conn)
        yield actions
        yield GaugeMetricFamily("churn_radar_retention_saved_eur",
                                "Сохранено удержанием: сумма под угрозой у клиентов с отметкой «продлил», EUR", value=to_euros(saved))

        yield GaugeMetricFamily("churn_radar_services_at_risk", "Услуги со score не ниже порога среднего риска",
                                value=s.services_at_risk)
        yield GaugeMetricFamily("churn_radar_data_quality_issues", "Найденные противоречия в данных",
                                value=s.quality_issues)

        scores = sorted(c.score for c in cur.result.customers if c.score is not None)
        buckets = [(str(b), sum(1 for x in scores if x <= b)) for b in SCORE_BUCKETS] + [("+Inf", len(scores))]
        yield HistogramMetricFamily("churn_radar_customer_risk_score", "Распределение score клиентов",
                                    buckets=buckets, sum_value=float(sum(scores)))

        yield GaugeMetricFamily("churn_radar_model_settings_version", "Версия настроек модели",
                                value=cur.settings_version)
        yield GaugeMetricFamily("churn_radar_calculation_version", "Номер пересчёта с момента запуска",
                                value=cur.version)
        yield GaugeMetricFamily("churn_radar_last_calculation_timestamp_seconds", "Время последнего пересчёта",
                                value=cur.computed_at.timestamp())
        yield GaugeMetricFamily("churn_radar_calculation_duration_seconds", "Длительность последнего пересчёта",
                                value=cur.duration_ms / 1000)
        yield GaugeMetricFamily("churn_radar_as_of_timestamp_seconds", "Дата расчёта (as of)",
                                value=s.as_of.timestamp())
        yield GaugeMetricFamily("churn_radar_live_clients", "Подключённые панели (SSE)",
                                value=self.radar.broker.count)
        yield GaugeMetricFamily("churn_radar_partial_recalculations", "Пересчёты только затронутых клиентов с момента запуска",
                                value=self.radar.partial_recalculations)
        with self.radar.pool.connection() as conn:
            inbox = db.inbox_counts(conn)
        queue = GaugeMetricFamily("churn_radar_inbox_events", "Очередь событий биллинга: ждут, применены, отклонены",
                                  labels=["state"])
        for state, n in inbox.items():
            queue.add_metric([state], n)
        yield queue


def build_registry(radar) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(RadarCollector(radar))
    return registry


@router.get("/metrics", summary="Агрегированные метрики в формате Prometheus",
            response_class=Response, responses={200: {"content": {"text/plain": {}}}, 401: {"description": "Нет доступа"}})
def metrics(request: Request) -> Response:
    token = request_token(request)
    allowed = bool(token) and hmac.compare_digest(token, request.app.state.config.metrics_token)
    if not allowed and token:
        with request.app.state.pool.connection() as conn:
            allowed = db.session_user(conn, token_hash(token)) is not None
    if not allowed:
        raise HTTPException(401, "Нужен Bearer-токен сборщика или вход в панель", headers={"WWW-Authenticate": "Bearer"})
    return Response(generate_latest(request.app.state.metrics_registry), media_type=CONTENT_TYPE_LATEST)
