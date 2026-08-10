# FACTS

**Generated file. Do not edit by hand.** Regenerate with
`uv run python scripts/generate_facts.py`. CI fails if this file drifts
from the code that produces it.

Every number in the demo video narration, the README, the landing page and
the Devpost description must come from this file and from nowhere else. A
figure sourced from a memory ledger or a research summary is exactly how a
published artifact ends up contradicting its own repository.


---

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
| `shasta_2024/addenda/6` | shasta | 2026-06-15 | 45.3 cfs | 50 cfs | restrict | restrict | **match** |

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
| PDFs fetched and byte-verified | 94 |
| Read via text layer | 81 |
| Read from rendered pages | 4 |
| Refused, no text layer | 0 |
| **Scorable** | **85** |

Never state a figure of the form "N of 100". The denominator of any claim is
the SCORABLE count, because that is what has actually been read. The gap
between declared and scorable is reported here rather than folded away.

The 2021 series is now individually enumerated. The Board does publish
per-addendum links for it, on two index pages the main drought index does
not surface: `scott_addendums.html` (51 Scott) and `shasta_addendums.html`
(14 Shasta). An earlier premise that no such links existed was wrong, and
the Shasta count was corrected from 16 to 14 against that page.

**Measured:** 4 of the 25 documents in the 2024 series carry no text layer
all, roughly 16 percent. `pdftotext` returns three or four bytes. That is
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
flag, because the reading sits inside the 10 cfs band
below the minimum. Field verification is what changed the answer.

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

- OPEN 2026-08-10: 67 addenda in the 2021 series are declared by count (scott_2021: 51, shasta_2021: 16) but not individually enumerated, because the Board's index pages publish no per-addendum URLs. They cannot be fetched, read or scored until located. They are excluded from the metric denominator rather than counted as failures.
- OPEN 2026-08-10: 4 fetched documents have no text layer (Scott 2024 addenda 3, 4, 7 and 8). pdftotext returns three or four bytes. Scott Addendum 8 is the July 22 2025 suspension that followed Watermaster field measurements, which is the project's central fixture, so reading it is not optional. These require a vision model or transcription and stay document_read: false until something actually reads them.
- OPEN 2026-08-10: Addendum 8 is signed by Erik Ekdahl, CHIEF Deputy Director, a third signing role. The approval model currently distinguishes Deputy Director (875(b) curtailment determinations) and Executive Director (875(b)(2) health, safety and livestock). Chief Deputy Director is not yet modelled and the delegation basis for that signature is not yet verified.
- OPEN 2026-08-10: a third scope class exists that the extractor does not model. Scott Addenda 3, 4 and 5 scope curtailment relief to NAMED DIVERTERS, not to priority groupings and not basin-wide, and Addendum 5 assigns two different dispositions inside one document. The backtest's per-right disposition metric needs this class; priority_groups and affects_all cannot express it.
- OPEN 2026-08-10: no 2021-era SHASTA document states a monthly minimum flow. All 18 recite that 'the Regulation establishes minimum instream flows' without printing the table, so the 2021 Shasta schedule stays empty and every 2021 Shasta date refuses. The 2021 SCOTT table is populated with the seven months documents state, all of which match the readopted table.

_Provenance: run `git log -- docs/FACTS.md` for when this was last regenerated, and `git log -- data/ core/src/` for the inputs it was computed from._
