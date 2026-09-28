"""Recovering the caller's SPIFFE ID from an ExtMCP request.

The gateway has already verified the JWT-SVID's signature, issuer, audience
and expiry before it calls us -- that is what `jwtAuth` does, and re-verifying
here would duplicate it. What this module does is decide whether the value the
gateway passed us is an identity we recognise at all.

Where the value comes from is measured, not assumed: the whole `McpRequest`
was dumped on this cluster against agentgateway v1.5.0, and the SPIFFE ID
arrives in `metadata_context` under the key `caller`, populated by the
gateway config `metadata: {caller: 'jwt.sub'}`, and it is *never* in a forwarded
`authorization` header -- `jwtAuth` strips the credential from its location
unless `preserveToken: true`, which we deliberately do not set. So `headers` is
accepted below and not read: it is the second half of the request shape, it is
where a future `preserveToken` design would look, and taking it keeps the
caller from having to know which half we use.
"""

# The trailing slash is load-bearing. Without it, `startswith` would accept
# `spiffe://northstar.local.evil.example/ns/a/sa/b` -- a different trust domain
# that merely begins with the string of ours.
TRUST_DOMAIN = "spiffe://northstar.local/"


def caller_spiffe_id(metadata: dict, headers: list) -> str | None:
    """The attested caller, or None if there isn't one we recognise.

    Returns None rather than raising, for every rejection. A missing or
    unrecognised identity is a *policy* input -- `rules.decide` denies on it,
    with a rule name and a reason. An exception here would instead become a
    gRPC error, which the gateway routes through `failureMode` and reports as a
    PDS outage. The two look nothing alike in a trace, and only one of them is
    true.
    """
    value = metadata.get("caller")
    if not isinstance(value, str):
        return None
    if not value.startswith(TRUST_DOMAIN):
        return None
    return value
