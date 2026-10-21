# Northstar M12 — Linkerd for reach — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cilium leaves the cluster. Ingress to the money path is authorized by Linkerd, by workload identity, with counted 403s; egress from the agent pods is denied by core NetworkPolicy enforced by kindnet, with Anthropic's published range the only way out for settlement-agent. Every reach guarantee M11 asserted is asserted again, against the new enforcement points.

**Architecture:** kind's default CNI (kindnet, NetworkPolicy on) replaces Cilium at stage 2. Linkerd `edge-26.10.1` (Helm `linkerd-crds` + `linkerd-control-plane`, Gateway API CRDs installed first, identity certificates made with openssl) meshes `northstar-agents`, `northstar-tools`, `northstar-systems`, plus `agentgateway` and `policy-decision-service`. `deploy/platform/reach/linkerd-policies.yaml` holds Servers, HTTPRoutes, AuthorizationPolicies and MeshTLS/Network authentications; `deploy/platform/reach/network-policies.yaml` holds the egress allowlists.

**Tech Stack:** kind v0.30.0 (node v1.34.0, kindnet with kube-network-policies), Linkerd edge-26.10.1 (proxy v2.370.0, native sidecar), Gateway API v1.5.1 standard channel (or the newest version edge-26.10.1 supports, verified in Task 1), Helm 3.16, openssl (ECDSA P-256).

**Spec:** `docs/superpowers/specs/2026-10-21-northstar-m12-linkerd-design.md`

## Global Constraints

- Never sample, never drop a span. **Proxy tracing stays off** (spec §1): the trace shape must not change. If a Linkerd default turns tracing on, turn it off explicitly.
- Pin exact versions, verified upstream (memory: pin-latest-released-versions): `edge-26.10.1` and its exact Helm chart version string (read it off `helm search repo linkerd-edge --versions`; do not guess `2026.10.1`).
- The money path is unchanged: agentgateway jwtAuth → ExtMCP → PDS failClosed, `human-approved.execute`, the €50,000 CEL ceiling, the ledger check on resume.
- Identities are `<serviceaccount>.<namespace>.serviceaccount.identity.linkerd.cluster.local`. A policy that names `default` as a caller is a bug: two workloads share it.
- Commit with explicit paths. No `Co-Authored-By` or any generated-by footer.
- Any change to a gate path (`agents apps contracts deploy platform scripts tests systems tools`) demotes `docs/telemetry-contract.md`'s gate row to `PREDICTED, NOT MEASURED` (enforced by `tests/unit/test_telemetry_contract_counts.py`). Task 1's commit does this. Every task that adds or removes tests updates the collected/gated counts in that file and the per-service counts it publishes (the counts test tells you which).
- **Subagents never run `kind delete`, `teardown.sh`, or touch `kind-registry`.** The one cluster recreation this milestone needs (the CNI is a create-time choice) is done by the orchestrator, after Task 1's code is committed.
- Never edit scripts while `setup-kind.sh` runs. Never commit during a gate run. A gate needs the cluster hands-off.
- A skip is invisible in a green gate. A reach test may skip only below stage 2; on a stage >= 2 cluster without Linkerd or without the policy objects it must **fail**.

## Rulings made while planning (2026-10-21)

