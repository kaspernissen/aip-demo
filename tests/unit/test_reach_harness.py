"""The reach probes' decision logic, against output recorded on the M12 spike.

The e2e reach files need a live cluster; the functions that decide what a
probe's output MEANS do not, so they live in `tests/harness/reach.py` and are
pinned here. Every sample below is a real output captured on 2026-10-21 from
the throwaway kind cluster `m12-spike` (kindnet + Linkerd edge-26.10.1), using
the exact probe source the e2e files run. The gRPC samples are the one
exception: they are shaped from the protocol (the real PERMISSION_DENIED at the
PDS was measured in the final review, plan M7, but not recorded as a sample).
"""
import json

import pytest

from tests.harness import reach

REFUSED_403 = '{"status": 403, "headers": {"connection": "close", "content-length": "0", "date": "Wed, 21 Oct 2026 20:53:11 GMT"}, "body_len": 0, "error": null, "stage": "done"}'
NO_ROUTE_404 = '{"status": 404, "headers": {"date": "Wed, 21 Oct 2026 20:53:12 GMT", "connection": "close", "content-length": "0"}, "body_len": 0, "error": null, "stage": "done"}'
APP_404 = '{"status": 404, "headers": {"server": "SimpleHTTP/0.6 Python/3.12.15", "content-length": "335"}, "body_len": 335, "error": null, "stage": "done"}'
APP_200 = '{"status": 200, "headers": {"server": "SimpleHTTP/0.6 Python/3.12.15", "content-length": "844"}, "body_len": 844, "error": null, "stage": "done"}'
APP_403_WITH_BODY = '{"status": 403, "headers": {"content-length": "17"}, "body_len": 17, "error": null, "stage": "done"}'
PROXY_504 = '{"status": 504, "headers": {"l5d-proxy-error": "endpoint 10.244.0.99:8080: client error (Connect)", "connection": "close", "l5d-proxy-connection": "close", "content-length": "0"}, "body_len": 0, "error": null, "stage": "done"}'
PROXY_403_WITH_ERROR_HEADER = '{"status": 403, "headers": {"l5d-proxy-error": "x"}, "body_len": 0, "error": null, "stage": "done"}'
PEER_PROXY_502 = '{"status": 502, "headers": {"content-length": "0"}, "body_len": 0, "error": null, "stage": "done"}'
APP_502_WITH_BODY = '{"status": 502, "headers": {"content-length": "11"}, "body_len": 11, "error": null, "stage": "done"}'
PROXY_502_WITH_ERROR = '{"status": 502, "headers": {"l5d-proxy-error": "x", "content-length": "0"}, "body_len": 0, "error": null, "stage": "done"}'
TLS_OK = '{"status": 404, "headers": {"server": "cloudflare", "cf-ray": "a4e326e87b94bc36-WAW"}, "body_len": 0, "error": null, "stage": "done"}'
TLS_RESET = '{"status": null, "headers": {}, "body_len": null, "error": "ConnectionResetError", "stage": "tls"}'
CONNECT_TIMEOUT = '{"status": null, "headers": {}, "body_len": null, "error": "TimeoutError", "stage": "connect"}'
TCP_CONNECTED = '{"status": null, "headers": {}, "body_len": null, "error": null, "stage": "connected"}'
TCP_REFUSED = '{"status": null, "headers": {}, "body_len": null, "error": "ConnectionRefusedError", "stage": "connect"}'
# Shaped from the protocol; the refusal was measured at the PDS in the final review (plan M7: PERMISSION_DENIED).
GRPC_STATUS_7 = '{"status": 200, "headers": {"grpc-status": "7", "content-type": "application/grpc"}, "body_len": 0, "error": null, "stage": "done"}'
GRPC_HTTP_403 = REFUSED_403


def parse(s):
    return reach.parse_probe_output(s + "\n")


def test_parse_takes_the_last_json_line_and_ignores_noise():
    assert parse("warning: something\n" + REFUSED_403)["status"] == 403


@pytest.mark.parametrize("bad", ["", "   \n", "not json", "[1, 2]", '{"hello": 1}'])
def test_parse_refuses_to_guess(bad):
    with pytest.raises(reach.ProbeError):
        reach.parse_probe_output(bad)


