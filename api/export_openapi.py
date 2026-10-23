"""Выгрузка OpenAPI-спецификации в docs/openapi.json: python export_openapi.py"""
import json
from pathlib import Path

from app.main import app

out = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(out)
