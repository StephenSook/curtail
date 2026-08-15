**A California regulation currently on the books says the penalty for stealing water during a drought curtailment is $500 a day. The statute it is supposed to implement raised that to $10,000 a day on January 1, 2025. The published rule has been wrong by a factor of twenty for over a year, and it is still the text a person reads.**

For the 8 days of one documented violation, that is $4,000 on the page against $80,000 in the law. Curtail computes both figures and shows the gap, because the gap is the whole point: the rules move faster than the systems that carry them.

## What is actually broken

When a river in California falls below its drought emergency minimum flow, someone has to work out which water rights must stop diverting, in strict priority order set by four separate court decrees, then draft the curtailment order, serve it on the right parties by a legally specified method, track the statutory clocks it starts, and rescind it when the river recovers.

Every one of those steps is done by hand today, and the record of that shows up in the Board's own documents.

**The state issued an entire order for the sole purpose of fixing its own curtailment list.** Order WR 2026-0005-DWR, June 5, 2026, Finding 8, verbatim: rights "were not included in State Water Board Order WR 2024-0024-DWR, even though they are within Priority Groups 1 through 8. This Order rectifies the error by imposing curtailment on the listed rights." Somebody eventually noticed, by hand, that a list of hundreds of water rights had rights missing from it. Every day between the omission and that order, those diverters kept taking water they were not entitled to, lawfully, because nobody had told them otherwise.

**Two addenda in the same order series disagree about what September's minimum flow is.** Scott Addendum 6, issued November 13, 2024, states that flows have been at or above "the September flow requirement (60 cfs)". Under 23 CCR 875 the Scott September minimum is 33 cfs. 60 cfs is November's number, which is the month the addendum was written in. Scott Addendum 3 states September correctly as 33 cfs in the same series. That is a person under load reaching for the wrong row of a table in a legally operative document, and it is exactly the error a computed system does not make.

**And it is slow.** In 2021 the Board issued curtailment orders "months after they were warranted". A single Scott order has since accumulated 12 addenda, and the 2021 Scott series carries 51, each one a hand-drafted document responding to a river that had already moved.

The state has systems that record water rights, display them on maps, and analyze them. None of them computes who must stop diverting, and none of them produces the order that makes it lawful. That is the missing layer, and it is missing everywhere: 17 to 19 western states run prior appropriation.

## What Curtail does

It watches the compliance gage, computes who must stop diverting with a per-right justification you can read line by line, drafts the order for a human official to sign, routes legal service and notification as two separate governed channels, and holds the season's state including the statutory review clocks that start the moment the order is adopted.

**It recommends. It never decides.** 23 CCR 875(b) vests the determination in a named human official, and 875(b)(3) preserves that official's discretion to decline, to use a smaller priority grouping, or to suspend. Curtail separates deterministic facts from judgment inputs and hands the second to the official rather than resolving them. No agent output self-executes.

The fleet is four ADK nodes and a deterministic core:

- **Gage Sentinel** reads the compliance gage against the operative minimum, which is date-period bounded rather than a month key, because the Scott minimum changes mid-month on June 24.
- **Allocation Core** is deterministic Python and not a model. Priority law is not a thing to sample from.
- **Order Scribe** drafts on Gemini 3.5 Flash through Vertex AI, behind Model Armor and behind a citation scrubber.
- **Herald** is two state machines, because formal legal service under Water Code 1121 and notification under 875(d)(2) are different legal acts. A green "delivered" in the notification lane is never reported as service.
- **Season Ledger** holds the season in Cloud Firestore, including the 30-day reconsideration clock, the 90-day Board response clock, and the exhaustion flag that makes reconsideration a prerequisite to judicial review for these delegated orders.

`POST /api/fleet/{basin}` builds the ADK runner and drives one real traversal, so all 4 nodes are exercised by clicking and not only by the test suite. The response names which node produced each part, read from ADK's own attribution rather than from a label this project wrote.