1. **Ingress parity, not more.** Exactly the ports Cilium protected get a Server: payments-mcp:8000, PDS:9100, settlement-agent:9002, payments-api:8080 (per route), customer-ui:8080 (the `/a2a/push` route only). agentgateway, claims-agent and reviewer-ui keep no ingress restriction, as under Cilium.
2. **L7 rules move from the caller's egress to the callee's ingress.** NetworkPolicy has no L7. payments-api's routes are authorized per caller identity at payments-api.
3. **Dedicated service accounts** for claims-agent, customer-ui (northstar-agents), payments-mcp (northstar-tools) and payments-api (northstar-systems). Today they run as `default`. Before changing them, grep every SPIFFE ID / `sa/default` / `serviceaccount` reference (SPIRE `ClusterSPIFFEID` templates, agentgateway `jwtAuth`/CEL, PDS rules, flagd targeting, Backstage `TARGETED_IDENTITIES`, tests): a workload whose SPIFFE ID is used anywhere must keep working, with every reference updated in the same commit.
4. **Probes:** a Server with HTTPRoutes no longer auto-authorizes kubelet probes. Each such Server gets an explicit probe route authorized for all networks (`NetworkAuthentication` `0.0.0.0/0` and `::/0`). tcpSocket probes are unaffected (the proxy accepts the connection).
5. **Egress is per pod** (`podSelector: app=...`, `policyTypes: [Egress]`), on the four `northstar-agents` workloads, exactly as Cilium had it. Each allows DNS (kube-dns 53 UDP/TCP), the Linkerd control plane (namespace `linkerd`, the identity/destination/policy ports the chart exposes; read them off the installed Services), the collector (4318), and its own peers on their service ports. settlement-agent alone gets `ipBlock` 160.79.104.0/23 and 2607:6bc0::/48 on TCP 443.
6. **Keystone** protects `networking.k8s.io/networkpolicies`, every `policy.linkerd.io` resource, `gateway.networking.k8s.io/httproutes`, and `namespaces` (the mesh annotation lives there) in place of the two `cilium.io` kinds.

## Measurements (Task 1 Step 8, 2026-10-21, on a throwaway kind v0.30.0 cluster `m12-spike`, kindnet `v20250512-df8de77b`, Linkerd edge-26.10.1 installed by Task 1's exact commands)

The demo cluster could not be recreated tonight (the orchestrator's `kind delete` was refused by the session's permission classifier), so the measurements ran on a second cluster with throwaway workloads: a meshed `web` (python http.server, readiness `GET /`), meshed clients `client` and `allowed`, an unmeshed `bare` pod labelled like `client`. These results override any assumption above.

- **M1 (egress, NetworkPolicy):** kindnet enforces egress `ipBlock`. From the **unmeshed** pod: `160.79.104.0/23:443` connects, `example.com:443` and `1.1.1.1:443` time out (kernel drop), DNS works with 53 allowed. From a **meshed** pod a TCP connect to *any* address "succeeds", because the pod's own outbound proxy accepts it locally; the real outcome shows only above TCP: a TLS handshake + HTTP request to `api.anthropic.com` completes, to `example.com` it is **reset** (`ConnectionResetError`). **Egress oracle from a meshed pod = TLS handshake/HTTP request fails (reset or timeout); never a TCP connect.** From an unmeshed pod a connect timeout is still the oracle.
- **M2 (meshed → meshed under an egress policy):** allowing only the destination's service port fails with HTTP **504** (`l5d-proxy-error: ... service in fail-fast`). Adding **TCP 4143** (the destination proxy's inbound port) makes it work. **Every egress rule to a meshed peer must allow the service port and 4143.** The Linkerd control plane needs namespace `linkerd` on TCP **8080** (identity), **8086** (destination), **8090** (policy).
- **M3 (port-forward):** `kubectl port-forward` (pod or service) to a port behind a denying Server **bypasses the proxy** (the app's own 501 came back, where the proxy answers 404 for an unmatched route). The harness's and `scripts/demo`'s port-forwards keep working; **no reach test may probe through a port-forward.**
- **M4 (the refusal):** a matched route (or a Server with no routes) and the wrong identity: **HTTP 403, empty body, no `l5d-proxy-error` header**. A request that matches **no route** on a Server that has routes: **HTTP 404 from the proxy**, empty body. An unmeshed caller gets the same 403; its TCP connect still succeeds. Counter on the destination proxy (`:4191/metrics`): `inbound_http_authz_deny_total{srv_kind="server",srv_name=...,route_kind="HTTPRoute",route_name=...,client_id="<sa>.<ns>.serviceaccount.identity.linkerd.cluster.local"}` (for unmeshed callers expect `tls="no_identity"`). gRPC (PDS) not measured here; Task 3 measures it on the demo cluster.
- **M5 (probes):** on a Server **without** routes, Linkerd's default `probe` route authorizes `GET <probe path>` for **every caller** (our `GET /` request from a denied identity got 200, `route_name="probe"`) — so a probe path must never be an API path. On a Server **with** routes, the kubelet probe is refused and the pod goes unready (confirmed); an `HTTPRoute` for the probe path authorized by a `NetworkAuthentication` of `0.0.0.0/0` and `::/0` makes it Ready again (confirmed).

- **M6 (after the Task 2 review, same cluster):** `tcpSocket` readiness **and** liveness probes pass behind a Server with `accessPolicy: deny` and an explicit `proxyProtocol` (`gRPC` on 9100 and `HTTP/1` on 8000, no authorization at all): Ready within seconds, zero restarts over a minute of 5 s liveness checks. So the PDS and payments-mcp do not fail their own probes. Not measured, owed to the first live run: whether agentgateway v1.6.0 speaks HTTP/1.1 (not h2c) to payments-mcp, which `proxyProtocol: HTTP/1` assumes; and real gRPC refusals at the PDS (`PERMISSION_DENIED`) (the gRPC refusal was measured later, see M7).

- **M7 (final review, measured in the final review on kind-m12-spike):** an egress NetworkPolicy that allows TCP 4143 to a meshed peer lets a **meshed** client reach **any non-loopback port** on that peer pod: the client proxy tunnels through 4143 and the peer's inbound proxy forwards to the requested port. A port with nothing listening answers **502**, empty body, no `l5d-proxy-error` header (the request reached the peer's proxy; the app had nothing to answer). The peer's admin port 4191 is not reachable (504). So **for meshed peers NetworkPolicy egress is per pod, not per port**; the ingress Servers still restrict per port and per identity. For an **unmeshed** source the port rules bind as written (a connect to a port outside the rule times out). gRPC refusal at the PDS, run with the harness's `GRPC_PROBE_SRC`: `PERMISSION_DENIED`, counter `inbound_http_authz_deny_total` with `route_name="default"`, `tls="true"` and `client_id` present; from an unmeshed caller `tls="no_identity"`.

