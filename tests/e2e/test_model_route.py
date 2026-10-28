"""The gateway's `llm` listener: who may call the model, and which model.

The agents hold no model key. Their model calls go to agentgateway's llm
listener (:4000), which verifies a JWT-SVID, lets only the two model agents'
identities through, adds the key itself and forwards to the provider. Each
refusal here is the gateway's own, so the request never reaches the provider.

**Cost: no model call.** The one request an allowed identity makes is for a
model that is not on the allowlist, which the gateway answers 404 before
anything is forwarded. A completed model call end to end is asserted by
`test_settlement_llm_trace.py` and `test_claims_chain_trace.py`.

Tokens are minted by SPIRE server-side (`tests.harness.live.mint_svid`), so a
real-signature token for any subject and audience is possible; none is printed.
"""
from __future__ import annotations

import re
import subprocess
import time
from contextlib import contextmanager

import httpx
import pytest

from tests.harness import live

CLAIMS_AGENT_ID = "spiffe://northstar.local/ns/northstar-agents/sa/claims-agent"
SETTLEMENT_AGENT_ID = live.CALLER_SPIFFE_ID
REVIEWER_UI_ID = "spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui"
OTHER_AUDIENCE = "spiffe://northstar.local/ns/northstar-tools/sa/payments-mcp"

ALLOWED_MODEL = "claude-sonnet-5"
UNLISTED_MODEL = "claude-not-on-the-allowlist"


@contextmanager
def _llm_port_forward():
    proc = subprocess.Popen(
        ["kubectl", "--context", live.KUBE_CONTEXT, "-n", live.GATEWAY_NAMESPACE,
         "port-forward", "svc/agentgateway", ":4000"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    port = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 4000", line)
            if match:
                port = int(match.group(1))
                break
        assert port, "could not port-forward svc/agentgateway :4000 (is the gateway up?)"
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def llm_url():
    with _llm_port_forward() as url:
        yield url


def _messages(url: str, model: str, token: str | None) -> httpx.Response:
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01",
               "x-api-key": "held-by-agentgateway"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return httpx.post(
        f"{url}/v1/messages",
        json={"model": model, "max_tokens": 1,
              "messages": [{"role": "user", "content": "hi"}]},
        headers=headers, timeout=30,
    )


def test_a_model_call_with_no_token_is_refused(llm_url):
    assert _messages(llm_url, ALLOWED_MODEL, None).status_code == 401


def test_a_model_call_with_a_bearer_that_is_not_a_jwt_is_refused(llm_url):
    assert _messages(llm_url, ALLOWED_MODEL, "not-a-jwt").status_code == 401


def test_an_svid_minted_for_another_audience_is_refused(llm_url):
    token = live.mint_svid(OTHER_AUDIENCE, SETTLEMENT_AGENT_ID)
    assert _messages(llm_url, ALLOWED_MODEL, token).status_code == 401


def test_a_genuine_svid_of_an_identity_that_is_not_a_model_agent_is_refused(llm_url):
    """Authenticated, then not authorized: 403, not 401. reviewer-ui is a real,
    registered identity that the MCP route knows; the llm listener does not."""
    token = live.mint_svid(live.GATEWAY_AUDIENCE, REVIEWER_UI_ID)
    response = _messages(llm_url, ALLOWED_MODEL, token)
    assert response.status_code == 403, (response.status_code, response.text[:200])


@pytest.mark.parametrize("subject", [CLAIMS_AGENT_ID, SETTLEMENT_AGENT_ID])
def test_a_model_agent_is_admitted_and_only_the_allowlisted_model_is_served(llm_url, subject):
    """Control: the identity gets past jwtAuth and authorization (a 404 for an
    unlisted model can only come after both), so the 401/403 above are about the
    caller and not an outage of the listener."""
    token = live.mint_svid(live.GATEWAY_AUDIENCE, subject)
    response = _messages(llm_url, UNLISTED_MODEL, token)
    assert response.status_code == 404, (response.status_code, response.text[:200])


def test_one_identity_two_doors_two_answers(llm_url):
    """claims-agent's SVID is the same token at both listeners: the llm listener
    admits it (404 for an unlisted model comes only after jwtAuth and
    authorization), the MCP route refuses it 403 at `initialize`. Holding a
    gateway-audience SVID for the model is not a way into the tools."""
    token = live.mint_svid(live.GATEWAY_AUDIENCE, CLAIMS_AGENT_ID)
    assert _messages(llm_url, UNLISTED_MODEL, token).status_code == 404
    with live.gateway_port_forward() as mcp_url:
        outcome = live.call_tool_directly(
            mcp_url, live.EXECUTE_TOOL,
            {"payment_id": "PAY-000000", "amount": 1.0, "currency": "EUR"},
            {"authorization": f"Bearer {token}"},
        )
    assert outcome.http_status == 403 and outcome.stage == "initialize", outcome
