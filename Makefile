.PHONY: setup check test run migrate health spike-plan spike
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

# Owner-only live read-only probe. Fails closed without --authorize-live-read.
# The owner runs it manually after independent QA, e.g.:
#   make spike SPIKE_ARGS=--authorize-live-read
spike:
	uv run --frozen python -m scripts.spike_readonly $(SPIKE_ARGS)
