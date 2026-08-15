# Curtail

**The state's systems record, display and analyze water rights. None of them computes who must stop diverting, or produces the order that makes it lawful. Curtail is that missing layer, and a human official always signs.**

## The problem

When a California river falls below its drought emergency minimum flow, someone has to work out exactly which water rights must stop diverting, in strict priority order set by four separate court decrees, then draft the curtailment order, serve it on the right parties by a legally specified method, track the statutory clocks it starts, and rescind it when the river recovers.

Today that is slow, and two public facts show the shape of the gap.

**The state issued an entire order to correct its own incomplete curtailment list.** Order WR 2026-0005-DWR, June 5, 2026, Finding 8: rights "were not included in State Water Board Order WR 2024-0024-DWR, even though they are within Priority Groups 1 through 8. This Order rectifies the error by imposing curtailment on the listed rights."

**The published regulation is stale on its face.** 23 CCR 875.9(b) still prints a penalty of up to $500 for each day of violation. The controlling statute, Water Code 1846(b) as amended by AB 460, has said $10,000 per day since January 1, 2025. Curtail computes both: for the 8 days of a real documented violation, $80,000 under the statute against $4,000 under the regulation text, an understatement factor of 20x. A system that read liability off the published rule would be wrong by that factor, and the multiple is computed from the two figures rather than asserted.

## What it does

A five-agent fleet plus a deterministic core, running as one ADK graph:

- **Gage Sentinel** reads the compliance gage against the operative minimum, which is date-period bounded rather than a month key.
- **Allocation Core** is deterministic Python, not a model. It produces a **recommendation** with a per-right justification ledger, separating deterministic facts from the judgment inputs it surfaces and never resolves.
- **Order Scribe** drafts the order on Gemini 3.5 Flash through Vertex AI, behind Model Armor and behind a citation scrubber.
- **Herald** runs **two** state machines, because formal legal service and notification are legally different acts. A green "delivered" in the notification lane is never reported as service.
- **Season Ledger** holds the season in Cloud Firestore, including the statutory review clocks.

**It recommends. It never decides.** 23 CCR 875(b) vests the determination in a named human official and 875(b)(3) preserves that official's discretion to decline, narrow or suspend. No agent output self-executes.

`POST /api/fleet/{basin}` builds the ADK runner and drives one real traversal, so all 4 nodes are exercised by clicking and not only by the test suite. The response names which node produced each part, read from ADK's own attribution.

## The Fortified Enterprise Fleet criteria, answered with mechanisms

- **Cataloged for cross-department use**: 4 Curtail agents are registered in Agent Registry, each carrying the route that actually reaches it. The registry enforces unique interface URLs, so four agents cannot share one address.
- **Context across weeks of asynchronous operations**: the Season Ledger is in Cloud Firestore, and a second, independently constructed client reads a season back with 2 statutory clocks still running. That second-client read is the check that distinguishes durable from remembered.
- **Failure-tolerant routing**, which the rubric asks by name: a Scribe draft is validated against the Core's computed set before it can reach the PDF generator, and a citation not present in the verified list is stripped and flagged. `make chaos` runs the drill live, including an indirect prompt injection both alone and embedded in an order document.
- **Data sovereignty**: Gemma 3 4B runs locally through Ollama over a published Board order and returns 4 fields, each verified verbatim against the source text before it is accepted. No document leaves the machine. An agency that cannot send landowner records to a third-party inference API can host these weights itself. The model identifies and files a document; it is not permitted to read law out of one.
- **Observability**: OpenTelemetry to Cloud Trace, with an `invoke_node` span per fleet member and an `invoke_workflow` span around the traversal, so the agent-hop trace is a property of the graph rather than something bolted on.
- **Unlikely Hero**: the watermaster, a county official standing in a river with an instrument, not a corporate role.

## How I built it

