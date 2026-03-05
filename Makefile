.PHONY: lint format typecheck check test test-client install install-hooks pdf

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
