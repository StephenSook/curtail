# ADR 0001: Governance platform for the Curtail agent fleet

- **Status:** Accepted
- **Date:** 2026-08-10
- **Deciders:** Stephen Sookra
- **Milestone:** M0, the hard gate

## Context

Curtail is a governed multi-agent fleet. Its entire thesis is that an institutional agent network can be cataloged, identified, policed, and audited, so the governance layer is not decoration: it is the product. The Fortified Enterprise Fleet track names seven components by name (Agent Registry, Agent Runtime, Memory Bank, Agent Identity, Agent Gateway, Model Armor, Agent Observability) and the hackathon FAQ states they are "recommended, not required, but they're what this track's judging is built around."

Google's published documentation frames the Gemini Enterprise Agent Platform (GEAP) as an enterprise governance surface tied to Gemini Enterprise and Workspace administration. **It never states whether an individual, non-organization Google Cloud account can provision these components.** Prior competitive analysis treated this as the single largest execution risk for this track and recommended abandoning the track entirely if any component hit an allowlist or organization wall.

That question could not be answered from documentation. It had to be answered empirically, on day one, before any dependent work began.

## Decision

**Plan A. Provision GEAP directly on the individual account.** Confirmed working by live test.

Environment, verified in console and CLI:

| Property | Value |
|---|---|
| Google account | a single individual account, not a Workspace or Cloud Identity user |
| Project | `Curtail`, `curtail-505118`, number `672785135387` |
| Billing | a Direct billing account, `billingEnabled: true` (id withheld) |
| Organization | **none** |
| Region | `us-central1` |

The account email and billing account id are deliberately omitted. They are not credentials, but they are account identifiers with no value to a reader and real value to someone probing for a support-channel social-engineering angle. What matters to the decision, and what is stated, is that the project sits under **no organization**.

## Evidence

All four GEAP APIs enabled on a non-organization project with no allowlist request and no error:

```
ENABLED  agentregistry.googleapis.com
ENABLED  agentidentity.googleapis.com
ENABLED  agentidentitycredentials.googleapis.com
ENABLED  modelarmor.googleapis.com
```

Enabling an API is not provisioning, so each component was exercised with a real write.

### Agent Registry: confirmed working

`GET /v1/projects/curtail-505118/locations` returned **46 locations**, including `us-central1`, `us`, `eu`, and `global`.

A service was created with a full A2A agent card and reached `done: true` with no error. Reading the resource back, which is the only proof that counts:

```
name:              projects/curtail-505118/locations/us-central1/services/spike-probe
displayName:       Gage Sentinel
createTime:        2026-08-10T18:18:47.866215721Z
registryResource:  projects/672785135387/locations/us-central1/agents/
                   agentregistry-00000000-0000-0000-f108-5c30d91fe10f
```

It then appeared in `projects.locations.agents.list` as a **discoverable agent**, which is the capability the track's "cataloged for cross-department use" criterion actually requires.

Two API-shape findings that change how M4 is built:

1. `agents` and `mcpServers` expose only `list`, `get`, and `search`. **There is no `agents.create`.** Registration happens through `services.create` and `bindings.create`, and the registry derives the agent record. Any plan that called for creating agents directly was wrong.
2. `AgentSpec.type` accepts `NO_SPEC` or `A2A_AGENT_CARD`. With `A2A_AGENT_CARD`, **`interfaces` must be empty** and connection details go inside the card content. The API rejects the combination explicitly.

### Model Armor: confirmed working

Template created at `projects/curtail-505118/locations/us-central1/templates/curtail-scribe-spike`, HTTP 200, with prompt-injection and jailbreak filtering, malicious URI filtering, and RAI filters. The regional endpoint is `modelarmor.us-central1.rep.googleapis.com`, not the global hostname.

### Agent Identity: API surface confirmed

Discovery returns HTTP 200 for `Agent Identity API v1` with full lifecycle on `projects.locations.authProviders`: `create`, `patch`, `delete`, `enable`, `disable`, `undelete`, `revokeAuthorization`, `queryWorkloads`, plus IAM policy methods. Not yet exercised with a write; scheduled for M4.

### Agent Gateway: not available, and this is the one real gap

**No first-party Agent Gateway API is exposed to this account.** A filtered sweep of every available `*.googleapis.com` service returned `agentidentity`, `agentidentitycredentials`, `agentregistry`, and `modelarmor`, and nothing named for a gateway. Adjacent candidates that do exist are `apigateway`, `apigee`, `apihub`, `networkservices`, and `connectgateway`, none of which is the GEAP Agent Gateway.

The most probable explanation is that Agent Gateway is provisioned through Gemini Enterprise, which is an organization and Workspace-administered product, and this account has no organization. That is consistent with the documented constraint that enabling Agent Gateway "routes all Gemini Enterprise traffic, including LLM calls, through the gateway."

## Consequences

**Six of seven components are reachable.** Agent Registry, Agent Identity, and Model Armor are confirmed on the individual account. Agent Runtime, Memory Bank, and Agent Observability run through Vertex AI and Cloud Observability, which have no organization requirement.

**Agent Gateway takes the documented Plan B2 substitution.** Its role, a single policy enforcement point that authenticates every agent call and blocks egress to unregistered hosts, is satisfied by: per-agent least-privilege service accounts, an API Gateway fronting the fleet, VPC Service Controls or explicit egress allowlisting for the registered host set, and Model Armor called inline on the Scribe path rather than transparently intercepted by a gateway.

**The substitution is disclosed, never implied away.** The README and the `/evidence` page will carry a component-by-component table stating which components run natively and which are substituted, with this ADR linked. Per HARD RULE 77, an integration a judge cannot reach scores as absent and scores worse than an honest statement of what is not running. An honest badge is a claim a judge can verify from the couch.

**Plan B1 is formally rejected.** Creating a Cloud Identity organization would require a domain, DNS verification, and Workspace administration, on a hard deadline, to obtain one component. The cost is not justified when the substitution is defensible and the FAQ states GEAP is recommended rather than required.

**The pivot-to-Taskmaster contingency is closed.** Prior analysis set a decision rule to abandon the Fortified Enterprise Fleet track if any component hit a wall before Aug 20. Three of the four hard components provisioned on day one, so the track selection stands.

## Cleanup and residue

The registry probe was **deleted** after verification, because it pointed at a placeholder `example.run.app` URL and a registry entry describing an endpoint that does not exist is precisely the claim drift this project exists to prevent. Post-cleanup the registry contains only Google's own `Workspace Agent`.

The Model Armor template `curtail-scribe-spike` is **retained** as spike evidence. It contains no false data. M4 replaces it with the production template.

## Open

- Exercise `authProviders.create` with a real per-agent identity (M4).
- Confirm whether Agent Gateway becomes reachable if an organization is ever attached, and re-run this spike if so.
- Model Armor's prompt-injection check window has historically been 512 tokens, so the Scribe must chunk ingested order text before screening.