## Review Focus

1. **A path that was denied under Cilium and is allowed now** (or the reverse). The reach matrix test in Task 2 encodes the whole intended table; review it against `deploy/platform/cilium-policies.yaml` at `main`.
2. **A Server whose routes forgot the probe**: the pod never becomes Ready, or, worse, the PDS fails closed and refuses every payment for the wrong reason.
3. **The `default` service account named as a caller anywhere.**
4. **A test that would pass with Linkerd uninstalled** (a skip, or an assertion on TCP connect).
5. **Egress that works only because the proxy happened to be bypassed**, or fails only because the proxy could not reach the control plane.

---

### Task 1: kindnet + Linkerd at stage 2; Cilium leaves; measure before building on it

**Files:**
- Modify: `scripts/setup-kind.sh` (pins, usage text, the existing-cluster check, Step 2b, Step 6, the eval-scorer comment)
- Modify: `deploy/kind/stage2.yaml`, `deploy/kind/stage3.yaml` (drop `disableDefaultCNI`; keep port mappings)
- Delete: `deploy/platform/cilium-values.yaml`
- Create: `deploy/platform/linkerd/values.yaml` (control-plane values), `scripts/linkerd-certs.sh` (or a function in setup-kind.sh; the certs go to a git-ignored dir, e.g. `.demo/linkerd-certs/`)
- Modify: the Namespace manifests for `northstar-agents`, `northstar-tools`, `northstar-systems` (find where they are created) — `linkerd.io/inject: enabled`; `deploy/platform/agentgateway/deployment.yaml` and `deploy/k8s/policy-decision-service.yaml` pod templates — `linkerd.io/inject: enabled`
- Modify: `.gitignore` if needed
- Replace: `tests/unit/test_stage2_cilium_config.py` → `tests/unit/test_stage2_linkerd_config.py`; move `_effective_script()` to a shared helper module (e.g. `tests/unit/_setup_script.py`) and update its importers (`test_stage2_pds_config.py`, `test_stage2_network_policy.py`); keep the CURRENT_STAGE stale-stage guard and the unimplemented-stage tests
- Modify: `tests/unit/test_pins.py`, `tests/unit/test_stage2_spire_config.py:1273` (reads the deleted values file), `docs/telemetry-contract.md` (gate row demoted; counts)

