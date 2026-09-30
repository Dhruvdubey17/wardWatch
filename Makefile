# Single entry point for building, testing and running WardWatch.
# Tools that the host lacks (cmake, ninja, clang-format, clang-tidy, pnpm) are
# installed into .tools/ by `make setup` and put on PATH here.

SHELL := /bin/bash
.DEFAULT_GOAL := help

TOOLS_DIR := $(CURDIR)/.tools
export PATH := $(TOOLS_DIR)/venv/bin:$(TOOLS_DIR)/node/node_modules/.bin:$(PATH)

FUZZ_SECONDS ?= 60

.PHONY: help setup setup-tools data lint lint-prose test-unit test-integration test \
	sanitize fuzz bench train eval coverage check up demo down autopilot

help:
	@grep -E '^[a-z-]+:' $(MAKEFILE_LIST) | cut -d: -f1 | sort | tr '\n' ' '; echo

setup-tools:
	@test -x $(TOOLS_DIR)/venv/bin/python || uv venv -q -p 3.12 $(TOOLS_DIR)/venv
	uv pip install -q -p $(TOOLS_DIR)/venv/bin/python cmake ninja clang-format clang-tidy
	npm install --silent --prefix $(TOOLS_DIR)/node pnpm@9

setup: setup-tools

data:
	@echo "data: added in Phase 0 task P0.6"

lint:
	@echo "lint: component linters are added with each component (Phases 1 to 6)"

lint-prose:
	@echo "lint-prose: added in Phase 0 task P0.4"

test-unit:
	@echo "test-unit: component suites are added with each component (Phases 0 to 6)"

test-integration:
	@echo "test-integration: component suites are added with each component (Phases 1 to 6)"

test: test-unit test-integration

sanitize:
	@echo "sanitize: added in Phase 1 with the ingest engine"

fuzz:
	@echo "fuzz: added in Phase 1 task P1.12"

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
