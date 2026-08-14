# Curtail

**A governed multi-agent fleet for prior-appropriation water curtailment administration.**

Built for the [Google x Devpost All Things Agentic Hackathon](https://allthingsagentichackathon.devpost.com/), track: The Fortified Enterprise Fleet.

> **Status: in active development.** Submission closes Aug 31, 2026. Sections marked *(not built yet)* are honest placeholders, not claims, and a test in the suite fails if one of them names something that has since shipped. See [what is actually running](#what-is-actually-running).

---

## The problem

When a California river drops below its drought emergency minimum flow, someone has to work out exactly which water rights must stop diverting, in strict priority order set by four separate court decrees, then draft the curtailment order, serve it on the right parties by a legally specified method, track the statutory clocks it starts, and rescind it when the river recovers.

Today that is slow. In 2021 the State Water Board issued curtailment orders "months after they were warranted." A single Scott River order has accumulated **12 addenda**; the 2021 Scott series carries **51**, individually enumerated from the Board's own index pages.

Two public facts show the shape of the gap:

**The state issued an entire order to correct its own incomplete curtailment list.** Order WR 2026-0005-DWR, June 5, 2026, Finding 8:

> "Through reporting errors, administrative errors, or changes in rights, the rights identified in Attachment A were not included in State Water Board Order WR 2024-0024-DWR, even though they are within Priority Groups 1 through 8. This Order rectifies the error by imposing curtailment on the listed rights."

**The published regulation is stale on its face.** 23 CCR 875.9(b) still prints a penalty of "up to five hundred dollars ($500) for each day." The controlling statute, Water Code 1846(b) as amended by AB 460, has said **$10,000 per day** since January 1, 2025. A system computing liability from the regulation text is wrong by a factor of twenty.

## What Curtail does

It watches the compliance gages, computes a **recommendation** with a per-right justification ledger, drafts the order for a human official to sign, routes legal service and notification as two separate governed channels, and holds the season's state including the statutory review clocks.

**It recommends. It never decides.** Section 875(b) vests the determination in a named human official, and 875(b)(3) preserves that official's discretion to decline, narrow, or suspend based on fisheries information, weather forecasting, ramping needs and voluntary flow contributions. Curtail separates **deterministic facts** from **judgment inputs** and surfaces the second for the official rather than resolving them. No agent output self-executes.

## Why a human must stay in the loop, and why a checkbox is not enough

Naive human review of machine drafts measurably fails. In a 2025 study of clinician review of AI-drafted patient messages, **35 to 45 percent of erroneous drafts were submitted entirely unedited**, and each embedded error was insufficiently addressed by 13 to 15 of 20 reviewers, while 80 percent of those same reviewers reported the drafts reduced their workload and 75 percent judged them safe.[^1]

That is the design constraint. The approval queue is built against the measured failure mode: a per-right justification ledger, a side-by-side diff of the agent draft against the real historical order, and a review flow that makes the reviewer engage with specifics instead of approving a block of text.

## Governance

The Fortified Enterprise Fleet track names seven platform components. This project states plainly which run natively and which are substituted.

| Component | Status |
|---|---|
| Agent Registry | **Native, and populated.** All four fleet agents were registered and discoverable in `agents.list` on a non-organization account ([ADR 0001](docs/adr/0001-governance-platform.md)) as of the stamped probe in [DEPLOYMENT.md](docs/DEPLOYMENT.md), each with its skills and the route that actually reaches it. That record is a **snapshot**: CI never queries the network on purpose, so `make deployed-check` re-probes and fails on drift, and it is run before recording or submitting. Registration is a committed, re-runnable script ([register_agents.py](scripts/register_agents.py)) that builds the catalog from the graph's own node constants, refuses to run if they drift, and refuses to register a route the deployed service does not serve. There is no `agents.create`: registration goes through `services.create` and the registry derives the agent record |
| Agent Identity | Native. API confirmed, write path scheduled |
| Model Armor | **Native, and called by the shipped Scribe as layer 2.** The Scribe node screens untrusted order text through Model Armor before drafting ([model_armor.py](agents/src/curtail_agents/model_armor.py)), chunked to stay inside the documented prompt-injection window so a payload buried on page 40 is still seen, and a partial or unreachable screen reports UNAVAILABLE rather than clean. **This row said "cannot be re-verified" for weeks and that was a wrong diagnosis, not a wrong API:** `gcloud model-armor templates list` returns PERMISSION_DENIED on this account while the REGIONAL endpoint `modelarmor.us-central1.rep.googleapis.com` answers 200 with the same credentials. Layer 1 stays application-side and offline: untrusted text is normalised, matched against injection patterns, fenced and stripped before it can reach a prompt ([sanitize.py](agents/src/curtail_agents/sanitize.py)), and every drafted citation is checked against a verified allowlist ([routing.py](agents/src/curtail_agents/routing.py)). **The two layers are not redundant, and `make chaos` demonstrates why:** on 2026-08-14 the vendor filter matched the injection presented alone and did NOT match the same injection embedded in a plausible Board order, which is exactly the indirect surface this system has. Layer 1 caught both |
| Agent Runtime / Memory Bank | **Partial, and the durable half is done as of revision 00031.** Signing an order writes its statutory clocks to Cloud Firestore, and a SECOND independently constructed client reads the season back ([SEASON.md](docs/SEASON.md), [season_store.py](agents/src/curtail_agents/season_store.py)). Production returns a season written by a different process on a different machine. Two honest limits remain and are stated rather than implied: the ADK traversal still constructs an `InMemorySessionService` **per request**, so the graph's own session state does not outlive a response, and the approval queue is still in-process. Every response carries `durable` and names its store. Vertex AI Agent Engine hosting *(not built yet)* |
| Agent Observability | **Native, and exporting.** The shipped source imports the Cloud Trace exporter, installs a tracer provider, and the HTTP entrypoint calls `configure_tracing` at import ([telemetry.py](agents/src/curtail_agents/telemetry.py)). ADK opens an `invoke_node` span per fleet node and an `invoke_workflow` span around the traversal, so the agent-hop trace is a property of the graph rather than something this project instruments by hand. It exports only where a project id is present, and **the fleet response says which of the two it did**, because a tracing layer that is off and a tracing layer that is working look identical from outside |
| Agent Gateway | **Substituted.** No first-party API is exposed to a non-organization account. Its role is covered by per-agent least-privilege service accounts, API Gateway, egress allowlisting, and Model Armor called inline. Reasoning in [ADR 0001](docs/adr/0001-governance-platform.md) |

An integration a judge cannot reach scores as absent, and scores worse than an honest statement of what is not running. So the substitution is disclosed here rather than implied away.

## Data

All hydrology, water rights, decrees, orders and priority data are **real and public**, drawn from the California State Water Resources Control Board and the USGS. Only notification contact details are synthetic, and they are visibly labeled as such in the interface.

Compliance gages: Scott River at [USGS 11519500](https://waterdata.usgs.gov/monitoring-location/USGS-11519500/) (Fort Jones) and Shasta River at [USGS 11517500](https://waterdata.usgs.gov/monitoring-location/USGS-11517500/) (near Yreka).

## What is actually running

Every figure below is generated by the code that produces it, not written by hand.
`docs/FACTS.md` is regenerated from the corpus and the engine, and CI fails if it
drifts from them, so a number quoted here cannot outlive the thing it measured.

**The backtest, and its wording is deliberate.**

> Curtail reproduces the direction of 6 of 6 scored historical curtailment decisions
> (0 refused, 5 excluded before scoring).

The denominator is what was actually scored, and the exclusions are reported beside
it rather than folded in. Every case is a decision the State Water Board published,
scored against the reading and the operative minimum in the order itself. Full table
and per-case sources in [docs/FACTS.md](docs/FACTS.md).

**The corpus.** 14 base orders and 84 addenda across both basins and both regulatory
eras, 100 documents in total. 95 are scorable; 91 were read from a text layer and 4
by vision. The counts are guarded: the declared total is computed from the series it
sums, addendum numbering must be contiguous, and nothing may be scored before it has
been read.

**The fleet.** An ADK `Workflow` graph, Gage Sentinel to Allocation Core to Order
Scribe to Herald, with the edges enforcing that no drafted order exists without a
computed recommendation behind it. **All four nodes act on their input**, and that
sentence is generated rather than written: [FACTS section 0](docs/FACTS.md) inspects
each node's source and reports whether it returns its input unchanged.

**The console runs the graph.** `POST /api/fleet/{basin}` builds the ADK runner and
drives one real traversal, so a judge clicking through the console exercises the fleet
itself rather than the domain functions the nodes wrap. Until this was wired, `api.py`
called `evaluate`, `recommend` and `draft_order` in order and never built a runner, so
what the console exercised was the agents' logic without ADK's orchestration around it,
and `deliver_order` had no call site at all: Herald, and with it the two service lanes,
could not be reached by anyone who was not running the test suite.

The response is attributed **per node**, read off ADK's own `node_info.path` rather
than inferred from arrival order, because a retried node emits twice and position would
then name the wrong member. That attribution is the point: a payload carrying only the
final answer is indistinguishable from one function computing it, which is precisely
the claim being checked.

Two columns in [FACTS section 0](docs/FACTS.md) keep this honest, and they are separate
on purpose. One asks whether each node **runs the domain function it is named for**,
the other whether the **console reaches it**. Collapsing them is what once hid the
Scribe, which acted on its input for several revisions while sanitizing rather than
drafting: "acts on its input" was true and useless while the graph could not produce an
order at all.

That check exists because this section went stale in the understating direction and no
guard could see it. The claim was prose, the guards checked markers, and a description
outlived the thing it described. A stale disclaimer misleads a judge exactly as much as
an inflated claim does.

The Sentinel classifies real readings against the operative minimum. The Core runs
the deterministic allocation on the Board's own rights table and emits a per-right
justification ledger. The Scribe drafts the order through Gemini 3.5 on Vertex and is
never trusted for a fact: it states its claims separately and they are checked against
the Core's ledger, retried once with the violation fed back, then escalated flagged
UNVERIFIED. Herald routes legal service and notification as two state machines,
because an email a provider calls delivered is not service under Water Code 1121.

**The rights table.** 87 application numbers read out of the attachment to Shasta
Addendum 6; 85 parsed, 71 placed on the priority ladder, and 14 refused placement
because the record states no priority precise enough to establish decree membership.
Owner names are never read, let alone stored: the rows anchor on the application
number, which is also the more correct anchor because the printed table blanks the
owner cell on continuation rows. Counts and the source hash in
[FACTS section 0](docs/FACTS.md).

**The signature.** A drafted order lands in an approval queue and is signed by a named
officer, server side. The wrong officer's signature is none, an approval binds to the
digest of the exact bytes reviewed, and an unverified draft cannot be approved without
naming every finding being overridden. The queue lives in the serving process and says
so on every response. Firestore holds the Season Ledger, not the queue: an unsigned
draft waiting for review is genuinely volatile, and the response says which of the two
a reader is looking at.

**The Gemma Normalizer**, and it runs on the operator's own hardware. A new addendum
arrives as a PDF whose layout drifts between issuances, and something has to turn it
into the ingestion schema. `gemma3:4b` does that through a local runtime: no API key, no
egress, no third party. That is the data sovereignty answer as a mechanism rather than a
sentence, because a watermaster district handles records about named landowners and
"send the documents to a third-party inference API" is a procurement conversation.

**Every value it extracts must appear verbatim in the source document**, or it is
reported as unverified and never returned as a value ([normalizer.py](agents/src/curtail_agents/normalizer.py)).
Three fields were deleted from its schema after live runs disagreed with them: the
narrative states eleven distinct cfs figures, two observed readings, and a priority
RANGE whose endpoints both pass a verbatim check while only one is legally operative.
Choosing between those is the determination 875(b) vests in the Deputy Director, so the
model does not get to make it. **Gemma identifies and files the document, the
deterministic parsers read its table, and a human reads its law.** A real local run is
recorded in [NORMALIZER.md](docs/NORMALIZER.md).

**Failure-tolerant routing**, which the track scores by name. Retries are scoped by
exception, so the deterministic Core never retries and the Sentinel does not retry a
flow-schedule refusal. A node timeout catches a model that loops rather than fails,
and `run_with_deadline` cancels waits that no timeout reaches, because ADK's node
timeout does not cover the session append that follows it.

**The chaos drill**, `make chaos`, runs in CI and can go red. Three injected
failures, three guards, and eight disarm tests prove it fails when a guard is
removed. It states one residual risk out loud: a worker that crashes between sending
a notice and recording it will re-send, which no dedup table can prevent.

**The Season Ledger**, which is what "weeks of asynchronous operations" means here, and
it is durable in production. Signing an order writes its statutory clocks to **Cloud
Firestore**, and `GET /api/season/{basin}` reads them back with what is still running:
the 30-day reconsideration window and 90-day Board response under Water Code 1122, the
30-day judicial review window under 1126(b), certification under 23 CCR 875.6 and the
information-order response under 875.8. The judicial-review window runs from final
action rather than adoption, because for a delegated order those are different events up
to 90 days apart, and the reconsideration window carries **exhaustion required**: these
orders issue under delegated authority, so a petition is a prerequisite to judicial
review rather than an optional first step.

The check that proves durability is a **second, independently constructed client**
reading a season back, recorded in [SEASON.md](docs/SEASON.md). Reading through the
object that just wrote is satisfied by a dictionary. An unreachable store answers 503
rather than an empty season, and every response carries `durable` and names its store,
because an empty season and a lost season look identical and mean opposite things.

**The approval boundary.** Nothing self-executes. An approval binds to the digest of
the exact draft reviewed, the officer identity comes from an HMAC-verified token
rather than a caller-supplied string, the clock is read at the point of decision so
an expired session cannot sign, and an unverified draft cannot be approved without
naming every finding being overridden.

**The demo login establishes a role, not a person, and says so on every record.** It
is gated by a shared passphrase with no default, and the identity comes from a fixed
roster of obviously synthetic ids. It shipped ungated for one revision: a review found
that any caller could mint a Deputy Director token with a name of their choosing, which
makes every signature behind it a fabricated record rather than a weak one. A real
deployment replaces this with IAP and drops the roster. No real official's name appears
anywhere in this system's demo data, because a fabricated order signed in a real
person's name is impersonation whatever disclaimer sits beside it.

**Evidence.** [docs/evals/](docs/evals/) carries an ADK eval set built from the
Board's own record, and an eval result that names which metrics were measured and
which were not. Three of ADK's prebuilt metrics are LLM-as-a-judge and need
credentials this environment does not hold, so they ship as pending with the reason
rather than as a placeholder score.

**Tests.** The suite runs on every push and its size is reported by CI rather than
restated here, because a count in prose goes stale the moment somebody adds a test.
What is worth stating is the discipline: guards are mutation-tested, so a guard that
cannot fail is treated as a guard that does not exist.

## Reach it

The console API runs on Cloud Run and needs no credentials to read:

| | |
|---|---|
| Liveness | https://curtail-console-api-672785135387.us-central1.run.app/api/healthz |
| Classify a reading | [48.7 cfs at Fort Jones, 20 July 2025](https://curtail-console-api-672785135387.us-central1.run.app/api/classify/scott?cfs=48.7&at=2025-07-20T21:30:00%2B00:00) |
| The generated fact sheet | https://curtail-console-api-672785135387.us-central1.run.app/api/facts |
| Interactive docs | https://curtail-console-api-672785135387.us-central1.run.app/docs |

That first link is the reading behind Addendum 7: 48.7 cfs against a 50 cfs July
minimum, classified near-threshold and pointing at restriction. Change the `cfs` and
the date and the answer follows the flow schedule, including the mid-month period
boundaries a month-keyed table cannot express. Ask it for a 2021 Shasta date and it
refuses with a reason rather than answering from the wrong era's table.

Every response says it is a recommendation, and every reading is labelled
`unsourced`, because the endpoint classifies a value you supply and never contacts
USGS.

## Setup

```
make install     # sync the workspace at the pinned interpreter
make verify      # lint, types, tests, tone, and the chaos drill
make chaos       # the drill on its own, live
```

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/). No cloud credentials are
needed for any of the above: the corpus is local, the engine is deterministic, and
the drill runs offline.

## Disclaimer

Curtail is a demonstration system. **It is not an official government system**, carries no government authority, and produces drafts for human review only. It uses no state seals or official branding.

## License

[Apache-2.0](LICENSE).

[^1]: Biro et al., "Opportunities and risks of artificial intelligence in patient portal messaging in primary care," *npj Digital Medicine* 8:222 (2025). DOI [10.1038/s41746-025-01586-2](https://doi.org/10.1038/s41746-025-01586-2).
