"""`model_client.py`: the model name passes through untouched unless a gateway is
configured, and when one is, every model call carries this workload's JWT-SVID.

No SPIRE and no network: `model_client.fetch_jwt_svid` is replaced, and the
model is only constructed, never called.
"""
from __future__ import annotations

import httpx2
import pytest

import model_client
from model_client import SvidBearer, gateway_model

GATEWAY = "http://agentgateway.northstar-platform.svc.cluster.local:4000"
AUDIENCE = "spiffe://northstar.local/ns/northstar-platform/sa/agentgateway"


def test_the_name_is_returned_unchanged_without_a_base_url(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("NORTHSTAR_GATEWAY_AUDIENCE", raising=False)
    assert gateway_model("anthropic:claude-sonnet-5") == "anthropic:claude-sonnet-5"


def test_an_empty_base_url_counts_as_unset(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    assert gateway_model("anthropic:claude-sonnet-5") == "anthropic:claude-sonnet-5"


def test_a_base_url_without_an_audience_fails_at_construction(monkeypatch):
    """A wrong or missing audience is a 401 on every model call; it must be a
    startup failure that names the variable, not a runtime 401."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", GATEWAY)
    monkeypatch.delenv("NORTHSTAR_GATEWAY_AUDIENCE", raising=False)
    with pytest.raises(KeyError, match="NORTHSTAR_GATEWAY_AUDIENCE"):
        gateway_model("anthropic:claude-sonnet-5")


def test_with_both_set_the_model_is_an_anthropic_model_whose_client_carries_the_bearer(monkeypatch):
    from pydantic_ai.models.anthropic import AnthropicModel

    monkeypatch.setenv("ANTHROPIC_BASE_URL", GATEWAY)
    monkeypatch.setenv("NORTHSTAR_GATEWAY_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "held-by-agentgateway")
    model = gateway_model("anthropic:claude-sonnet-5")
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-5"
    http_client = model.client._client
    assert isinstance(http_client.auth, SvidBearer)
    assert http_client.auth.audience == AUDIENCE
    assert str(model.client.base_url).rstrip("/") == GATEWAY


async def test_the_bearer_sets_authorization_from_a_fresh_svid_per_request(monkeypatch):
    audiences: list[str] = []
    tokens = iter(["token-1", "token-2"])

    async def fake_fetch(audience: str) -> str:
        audiences.append(audience)
        return next(tokens)

    monkeypatch.setattr(model_client, "fetch_jwt_svid", fake_fetch)
    auth = SvidBearer(AUDIENCE)
    seen = []
    for _ in range(2):
        request = httpx2.Request("POST", GATEWAY + "/v1/messages")
        flow = auth.async_auth_flow(request)
        sent = await flow.__anext__()
        seen.append(sent.headers["Authorization"])
    assert seen == ["Bearer token-1", "Bearer token-2"]
    assert audiences == [AUDIENCE, AUDIENCE]


async def test_the_bearer_replaces_an_authorization_header_already_present(monkeypatch):
    async def fake_fetch(audience: str) -> str:
        return "token-1"

    monkeypatch.setattr(model_client, "fetch_jwt_svid", fake_fetch)
    request = httpx2.Request("POST", GATEWAY, headers={"Authorization": "Bearer stale"})
    sent = await SvidBearer(AUDIENCE).async_auth_flow(request).__anext__()
    assert sent.headers.get_list("Authorization") == ["Bearer token-1"]
