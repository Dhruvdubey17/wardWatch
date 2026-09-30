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
CPP_SOURCES = $(shell cd $(INGEST) && for f in $$(git ls-files -co --exclude-standard '*.cpp' '*.hpp' | grep -v '^build/'); do \
	[ -f $$f ] && echo $$f; done)

# Lists the test directories of one layer that contain at least one test file,
# so pytest never runs on an empty directory and exits with code 5.
py_test_dirs = $(shell cd python && for d in tests/$(1) ../scripts/tests/$(1) $(addsuffix /tests/$(1),$(PY_PACKAGES)); do \
	ls $$d/test_*.py >/dev/null 2>&1 && echo $$d; done)

.PHONY: ml-smoke openapi python-coverage python-env python-setup python-lint python-test-unit python-test-integration
.PHONY: ingest-coverage ingest-build ingest-lint ingest-test-unit ingest-test-integration ingest-sanitize ingest-fuzz
.PHONY: frontend-setup frontend-lint frontend-test-unit frontend-test-e2e frontend-coverage
.PHONY: format help setup setup-tools data lint lint-prose test-unit test-integration test \
	sanitize fuzz bench train eval coverage check up demo down autopilot

help:
	@grep -E '^[a-z-]+:' $(MAKEFILE_LIST) | cut -d: -f1 | sort | tr '\n' ' '; echo

setup-tools:
	@test -x $(TOOLS_DIR)/venv/bin/python || uv venv -q -p 3.12 $(TOOLS_DIR)/venv
	uv pip install -q -p $(TOOLS_DIR)/venv/bin/python cmake ninja clang-format clang-tidy
	npm install --silent --prefix $(TOOLS_DIR)/node pnpm@9

python-setup:
	cd python && uv sync -q
	./scripts/macos_openmp_rpath.sh
	cd python && uv run pre-commit install

FRONTEND := frontend
PNPM := $(or $(wildcard $(TOOLS_DIR)/node/node_modules/.bin/pnpm),pnpm) --dir $(FRONTEND)

frontend-setup:
	$(PNPM) install --frozen-lockfile

setup: setup-tools python-setup frontend-setup

# lib/api/schema.ts is generated from contracts/openapi.json; the lint fails
# when the two drift apart, and `pnpm --dir frontend generate:api` fixes it.
frontend-lint:
	$(PNPM) lint
	$(PNPM) typecheck
	$(PNPM) exec openapi-typescript ../contracts/openapi.json --output ../.tools/openapi-schema.ts >/dev/null
	$(PNPM) exec prettier --stdin-filepath lib/api/schema.ts < .tools/openapi-schema.ts | diff -q - $(FRONTEND)/lib/api/schema.ts >/dev/null \
		|| { echo "frontend-lint: lib/api/schema.ts is stale; run pnpm --dir frontend generate:api" >&2; exit 1; }

frontend-test-unit:
	$(PNPM) test

frontend-coverage:
	$(PNPM) test:coverage

# Needs the compose stack from `make up`.
frontend-test-e2e:
	$(PNPM) test:e2e

python-lint:
	cd python && uv run ruff check --config pyproject.toml . ../scripts && uv run ruff format --config pyproject.toml --check . ../scripts
	cd python && uv run mypy layer_markers.py conftest.py tests testsupport
	cd python && uv run mypy ../scripts/lint_prose.py ../scripts/tests
	cd python && for pkg in $(PY_PACKAGES); do uv run mypy $$pkg/src $$( [ -n "$$(ls $$pkg/tests/*/*.py 2>/dev/null)" ] && echo $$pkg/tests ) || exit 1; done

# uv run may reinstall packages, which drops the macOS OpenMP rpath fix, so
# every Python target that imports xgboost reapplies it first (a no-op when set).
python-env:
	@./scripts/macos_openmp_rpath.sh

python-test-unit: python-env
	cd python && uv run pytest -c pyproject.toml -q -m unit $(call py_test_dirs,unit)

# Integration fixtures use the services from scripts/local_services.sh when its
# environment file exists, and testcontainers otherwise.
LOCAL_SERVICES_ENV := .tools/services/env
with_services = if [ -f $(LOCAL_SERVICES_ENV) ]; then . ./$(LOCAL_SERVICES_ENV); fi;

python-test-integration: ingest-build python-env
	@dirs="$(call py_test_dirs,integration)"; if [ -z "$$dirs" ]; then echo "python-test-integration: no integration suites yet"; \
	else $(with_services) cd python && uv run pytest -c pyproject.toml -q -m integration $$dirs; fi

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

ingest-test-integration: ingest-build
	cd $(INGEST) && ctest --preset integration

