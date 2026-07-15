.DEFAULT_GOAL := help
.PHONY: help install dev run migrate makemigration module test lint format typecheck up down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime + dev dependencies (uv)
	uv pip install -e ".[dev]"

run: ## Run the dev server with reload
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev:
	uvicorn app.main:app --reload

migrate: ## Apply migrations to head
	alembic upgrade head

makemigration: ## Autogenerate a migration: make makemigration m="add products"
	alembic revision --autogenerate -m "$(m)"

module: ## Scaffold a new module: make module name=product
	python -m app.cli new-module $(name)

routes: ## Print the registered route table
	python -m app.cli routes

test: ## Run the test suite
	pytest

lint: ## Lint with ruff
	ruff check app tests

format: ## Format with ruff
	ruff format app tests

typecheck: ## Type-check with mypy
	mypy app

up: ## Start full stack (api + postgres + redis)
	docker compose up --build

down: ## Stop the stack
	docker compose down