@pytest.mark.parametrize(
    "sample,expected",
    [
        (REFUSED_403, "refused-403"),
        (NO_ROUTE_404, "refused-404"),
        (APP_404, "answered"),          # an app 404 has a body: not a refusal
        (APP_200, "answered"),
        (APP_403_WITH_BODY, "answered"),  # an app speaking is not the proxy refusing
        (PROXY_504, "proxy-error"),
        (PROXY_403_WITH_ERROR_HEADER, "proxy-error"),  # l5d-proxy-error disqualifies
        (TLS_RESET, "no-response"),
    ],
)
def test_ingress_classification(sample, expected):
    assert reach.classify_ingress(parse(sample)) == expected


@pytest.mark.parametrize(
    "sample,expected",
    [(GRPC_STATUS_7, True), (GRPC_HTTP_403, True), (APP_200, False),
     (PROXY_504, False), (TLS_RESET, False)],
)
def test_grpc_refusal_accepts_either_form(sample, expected):
    assert reach.grpc_refused(parse(sample)) is expected


@pytest.mark.parametrize(
    "sample,expected",
    [
        (TLS_OK, ("reached", "answered")),
        (APP_200, ("reached", "answered")),
        (TLS_RESET, ("dropped", "reset")),
        (CONNECT_TIMEOUT, ("dropped", "timeout")),
        (PROXY_504, ("dropped", "proxy-error")),
        (PEER_PROXY_502, ("reached", "peer-proxy-502")),  # M7: not a drop
        (APP_502_WITH_BODY, ("reached", "answered")),  # an app speaking
        (PROXY_502_WITH_ERROR, ("dropped", "proxy-error")),
        ('{"status": null, "headers": {}, "body_len": null, "error": "SSLError", "stage": "tls"}',
         ("dropped", "other:SSLError")),
    ],
)
def test_egress_classification(sample, expected):
    assert reach.classify_egress(parse(sample)) == expected


@pytest.mark.parametrize(
    "sample,expected",
    [(TCP_CONNECTED, "connected"), (CONNECT_TIMEOUT, "timeout"),
     (TCP_REFUSED, "refused")],
)
def test_tcp_classification_for_unmeshed_pods(sample, expected):
    assert reach.classify_tcp(parse(sample)) == expected


def test_a_tcp_connect_is_never_reached_for_a_meshed_pod():
    """The probe's `connected` result carries no HTTP status, so classify_egress
    (the meshed oracle) cannot call it reached: a connect from a meshed pod
    proves nothing (M1)."""
    assert reach.classify_egress(parse(TCP_CONNECTED))[0] == "dropped"


METRICS = """\
# HELP inbound_http_authz_allow_total The total number of inbound HTTP requests that were authorized
# TYPE inbound_http_authz_allow_total counter
inbound_http_authz_allow_total{target_addr="10.244.0.12:8080",srv_name="web-http",route_name="web-x",authz_name="web-x-allowed",tls="true",client_id="allowed.src.serviceaccount.identity.linkerd.cluster.local"} 2
# HELP inbound_http_authz_deny_total The total number of inbound HTTP requests that could not be processed due to a proxy error.
# TYPE inbound_http_authz_deny_total counter
inbound_http_authz_deny_total{target_addr="10.244.0.12:8080",target_ip="10.244.0.12",target_port="8080",srv_kind="server",srv_name="web-http",srv_port="8080",route_kind="HTTPRoute",route_name="web-x",tls="no_identity",no_tls_reason="no_tls_from_remote"} 1
inbound_http_authz_deny_total{target_addr="10.244.0.12:8080",target_ip="10.244.0.12",target_port="8080",srv_kind="server",srv_name="web-http",srv_port="8080",route_kind="HTTPRoute",route_name="web-x",tls="true",client_id="client.src.serviceaccount.identity.linkerd.cluster.local"} 2
inbound_http_authz_deny_total{target_addr="10.244.0.17:8000",srv_kind="server",srv_name="tcp-http",route_kind="default",route_name="default",tls="true",client_id="client.src.serviceaccount.identity.linkerd.cluster.local"} 1
"""


def test_deny_counters_are_parsed_and_the_allow_counter_is_not():
    counters = reach.parse_deny_counters(METRICS)
    assert len(counters) == 3
    assert {c.label("srv_name") for c in counters} == {"web-http", "tcp-http"}


