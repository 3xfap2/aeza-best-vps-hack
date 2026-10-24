from .base import Feature, ServiceContext, Signal


class PlanDowngrade(Feature):
    key = "plan_downgrade"
    title = "Перешёл на тариф дешевле"
    description = (
        "Сигнал из биллинга: клиент недавно понизил тариф (signals.downgraded услуги) — "
        "часто первый шаг перед уходом. Без этих данных признак не срабатывает."
    )
    order = 95
    default_weight = 15

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        if not ctx.service.signals.get("downgraded"):
            return None
        return Signal(1.0, f"{ctx.service.name}: тариф понижен")
