"""Реестр признаков риска: подключаются автоматически.

Каждый модуль этой папки импортируется при старте, а все наследники Feature
с заданным key попадают в FEATURES. Новый признак — новый файл, без правки
этого кода и без ветвлений в движке.
"""
import importlib
import pkgutil

from .base import Feature, ServiceContext, Signal


def _discover() -> list[Feature]:
    for module in pkgutil.iter_modules(__path__):
        if module.name != "base":
            importlib.import_module(f"{__name__}.{module.name}")
    found = [cls() for cls in _subclasses(Feature) if getattr(cls, "key", None)]
    return sorted(found, key=lambda f: (f.order, f.key))


def _subclasses(cls: type) -> list[type]:
    out = []
    for sub in cls.__subclasses__():
        out.append(sub)
        out.extend(_subclasses(sub))
    return out


FEATURES: list[Feature] = _discover()

__all__ = ["FEATURES", "Feature", "ServiceContext", "Signal"]
