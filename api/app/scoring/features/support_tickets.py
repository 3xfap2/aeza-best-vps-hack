from .base import Feature, ServiceContext, Signal


class SupportTickets(Feature):
    key = "support_tickets"
    title = "Часто пишет в поддержку"
    description = (
        "Сигнал из системы поддержки: обращения за 30 дней (signals.supportTickets30d аккаунта). "
        "Сила — обращений / 3, не больше 1. Без этих данных признак не срабатывает."
    )
    order = 80
    default_weight = 20

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        n = ctx.account.signals.get("supportTickets30d")
        if not n or n <= 0:
            return None
        return Signal(min(1.0, n / 3), f"{int(n)} обращений в поддержку за 30 дней")