- [ ] **Step 1: Pins.** `helm repo add linkerd-edge https://helm.linkerd.io/edge && helm search repo linkerd-edge --versions | head`. Record the chart version whose app version is `edge-26.10.1`. Read `helm show values linkerd-edge/linkerd-crds --version <v>` for `installGatewayAPI` (expected `false`) and `helm show values linkerd-edge/linkerd-control-plane --version <v>` for the tracing, nativeSidecar and proxyInit keys. Confirm the Gateway API version range edge-26.10.1 supports and pick the newest standard-channel release in it. Put the pins at the top of setup-kind.sh like the Cilium ones were.
- [ ] **Step 2: Certificates with openssl.** ECDSA P-256 trust anchor (`root.linkerd.cluster.local`, CA, 10 years) and issuer (`identity.linkerd.cluster.local`, intermediate CA, `pathlen:0`, 1 year), signed by the anchor. Idempotent: reuse existing files. Unit-test the script's openssl invocation shape (curve, CA constraints) without running it, or run it into a tmp dir and inspect with `openssl x509 -text`.
- [ ] **Step 3: Install order in setup-kind.sh (stage >= 2), right after cluster creation, before any workload:** Gateway API CRDs (`kubectl apply --server-side -f <pinned URL>`), `linkerd-crds`, `linkerd-control-plane` (`--set-file identityTrustAnchorsPEM=... identity.issuer.tls.crtPEM=... keyPEM=...`, `-f deploy/platform/linkerd/values.yaml`, `--wait`), then wait for every Deployment in `linkerd` to be Available. Print "Linkerd <version> is ready".
- [ ] **Step 4: The existing-cluster check, reversed.** Stage >= 2 on an existing cluster that still runs Cilium (a `kube-system/cilium` DaemonSet) must exit 1 with the delete-and-recreate hint (it was created with `disableDefaultCNI`). A stage-1 cluster upgraded to stage 2 is now fine (same CNI). Port the fake-bin harness tests to the new condition.
- [ ] **Step 5: Mesh annotations** as listed above. Nothing in `observability`, `spire*`, `kyverno`, `flagd`/OpenFeature, `cert-manager`, `kube-system` or `linkerd` is meshed.
- [ ] **Step 6: Unit tests and counts.** `test_stage2_linkerd_config.py` asserts: no `disableDefaultCNI` anywhere; the pins; the install order (Linkerd ready before any workload apply); the namespace/pod annotations; tracing off; no `cilium` in setup-kind.sh except the existing-cluster refusal. Run `tests/unit` fully; update the telemetry-contract counts and demote the gate row.
- [ ] **Step 7: Commit.** `git add` explicit paths. Report the chart version, the Gateway API version, and every value you set and why.
- [ ] **Step 8 (orchestrator, not the subagent): recreate and measure.** `kind delete cluster --name northstar`, then `./scripts/setup-kind.sh --stage 7`. All stage-7 workloads must reach Ready with proxies injected where intended (`kubectl get pods -A -o jsonpath` container names include `linkerd-proxy`). Then measure, in a scratch namespace or with throwaway pods, and record each result in the ledger before Task 2 starts:
  - **M1** kindnet enforces an egress NetworkPolicy with `ipBlock` (an allowed CIDR connects, another IP times out) and keeps DNS working when port 53 is allowed.
  - **M2** a meshed pod under an egress policy that allows only the destination's service port (not 4143) still reaches a meshed destination; and what it needs to reach the Linkerd control plane.
  - **M3** `kubectl port-forward` to a meshed pod's port that has a denying Server: allowed (loopback bypass) or denied. The test harness and `scripts/demo` depend on the answer.
  - **M4** the exact denial: HTTP status and headers (is `l5d-proxy-error` present?), gRPC status, and the `inbound_http_authz_deny_total` labels on `:4191/metrics`.
  - **M5** a kubelet `httpGet` probe on a Server with routes and no probe authorization is denied (so ruling 4 is needed).
  If M1 or M2 fails, STOP and report: the design rests on them.

### Task 2: the reach policy objects, the keystone retargeted

