.DEFAULT_GOAL := help
SHELL := /bin/bash

# Every target runs its command BARE. No pipes on an exit path, because a
# pipeline reports its last command's status and `cmd | tail` turns a failed
# gate into a green one.

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Sync the Python workspace at the pinned interpreter
	uv sync --all-packages

.PHONY: lint
lint: ## ruff check plus format check
	uv run ruff check .
	uv run ruff format --check .

.PHONY: fmt
fmt: ## Apply ruff formatting
	uv run ruff format .
	uv run ruff check --fix .

.PHONY: types
types: ## mypy strict
	uv run mypy core/src agents/src core/tests

.PHONY: test
test: ## pytest with coverage gate
	uv run pytest --cov=curtail_core --cov-report=term-missing --cov-fail-under=90

.PHONY: tone
tone: ## AI-tone and em-dash gate
	bash scripts/check-ai-tone.sh

.PHONY: secrets
secrets: ## Full-history secret scan
	gitleaks detect --source . --redact --no-banner --exit-code 1

.PHONY: verify
verify: lint types test tone ## The pre-commit triplet plus tone. Run before every commit.
	@echo ""
	@echo "all gates green"

.PHONY: ci
ci: verify secrets ## Everything CI runs, locally
	@echo ""
	@echo "full CI suite green locally"
