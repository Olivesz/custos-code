.PHONY: check fix test lint type sync

sync:
	uv sync --all-extras

lint:
	uv run ruff check .

type:
	uv run mypy src

test:
	uv run pytest -q

check: lint type test

fix:
	uv run ruff check . --fix
	uv run ruff format .
