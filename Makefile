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
# **Both packages.** The gate measured `curtail_core` only, so the entire agents package
# was unmeasured: the graph, the API, the Scribe, the Herald and everything added since.
# A coverage gate with a blind spot the size of half the shipped code reports a number
# about the half nobody was worried about. Combined is 94%, so closing it cost nothing
# except discovering that season_store.py had been at 57%.
	uv run pytest --cov=curtail_core --cov=curtail_agents --cov-report=term-missing --cov-fail-under=90

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

scott-rights: ## Re-parse the Scott Attachment A into the committed record
	uv run python scripts/extract_scott_attachment_a.py

scott-rights-check: ## Fail if the committed Scott record has drifted from its source PDF
# NOT in the test suite, and that is deliberate. The corpus is fetched rather than
# vendored, so a test that re-parses the PDF SKIPS on a CI runner, and this project fails
# the build on any skip because a skipped guard is a false green. A human with the corpus
# runs this; the record's internal consistency is asserted by tests that need no PDF.
	uv run python scripts/extract_scott_attachment_a.py --check

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

.PHONY: diagram
diagram: ## Re-render the submission architecture diagram from docs/architecture.html
# Devpost field 28092 is a REQUIRED file upload. The HTML is the source of truth and the
# PNG is generated from it, so the diagram is regenerated rather than redrawn whenever
# the architecture moves. Bare, like every other target: a render that silently drew no
# connections must be able to go red.
	uv run python scripts/render_architecture.py

.PHONY: normalizer
normalizer: ## Run the local Gemma over a real Board order and record what it produced
# Deliberately NOT part of `verify`, and for the same reason as `deployed`: this needs a
# local model CI does not have and a corpus the repository does not carry. The offline
# half runs in `test`, reading the record this writes and failing when it is vacuous,
# unpinned or overclaiming. Bare, so an unreachable model reddens rather than passes.
	uv run python scripts/run_normalizer.py
	uv run python scripts/generate_facts.py

.PHONY: season
season: ## Prove the Season Ledger survives the process, with a SECOND Firestore client
# Not part of `verify`: this needs credentials CI does not have, and a test that wrote
# to the real seasons collection would be editing a legal record. The offline half runs
# in `test`, reading the record this writes.
	uv run python scripts/probe_season_store.py
	uv run python scripts/generate_facts.py

.PHONY: submission
submission: ## Regenerate the submission sheet from what the repository actually contains
	uv run python scripts/generate_submission.py

.PHONY: deploy
deploy: ## Deploy to Cloud Run, stamping the commit, WITHOUT wiping the environment
# **`--update-env-vars`, never `--set-env-vars`.** The second REPLACES the whole
# environment, and using it to add the revision stamp wiped GOOGLE_CLOUD_PROJECT, the
# signing key and the demo passphrase in one command. The service stayed up, every route
# answered, `/api/healthz` said ok, and the Season Ledger silently fell back to
# in-process memory. Nothing was down, so nothing looked wrong.
#
# Run `make deployed` afterwards: it records what the running container can DO, not just
# whether it responds, and the offline test fails if durability or the stamp is missing.
	@test -n "$$GOOGLE_CLOUD_PROJECT" || { \
	  echo "GOOGLE_CLOUD_PROJECT is not set. Refusing to guess which project to deploy to."; \
	  exit 1; }
	gcloud run deploy curtail-console-api --source . --region us-central1 \
	  --project $$GOOGLE_CLOUD_PROJECT \
	  --update-env-vars "CURTAIL_REVISION=$$(git rev-parse HEAD)" --quiet

.PHONY: chaos
chaos: ## The chaos drill: three injected failures, three guards. Run live on camera.
# **Set GOOGLE_CLOUD_PROJECT before recording.** Without it the poisoned-document
# scenario cannot reach Model Armor, reports PARTIAL, and loses the strongest evidence
# it produces: that the vendor filter matches the bare payload and MISSES the same
# payload embedded in an order, while layer 1 catches both. The drill says so and
# refuses to call that run complete, but a recording is made once.
# Bare, like every other target. This one especially: the drill's whole value is that
# it can go RED, so a pipe swallowing its exit code would turn the demonstration into
# the exact theatre it exists to disprove.
	uv run python -m curtail_agents.chaos --allow-partial

.PHONY: chaos-recording
chaos-recording: ## The drill, STRICT. Every layer must actually run. Use before recording.
# The difference from `chaos` is the exit code, and it is the whole point. `verify` must
# stay runnable by a developer with no cloud credentials, so it accepts a partial drill
# and says so out loud. A RECORDING must not: a run that demonstrates layer 1 only looks
# identical on camera to one that demonstrates both, and it silently loses the strongest
# finding the drill produces.
# **No default project, and that is the correction.** This target used to substitute
# `curtail-505118` when GOOGLE_CLOUD_PROJECT was unset, which contradicted its own
# contract: a target whose whole purpose is "set the project before recording" cannot
# set it for you. Worse, on any machine but this one it would aim authenticated live
# requests at a project the operator never chose, which is the ambient-default failure
# that has bitten this account before on another platform.
#
# Bare `test`, so an unset variable exits non-zero rather than being reported in prose
# above a green run.
	@test -n "$$GOOGLE_CLOUD_PROJECT" || { \
	  echo "GOOGLE_CLOUD_PROJECT is not set."; \
	  echo "Set it to the project whose Model Armor template this drill should call:"; \
	  echo "    GOOGLE_CLOUD_PROJECT=<your-project> make chaos-recording"; \
	  echo "Refusing to pick one: a recorded drill must call the project you meant."; \
	  exit 1; }
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
