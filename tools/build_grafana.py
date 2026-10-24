"""Собирает дашборд Grafana «Радар оттока» из метрик /metrics → grafana/dashboards/churn-radar.json.

Запуск: python tools/build_grafana.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "grafana" / "dashboards" / "churn-radar.json"
DS = {"type": "prometheus", "uid": "prometheus"}
EUR = {"unit": "currencyEUR", "decimals": 0}

panels: list[dict] = []


def add(panel: dict, x: int, y: int, w: int, h: int) -> None:
    panel.setdefault("datasource", DS)
    panel["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
    panel["id"] = len(panels) + 1
    for i, t in enumerate(panel["targets"]):
        t["refId"] = chr(65 + i)
    panels.append(panel)


def target(expr: str, legend: str = "", instant: bool = False) -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "instant": instant, "range": not instant}


def stat(title: str, expr: str, desc: str, color: str, unit: dict | None = None) -> dict:
    return {
        "type": "stat", "title": title, "description": desc,
        "targets": [target(expr, instant=True)],
        "fieldConfig": {"defaults": {**(unit or {}), "color": {"mode": "fixed", "fixedColor": color}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "none",
                    "textMode": "value", "justifyMode": "center"},
    }


def series(title: str, targets: list[dict], desc: str, unit: dict | None = None, stack: bool = False) -> dict:
    return {
        "type": "timeseries", "title": title, "description": desc, "targets": targets,
        "fieldConfig": {"defaults": {**(unit or {}), "custom": {
            "lineWidth": 2, "fillOpacity": 25 if stack else 8, "showPoints": "never",
            "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }


def bars(title: str, expr: str, legend: str, desc: str, unit: dict | None = None) -> dict:
    return {
        "type": "bargauge", "title": title, "description": desc,
        "targets": [target(expr, legend, instant=True)],
        "fieldConfig": {"defaults": {**(unit or {}), "min": 0, "color": {"mode": "continuous-YlRd"}}, "overrides": []},
        "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                    "reduceOptions": {"calcs": ["lastNotNull"]}},
    }


H = '{horizon_days=~".+"}'
add(stat("Потеря по прайсу, 30 дней", f"churn_radar_expected_loss_eur{H}",
         "Σ выручка услуги за горизонт × вероятность непродления — формула задания", "#C2410C", EUR), 0, 0, 4, 4)
add(stat("Реально собираемая", f"churn_radar_expected_loss_paid_eur{H}",
         "Выручка клиента не больше, чем он платит в среднем или держит на счёте", "#B45309", EUR), 4, 0, 4, 4)
add(stat("Худший случай", f"churn_radar_worst_case_loss_eur{H}", "Вся выручка клиентов высокого риска", "#7F1D1D", EUR), 8, 0, 4, 4)
add(stat("LTV под угрозой", "churn_radar_ltv_at_risk_eur", "Выручка за срок жизни клиента × вероятность непродления", "#6B4F3A", EUR), 12, 0, 4, 4)
add(stat("Сохранено удержанием", "churn_radar_retention_saved_eur",
         "Сумма под угрозой у клиентов с отметкой «продлил»", "#15803D", EUR), 16, 0, 4, 4)
add(stat("Клиентов высокого риска", 'churn_radar_customers{level="high"}', "Score от порога высокого риска", "#B91C1C"), 20, 0, 4, 4)

add(series("Деньги под угрозой", [
    target(f"churn_radar_expected_loss_eur{H}", "по прайсу"),
    target(f"churn_radar_expected_loss_paid_eur{H}", "реально собираемая"),
    target("churn_radar_retention_saved_eur", "сохранено удержанием"),
], "Как меняется сумма после событий биллинга, правок модели и работы маркетологов", EUR), 0, 4, 14, 9)
add(series("Клиенты по уровням риска", [
    target('churn_radar_customers{level="high"}', "высокий"),
    target('churn_radar_customers{level="medium"}', "средний"),
    target('churn_radar_customers{level="low"}', "низкий"),
], "Число клиентов в каждом уровне", stack=True), 14, 4, 10, 9)

add(bars("За что теряем деньги", "churn_radar_expected_loss_by_cause_eur", "{{cause}} · {{kind}}",
         "Клиенты высокого и среднего риска по главной причине: involuntary — не прошла оплата", EUR), 0, 13, 10, 9)
add(bars("Работа с клиентами", "churn_radar_retention_actions", "{{kind}}",
         "Отметки маркетологов: called, messaged, renewed, churned…"), 10, 13, 7, 9)
add(series("Пересчёт модели", [
    target("churn_radar_calculation_duration_seconds", "длительность, с"),
], "Полный пересчёт всех клиентов после каждого изменения", {"unit": "s"}), 17, 13, 7, 9)

dashboard = {
    "uid": "churn-radar", "title": "Радар оттока", "tags": ["churn-radar"], "timezone": "browser",
    "schemaVersion": 39, "version": 1, "refresh": "15s", "time": {"from": "now-6h", "to": "now"},
    "panels": panels,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print(OUT, len(panels), "панелей")
