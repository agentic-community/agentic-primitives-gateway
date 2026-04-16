.PHONY: lint format typecheck check test test-client test-agentcore test-selfhosted install install-hooks pdf ui-install ui-dev ui-build ui-clean docs docs-serve

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
	python -m pytest tests/integration/test_browser.py tests/integration/test_code_interpreter.py \
		tests/integration/test_evaluations.py tests/integration/test_identity.py \
		tests/integration/test_memory.py tests/integration/test_observability.py \
		tests/integration/test_policy.py tests/integration/test_tools.py \
		tests/integration/test_llm_bedrock.py -v

test-selfhosted:
	python -m pytest tests/integration/test_browser_selenium.py \
		tests/integration/test_code_interpreter_jupyter.py \
		tests/integration/test_evaluations_langfuse.py \
		tests/integration/test_identity_keycloak.py tests/integration/test_identity_entra.py \
		tests/integration/test_identity_okta.py tests/integration/test_memory_milvus.py \
		tests/integration/test_observability_langfuse.py tests/integration/test_tools_mcp.py \
		tests/integration/test_redis_stores.py tests/integration/test_checkpoint_redis.py -v

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
