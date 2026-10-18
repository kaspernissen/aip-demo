"""Claim outcomes pushed by the claims-agent, held for the browser that asked.

One token per claim: issued to the browser, handed by the browser to the
claims-agent in its push config, sent back by the SDK as
X-A2A-Notification-Token. The first push binds the token to its task id. A
waiter blocks on an asyncio.Event, never on a clock: the relay has no loop.
Bounded: the oldest token is forgotten past MAX_CLAIMS.

Known limits, acceptable for this demo: issuing a token (/claims/push-config)
is unauthenticated, so anyone who can reach the server can mint tokens, and
MAX_CLAIMS calls evict every live claim (its browser then gets a 401 on the
events stream and falls back to one tasks/get). State is in memory and
single-replica: a restart forgets all tokens.
"""
from __future__ import annotations

import asyncio
import hmac
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field

MAX_CLAIMS = 1000
TERMINAL = {"completed", "failed", "canceled", "rejected"}


@dataclass
class _Claim:
    task_id: str | None = None
    snapshots: list[dict] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class Relay:
    def __init__(self, max_claims: int = MAX_CLAIMS):
        self._claims: OrderedDict[str, _Claim] = OrderedDict()
        self._max = max_claims

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        self._claims[token] = _Claim()
        while len(self._claims) > self._max:
            self._claims.popitem(last=False)
        return token

    def _find(self, token: str | None) -> _Claim | None:
        if not token:
            return None
        try:
            # Bytes: compare_digest raises TypeError on a non-ASCII str, and
            # Starlette decodes header values as latin-1.
            candidate = token.encode("latin-1")
        except UnicodeEncodeError:
            return None
        found = None
        for known, claim in self._claims.items():
            # No early exit: every known token is compared, constant-time each.
            if hmac.compare_digest(known.encode("ascii"), candidate):
                found = claim
        return found

    def claim_for(self, token: str | None) -> _Claim | None:
        """Resolve a token once; hand the result to follow()."""
        return self._find(token)

    def push(self, token: str | None, task: dict) -> int:
        claim = self._find(token)
        if claim is None:
            return 401
        if claim.task_id is None:
            claim.task_id = task.get("id")
        elif claim.task_id != task.get("id"):
            return 409
        claim.snapshots.append(task)
        claim.changed.set()
        return 204

    def known(self, token: str | None) -> bool:
        return self._find(token) is not None

    async def follow(self, claim: _Claim):
        """Yield every snapshot of this claim, in order, until a terminal one.

        Takes the claim itself, not the token: an eviction after the caller
        checked the token must not leave a stream with nothing to follow.
        """
        sent = 0
        while True:
            while sent < len(claim.snapshots):
                snap = claim.snapshots[sent]
                sent += 1
                yield snap
                if snap.get("status", {}).get("state") in TERMINAL:
                    return
            claim.changed.clear()
            if sent < len(claim.snapshots):
                continue
            await claim.changed.wait()
