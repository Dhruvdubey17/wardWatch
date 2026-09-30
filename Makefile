# Single entry point for building, testing and running WardWatch.
# Tools that the host lacks (cmake, ninja, clang-format, clang-tidy, pnpm) are
# installed into .tools/ by `make setup` and put on PATH here.

SHELL := /bin/bash
.DEFAULT_GOAL := help

TOOLS_DIR := $(CURDIR)/.tools
export PATH := $(TOOLS_DIR)/venv/bin:$(TOOLS_DIR)/node/node_modules/.bin:$(PATH)

FUZZ_SECONDS ?= 60
PY_PACKAGES := simulator ml fhir_service scorer
INGEST := ingest
# clang-tidy from PyPI does not know where the macOS SDK keeps libc++.
TIDY_EXTRA := $(if $(filter Darwin,$(shell uname -s)),--extra-arg=-isysroot$(shell xcrun --show-sdk-path 2>/dev/null),)
CPP_SOURCES = $(shell cd $(INGEST) && git ls-files -co --exclude-standard '*.cpp' '*.hpp' | grep -v '^build/')

# Lists the test directories of one layer that contain at least one test file,
# so pytest never runs on an empty directory and exits with code 5.
py_test_dirs = $(shell cd python && for d in tests/$(1) ../scripts/tests/$(1) $(addsuffix /tests/$(1),$(PY_PACKAGES)); do \
	ls $$d/test_*.py >/dev/null 2>&1 && echo $$d; done)

.PHONY: python-setup python-lint python-test-unit python-test-integration
.PHONY: ingest-build ingest-lint ingest-test-unit ingest-sanitize ingest-fuzz
.PHONY: help setup setup-tools data lint lint-prose test-unit test-integration test \
	sanitize fuzz bench train eval coverage check up demo down autopilot

help:
	@grep -E '^[a-z-]+:' $(MAKEFILE_LIST) | cut -d: -f1 | sort | tr '\n' ' '; echo

setup-tools:
	@test -x $(TOOLS_DIR)/venv/bin/python || uv venv -q -p 3.12 $(TOOLS_DIR)/venv
	uv pip install -q -p $(TOOLS_DIR)/venv/bin/python cmake ninja clang-format clang-tidy
	npm install --silent --prefix $(TOOLS_DIR)/node pnpm@9

python-setup:
	cd python && uv sync -q
	cd python && uv run pre-commit install

setup: setup-tools python-setup

python-lint:
	cd python && uv run ruff check --config pyproject.toml . ../scripts && uv run ruff format --config pyproject.toml --check . ../scripts
	cd python && uv run mypy layer_markers.py conftest.py tests
	cd python && uv run mypy ../scripts/lint_prose.py ../scripts/tests
	cd python && for pkg in $(PY_PACKAGES); do uv run mypy $$pkg/src $$( [ -n "$$(ls $$pkg/tests/*/*.py 2>/dev/null)" ] && echo $$pkg/tests ) || exit 1; done

python-test-unit:
	cd python && uv run pytest -c pyproject.toml -q -m unit $(call py_test_dirs,unit)

python-test-integration:
	@dirs="$(call py_test_dirs,integration)"; if [ -z "$$dirs" ]; then echo "python-test-integration: no integration suites yet"; \
	else cd python && uv run pytest -c pyproject.toml -q -m integration $$dirs; fi

data:
	./scripts/fetch_physionet.sh
	./scripts/generate_synthea.sh

ingest-build:
	cd $(INGEST) && cmake --preset debug >/dev/null && cmake --build --preset debug

ingest-lint: ingest-build
	cd $(INGEST) && clang-format --dry-run --Werror $(CPP_SOURCES)
	cd $(INGEST) && clang-tidy -quiet -p build/debug $(TIDY_EXTRA) $$(git ls-files -co --exclude-standard 'src/*.cpp' 'apps/*.cpp')

ingest-test-unit: ingest-build
	cd $(INGEST) && ctest --preset unit

ingest-sanitize:
	cd $(INGEST) && cmake --preset asan >/dev/null && cmake --build --preset asan && ctest --preset asan
	cd $(INGEST) && cmake --preset tsan >/dev/null && cmake --build --preset tsan && ctest --preset tsan

ingest-fuzz:
	cd $(INGEST) && cmake --preset fuzz >/dev/null && cmake --build --preset fuzz
	cd $(INGEST) && for target in build/fuzz/fuzz/*_fuzz; do \
		name=$$(basename $$target); corpus=build/fuzz/corpus/$$name; mkdir -p $$corpus; \
		seeds=fuzz/corpus/$$name; [ -d $$seeds ] || seeds=""; \
		$$target -max_total_time=$(FUZZ_SECONDS) -print_final_stats=1 $$corpus $$seeds || exit 1; done

lint: python-lint ingest-lint

lint-prose:
	cd python && uv run python ../scripts/lint_prose.py

test-unit: python-test-unit ingest-test-unit

test-integration: python-test-integration

test: test-unit test-integration

sanitize: ingest-sanitize

fuzz: ingest-fuzz

bench:
	@echo "bench: added in Phase 1 task P1.14"

train:
	@echo "train: added in Phase 3"

eval:
	@echo "eval: added in Phase 3 task P3.11"

coverage:
	@echo "coverage: gates are added with each component"

check: lint lint-prose test sanitize coverage

up:
	@echo "up: added in Phase 7"

demo:
	@echo "demo: added in Phase 7 task P7.4"

down:
	@echo "down: added in Phase 7"

autopilot:
	./scripts/autopilot.sh
