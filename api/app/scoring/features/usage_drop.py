from .base import Feature, ServiceContext, Signal


class UsageDrop(Feature):
    key = "usage_drop"
    title = "Сервером почти не пользуются"
    description = (
        "Сигнал из мониторинга: насколько упала нагрузка на сервер за последние недели "
        "(signals.loadDrop услуги, 0–1). Без этих данных признак не срабатывает."
    )
    order = 90
    default_weight = 20

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        drop = ctx.service.signals.get("loadDrop")
        if not drop or drop <= 0:
            return None
        return Signal(min(1.0, float(drop)), f"Нагрузка на {ctx.service.name} упала на {round(float(drop) * 100)} %")
