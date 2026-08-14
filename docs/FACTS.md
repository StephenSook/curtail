# FACTS

**Generated file. Do not edit by hand.** Regenerate with
`uv run python scripts/generate_facts.py`. CI fails if this file drifts
from the code that produces it.

Every number in the demo video narration, the README, the landing page and
the Devpost description must come from this file and from nowhere else. A
figure sourced from a memory ledger or a research summary is exactly how a
published artifact ends up contradicting its own repository.


---

## 0. What is wired

Computed from the source, not described. Each node is inspected for whether it
returns its input unchanged.

| Fleet node | Acts on its input | Runs the function it is named for | Reached by the console |
|---|---|---|---|
| `sentinel` | yes | yes, `evaluate` | yes, the console runs the graph |
| `core` | yes | yes, `recommend` | yes, the console runs the graph |
| `scribe` | yes | yes, `draft_order` | yes, the console runs the graph |
| `herald` | yes | yes, `deliver_order` | yes, the console runs the graph |

4 of 4 nodes act on their input and 4 of 4 run the domain function they are named for.

**The console runs the graph.** `POST /api/fleet/{basin}` builds the ADK runner and drives one real traversal, so every node above is exercised by clicking rather than only by the test suite. The response names which node produced each part, read from ADK's own attribution.

**The rights table.** Read from the Board's own attachment to Order WR 2024-0006-DWR, Addendum 6, Updated Attachment, issued 2026-06-16, sha256 `e65b1e5dc5474d22...`.

- 87 application numbers seen
- 85 rows parsed, 1 imprecise, 0 ambiguous, 1 unparsed
- 71 placed on the priority ladder (Tier A: 65, Tier B: 6), 14 refused placement because the record
  states no priority precise enough to establish decree membership

**The Scott rights table.** Read from the Board's own attachment to Order WR 2024-0024-DWR, Addendum 12, Attachment A, issued 2026-06-09, sha256 `c8d0d764615c5052...`. 384 rights, reconciled against 384 application numbers found anywhere in the document. **The grouping is the Board's own column, not this project's inference** (group 1: 8, group 3: 96, group 4: 1, group 5: 4, group 6: 9, group 7: 8, group 8: 258). Placing these rights from attributes alone instead agrees with the Board on 8 of 384: inference lands them at group 1: 384, while the Board puts 258 in group 8. Group 1 is curtailed first and group 8 is curtailed nearly last, so the gap is not a rounding difference, it is the difference between a ranch irrigating and a ranch shutting off.

**Not wired in the DEPLOYED service, named so it cannot be implied away.**

- The session service is in memory and built PER REQUEST, so nothing a traversal
  records survives the response and no season state persists in production. A
  test injects a real `DatabaseSessionService` and proves the ledger round-trips
  across a restart, which is a different claim about a different deployment.
- No Cloud SQL, and the approval queue lives in the serving process.
- No Pub/Sub broker, and no delivery vendor: the transport is explicitly
  synthetic and every report says so.
- OpenTelemetry export to Cloud Trace is WIRED: the shipped source imports the Cloud Trace exporter, installs a tracer provider, and the HTTP entrypoint calls `configure_tracing` at import. ADK opens an `invoke_node` span per fleet node and an `invoke_workflow` span around the traversal, so the agent-hop trace is a property of the graph. It exports only where a project id is present, and the fleet response says so either way.
- Model Armor is CALLED as layer 2: the Scribe path screens untrusted order text before drafting, chunked to stay inside the documented prompt-injection window, and an unreachable or partial screen reports UNAVAILABLE rather than clean. `make chaos` screens the same injection alone and embedded in an order and reports both verdicts.
- 4 Curtail agents were registered in Agent Registry, as recorded by `scripts/probe_deployment.py` at 2026-08-14T05:18:00+00:00 against revision curtail-console-api-00028-7jz. **This is a snapshot, not a live reading.** Nothing re-probes on its own and CI never queries the network, so run `make deployed-check` to re-probe and fail on drift before quoting this anywhere that cannot be corrected.

## 1. The backtest

> Curtail reproduces the direction of 6 of 6 scored historical curtailment decisions (0 refused, 5 excluded before scoring).

The denominator is what was actually scored. Refusals and exclusions are
reported alongside rather than folded in.

