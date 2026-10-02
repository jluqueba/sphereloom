# Local checks, identical to what CI runs.
#
# `check.ps1` is the Windows twin of this file. Both run the same four commands in the same
# order as the CI workflow, so a green run locally means a green run in CI.

UV ?= uv

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Create the virtual environment and install the project with dev tools
	$(UV) venv --python 3.13
	$(UV) pip install -e ".[dev]"

.PHONY: format
format: ## Apply formatting
	$(UV) run --no-sync ruff format .

.PHONY: lint
lint: ## Check formatting and lint rules
	$(UV) run --no-sync ruff format --check .
	$(UV) run --no-sync ruff check .

.PHONY: typecheck
typecheck: ## Run the strict type checker
	$(UV) run --no-sync mypy

.PHONY: test
test: ## Run the test suite, excluding the hardware tier
	$(UV) run --no-sync pytest -m "not hardware"

.PHONY: coverage
coverage: ## Run the test suite with coverage reporting
	$(UV) run --no-sync pytest -m "not hardware" --cov --cov-report=term-missing

.PHONY: docs-lint
docs-lint: ## Lint markdown. Needs Node, so it is not part of `check`; CI runs it separately.
	npx --yes markdownlint-cli2

.PHONY: check
check: lint typecheck test ## Run every check CI runs against the Python sources

.PHONY: hardware-test
hardware-test: ## Run the real-camera tier. Needs a camera and an opt-in flag.
	$(UV) run --no-sync pytest -m hardware

