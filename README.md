# Bye Bye Boss — Backend

Async FastAPI modular monolith. Architecture and domain rules:
## Prérequis

- Python >= 3.12
- uv

## Démarrage

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
make migrate
make run
```

API sur `http://localhost:8000`, docs sur `/api/v1/docs`, health sur `/health`.

## Commandes

```bash
make run                       # serveur de dev (reload)
make test                      # pytest (SQLite)
make lint format typecheck     # ruff + mypy
make migrate                   # alembic upgrade head
make makemigration m="..."     # génère une révision
make module name=offers        # scaffold un module
python -m app.cli list-modules
python -m app.cli routes
```

## Structure

```
app/core/       infrastructure partagée (config, db, events, cache, scheduler...)
app/modules/    modules métier (vertical slices), auto-découverts
migrations/     Alembic
tests/          pytest (SQLite, httpx.ASGITransport)
```

Un module vit dans `app/modules/<name>/` et expose `module = Module(...)` — aucun
routeur à câbler à la main.