**Files:**
- Delete: `deploy/platform/cilium-policies.yaml`
- Create: `deploy/platform/reach/linkerd-policies.yaml`, `deploy/platform/reach/network-policies.yaml` (one document per rule, each with a comment saying what it enforces and why, in the style of the Cilium file)
- Create: ServiceAccounts for claims-agent, customer-ui, payments-mcp, payments-api (ruling 3) in their manifests; `serviceAccountName` on each Deployment
- Modify: `scripts/setup-kind.sh` (apply the two reach files where cilium-policies.yaml was applied)
- Modify: `deploy/platform/kyverno/keystone-policy.yaml` (ruling 6), `deploy/platform/kyverno/values.yaml` (read RBAC: drop cilium.io, add networking.k8s.io networkpolicies, policy.linkerd.io, gateway.networking.k8s.io)
- Replace: `tests/unit/test_stage2_network_policy.py` with `tests/unit/test_reach_policy.py` — a **reach matrix** test: one parametrized table of (caller identity, destination, port, method+path) → allow/deny, evaluated by a small pure-Python reader of the two YAML files (route matching by method + path regex/prefix, Server port, AuthorizationPolicy identities; NetworkPolicy podSelector/ports/ipBlock). The table is the spec: include every allowed path and at least one denied path per destination.
- Modify: `tests/unit/test_stage2_pds_config.py:556-640` (the PDS ingress assertions now read the Linkerd Server), `tests/unit/test_stage7_keystone_config.py`
- Modify: `docs/telemetry-contract.md` counts

**Routes on payments-api:8080 (verify against the clients before writing):** read `tools/payments-mcp/payments_client.py` (or wherever `PaymentsClient` lives), `apps/reviewer-ui/ledger.py`, `agents/claims-agent/ledger.py`, the PDS ledger client under `platform/policy-decision-service/`, and `systems/payments-api` routes. Expected: payments-mcp → propose, execute, get one; reviewer-ui → list (`GET /payments` with query), get one, reject; claims-agent → get one; PDS → get one (verify). The readiness probe path → all networks.

- [ ] **Step 1:** SPIFFE/identity audit for ruling 3 (grep list in ruling 3). Write the list of references found into the report.
- [ ] **Step 2:** Write the reach matrix test first, from `cilium-policies.yaml` at `main` plus the route list above. Run it: it fails (files missing).
- [ ] **Step 3:** Write the two reach files and the ServiceAccounts until the matrix passes. Use `policy.linkerd.io/v1beta3` Server (verify the served versions on the installed CRDs: `kubectl get crd servers.policy.linkerd.io -o jsonpath='{.spec.versions[*].name}'`), `policy.linkerd.io/v1alpha1` AuthorizationPolicy, MeshTLSAuthentication, NetworkAuthentication, and the HTTPRoute group Linkerd's docs use for per-route authorization in this release.
- [ ] **Step 4:** Validate against the live API: `kubectl apply --dry-run=server -f deploy/platform/reach/` (the cluster from Task 1 Step 8 has the CRDs).
- [ ] **Step 5:** Keystone + Kyverno values + their unit tests.
- [ ] **Step 6:** `tests/unit` fully green; counts updated; commit. Then (orchestrator) apply to the live cluster with `./scripts/setup-kind.sh --stage 7` (idempotent) and check: every pod Ready, a full `./scripts/demo` five-minute flow settles one claim and holds one (the money path still works through the mesh).

### Task 3: live probes and the demo's reach beat

