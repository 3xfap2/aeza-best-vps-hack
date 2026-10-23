"""Реестр признаков риска. Новый признак — новый файл и одна строка в FEATURES."""
from .base import Feature, ServiceContext, Signal
from .expiring import ExpiringWithoutAutoprolong
from .funds import InsufficientFunds
from .prolong_wait import ProlongWait
from .suspended import Suspended
from .unpaid import UnpaidTransactions

FEATURES: list[Feature] = [
    ProlongWait(),
    ExpiringWithoutAutoprolong(),
    InsufficientFunds(),
    UnpaidTransactions(),
    Suspended(),
]

__all__ = ["FEATURES", "Feature", "ServiceContext", "Signal"]
