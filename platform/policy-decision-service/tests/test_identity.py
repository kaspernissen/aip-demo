import pytest
from pds.identity import caller_spiffe_id


def test_extracts_the_caller_from_metadata_context():
    assert caller_spiffe_id({"caller": "spiffe://northstar.local/ns/a/sa/b"}, []) \
        == "spiffe://northstar.local/ns/a/sa/b"


def test_missing_identity_is_none_not_an_exception():
    """A missing identity must reach `decide` as None and be denied there,
    not raise here -- an exception would hit the gateway's failureMode path
    and be reported as a PDS outage rather than as a policy denial."""
    assert caller_spiffe_id({}, []) is None


def test_a_non_spiffe_value_is_rejected():
    """Anything not a SPIFFE ID is not an identity. Returning it would let a
    forged metadata value become a caller."""
    assert caller_spiffe_id({"caller": "admin"}, []) is None


def test_the_trust_domain_must_match():
    """An SVID from another trust domain is not ours, however well-formed."""
    assert caller_spiffe_id({"caller": "spiffe://evil.example/ns/a/sa/b"}, []) is None


def test_a_prefix_lookalike_trust_domain_is_rejected():
    """`startswith` on a bare scheme+host would accept
    `spiffe://northstar.local.evil.example/...`. The trailing slash in
    TRUST_DOMAIN is what stops it, so assert on the thing the slash buys.

    Measured provenance: the real value Task 1 observed in `metadata_context`
    was `spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent`, so
    the legitimate form always has that separator.
    """
    assert caller_spiffe_id(
        {"caller": "spiffe://northstar.local.evil.example/ns/a/sa/b"}, []) is None


def test_a_non_string_metadata_value_is_rejected_not_coerced():
    """`metadata_context` is a `google.protobuf.Struct`: a CEL expression that
    resolved to a number or a nested object arrives here as a float or a dict,
    not a string. Coercing one with `str()` would invent an identity."""
    assert caller_spiffe_id({"caller": 42.0}, []) is None
    assert caller_spiffe_id({"caller": None}, []) is None
