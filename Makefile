.PHONY: lint format typecheck check test test-client test-agentcore test-selfhosted install install-hooks pdf ui-install ui-dev ui-build ui-test ui-clean docs docs-serve

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

test-agentcore:
	python -m pytest \
		tests/integration/browser/test_agentcore.py \
		tests/integration/code_interpreter/test_agentcore.py \
		tests/integration/evaluations/test_agentcore.py \
		tests/integration/identity/test_agentcore.py \
		tests/integration/llm/test_bedrock.py \
		tests/integration/memory/test_agentcore.py \
		tests/integration/observability/test_agentcore.py \
		tests/integration/policy/test_agentcore.py \
		tests/integration/tools/test_agentcore.py -v

test-selfhosted:
	python -m pytest \
		tests/integration/browser/test_selenium.py \
		tests/integration/code_interpreter/test_jupyter.py \
		tests/integration/evaluations/test_langfuse.py \
		tests/integration/identity/test_keycloak.py \
		tests/integration/identity/test_entra.py \
		tests/integration/identity/test_okta.py \
		tests/integration/llm/test_openai.py \
		tests/integration/memory/test_milvus.py \
		tests/integration/observability/test_langfuse.py \
		tests/integration/tools/test_mcp.py \
		tests/integration/stores/test_redis.py \
		tests/integration/stores/test_checkpoint_redis.py -v

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

ui-test:
	cd ui && npm test

ui-clean:
	rm -rf src/agentic_primitives_gateway/static ui/node_modules

docs:
	mkdocs build

docs-serve:
	mkdocs serve