def test_client_id_matches_the_label_linkerd_writes():
    cid = reach.client_id("client", "src")
    assert cid == "client.src.serviceaccount.identity.linkerd.cluster.local"
    counters = reach.parse_deny_counters(METRICS)
    assert reach.deny_total(counters, srv_name="web-http", client_id=cid) == 2


def test_delta_counts_only_the_matching_caller():
    before = reach.parse_deny_counters(METRICS)
    after = reach.parse_deny_counters(
        METRICS.replace('client.src.serviceaccount.identity.linkerd.cluster.local"} 2',
                        'client.src.serviceaccount.identity.linkerd.cluster.local"} 3'))
    cid = reach.client_id("client", "src")
    assert reach.deny_delta(before, after, srv_name="web-http", client_id=cid) == 1
    assert reach.deny_delta(before, after, srv_name="web-http",
                            client_id=reach.client_id("other", "src")) == 0


def test_a_counter_that_went_down_is_not_a_refusal():
    """A restarted proxy resets its counters."""
    before = reach.parse_deny_counters(METRICS)
    assert reach.deny_delta(before, [], srv_name="web-http") == 0


def test_refused_by_names_server_and_route_that_rose():
    before = reach.parse_deny_counters(METRICS)
    after = reach.parse_deny_counters(
        METRICS.replace('route_name="default",tls="true",client_id="client.src.serviceaccount.identity.linkerd.cluster.local"} 1',
                        'route_name="default",tls="true",client_id="client.src.serviceaccount.identity.linkerd.cluster.local"} 2'))
    by = reach.refused_by(before, after)
    assert by == ("tcp-http", "default", 1.0)
    assert reach.refusal_line(403, by) == "REFUSED 403 by tcp-http/default (deny counter +1)"


def test_refused_by_is_none_when_nothing_rose():
    c = reach.parse_deny_counters(METRICS)
    assert reach.refused_by(c, c) is None
    assert "not attributable" in reach.refusal_line(403, None)


def test_a_new_series_counts_from_zero():
    after = reach.parse_deny_counters(METRICS)
    assert reach.refused_by([], after, srv_name="tcp-http") == ("tcp-http", "default", 1.0)


def test_egress_lines():
    assert reach.egress_line("reached", "answered", True) == "REACHED"
    assert reach.egress_line("dropped", "timeout", False) == "DROPPED (timeout)"
    assert reach.egress_line("dropped", "reset", True) == "DROPPED (TLS reset)"
    assert reach.egress_line("dropped", "proxy-error", True) == "DROPPED (proxy error)"


def test_metrics_are_read_through_the_api_server_not_a_port_forward():
    path = reach.metrics_proxy_path("northstar-tools", "payments-mcp-abc")
    assert path == "/api/v1/namespaces/northstar-tools/pods/payments-mcp-abc:4191/proxy/metrics"


def test_the_probe_source_compiles_and_has_a_connect_only_mode():
    compile(reach.PROBE_SRC, "probe", "exec")
    assert '"tcp"' in reach.PROBE_SRC


# --- gRPC ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"grpc_code": "PERMISSION_DENIED", "error": None}, "refused"),
        ({"grpc_code": "OK", "error": None}, "reached"),
        ({"grpc_code": "INVALID_ARGUMENT", "error": None}, "reached"),  # the server answered
        ({"grpc_code": "UNAVAILABLE", "error": None}, "unreachable"),
        ({"grpc_code": "DEADLINE_EXCEEDED", "error": None}, "unreachable"),
        ({"grpc_code": None, "error": "ModuleNotFoundError"}, "error"),
    ],
)
def test_grpc_classification(result, expected):
    """Shaped from the protocol; the real refusal was measured in the final review (plan M7)."""
    assert reach.classify_grpc(result) == expected


def test_the_grpc_probe_source_compiles_and_uses_the_real_pds_method():
    compile(reach.GRPC_PROBE_SRC, "grpc-probe", "exec")
    assert reach.PDS_METHOD.startswith("/agentgateway.dev.ext_mcp.ExtMcp/")


# --- the objects the cluster must hold ----------------------------------------------

DOCS = reach.repo_reach_documents()


