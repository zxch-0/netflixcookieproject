"""Netflix dispo checker.

Le package `app` est l'unique point d'entrée ASGI. Il ré-exporte l'instance
FastAPI de `app.main` pour que `uvicorn app:app` fonctionne : sans ce
ré-export, uvicorn importe le package (prioritaire sur un module `app.py`)
et échoue avec `Attribute "app" not found in module "app"`.
"""

from __future__ import annotations

from .main import app

__all__ = ["app"]
