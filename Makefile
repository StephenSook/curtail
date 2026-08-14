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
# The path list must match CI exactly. It did not: this target checked
# core/src agents/src core/tests while CI also checks agents/tests, so `make types`
# could pass on a change CI would reject. A local gate weaker than the remote one is
# a false green by construction, and the whole point of running gates locally is to
# learn the same verdict sooner.
	uv run mypy core/src agents/src core/tests agents/tests

.PHONY: test
test: ## pytest with coverage gate
	uv run pytest --cov=curtail_core --cov-report=term-missing --cov-fail-under=90

test-browser: ## The console's failure paths in a real Chromium
	@uv run playwright install chromium
	uv run pytest -m browser -p no:cacheprovider

.PHONY: tone
tone: ## AI-tone and em-dash gate
	bash scripts/check-ai-tone.sh

.PHONY: secrets
secrets: ## Full-history secret scan
	gitleaks detect --source . --redact --no-banner --exit-code 1

.PHONY: evals
evals: ## Regenerate the committed eval artifacts from the Board's own cases
	uv run python scripts/export_evals.py

rights: ## Re-parse Attachment A from the fetched corpus into the committed record
	uv run python scripts/extract_attachment_a.py

rights-check: ## Fail if the committed rights record has drifted from the source PDF
	uv run python scripts/extract_attachment_a.py --check

.PHONY: agents
agents: ## Register the fleet in the Agent Registry, from the graph's own node names
# Idempotent: re-running patches the existing cards rather than duplicating them. Refuses
# to write if the node constants have drifted from fleet.py, or if the deployed service
# does not serve a route a card would name. A catalog of unreachable agents is worse than
# an empty catalog, because it is an empty catalog that lies.
	uv run python scripts/register_agents.py

agents-check: ## Fail if the live registry does not hold exactly the agents the graph defines
	uv run python scripts/register_agents.py --check

.PHONY: deployed
deployed: ## Probe the live service, record what it serves, and refresh the fact sheet
# Deliberately NOT part of `verify`. This needs the network and the deployed service,
# and a gate that reddens when a service is intentionally powered down is a gate people
# learn to override. The offline half runs in `test`: it reads the record this writes
# and fails when the repository claims a capability production does not serve.
#
# **The fact sheet is regenerated in the SAME target, because it reads this record.**
# The registry claim quotes the probe stamp, so re-probing without regenerating leaves
# FACTS.md quoting a timestamp that no longer exists in DEPLOYMENT.md. That is not
# hypothetical: it turned a PR red on exactly this, one commit after the stamp was
# introduced. Two commands a human has to remember to run in order is a footgun; one
# target that runs both is not.
	uv run python scripts/probe_deployment.py
	uv run python scripts/generate_facts.py

deployed-check: ## Fail if the committed deployment record has drifted from the live service
	uv run python scripts/probe_deployment.py --check

.PHONY: chaos
chaos: ## The chaos drill: three injected failures, three guards. Run live on camera.
# Bare, like every other target. This one especially: the drill's whole value is that
# it can go RED, so a pipe swallowing its exit code would turn the demonstration into
# the exact theatre it exists to disprove.
	uv run python -m curtail_agents.chaos

.PHONY: verify
verify: lint types test tone chaos ## The pre-commit triplet, tone, and the drill.
# `chaos` belongs here, not beside it. A review pointed out that a standalone target
# nothing runs can rot silently and still be presented as working on camera, which is
# the same defect as a guard that is described rather than attached. Its unit tests
# check the functions; only running the target checks the target.
	@echo ""
	@echo "all gates green"

.PHONY: ci
ci: verify secrets ## Everything CI runs, locally
	@echo ""
	@echo "full CI suite green locally"
