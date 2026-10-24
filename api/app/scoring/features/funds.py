from app.money import fmt_cents

from .base import Feature, ServiceContext, Signal, clamp


class InsufficientFunds(Feature):
    key = "insufficient_funds"
    title = "Не хватает средств на продление"
    description = (
        "Сравниваем доступные деньги (баланс + разблокированные бонусы) с суммой продлений "
        "всех услуг аккаунта в горизонте. Сила — доля, которую нечем оплатить."
    )
    order = 30
    default_weight = 40

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        need = ctx.account_renewal_cost
        if ctx.renewal_cost == 0 or need == 0:
            return None
        funds = ctx.account.available_funds
        if funds >= need:
            return None
        strength = clamp((need - funds) / need)
        return Signal(
            strength,
            f"На счёте {fmt_cents(funds)}, а на продления за {ctx.horizon_days} дн. нужно {fmt_cents(need)}",
        )