| Case | Basin | Date | Reading | Minimum | Board | Engine | Outcome |
|---|---|---|---|---|---|---|---|
| `scott_2024/addenda/7` | scott | 2025-07-20 | 48.7 cfs | 50 cfs | restrict | restrict | **match** |
| `scott_2024/addenda/8` | scott | 2025-07-22 | 78.4 cfs | 50 cfs | relieve | relieve | **match** |
| `shasta_2024/addenda/2` | shasta | 2024-10-25 | 126 cfs | 105 cfs | relieve | relieve | **match** |
| `shasta_2024/addenda/3` | shasta | 2024-10-31 | 140 cfs | 105 cfs | relieve | relieve | **match** |
| `shasta_2024/addenda/5` | shasta | 2025-10-13 | 145 cfs | 105 cfs | relieve | relieve | **match** |
| `shasta_2024/addenda/6` | shasta | 2026-06-15 | 46.5 cfs | 50 cfs | restrict | restrict | **match** |

Excluded before scoring: 5.

- `scott_2024/addenda/6`: States a bound, not a reading: flows 'have been at or above the September flow requirement (60 cfs)'. Scoring a bound as a measurement would invent precision the Board did not publish.
- `scott_2024/addenda/9`: No sentence in the document states a point reading with its date. The constitution carries '145 versus the 150 May minimum', but that figure was not confirmed against this document, and a case sourced from a summary is not evidence.
- `scott_2024/addenda/12`: Same as Addendum 9. The trigger reading is not yet verified from the document itself.
- `scott_2024/addenda/3`: Scoped to one named diverter with a volumetric cap rather than to a basin-wide threshold decision. The engine's below-minimum test is not the question this document answers.
- `scott_2024/addenda/4`: States a range observed over a week, '44 to 46 cfs during the last week', not a point reading on a decision date. Also scoped to named diverters.

**What this does not claim.** The engine determines the priority grouping or
tier to which curtailment must extend to provide reasonable assurance of
meeting the drought emergency minimum flow at the compliance gage. It does
not derive the cutoff dates; those are decree-defined tiers. A divergence is
not automatically an engine error, because 23 CCR 875(b)(3) permits the
official to decline to issue, to narrow the grouping, or to suspend.

## 2. The corpus

| Measure | Count |
|---|---|
| Declared across the Board's index pages | 100 |
| Individually enumerated in the manifest | 98 |
| PDFs fetched and byte-verified | 98 |
| Read via text layer | 91 |
| Read from rendered pages | 4 |
| Refused, no text layer | 0 |
| **Scorable** | **95** |

Never state a figure of the form "N of 100". The denominator of any claim is
the SCORABLE count, because that is what has actually been read. The gap
between declared and scorable is reported here rather than folded away.

The 2021 series is now individually enumerated. The Board does publish
per-addendum links for it, on two index pages the main drought index does
not surface: `scott_addendums.html` (51 Scott) and `shasta_addendums.html`
(14 Shasta). An earlier premise that no such links existed was wrong, and
the Shasta count was corrected from 16 to 14 against that page.

**Measured:** 4 of the 25 documents in the 2024 series carry no text layer
at all, roughly 16 percent. `pdftotext` returns three or four bytes. That
is
what makes a vision model load-bearing on this project rather than
decorative: it is the only way to read those documents, and one of them is
the July 2025 fixture the entry is built around.

## 3. The July 2025 sequence

Both documents are scans and were read from their rendered pages.

| | Addendum 7 | Addendum 8 |
|---|---|---|
| When | 2025-07-20 21:30 | 2025-07-22 07:30 |
| Fort Jones reading | **48.7 cfs** | **78.4 cfs** |
| Action | Reinstate, all surface water and groundwater diverters | Suspend all curtailments |
| Signed | not captured | Erik Ekdahl, Chief Deputy Director |

The river did not rise. The Scott Valley and Shasta Valley Watermaster
District measured, USGS revised the rating curve upward, and the same water
read 48.7 cfs on Sunday night and 78.4 cfs on Tuesday morning. Addendum 8,
verbatim: "Several community members expressed concern regarding the
accuracy of the measurement, and USGS has revised its flow measurements
upward based on measurements taken by the Scott Valley and Shasta Valley
Watermaster District (Watermaster)."

At 48.7 cfs the engine recommends curtailment and raises a near-threshold
flag, because the reading sits within the 10 cfs band
AROUND the minimum. The band is symmetric in the code: a reading just
above the line is flagged too, since a decision to release is as worth
checking as a decision to curtail. An earlier version of this sentence
described it as one-sided, which the implementation never was.

**Do not use "above 75 cfs"** in any artifact. That figure comes from the
August 5 2025 Executive Director's Report paraphrasing the event. The
Addendum itself states 78.4 cfs.

## 4. Penalties, and the gap the regulation still prints

Computed from Water Code 1846(b) as amended by AB 460 (Stats. 2024, Ch. 342),
effective January 1, 2025.

- Statutory exposure, 8 days of violation: **$80,000**
- The same 8 days computed from 23 CCR 875.9(b), which still prints $500 per
  day: **$4,000**
