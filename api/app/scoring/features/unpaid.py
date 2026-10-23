from .base import Feature, ServiceContext, Signal


class UnpaidTransactions(Feature):
    key = "unpaid_transactions"
    title = "Неоплаченные счета"
    description = "Доля транзакций услуги в статусе not_performed: клиент создал заказ или продление, но не оплатил."
    default_weight = 15

    def evaluate(self, ctx: ServiceContext) -> Signal | None:
        if ctx.tx_total == 0 or ctx.tx_unpaid == 0:
            return None
        return Signal(ctx.tx_unpaid / ctx.tx_total, f"Не оплачено {ctx.tx_unpaid} из {ctx.tx_total} транзакций")
