.PHONY: check fix test lint type sync build

UV ?= uv

sync:
	$(UV) sync --locked --all-extras

lint:
	$(UV) run --locked ruff check .

type:
	$(UV) run --locked mypy src

test:
	$(UV) run --locked pytest -q

check: lint type test

fix:
	$(UV) run --locked ruff check . --fix
	$(UV) run --locked ruff format .

build:
	$(UV) build
