**A California regulation currently on the books says the penalty for taking water during a drought curtailment is $500 a day. The statute it implements raised that to $10,000 a day on January 1, 2025. The published rule has been wrong by a factor of twenty for over a year, and it is still the text a person reads.**

Curtail is the coordination layer that computes what the law actually requires, drafts the order, and hands it to a named human official to sign. It never signs anything itself.

## What is actually broken

**The state issued an entire order for the sole purpose of fixing its own curtailment list.** Order WR 2026-0005-DWR, June 5, 2026, Finding 8, verbatim: rights "were not included in State Water Board Order WR 2024-0024-DWR, even though they are within Priority Groups 1 through 8. This Order rectifies the error by imposing curtailment on the listed rights." Somebody eventually noticed, by hand, that a list of hundreds of water rights had rights missing from it.

**Two addenda in the same series disagree about what September's minimum flow is.** Scott Addendum 6 states flows were at or above "the September flow requirement (60 cfs)". Under 23 CCR 875 the Scott September minimum is 33 cfs. 60 cfs is November's number, which is the month that addendum was written in. Addendum 3 states September correctly in the same series. That is a person under load reaching for the wrong row of a table in a legally operative document.

**And nobody is watching between documents.** Across the 8 curtailment actions in the verified regulatory era, the river had already been below its minimum for a median of 3 days, up to 8, when the Board's document was dated. That figure is computed from the USGS daily discharge record and the dates of the Board's own documents, and it is deliberately narrow: 875(b) directs the official to weigh hydrologic conditions, so part of any gap is judgment. **Curtail does not remove the official's judgment. It removes the wait for somebody to notice.**

## Is the task complex enough to warrant a multi-agent system?

The work decomposes into jobs with genuinely different failure modes, different trust levels, and different clocks, which is the test.

Watching a gage is a polling problem measured in minutes. Deciding who must stop is a deterministic legal computation over a priority ladder defined by four separate court adjudications. Drafting the order is generative and therefore the only place hallucination can enter. Serving it is a statutory act under Water Code 1121 with its own clocks. Holding the season is weeks of asynchronous state.

Putting those in one agent means a model's mistake can move a priority date. Keeping them apart means it cannot.

## Does the system intelligently delegate to specialized sub-agents?

Five agents and one deterministic core, on Google ADK, each a separate failure domain.

**Gage Sentinel** polls the USGS OGC API and classifies. **Allocation Core is not an LLM at all**, and that is the central design decision: the priority computation is deterministic Python with a per-right justification ledger, so no model can move a water right. **Order Scribe** drafts through Gemini 3.5 on Vertex behind Model Armor. **Herald is two state machines, not one**, because formal service and notification are legally different acts and a green delivered indicator in the notification lane must never be displayed as service. **Season Ledger** holds the statutory clocks. **Gemma 3 runs locally** through Ollama for document normalization, so an agency that cannot send landowner records to a third-party API can host the weights itself.

## Strictly enforced separation of concerns

The Core recommends and never determines. 875(b) vests determination in a named official and 875(b)(3) preserves that official's discretion to decline, to narrow, or to suspend. Curtail separates deterministic facts from judgment inputs and hands the second to the human rather than resolving them. The word `recommendation` is used throughout the codebase; `determination` is reserved for the human act.

## Failure-tolerant inter-agent routing: how it recovers from a looping or hallucinating worker

**Two independent guards on hallucination.** Every Scribe draft is validated against the Core's computed set before it can reach the PDF generator; a draft asserting a right or a priority date outside that set is rejected with a diff, retried once, then escalated flagged UNVERIFIED. Separately, a deterministic scrubber strips any citation not in an allowlist, because a prohibition inside a system prompt is not a guardrail. Six external models reviewing this concept produced fabricated case law, including two cases that exist in no database.

**Loops are bounded** by a per-node iteration ceiling, a deadline that cancellation actually enforces rather than a timeout that does not, and a dead-letter path preserving the correlation id.

**`make chaos` runs in CI and can go red.** It kills Herald mid-run and shows the dedup table making the replay a no-op, feeds a poisoned order PDF through Model Armor, and forces a Scribe hallucination into both guards. Eight disarm tests prove the drill fails when a guard is removed.

