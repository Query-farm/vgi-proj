# vgi-proj — dev and test targets.
#
# Usage:
#   make test       # unit/integration (pytest) + end-to-end SQL (haybarn-unittest)
#   make test-unit  # pytest only
#   make test-sql   # DuckDB sqllogictest .test files via haybarn-unittest
#
# test-sql is self-contained: it points VGI_PROJ_WORKER at the worker run as a
# stdio subprocess (exactly how DuckDB drives it after ATTACH) and runs the
# files under test/sql/. haybarn-unittest is a uv tool:
#   uv tool install haybarn-unittest   # installs ~/.local/bin/haybarn-unittest

# Worker command DuckDB uses for ATTACH (overridable). Use the project venv's
# interpreter with absolute paths: haybarn cd's into a staging dir before ATTACH,
# and the venv already has the current SDK installed (unlike `uv run`, whose
# PEP 723 script env can resolve a stale cached SDK).
WORKER_STDIO    ?= $(CURDIR)/.venv/bin/python $(CURDIR)/proj_worker.py

# haybarn-unittest lives in the uv tools bin; keep it on PATH.
HAYBARN_BIN     ?= $(HOME)/.local/bin
TEST_DIR         = .
TEST_PATTERN     = test/sql/*

.PHONY: test test-unit test-sql lint

test: test-unit test-sql

test-unit:
	uv run pytest -q

test-sql:
	PATH="$(HAYBARN_BIN):$$PATH" \
		VGI_PROJ_WORKER="$(WORKER_STDIO)" \
		haybarn-unittest --test-dir "$(TEST_DIR)" "$(TEST_PATTERN)"

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy vgi_proj/