**Files:**
- Rewrite: `tests/e2e/test_gateway_is_the_only_route.py`, `tests/e2e/test_egress_policy.py`
- Modify: `tests/e2e/test_human_in_the_loop.py:~418` (execute from a non-payments-mcp identity is now a 403 from payments-api's proxy), `tests/e2e/test_keystone.py:~440-455` (the ring list: the agent creating a NetworkPolicy and a Linkerd AuthorizationPolicy is refused), `tests/harness/livecheck.py` (noise filter comment)
- Modify: `scripts/reach-probe.py`, `scripts/beat-reach.sh`, `scripts/demo.py` (reach output)
- Modify: `docs/telemetry-contract.md` counts, `scripts/smoke-test.sh` if it names Cilium

**New oracles (from the M1–M5 measurements):**
- Ingress refused = the HTTP status (403) or gRPC `PERMISSION_DENIED` **and** `inbound_http_authz_deny_total` for that Server/policy on the destination proxy rising by at least one across the call. Never TCP connect.
- Egress refused = connect timeout from inside the pod (NetworkPolicy drops), as before.
- Bypass: a throwaway pod labelled `app=claims-agent` in `northstar-agents` with `linkerd.io/inject: disabled` still cannot reach `160.79.104.1:443` or `example.com:443` (the kernel, not the proxy, enforces egress), and is refused by payments-mcp (no mesh identity).
- Keep every M11 assertion's intent: claims-agent and customer-ui cannot reach Anthropic; settlement-agent reaches `api.anthropic.com:443` and not `example.com` or `api.openai.com`; the gateway is the only route to payments-mcp; reviewer-ui cannot execute; claims-agent may POST `/a2a/push` and reviewer-ui may not; DNS resolves everywhere.

- [ ] **Step 1:** Rewrite the two e2e files with fixtures that FAIL (not skip) on a stage >= 2 cluster missing Linkerd or the reach objects.
- [ ] **Step 2:** Update the HITL and keystone tests, the harness comment.
- [ ] **Step 3:** reach-probe / beat-reach / demo: ingress lines print `REFUSED 403 by <policy>`; egress lines print `DROPPED (timeout)`. The demo's reach beat shows one of each and the deny counter.
- [ ] **Step 4:** Run the rewritten e2e files and the unit suite against the live cluster (not the gate). Counts updated. Commit.

### Task 4: the words — docs, runbook, deck

**Files:** `README.md`, `docs/architecture.md` (the Reach section `:1436-1475` rewritten, component lists, namespaces, flows, the "PDS protected by Cilium" line), `docs/demo-runbook.md` (reach beat, troubleshooting: `kubectl get servers,authorizationpolicies -A`, `kubectl get networkpolicy -A`), `docs/telemetry-contract.md` (the "drops are in no trace" item: refusals are now counted, still not spans), `talk/slides/30-act3a.html` (toolbox Network cell: Linkerd + NetworkPolicy, with `talk/assets/logos/linkerd.svg` from the CNCF artwork repo; the reach code card quotes the new files), `talk/slides/31-act3b.html` + notes (the optional "Reach has two layers" slide becomes what the demo runs), `talk/slides/40-act4.html` (the no-trace row), `talk/slides/50-close.html`, `talk/notes/*.json`, `talk/PLAN.md`, the code comments listed in the inventory (`agents/claims-agent/push.py`, `apps/reviewer-ui/main.py`, `ledger.py`, `systems/payments-api/store.go`, `platform/policy-decision-service/pds/rules.py`, Backstage `homeModule.tsx`, the two `docs/index.md`), `telemetry/live-check.toml:7`.

- [ ] **Step 1:** Rewrite. `tests/unit/test_guardrail_config.py` holds the deck's "The guardrails are config" slide and `demo config` to their files: if a quoted file was `cilium-policies.yaml`, quote the new reach files instead, and keep the test green.
- [ ] **Step 2:** Stale-prose pass: `grep -rni cilium` outside `docs/superpowers/` and historical telemetry-contract rows; every remaining hit is either history (dated, past tense) or wrong.
- [ ] **Step 3:** `python3 talk/build/build.py`, `python3 talk/build/plan.py --check`, the deck unit tests; commit.

### Task 5: final review, fix wave, gate

- [ ] **Step 1:** Final whole-branch review (opus) against the Review Focus above and the spec.
- [ ] **Step 2:** Fix wave; skip-path mutation pass on the new e2e fixtures (remove the reach objects → the tests fail, not skip; uninstall nothing, use a dry flag or a mutated copy).
- [ ] **Step 3:** Gate: cold `./scripts/setup-kind.sh --stage 7` from the clean tree, `./scripts/start-demo.sh`, `./scripts/smoke-test.sh`; record MEASURED (or MEASURED-FAILED) in `docs/telemetry-contract.md`; commit the row.