- Understatement factor: **20x**

The published regulation is stale on its face while the statute has moved. A
system computing liability from the regulation text would be wrong by that
factor. The multiple is computed from the two figures, not asserted.

## 5. A second live drafting gap, found in the corpus

Scott Addendum 6, issued **November 13, 2024**, states: "Flows at the USGS
Fort Jones gage have been at or above the **September flow requirement (60
cfs)**". Under 23 CCR 875 the Scott September minimum is **33 cfs**; 60 cfs
is **November's** figure. Scott Addendum 3 states September as 33 cfs in the
same order series, so two addenda in one series disagree about September,
and the number matches the month the addendum was issued in rather than the
month it names.

## 6. Flow minimums, as encoded and as confirmed

Date-period bounded, not month keys. Scott changes mid-month on June 24;
Shasta on March 25 and September 16.

### Scott at `USGS-11519500`

| Period | cfs |
|---|---|
| 01-01 to 01-31 | 200 |
| 02-01 to 02-29 | 200 |
| 03-01 to 03-31 | 200 |
| 04-01 to 04-30 | 150 |
| 05-01 to 05-31 | 150 |
| 06-01 to 06-23 | 125 |
| 06-24 to 06-30 | 90 |
| 07-01 to 07-31 | 50 |
| 08-01 to 08-31 | 30 |
| 09-01 to 09-30 | 33 |
| 10-01 to 10-31 | 40 |
| 11-01 to 11-30 | 60 |
| 12-01 to 12-31 | 150 |

### Shasta at `USGS-11517500`

| Period | cfs |
|---|---|
| 01-01 to 01-31 | 125 |
| 02-01 to 02-29 | 125 |
| 03-01 to 03-24 | 125 |
| 03-25 to 03-31 | 105 |
| 04-01 to 04-30 | 70 |
| 05-01 to 05-31 | 50 |
| 06-01 to 06-30 | 50 |
| 07-01 to 07-31 | 50 |
| 08-01 to 08-31 | 50 |
| 09-01 to 09-15 | 50 |
| 09-16 to 09-30 | 75 |
| 10-01 to 10-31 | 105 |
| 11-01 to 11-30 | 125 |
| 12-01 to 12-31 | 125 |

Independently confirmed from the documents: Scott September 33 cfs
(Addendum 3), Scott October 40 cfs (Addendum 5), Scott July 50 and August 30
cfs (Addendum 7), Shasta October 105 cfs (Addendum 2), Shasta October 105 and
November 125 cfs (Addendum 3). Every month stated in a 2024-era addendum
matches the encoded 2025-readopted schedule.

## 7. Open, and stated as open

Every item is rendered, routed by status. Nothing is filtered out.

An adversarial review found the previous version published only items
whose text began with the literal string OPEN, which silently dropped
three genuinely unresolved items written without the prefix, plus one
stale item that should have been retired rather than hidden. In a
project whose thesis is that undisclosed gaps are the failure mode, a
section headed "stated as open" that omitted open items because of a
formatting convention was the worst defect in this file.

### Still open (9)

- OPEN (undated, carried from an earlier haul): The Shasta 2021 order-number year inconsistency (2021 versus 2022 for orders 0162 and 0167) is on the Board's own index page and must be resolved from the documents.
- OPEN (undated, carried from an earlier haul): The Scott 2021 surface-water order carries no order number on the index page.
- OPEN (undated, carried from an earlier haul): Scott 2021 addenda 1 through 51 have index-verified numbering but no per-addendum dates on the index page.
- OPEN 2026-08-10: 67 addenda in the 2021 series are declared by count (scott_2021: 51, shasta_2021: 16) but not individually enumerated, because the Board's index pages publish no per-addendum URLs. They cannot be fetched, read or scored until located. They are excluded from the metric denominator rather than counted as failures.
- OPEN 2026-08-10: 4 fetched documents have no text layer (Scott 2024 addenda 3, 4, 7 and 8). pdftotext returns three or four bytes. Scott Addendum 8 is the July 22 2025 suspension that followed Watermaster field measurements, which is the project's central fixture, so reading it is not optional. These require a vision model or transcription and stay document_read: false until something actually reads them.
- OPEN 2026-08-10: Addendum 8 is signed by Erik Ekdahl, CHIEF Deputy Director, a third signing role. The approval model currently distinguishes Deputy Director (875(b) curtailment determinations) and Executive Director (875(b)(2) health, safety and livestock). Chief Deputy Director is not yet modelled and the delegation basis for that signature is not yet verified.
- OPEN 2026-08-10: a third scope class exists that the extractor does not model. Scott Addenda 3, 4 and 5 scope curtailment relief to NAMED DIVERTERS, not to priority groupings and not basin-wide, and Addendum 5 assigns two different dispositions inside one document. The backtest's per-right disposition metric needs this class; priority_groups and affects_all cannot express it.
- OPEN 2026-08-10: no 2021-era SHASTA document states a monthly minimum flow. All 18 recite that 'the Regulation establishes minimum instream flows' without printing the table, so the 2021 Shasta schedule stays empty and every 2021 Shasta date refuses. The 2021 SCOTT table is populated with the seven months documents state, all of which match the readopted table.
- OPEN 2026-08-10: two documents resist classification for stated reasons. Scott 2021 Addendum 23 has a CORRUPTED HEADER text layer: the subject line decodes only under a +29 character shift ('6FRWW5LYHU' is 'Scott River'), and the body never restates the act self-referentially. Measured, the document is NOT wholly mojibake: 20.4 percent of its words are recognisable against 20.7 percent for a known-good sibling, so a garbage-text heuristic would not catch it. The decoded header reads 'Scott River Temporary Extension of Conditional Suspension of Curtailments'. Shasta 2021 order WR 2022-0161-DWR is a COVER LETTER describing an enclosed order in the third person ('The enclosed Order curtails certain surface water and groundwater diversions'), so its own action is transmittal, not curtailment.