def test_the_expected_objects_are_read_from_the_two_reach_files():
    expected = reach.expected_objects(DOCS)
    kinds = {k for k, _, _ in expected}
    assert kinds == {"Server", "AuthorizationPolicy", "HTTPRoute", "MeshTLSAuthentication",
                     "NetworkAuthentication", "NetworkPolicy"}
    assert ("Server", "northstar-tools", "payments-mcp") in expected
    assert ("NetworkPolicy", "northstar-agents", "settlement-agent-egress") in expected
    assert all(ns for _, ns, _ in expected), "every reach object is namespaced"


def test_every_kind_in_the_files_has_a_resource_to_list():
    for kind in {k for k, _, _ in reach.expected_objects(DOCS)}:
        assert reach.live_objects_query([kind])


def test_missing_objects_names_exactly_what_is_absent():
    expected = reach.expected_objects(DOCS)
    live = [{"kind": k, "metadata": {"namespace": ns, "name": n}} for k, ns, n in sorted(expected)]
    assert reach.missing_objects(expected, json.dumps({"items": live})) == []
    gone = live.pop(3)
    assert reach.missing_objects(expected, json.dumps({"items": live})) == [
        (gone["kind"], gone["metadata"]["namespace"], gone["metadata"]["name"])]
    assert len(reach.missing_objects(expected, json.dumps({"items": []}))) == len(expected)


def test_identities_authorized_for_reads_the_policy_chain():
    gw = reach.client_id("agentgateway", "northstar-platform")
    assert reach.identities_authorized_for("Server", "payments-mcp", DOCS) == {gw}
    assert reach.identities_authorized_for("Server", "policy-decision-service", DOCS) == {gw}
    assert reach.identities_authorized_for("Server", "settlement-agent", DOCS) == {
        reach.client_id("claims-agent", "northstar-agents")}
    assert reach.identities_authorized_for("HTTPRoute", "customer-ui-push", DOCS) == {
        reach.client_id("claims-agent", "northstar-agents")}
    assert reach.identities_authorized_for("Server", "no-such-server", DOCS) == set()


def test_no_policy_in_the_repo_admits_the_default_service_account():
    for d in DOCS:
        if d["kind"] == "MeshTLSAuthentication":
            assert not any(i.startswith("default.") for i in d["spec"]["identities"])


def test_egress_drift_flags_a_widened_rule_and_ignores_the_rest():
    np = next(d for d in DOCS if d["kind"] == "NetworkPolicy")
    same = json.loads(json.dumps(np))
    same["status"] = {"x": 1}
    same["spec"]["policyTypes"] = ["Egress"]
    assert reach.egress_drift(np, same) == []
    wide = json.loads(json.dumps(np))
    wide["spec"]["egress"].append({"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]})
    assert reach.egress_drift(np, wide) == ["egress"]


def test_throwaway_unmeshed_pod_skips_the_mesh_and_has_no_identity():
    (pod,) = reach.throwaway_manifests(
        "p", "northstar-agents", "img:dev", {"app": "claims-agent"}, meshed=False)
    assert pod["metadata"]["annotations"] == {"linkerd.io/inject": "disabled"}
    assert pod["metadata"]["labels"] == {"app": "claims-agent"}
    assert pod["spec"]["restartPolicy"] == "Never"
    assert "serviceAccountName" not in pod["spec"]


def test_throwaway_meshed_pod_gets_its_own_service_account():
    sa, pod = reach.throwaway_manifests(
        "p", "northstar-agents", "img:dev", {"app": "reach-probe"}, meshed=True,
        service_account="reach-probe-wrongid")
    assert sa["kind"] == "ServiceAccount" and sa["metadata"]["name"] == "reach-probe-wrongid"
    assert pod["spec"]["serviceAccountName"] == "reach-probe-wrongid"
    assert pod["metadata"]["annotations"] == {"linkerd.io/inject": "enabled"}
    assert pod["spec"]["containers"][0]["name"] == "probe"


def test_a_never_ready_throwaway_cannot_join_service_endpoints():
    """The bypass pod carries `app=claims-agent`, so without a failing readiness
    probe it would become an endpoint of the real claims-agent Service."""
    (pod,) = reach.throwaway_manifests("p", "ns", "img", {"app": "x"}, meshed=False,
                                       never_ready=True)
    assert pod["spec"]["containers"][0]["readinessProbe"]["exec"]["command"] == ["false"]
    (plain,) = reach.throwaway_manifests("p", "ns", "img", {"app": "x"}, meshed=False)
    assert "readinessProbe" not in plain["spec"]["containers"][0]
