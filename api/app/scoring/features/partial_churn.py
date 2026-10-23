from .base import Feature, ServiceContext, Signal


class PartialChurn(Feature):
    key = "partial_churn"
    title = "Уже отказывался от услуг"
    description = (
        "Часть услуг клиента уже удалена или заблокирована. Сила — доля таких услуг: "
        "клиент начал уходить, оставшиеся услуги под угрозой."
    )
    order = 70
    default_weight = 20

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        if ctx.account_services_gone == 0 or ctx.account_services_total == 0:
            return None
        share = ctx.account_services_gone / ctx.account_services_total
        return Signal(share, f"Удалено или заблокировано {ctx.account_services_gone} из {ctx.account_services_total} услуг клиента")
