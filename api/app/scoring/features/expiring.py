from .base import Feature, ServiceContext, Signal, clamp, humanize_days


class ExpiringWithoutAutoprolong(Feature):
    key = "expiring_no_autoprolong"
    title = "Скоро истекает без автопродления"
    description = "Автопродление выключено, а срок наступает в пределах горизонта. Чем ближе срок, тем выше сила."
    default_weight = 35

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        s = ctx.service
        if s.status not in ("active", "suspended") or s.autoProlong:
            return None
        if ctx.days_left > ctx.horizon_days:
            return None
        if ctx.days_left <= 0:
            return Signal(1.0, f"Срок истёк {humanize_days(-ctx.days_left)} назад, автопродление выключено")
        strength = clamp(1 - ctx.days_left / ctx.horizon_days)
        return Signal(strength, f"Истекает через {humanize_days(ctx.days_left)}, автопродление выключено")