## Run it yourself, right now, with no account and no key

Every link below is an unauthenticated GET against the live Cloud Run service.

- **Classify the reading that triggered a real order.** 48.7 cfs at Fort Jones on July 20, 2025, against the July minimum of 50: https://curtail-console-api-672785135387.us-central1.run.app/api/classify/scott?cfs=48.7&at=2025-07-20T21:30:00%2B00:00
- **The fact sheet the service generates about itself**, which is the same file every number on this page is drawn from: https://curtail-console-api-672785135387.us-central1.run.app/api/facts
- **The Season Ledger**, which reports whether it is durable and names its store on every response, so an empty season can never read as a quiet river: https://curtail-console-api-672785135387.us-central1.run.app/api/season/shasta

Or from a terminal:

```
curl -s "https://curtail-console-api-672785135387.us-central1.run.app/api/classify/scott?cfs=48.7&at=2025-07-20T21:30:00%2B00:00"
```

That one comes back `reading_near_threshold` with `direction: restrict`, and it labels the provenance of the number you handed it as `unsourced`, because it classified a value from the caller and did not fetch one. A reading the system did not obtain itself is never allowed to look like a measurement it did.

## What it proves, and what it refuses

**Curtail reproduces the direction of 6 of 6 scored historical curtailment decisions**, replaying the Board's own published readings against the Board's own published decisions, with **5 cases excluded before scoring and every exclusion named with its reason**: a document stating a bound rather than a reading, a range observed over a week rather than a point reading on a decision date, and decisions scoped to a single named diverter rather than to a basin-wide threshold. The denominator is what was actually scored, and the refusals are published rather than folded in.

**14 rights are refused placement on the priority ladder**, out of 87 application numbers seen and 85 rows parsed from the Board's own attachment to Order WR 2024-0006-DWR. Their records state no priority precise enough to establish decree membership. Refusing is the correct answer there, and it is the number a demo is tempted to hide. Feeding an unknown priority into the sort as though it were a real date puts undated rights in the first group curtailed, which is the group that shuts off first.

**The Board's own column is read, not inferred.** For the 384 Scott rights, placing them from their attributes alone agrees with the Board on 8 of 384. Inference puts all 384 in group 1; the Board puts 258 of them in group 8. Group 1 is curtailed first and group 8 is curtailed nearly last, so that is not a rounding difference. It is the difference between a ranch irrigating and a ranch shutting off, and the only reason anyone knows the inference was wrong is that both were computed and compared.

## The Fortified Enterprise Fleet criteria, answered with mechanisms

- **Cataloged for cross-department use.** 4 Curtail agents are registered in Agent Registry, each carrying the route that actually reaches it. The registry enforces unique interface URLs, so four agents cannot share one address, and that constraint is correct: an address identifies an agent.
- **Context across weeks of asynchronous operations.** The Season Ledger is in Cloud Firestore, and a second, independently constructed client reads a season back with 2 statutory clocks still running. That second-client read is the check that separates durable from remembered, and a ledger that only its own writer can see is the latter.
- **Failure-tolerant routing**, which this track's rubric asks by name. A Scribe draft is validated against the Core's computed set before it can reach the PDF generator, and any citation not on the verified list is stripped and flagged. `make chaos` runs the drill live, including an indirect prompt injection screened both on its own and embedded inside an order document, because those are different tests and only one of them is easy.
- **Data sovereignty.** Gemma 3 4B runs locally through Ollama over a published Board order and returns 4 fields, each checked verbatim against the source text before it is accepted. No document leaves the machine. An agency that cannot send landowner records to a third-party inference API can host these weights itself. The model identifies and files a document; it is not permitted to read law out of one, and the field that would have let it was removed from the schema for that reason.
- **Observability.** OpenTelemetry to Cloud Trace, with an `invoke_node` span per fleet member and an `invoke_workflow` span around the traversal, so the agent-hop trace is a property of the graph rather than instrumentation bolted alongside it.
- **The Unlikely Hero** is the watermaster: a county official standing in a river holding a flow meter, who in July 2025 disputed a gage reading, took field measurements, sent them to USGS, moved the rating curve, and got curtailment lifted. An agent architecture that cannot be corrected by the person responsible for it is not governed. That correction path is the design.

