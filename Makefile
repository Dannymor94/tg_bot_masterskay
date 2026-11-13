.PHONY: setup check test run migrate health spike-plan
setup:
	uv sync --frozen
check:
	uv run --frozen ruff format --check .
	uv run --frozen ruff check .
	uv run --frozen pyright
	uv run --frozen pytest
test:
	uv run --frozen pytest
run:
	uv run --frozen python -m app
migrate:
	uv run --frozen alembic upgrade head
health:
	uv run --frozen python -m app.health

spike-plan:
	uv run --frozen python -m scripts.spike_preflight
