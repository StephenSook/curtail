# Demo video: shot list and narration

Four minutes maximum. **Every figure below is quoted from `docs/FACTS.md` and nowhere
else.** A published video cannot be corrected, and a previous project shipped a spoken
number that had come from a memory note with its qualifier stripped. If a number is not in
the fact sheet it does not get said.

## The shape, and why it is not the obvious one

Three grand-prize videos studied for this build opened on a **claim**, not a problem
preamble, ran the live product for most of the runtime, and put the architecture LAST.
The ADK grand prize deferred all architecture past 3:56 and ran 63 percent over its stated
cap without being penalised. Globot's winning entry visibly waits on its own system, with
the narrator saying "just wait a second".

So: hook, then one continuous unedited run, then architecture. The rules ask whether the
video shows "an unedited, live execution of the agent performing its task", and a composited
film answers that question badly.

**The traversal takes about 63 seconds** because the Scribe calls Gemini for real. That is a
quarter of the runtime. It is not cut, and it is not sped up. The narration talks through it
while it works, which is what an operator watching this actually experiences.

---

## Beat 1, hook (0:00 to 0:18)

*Screen: the console, live, already loaded.*

> A California regulation on the books today says the penalty for taking water during a
> drought curtailment is five hundred dollars a day. The statute it implements raised that
> to ten thousand. Eight days of violation is eighty thousand dollars under the statute and
> four thousand under the regulation. Twenty times wrong, in the text a person actually
> reads.

*(FACTS.md section 4: $80,000, $4,000, 20x, all computed from Water Code 1846(b) as amended
by AB 460, effective January 1 2025.)*

## Beat 2, the river, live (0:18 to 0:45)

*Screen: click the live gage. A real USGS read returns.*

> This is the Shasta River, read live from the USGS gage seconds ago. Curtail is the
> coordination layer between that reading and a legal order, and it never signs anything.

*Screen: run the Allocation Core. The recommendation and the per-right ledger appear.*

> The Allocation Core is not a language model. It is deterministic Python over a priority
> ladder defined by four court adjudications, and it writes a justification line for every
> right. No model can move a water right here.

## Beat 3, the refusal (0:45 to 1:10)

*Screen: enter a near-threshold reading. The system declines to order.*

> Watch what it does when the reading is too close to call. It does not draft an order. It
> asks for a field measurement, which is what the Fort Jones watermaster actually does.
>
> Five restraint cases are scored: two where the system refuses outright, three where it
> withholds the consequential act and still says what it saw. Scoring them found a real
> defect. The Sentinel accepted a discharge of minus five cubic feet per second and
> classified it. A sensor fault reads as far below the minimum and points at curtailment.
> It is now unrepresentable.

## Beat 4, the fleet, running (1:10 to 2:20)

*Screen: start the fleet traversal. It runs for about 63 seconds. Do not cut.*

> This is the agent fleet running end to end on Google's ADK. Gage Sentinel, Allocation
> Core, Order Scribe, Herald. It takes about a minute because the Scribe is calling Gemini
> 3.5 on Vertex for real, and this is not sped up.
>
> While it works: Herald is two state machines, not one, because formal service under Water
> Code 1121 and notification are legally different acts. A green delivered indicator in the
> notification lane must never be shown as service.
>
> Every draft is checked against the Core's computed set before it can become a PDF. A draft
> that names a right the Core never reached is rejected with a diff. Separately, a
> deterministic scrubber strips any citation not on an allowlist, because a prohibition
> inside a system prompt is not a guardrail. Six external models reviewing this concept
> produced fabricated case law, including two cases that exist in no database.

*Screen: the traversal completes, each node attributed.*

## Beat 5, what it refuses and what it proves (2:20 to 2:50)

*Screen: the backtest and the rights ledger.*

> Against the Board's own published decisions, the engine reproduces six of six scored
> historical actions, with five excluded before scoring and every exclusion named.
>
> Fourteen rights of eighty-seven are refused a place on the ladder, because their records
> state no priority precise enough. Refusing is correct: an unknown priority fed in as a
> real date puts undated rights in the group that shuts off first.

*Screen: corpus search, a natural-language question.*

> Five hundred and seventy-seven passages from a hundred and one Board documents, indexed
> with gemini-embedding-001. It names the five it cannot search.

## Beat 6, proof it runs on Google Cloud (2:50 to 3:25)

*Screen, unedited, in this order: Cloud Run revision; Cloud Trace showing the traversal just
run; Cloud Scheduler jobs; Firestore.*

> This is the Cloud Run revision serving that session. This is Cloud Trace: seven spans, one
> per agent, from the traversal you just watched. These are the Cloud Scheduler jobs that
> poll both rivers on offset minutes, and this is the Season Ledger in Firestore holding the
> statutory clocks.

## Beat 7, the architecture and the Unlikely Hero (3:25 to 3:55)

*Screen: the architecture diagram.*

> The unlikely hero here is a watermaster. Not a developer. A public official who drives to
> a river with a flow meter.
>
> In July 2025 the gage read low, curtailment was reinstated, the community disputed the
> reading, field flows went to USGS, the rating curve shifted, and curtailment was lifted.
> This system is built so that correction flows back into the agents' reasoning, from a
> phone, offline. The device never holds authority: it submits evidence, the server acts.
>
> Gemma 3 runs locally, so an agency that cannot send landowner records to a third-party
> API hosts the weights itself.

## Beat 8, close (3:55 to 4:00)

> Curtail. It recommends. A named human decides.

*Screen: repo URL and live URL.*

---

## Figures used, and where each comes from

| Spoken | Source in FACTS.md |
|---|---|
| $80,000 / $4,000 / 20x | section 4, computed from Water Code 1846(b) |
| 5 restraint cases, 2 refuse and 3 withhold | "Knowing when to decline" |
| minus 5 cfs defect | same section |
| 6 of 6, 5 excluded | backtest table |
| 14 of 87 refused placement | rights record |
| 577 passages, 101 documents, 5 unsearchable | "What the corpus index covers" |
| 7 spans | `docs/DEPLOYMENT.md`, re-probed after the traversal |
| Gemma 3 local | normalizer section |

**Deliberately NOT said:** "above 75 cfs", which FACTS.md forbids in any artifact because it
comes from a report paraphrasing the event while the Addendum itself states 78.4 cfs. The
median 3-day response lag is also left out: it needs its "this is not administrative delay"
caveat to be honest, and a caveat that long does not survive being spoken quickly.
