/* The portal's landing page.
 *
 * A tutorial about Backstage itself is a poor first screen for an internal
 * platform -- the demo opens Backstage at its punchline, so the landing page
 * needs to be about Northstar, not about the tool.
 *
 * Three cards rather than one, because they answer different questions and get
 * read at different moments: "what is this system" (the story, once), "what
 * does the demo prove" (the arc, once) and "where do I click" (the surfaces,
 * every time).
 */
import { createFrontendModule } from '@backstage/frontend-plugin-api';
import { HomePageWidgetBlueprint } from '@backstage/plugin-home-react/alpha';
import { MarkdownContent } from '@backstage/core-components';
import { Mermaid } from '../mermaid';

const overviewIntro = `
**Motor-claims settlement, where agents do the work and the platform decides
what they are allowed to do.**

A claimant files a vehicle-damage claim. Two agents read it, assess it and
propose a settlement. Whether that settlement is *paid* is not the agents'
decision to make — and the machinery that makes that sentence true, rather
than aspirational, is what this system is for.
`;

/* The claim path of docs/index.md, drawn by the same renderer -- but reached
 * differently and deliberately coarser.
 *
 * Reached differently: a TechDocs page carries its diagram as a raw
 * `<pre class="mermaid">` block, which the addon finds in the shadow root.
 * This card renders through `MarkdownContent`, which handles no blocks of any
 * kind, so it takes the `<Mermaid>` component directly.
 *
 * Coarser on purpose: drawing all seven services the documentation does
 * would, in a 700px card, have `useMaxWidth` scale a seven-column chart down
 * until the labels are a few pixels tall -- legible in a screenshot at full
 * resolution, not from the third row of a room, which is the only place this
 * card is ever read from. The two pairs that are always adjacent are
 * collapsed into one node each, giving four columns and labels that survive
 * a projector. The seven-service version is one click away, and the card's
 * own last line is the link to it.
 */
const claimPath = `
flowchart LR
    browser(["browser"]) -->|A2A| agents["claims-agent → settlement-agent"]
    agents -->|"MCP + JWT-SVID"| gw{{"agentgateway"}}
    gw <-->|"gRPC — permit or deny"| pds["policy-decision-service"]
    pds -->|reads| flag[("northstar-autonomy")]
    gw -->|"permit only"| pay["payments-mcp → payments-api"]
`;

const overviewRest = `
**Every \`tools/call\` is referred to the policy decision service before it is
forwarded**, and the gateway is \`failureMode: failClosed\` — a policy service
that is down refuses payments rather than waving them through.

Four things stop a payment, independently, none of them relying on an agent
choosing to behave: the **policy decision service** (the only decision point),
the **autonomy contract** (an OpenFeature flag that ships refusing),
**workload identity** (an attested SPIFFE ID, not a self-declared name), and
the **network** (the agent plane cannot reach the policy service at all).

A refusal is the platform working. Both UIs render one in ochre, never red,
and the \`northstar.policy.decision\` span is deliberately not an error.

→ **[Read the solution documentation](/docs/default/system/northstar)** —
architecture, the demo runbook, and the telemetry contract.
`;

const surfaces = `
| Surface | What it shows |
|---|---|
| [Claim form](http://localhost:8080) | filing a claim, and the determination |
| [Review console](http://localhost:8081) | settlements awaiting a human, and the ledger |
| [Autonomy contract](/autonomy-contract) | the live OpenFeature flag, and the one write that flips it |
| [Traces — Jaeger](http://localhost:16686) | one claim, one trace, ~150 spans across 7 services |
| [Metrics — Prometheus](http://localhost:9090) | request rates, LLM tokens, model cost |

**In the portal**

- [Components](/catalog) — all nine, each with its own docs and pre-filtered
  links into Jaeger and Prometheus
- [Catalog Graph](/catalog-graph?rootEntityRefs%5B%5D=system%3Adefault%2Fnorthstar) —
  the call chain as a picture, including the gateway and the flag
- [Docs](/docs) — every component's documentation, and the system's
- [OpenFeature](/autonomy-contract) — read and flip the autonomy contract

Nothing here is authenticated. On a laptop cluster reached only through an
explicit port-forward that is a deliberate, recorded choice; anywhere else it
would not be.
`;

/* The arc, from docs/demo-runbook.md §1. It is on the landing page rather than
 * only in the runbook because the runbook is the operator's document -- read
 * once, before the demo, by the person driving -- and this is the audience's.
 * Someone handed this portal cold needs to know that the six ideas are
 * sequential and that each one is a deployed thing, not a slide.
 *
 * The stage names and their order are duplicated from the runbook, which is a
 * cost: two places to change if a stage is renamed. Accepted because the
 * alternative is a build-time import from a markdown table, and the arc is
 * stable.
 */
const stages = `
The demo tells its story one idea at a time. All of it runs in this one
cluster, and each stage below is the part that idea added.

| | Stage | What it proves | Added |
|---|---|---|---|
| 1 | Connected | they can talk to each other, and one trace says so | the agents, the MCP servers, Jaeger |
| 2 | Governed | the platform decides, not the agent — and identity is an *input* to that decision | Linkerd, SPIFFE/SPIRE, agentgateway, the policy decision service |
| 3 | Supervised | a refusal is not the end; a human can overturn one | the reviewer console, which releases the money itself |
| 4 | Observable | why €5,800 was paid, from telemetry alone and no new app code | Prometheus, Weaver, AgentEvals |
| 5 | Progressive autonomy | one live value changes what the platform permits — no redeploy, no restart | Open Feature Operator, the \`FeatureFlag\` |
| 6 | Golden path | the resource **is** the platform API; this portal is a view onto it | Backstage |

> The platform API is the resource. Backstage is just the view a human uses to
> read and change it.

→ **[The demo runbook](/docs/default/system/northstar/demo-runbook/)** — every
beat, in order, with what to say and what will break.
`;

const overviewWidget = HomePageWidgetBlueprint.make({
  name: 'northstar-overview',
  params: {
    name: 'NorthstarOverview',
    title: 'Northstar — what this system is',
    description: 'The claim path, and the four things that stop a payment',
    components: async () => ({
      Content: () => (
        <>
          <MarkdownContent content={overviewIntro} />
          <Mermaid chart={claimPath} />
          <MarkdownContent content={overviewRest} />
        </>
      ),
    }),
  },
});

const surfacesWidget = HomePageWidgetBlueprint.make({
  name: 'northstar-surfaces',
  params: {
    name: 'NorthstarSurfaces',
    title: 'Everything you can look at',
    description: 'Every surface this demo exposes, with a link to each',
    components: async () => ({
      Content: () => <MarkdownContent content={surfaces} />,
    }),
  },
});

const stagesWidget = HomePageWidgetBlueprint.make({
  name: 'northstar-stages',
  params: {
    name: 'NorthstarStages',
    title: 'The arc — six stages, one claim, one trace',
    description: 'What each stage of the demo adds, and what it proves',
    components: async () => ({
      Content: () => <MarkdownContent content={stages} />,
    }),
  },
});

export const homeModule = createFrontendModule({
  pluginId: 'home',
  extensions: [overviewWidget, surfacesWidget, stagesWidget],
});