### Closed, kept for provenance (9)

- RESOLVED 2026-08-10: superseded. This item read 'No document has been parsed yet'. 85 of 98 documents are now read and scorable. Retired rather than deleted so the record shows it was answered, not lost.
- RESOLVED 2026-08-10 by reading Addendum 6: the Shasta cutoff is November 25, 1912, stated twice in bold. The same read corrected a 39.3 cfs figure that appears nowhere in the document; the actual readings are 45.3 and 46.5 cfs.
- RESOLVED 2026-08-10: Scott Addenda 7 and 8 read from rendered pages, having no text layer. The July 2025 sequence is 48.7 cfs at 21:30 on July 20 (reinstate) and 78.4 cfs at 07:30 on July 22 (suspend), after USGS revised the rating curve on Watermaster measurements. Earlier drafts carried 'above 75 cfs' from the August 5 2025 Executive Director's Report paraphrase; the Addendum itself states 78.4. Addenda 3 and 4 remain unread scans.
- RESOLVED 2026-08-10: Scott Addenda 3 and 4 read from rendered pages. All 4 scans in the fetched corpus are now read. Both are limited conditional suspensions scoped to NAMED DIVERTERS rather than to priority groupings, with per-diverter volumetric caps, a basin floor of 33 cfs for September, and an expiry of 2024-09-30. Addendum 4 ties Scott Valley Irrigation District to Schedule D3 of the Scott River Adjudication.
- RESOLVED 2026-08-10: the 2021 addenda ARE individually published, on two index pages the main drought index does not surface: scott_addendums.html (51 Scott) and shasta_addendums.html (14 Shasta). All 65 are now enumerated with URLs where the filename carries a number. The earlier premise that no per-addendum URLs exist was wrong.
- CORRECTED 2026-08-10: Shasta 2021 addenda count 16 to 14, verified against the Board's own index page. Totals fall from 86 to 84 addenda and from 102 to 100 documents.
- PROGRESS 2026-08-10: the 2021 corpus is fetched. 94 PDFs on disk, 85 documents read and scorable, up from 24. The 8 second fetch pacing held with zero WAF blocks across 70 requests.
- RESOLVED 2026-08-10: the four records that carried a note instead of a URL are resolved. Their filenames carry no number (scott-curtailment-suspension, scott-more-suspensions, addendum-two-to-order-... spelling the number out, shasta-more-suspensions), which is exactly what the plan predicted would defeat a filename-based mapping. Each number was then CONFIRMED FROM THE DOCUMENT'S OWN TEXT rather than from its position on the index page, because page order is an inference and the title is evidence.
- RESOLVED 2026-08-10: both recorded conflicts are closed from the documents. (1) The Shasta order-number year is 2022, not 2021: WR 2022-0162-DWR is dated August 2 2022 and WR 2022-0167-DWR September 13 2022, each printing its own number, and both amend the 2021 parent order which is the likely source of the index page's 2021 reference. (2) The Scott September 9 2021 surface-water order is genuinely UNNUMBERED as published, carrying no WR identifier at all, so this was never an index-page omission to fix.

_All 18 items accounted for: 9 open, 9 closed._

_Provenance: run `git log -- docs/FACTS.md` for when this was last regenerated, and `git log -- data/ core/src/` for the inputs it was computed from._
