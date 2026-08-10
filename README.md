# Curtail

**A governed multi-agent fleet for prior-appropriation water curtailment administration.**

Built for the [Google x Devpost All Things Agentic Hackathon](https://allthingsagentichackathon.devpost.com/), track: The Fortified Enterprise Fleet.

> **Status: in active development.** Submission closes Aug 31, 2026. This README grows with the build. Sections marked *(not built yet)* are honest placeholders, not claims. See [what is actually running](#what-is-actually-running).

---

## The problem

When a California river drops below its drought emergency minimum flow, someone has to work out exactly which water rights must stop diverting, in strict priority order set by four separate court decrees, then draft the curtailment order, serve it on the right parties by a legally specified method, track the statutory clocks it starts, and rescind it when the river recovers.

Today that is slow. In 2021 the State Water Board issued curtailment orders "months after they were warranted." A single Scott River order has accumulated **12 addenda**; the 2021 series carries roughly **51**.

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
| Agent Registry | Native. Verified provisioning on a non-organization account ([ADR 0001](docs/adr/0001-governance-platform.md)) |
| Agent Identity | Native. API confirmed, write path scheduled |
| Model Armor | Native. Template provisioned |
| Agent Runtime / Memory Bank | Vertex AI *(not built yet)* |
| Agent Observability | OpenTelemetry to Cloud Trace, Logging, Monitoring *(not built yet)* |
| Agent Gateway | **Substituted.** No first-party API is exposed to a non-organization account. Its role is covered by per-agent least-privilege service accounts, API Gateway, egress allowlisting, and Model Armor called inline. Reasoning in [ADR 0001](docs/adr/0001-governance-platform.md) |

An integration a judge cannot reach scores as absent, and scores worse than an honest statement of what is not running. So the substitution is disclosed here rather than implied away.

## Data

All hydrology, water rights, decrees, orders and priority data are **real and public**, drawn from the California State Water Resources Control Board and the USGS. Only notification contact details are synthetic, and they are visibly labeled as such in the interface.

Compliance gages: Scott River at [USGS 11519500](https://waterdata.usgs.gov/monitoring-location/USGS-11519500/) (Fort Jones) and Shasta River at [USGS 11517500](https://waterdata.usgs.gov/monitoring-location/USGS-11517500/) (near Yreka).

## What is actually running

*(This section will carry the live component status, the backtest metric and the eval scores as they are produced. It is empty because the build is at M0. Nothing is claimed here that is not yet true.)*

## Setup

*(not built yet, arrives with the first service)*

## Disclaimer

Curtail is a demonstration system. **It is not an official government system**, carries no government authority, and produces drafts for human review only. It uses no state seals or official branding.

## License

[Apache-2.0](LICENSE).

[^1]: Biro et al., "Opportunities and risks of artificial intelligence in patient portal messaging in primary care," *npj Digital Medicine* 8:222 (2025). DOI [10.1038/s41746-025-01586-2](https://doi.org/10.1038/s41746-025-01586-2).