Python 3.13, Google ADK, FastAPI, deployed on Cloud Run. Gemini 3.5 Flash through Vertex AI. Cloud Firestore for the Season Ledger. Model Armor on the drafting path. Agent Registry for the catalog. Cloud Trace for telemetry. Gemma 3 4B locally through Ollama for the sovereign normalization path.

Every claim in the README and in this description is computed by a generator from the shipped source, and CI fails when the two drift. That was not tidiness: this project retired three figures during the build that had come from research summaries rather than documents, one of which had already reached a test assertion, where a wrong number is defended by the suite rather than caught by it.

## Other data sources used

All hydrology, rights and order data is real and public.

- **The Shasta rights table** is parsed from the Board's own attachment to Order WR 2024-0006-DWR, Addendum 6. 87 application numbers seen, 85 rows parsed, 71 placed on the priority ladder, and **14 refused placement** because the record states no priority precise enough to establish decree membership. Refusing is the correct answer there, and this is the number a demo is tempted to hide.
- **The Scott rights table** is parsed from Order WR 2024-0024-DWR, Addendum 12, Attachment A: 384 rights, reconciled against 384 application numbers found anywhere in the document. **The grouping is the Board's own column, not inference.** Placing those rights from attributes alone agrees with the Board on 8 of 384. Group 1 is curtailed first and group 8 is curtailed nearly last, and the Board puts 258 rights in group 8, so that is not a rounding difference. It is the difference between a ranch irrigating and a ranch shutting off.
- **The backtest** replays the Board's own published readings against its own published decisions. Curtail reproduces the direction of **6 of 6 scored historical curtailment decisions**, with **5 excluded before scoring and each exclusion named with its reason**: a document stating a bound rather than a reading, a range observed over a week rather than a point reading, and decisions scoped to one named diverter rather than to a basin-wide threshold.

## Findings and learnings

**A guard is only as wide as the set it walks, and that is where the bugs live.** A repository hygiene guard resolved its files with `git ls-files`, which cannot see a file until it is committed, so the local run was structurally incapable of catching the file about to ship. A blacklist of fabricated legal citations went vacuous through its own exclusions. Three separate assertions only ran on the branch that happened to be true, and all three went silent at the commit that mattered.

**A printed caveat is not a verdict.** This happened four times. The chaos drill printed a partial result and exited 0. The offline switch printed "these prove nothing" and returned success. The deployment record printed the served commit beside the repository commit, said in prose that nothing here deploys on merge, and passed. Each is the same defect: the exit code is what a pipeline and a tired human both read.

**A quote is where fabrication hides.** A check that greps each quoted sentence against its named source failed on its first run, on the very record created to correct an earlier fabricated figure. The "quote" was a sentence I had composed. It contained correct numbers, and its two dates were swapped. A composed sentence carrying correct figures is harder to catch than a wrong number, because everything checkable about it checks out.

**Inference disagreed with the agency, and the agency was right.** Placing Scott's rights on the priority ladder from their attributes matched the Board on 8 of 384. The Board publishes the group in its own column. Reading it was correct and deriving it was not, and only comparing the two revealed that.

**Six external models reviewing this concept produced fabricated case law**, including two cases that exist in no database, plus a real Nevada case miscited as Idaho. The legal anchor this project started with turned out to be a Colorado change-of-water-right case, overruled in part, that never held what it was cited for. That is why the citation guard is a deterministic scrubber after the model call rather than an instruction inside the prompt: a prohibition in a system prompt is not a guardrail.

## What is not claimed

Named explicitly, because an undisclosed gap is the failure mode this product exists to address. There is no Cloud SQL and no Pub/Sub broker: `messaging.py` implements the ordering key, dead letter and dedup discipline against the real library's types, but nothing publishes to a topic, so the box stays unticked. The notification transport is synthetic and every report says so. Scott has no scored backtest cases of its own beyond the two listed, and the reasons are published rather than summarized away.

## What is next

Wire the Pub/Sub broker that `messaging.py` was written against, extend the backtest across the 2021 order series once those readings are verified from the documents themselves rather than from summaries, and take the field capture path to a physical device.
