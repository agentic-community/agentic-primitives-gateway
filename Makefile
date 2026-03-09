.PHONY: lint format typecheck check test test-client install install-hooks pdf ui-install ui-dev ui-build ui-clean docs docs-serve

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

typecheck:
	mypy

check: lint typecheck test test-client

test:
	python -m pytest tests/ -v

test-client:
	cd client && python -m pytest tests/ -v

install:
	pip install -e ".[dev]"
	cd client && pip install -e ".[dev]"

install-hooks:
	pre-commit install

pdf:
	python scripts/md2pdf.py

ui-install:
	cd ui && npm install

ui-dev:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build

ui-clean:
	rm -rf src/agentic_primitives_gateway/static ui/node_modules

docs:
	mkdocs build

docs-serve:
	mkdocs serve