## Built with

Python 3.13, Google ADK, FastAPI on Cloud Run. Gemini 3.5 Flash through Vertex AI. Cloud Firestore. Model Armor. Agent Registry. Cloud Trace. Gemma 3 4B locally through Ollama.

## Findings and learnings

**Every claim in the README and in this description is computed by a generator from the shipped source, and CI fails when the two drift.** That was not tidiness. Three figures were retired during this build that had come from research summaries rather than from documents, and one of them had already reached a test assertion. A wrong number inside an assertion is worse than a wrong number anywhere else, because correcting the code then fails CI and reads as a regression. The suite defends the error instead of catching it.

**A guard is only as wide as the set it walks.** A repository hygiene guard resolved its files with `git ls-files`, which cannot see a file until it is committed, so the local run was structurally incapable of catching the file that was about to ship. A blacklist of fabricated legal citations went vacuous through its own exclusions. Three separate assertions ran only on the branch that happened to be true, and all three went silent at the commit where they mattered.

**A printed caveat is not a verdict.** This happened four times in one project. The chaos drill printed a partial result and exited 0. An offline switch printed "these prove nothing" and returned success. The deployment record printed the served commit beside the repository commit, stated in prose that nothing here deploys on merge, and passed. The exit code is what a pipeline and a tired person both read, and everything else is decoration.

**A quote is where fabrication hides.** A check that greps every quoted sentence against its named source failed on its first run, on the very record created to correct an earlier bad figure. The quote was a sentence I had composed. It carried correct numbers and its two dates were swapped. A composed sentence with correct figures is harder to catch than a wrong number, because everything checkable about it checks out.

**Six external models reviewing this concept produced fabricated case law**, including two cases that exist in no database, plus a real Nevada case miscited as Idaho. The legal anchor this project started with turned out to be a Colorado change-of-water-right case, overruled in part, that never held what it was cited for. That is why the citation guard is a deterministic scrubber that runs after the model call rather than an instruction inside the prompt. A prohibition in a system prompt is not a guardrail.

**There is measured evidence that human review of AI drafts fails**, which is why the approval queue is designed the way it is. In a 2025 study of clinicians reviewing drafted patient messages containing planted errors, 35 to 45 percent of erroneous drafts were submitted entirely unedited, while most reviewers reported the drafts reduced their workload and felt safe. Every project in this space will show a human-in-the-loop checkbox. A checkbox is the thing that study measured failing. The per-right justification ledger, the diff against the historical order, and a queue that makes the reviewer engage with specifics are the design response to a known failure mode rather than to an imagined one.

## What is not claimed

Stated plainly, because an undisclosed gap is the exact failure this product exists to address. There is no Cloud SQL and no Pub/Sub broker: `messaging.py` implements the ordering key, dead letter and dedup discipline against the real library's types, and nothing publishes to a topic, so that box stays unticked. Ticking it would be true of the design and false of the system. The notification transport is synthetic and every report says so. Scott contributes two scored backtest cases and the reasons the others were excluded are published rather than summarized away.

## What is next

Wire the Pub/Sub broker `messaging.py` was written against, extend the backtest across the 2021 order series once those readings are verified from the documents themselves rather than from summaries, and take the field capture path to a physical device so the watermaster who corrected the machine in July 2025 could have done it from the riverbank.

A rancher should learn that their water right has been curtailed on the day the river crossed the line, from an order that cites the law that actually applies, signed by the official the statute names. None of that requires a new legal theory. It requires the coordination layer nobody has built, and this is a working one.
