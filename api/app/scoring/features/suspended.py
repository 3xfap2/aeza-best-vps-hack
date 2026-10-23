from .base import Feature, ServiceContext, Signal


class Suspended(Feature):
    key = "suspended"
    title = "Сервер выключен клиентом"
    description = "Клиент выключил сервер, но продолжает платить — услугой не пользуются и могут отказаться."
    default_weight = 15

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        if ctx.service.status != "suspended":
            return None
        return Signal(1.0, f"Сервер {ctx.service.name} выключен, но оплачивается")
