"""The HTTP surface: ask for a settlement's scores, get them and emit them.

Deliberately `http.server` from the standard library rather than a framework.
This service has two routes, no forms, no templates and no clients of its own,
and every framework in this repo's dependency closure would come with an
instrumentation library whose spans would land in the *caller's* trace -- the
one place these scores must not be (see `scorer/emit.py::configure`). The
dependency this service does add is heavy enough already (the SDK pulls
`botocore` and two OTLP exporters); adding a web framework to route two paths
would be the only unjustified line in `requirements.txt`.

    POST /score   {"claimId": "CLM-1842", "sinceMs": 1789322500000,
                   "emit": true}
                  -> 200 {"claimId", "traceIds", "spanCount", "decisionCount",
                          "anchorSpanId",
                          "scores": {name: {"value", "label", "explanation"}}}
    GET  /healthz -> 200 "ok"

`sinceMs` is optional and defaults to `DEFAULT_WINDOW_S` ago; see
`scorer/opensearch.py` for why a window is load-bearing rather than hygiene.

**`emit` is optional, defaults to true, and exists because OpenSearch is
near-real-time.** A settlement's spans arrive over a second or two, so a score
request made the instant a settlement returns can legitimately see the
trajectory half-written -- and scoring it then produces two wrong things at
once: the scores judge a partial trajectory, and they are attached to whatever
span happened to look like its root at that moment. Measured, not predicted:
polling `/score` three times while a supervised settlement settled put
**fifteen** score spans in the trace under **two different parents**. So a
caller that needs to wait for completeness polls with `"emit": false` --
reading `traceIds`, `spanCount` and `decisionCount` to decide -- and then asks
once, for real. `tests/e2e/test_policy_scores.py` does exactly that.

Re-scoring the same claim is still allowed and still emits again. That is
correct for an evaluator: each run is an evaluation of the trajectory as it
stood, and `gen_ai.evaluation.*` spans are events, not records to be updated.
What `emit` removes is the *accidental* repetition.
"""
from __future__ import annotations

import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scorer import emit
from scorer.contract import TrajectoryUnscorable
from scorer.opensearch import (
    DEFAULT_WINDOW_S,
    OpenSearchClient,
    OpenSearchError,
)
from scorer.scores import SCORERS, scores_for

logger = logging.getLogger(__name__)

PORT = int(os.environ.get("NORTHSTAR_EVAL_SCORER_PORT", "8300"))

# Refuses a body larger than this rather than reading it into memory. The real
# body is three short keys; anything near this is a mistake or an attack, and
# `Content-Length` is attacker-controlled either way.
MAX_BODY_BYTES = 64 * 1024


def score_claim(
    client: OpenSearchClient, claim_id: str, since_ms: int, *, emit_spans: bool = True
) -> dict:
    """Reconstruct, score, optionally emit, and return the row.

    Split out of the handler so the unit suite can drive the whole path
    against a fake client with no socket, no cluster and no exporter -- the
    same split `pds/rules.py` has from `pds/server.py`.

    `emit_spans=False` returns exactly the same row with `anchorSpanId: null`
    and nothing written. See the module docstring for why a caller wants that.

    **`scores` may hold fewer than five entries.** `scores_for` omits
    (never guesses) a dimension whose scorer raised
    `contract.TrajectoryUnscorable` -- today that can only be
    `identity_had_authority`, and only while the contract publishes no
    version. The row's own `unscorable` field names any such dimension
    explicitly, precisely so a caller cannot mistake four scores for a
    complete five: the "a partial trajectory always reads higher" lesson
    applies to a partial SCORE SET, not only a partial trajectory. The
    field is present with an empty list when nothing was omitted, so its
    absence is never itself ambiguous.
    """
    trajectory = client.trajectory_for_claim(claim_id, since_ms)
    scores = scores_for(trajectory)
    anchor_span_id = emit.emit(trajectory, scores) if emit_spans else None
    if emit_spans:
        emit.flush()
    return {
        "claimId": claim_id,
        "traceIds": list(trajectory.trace_ids()),
        "spanCount": len(trajectory.spans),
        "decisionCount": len(trajectory.decisions()),
        "anchorSpanId": anchor_span_id,
        "scores": {
            name: {
                "value": result.value,
                "label": result.label,
                "explanation": result.explanation,
            }
            for name, result in scores.items()
        },
        "unscorable": sorted(set(SCORERS) - set(scores)),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "eval-scorer"
    # Never echo the request line into the response; log it instead.
    protocol_version = "HTTP/1.1"

    client: OpenSearchClient

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _respond(self, status: int, payload: dict | str) -> None:
        body = (
            payload if isinstance(payload, str) else json.dumps(payload)
        ).encode("utf-8")
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "text/plain" if isinstance(payload, str) else "application/json",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        if self.path == "/healthz":
            self._respond(200, "ok")
            return
        self._respond(404, {"error": f"no such route: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        if self.path != "/score":
            self._respond(404, {"error": f"no such route: {self.path}"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._respond(413, {"error": "body too large"})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._respond(400, {"error": f"body is not JSON: {exc}"})
            return

        claim_id = request.get("claimId")
        if not claim_id or not isinstance(claim_id, str):
            self._respond(400, {"error": "claimId is required"})
            return
        since_ms = request.get("sinceMs")
        if since_ms is None:
            since_ms = int((time.time() - DEFAULT_WINDOW_S) * 1000)
        emit_spans = request.get("emit", True)
        if not isinstance(emit_spans, bool):
            # Not coerced: `"emit": "false"` is truthy in Python and would
            # silently emit, which is the opposite of what the caller asked
            # for and the one mistake this flag exists to prevent.
            self._respond(400, {"error": "emit must be a boolean"})
            return

        try:
            self._respond(
                200,
                score_claim(
                    self.client, claim_id, int(since_ms), emit_spans=emit_spans
                ),
            )
        except OpenSearchError as exc:
            # 502, not 500, and never a 200 carrying five compliant scores:
            # "I could not look" must never be recorded as "there was nothing
            # to find". That is the same discriminator rule this repo's
            # cluster-dependent tests hold themselves to.
            logger.exception("claim %s: could not be scored", claim_id)
            self._respond(502, {"error": str(exc)})
        except TrajectoryUnscorable as exc:  # pragma: no cover - defense in
            # depth, not the designed path: `score_claim` -> `scores_for`
            # already catches this per-scorer so a contract-version
            # divergence never reaches here today. Caught anyway, and
            # answered 502 rather than 500, so that IF it ever does reach
            # this handler -- a future scorer that calls `contract_level_at`
            # outside `scores_for`'s try/except, say -- it gets the same "I
            # could not look, not a bug in this service" discriminator
            # `OpenSearchError` gets, matching what
            # `contract.TrajectoryUnscorable`'s own docstring already
            # claims, rather than falling through to the bare
            # `except Exception` below and answering 500.
            logger.exception("claim %s: could not be scored", claim_id)
            self._respond(502, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - genuine bug path
            logger.exception("claim %s: scoring failed", claim_id)
            self._respond(500, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("NORTHSTAR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    emit.configure()
    Handler.client = OpenSearchClient()
    logger.info(
        "eval-scorer listening on %d, reading %s",
        PORT, Handler.client.base_url,
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
