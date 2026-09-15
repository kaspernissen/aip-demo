# Northstar Internal Platform

The portal you are reading this in. It is Northstar's internal view of its own
platform — built on Backstage, which is an implementation detail.

## What you can do here

| Area | What it gives you |
|---|---|
| **Catalog** | Every component in the system, derived from `catalog-info.yaml` files committed in the repo |
| **Docs** | These pages — one TechDocs site per service, written beside the code they describe |
| **Kubernetes tab** | The live pods, deployments and services behind each component, read through a read-only ServiceAccount |
| **Catalog Graph** | The relations between components and the APIs they provide |
| **Autonomy contract** | The one thing this portal can *change* |

## The one write path

`/autonomy-contract` reads the `northstar-autonomy` `FeatureFlag` live, per
identity, and can flip `settlement-agent` between `assisted` and `autonomous`.

That write goes through a **separate, deliberately narrow ServiceAccount** —
`backstage-autonomy-writer`, holding `get`/`list`/`patch` on
`resourceNames: [northstar-autonomy]` and nothing else, no `delete`, no
`create` — kept independently reviewable from the read-only ServiceAccount the
Kubernetes plugin uses.

**Anyone who can reach this port can flip the autonomy contract and nothing
else.** That narrowness, not a login, is what keeps this safe.

Every successful flip emits a `northstar.autonomy_contract.change` span under
service `backstage`. That span is the durable half of the audit trail: the
object's own `managedFields` entry is overwritten by the very next write,
whichever tool makes it, so it is a point-in-time fact rather than a history.

## The architectural point

This portal does not decide anything. The Policy Decision Service is still the
only decision point; this card only ever changes the resource the PDS already
reads.

> The platform API is the resource. Backstage is just the view a human uses to
> read and change it.

## Known limitations — stated, not hidden

- **This portal is unauthenticated.** The guest login is a real
  `POST /api/auth/guest/refresh` round trip, and it asks nothing of the caller.
  The backend *does* check a credential — a bare `curl` gets HTTP 401 — but
  anyone can mint one unchallenged. Acceptable on a laptop cluster reached only
  through an explicit `kubectl port-forward`; not acceptable anywhere else.
- **There is no scaffolder.** The "Create" entry is stock Backstage with no
  template wired in, so the claim that a platform team could add a service
  through the same API is only half demonstrated here.
- **The catalog is static.** It reflects `catalog-info.yaml` files staged into
  this image at build time, not a discovered repository — there is no git
  remote for the discovery processors to point at. A new component appears
  after a rebuild, and `tests/unit/test_catalog_matches_the_tree.py` catches
  the drift before a demo does.
- **Search does not index these docs' routes.** The search backend covers the
  catalog and techdocs plugins only; a frontend route such as the autonomy card
  is not indexed, so reach it from the sidebar rather than by searching.
