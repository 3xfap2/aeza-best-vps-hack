from app.money import fmt_cents

from .base import Feature, ServiceContext, Signal


class NoAutoprolongNoFunds(Feature):
    key = "no_autoprolong_no_funds"
    title = "Нет автопродления и нет денег"
    description = (
        "Комбинация: автопродление выключено, а денег на счёте не хватает даже на одно продление. "
        "Похоже, клиент не собирается продлевать."
    )
    order = 40
    default_weight = 25

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        s = ctx.service
        if s.autoProlong or s.status not in ("active", "suspended", "prolong_wait"):
            return None
        funds = ctx.account.available_funds
        if funds >= s.price:
            return None
        return Signal(1.0, f"Автопродление выключено, а на счёте {fmt_cents(funds)} при цене продления {fmt_cents(s.price)}")