# CMake silently drops preset variables when it resets a cache after a
# compiler change, so each sanitizer build checks its flags before testing.
check_cache = grep -q '^$(2)$$' $(INGEST)/build/$(1)/CMakeCache.txt || \
	{ echo "build/$(1) lost $(2); delete ingest/build/$(1)/CMakeCache.txt and rerun" >&2; exit 1; }

ingest-sanitize:
	cd $(INGEST) && cmake --preset asan >/dev/null && cmake --build --preset asan
	$(call check_cache,asan,WARDWATCH_SANITIZERS:STRING=address;undefined)
	cd $(INGEST) && ctest --preset asan
	cd $(INGEST) && cmake --preset tsan >/dev/null && cmake --build --preset tsan
	$(call check_cache,tsan,WARDWATCH_SANITIZERS:STRING=thread)
	cd $(INGEST) && ctest --preset tsan

ingest-fuzz:
	cd $(INGEST) && cmake --preset fuzz >/dev/null && cmake --build --preset fuzz
	cd $(INGEST) && for source in fuzz/*_fuzz.cpp; do \
		name=$$(basename $$source .cpp); target=build/fuzz/fuzz/$$name; seed_name=$${name%_fuzz}; \
		corpus=build/fuzz/corpus/$$seed_name; mkdir -p $$corpus; \
		seeds=fuzz/corpus/$$seed_name; [ -d $$seeds ] || seeds=""; \
		$$target -max_total_time=$(FUZZ_SECONDS) -print_final_stats=1 $$corpus $$seeds || exit 1; done

format:
	$(PNPM) format
	cd $(INGEST) && clang-format -i $(CPP_SOURCES)
	cd python && uv run ruff format -q --config pyproject.toml . ../scripts && uv run ruff check -q --fix --config pyproject.toml . ../scripts

lint: python-lint ingest-lint frontend-lint

lint-prose:
	cd python && uv run python ../scripts/lint_prose.py

test-unit: python-test-unit ingest-test-unit frontend-test-unit

test-integration: python-test-integration ingest-test-integration

test: test-unit test-integration

sanitize: ingest-sanitize

fuzz: ingest-fuzz

bench:
	cd $(INGEST) && cmake --preset release >/dev/null && cmake --build --preset release --target wardwatch_bench
	cd $(INGEST) && ./build/release/bench/wardwatch_bench --benchmark_repetitions=5 \
		--benchmark_report_aggregates_only=true --benchmark_out_format=json \
		--benchmark_out=bench/results/micro-release.json
	@echo "bench: wrote ingest/bench/results/micro-release.json"

# The whole ML pipeline on the committed fixtures with tiny models, for CI.
ml-smoke: python-env
	cd python && uv run wardwatch-ml eval --smoke --data-dir ml/tests/fixtures/physionet \
		--reports-dir ../.tools/ml-smoke-reports --bootstrap-resamples 100

train: python-env
	@test -d data/physionet/training_setA || { echo "train: run make data first" >&2; exit 1; }
	cd python && uv run wardwatch-ml train

eval: python-env
	@test -d data/physionet/training_setA || { echo "eval: run make data first" >&2; exit 1; }
	cd python && uv run wardwatch-ml eval

ingest-coverage:
	./scripts/ingest_coverage.sh

# Line coverage gates from CLAUDE.md, measured over each package's unit and
# integration suites together. Packages without tests yet are skipped.
PY_COVERAGE_GATES := simulator:wardwatch_sim:85 ml:wardwatch_ml:90 fhir_service:wardwatch_fhir:85 scorer:wardwatch_scorer:85

python-coverage: ingest-build python-env
	$(with_services) cd python && for gate in $(PY_COVERAGE_GATES); do \
		pkg=$${gate%%:*}; rest=$${gate#*:}; module=$${rest%%:*}; minimum=$${rest#*:}; \
		ls $$pkg/tests/*/test_*.py >/dev/null 2>&1 || { echo "python-coverage: $$pkg has no tests yet"; continue; }; \
		uv run pytest -c pyproject.toml -q --cov=$$module --cov-report=term-missing:skip-covered \
			--cov-report=json:../.tools/coverage-$$pkg.json --cov-fail-under=$$minimum $$pkg/tests || exit 1; \
	done
	cd python && uv run python ../scripts/coverage_file_gates.py ../.tools/coverage-fhir_service.json

openapi:
	cd python && uv run wardwatch-fhir openapi --output ../contracts/openapi.json

coverage: ingest-coverage python-coverage frontend-coverage

check: lint lint-prose test sanitize coverage

up:
	@echo "up: added in Phase 7"

demo:
	@echo "demo: added in Phase 7 task P7.4"

down:
	@echo "down: added in Phase 7"

autopilot:
	./scripts/autopilot.sh