## State and memory

The Season Ledger is durable in Cloud Firestore, and durability is proven by a **second, independently constructed client reading a season back** rather than by reading through the writer. `GET /api/season/{basin}` reports whether its store is durable on every response, so an empty season can never be read as a quiet river. Cloud Scheduler polls both rivers on offset minutes; the poll is idempotent on the reading's own timestamp, so two firings between USGS publications record once.

## Efficient vector embedding strategies

98 fetched Board documents indexed as 552 passages with `gemini-embedding-001`, covering 94 of 98. **The 4 it cannot search are named on every response** with the reason: they are scanned images with no text layer. A search that quietly covers most of a corpus invites the reader to conclude the corpus is silent.

No owner name, business name, contact address or right identifier is indexed. An earlier build leaked 49 Attachment A fragments carrying private individuals' names before the filter was corrected, and a test now asserts the committed artifact against every one of those patterns.

## An Unlikely Hero outside standard corporate roles

The watermaster. Not a developer, not an analyst, a public official who drives to a river with a flow meter. The system is built so the machine can be **overruled by that person**, which is the strongest possible demonstration of governance. In July 2025 the Fort Jones gage read low, curtailment was reinstated, the community disputed the measurement, the Watermaster District took field flows, USGS shifted the rating curve, and curtailment was lifted on July 22. Curtail's field surface exists to carry exactly that correction back into the agents' reasoning, and the device may never hold authority: it submits append-only evidence and the server acts.

## What it refuses to do, scored

**5 of 5 restraint cases behave correctly**, cases where the right answer is not the obvious action. Two refuse outright, raising rather than answering. Three withhold the consequential act while still reporting what they saw, which is the better behaviour: a system that answers "field verification first" is more useful than one that goes silent.

Scoring that axis immediately found a real defect. The Sentinel accepted a discharge of **-5 cfs** and classified it, which on a sensor fault reads as far below the minimum and points at curtailment. It is now unrepresentable.

**Curtail reproduces the direction of 6 of 6 scored historical decisions**, with 5 cases excluded before scoring and every exclusion named. **14 rights are refused placement** on the priority ladder out of 87 seen, because their records state no priority precise enough. Feeding an unknown priority into the sort as a real date puts undated rights in the group curtailed first.

**The Board's own column is read, not inferred.** For the 384 Scott rights, inference agrees with the Board on 8 of 384. Inference puts all 384 in group 1; the Board puts 258 in group 8. Group 1 shuts off first. That is the difference between a ranch irrigating and a ranch shutting off.

## Reproducible setup and visual proof of Google Cloud deployment

Public repo, Apache-2.0, one-command setup in the README, live on Cloud Run with the commit stamped at `/api/version`, the Season Ledger in Cloud Firestore, and both rivers polled by Cloud Scheduler on offset minutes. Every one of those is checkable right now without an account: `/api/version` returns the deployed commit, `/api/watch/{basin}` returns the scheduled observations with the name of the store holding them, and `/api/response-lag` recomputes the headline figure on request.

## Bonuses

- **Additional Google AI models beyond Gemini 3.5:** Gemma 3 (local, Ollama), Chirp 3 HD text-to-speech, Chirp 3 speech-to-text, `gemini-embedding-001`.
- **Content:** linked in the submission form.
- **Social post:** linked in the submission form.

## What I learned

**A claim and a check are different things.** Four separate review rounds this build found defects in code I had just declared finished: a normalisation flag that was never measured, a tolerance check that silently passes NaN, an actionable error message sitting in unreachable code, and a fact sheet claiming five components refused when only two do. Every one passed its own tests. The pattern is that I verified the shape of my reasoning rather than the behaviour underneath it.

**A guard is only as wide as the set it walks.** A hygiene guard resolved files with `git ls-files`, which cannot see a file until it is committed, so the local run was structurally incapable of catching the file about to ship.

**A printed caveat is not a verdict.** Four times, something printed an honest warning and exited 0. The exit code is what a pipeline and a tired person both read.

**Never let an unverified figure become a test assertion.** Three figures were retired during this build that came from research summaries rather than documents, and one had reached an assertion. A wrong number inside a test is worse than anywhere else, because correcting the code then fails CI and reads as a regression.
