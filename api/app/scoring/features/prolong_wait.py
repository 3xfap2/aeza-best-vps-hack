from app.domain import GRACE_PERIOD

from .base import Feature, ServiceContext, Signal, humanize_days

GRACE_DAYS = GRACE_PERIOD.total_seconds() / 86400


class ProlongWait(Feature):
    key = "prolong_wait"
    title = "Ждёт оплаты"
    description = "Срок услуги истёк, она ждёт оплаты 5 дней и затем удаляется. Чем ближе удаление, тем выше сила."
    default_weight = 60

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        if ctx.service.status != "prolong_wait":
            return None
        days_to_delete = ctx.days_left + GRACE_DAYS
        if days_to_delete <= 0:
            return Signal(1.0, f"Ждёт оплаты, срок удаления прошёл {humanize_days(-days_to_delete)} назад")
        strength = 1 - 0.5 * min(days_to_delete / GRACE_DAYS, 1)
        return Signal(strength, f"Ждёт оплаты, до удаления {humanize_days(days_to_delete)}")
