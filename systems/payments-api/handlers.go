package main

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"strings"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/baggage"
	"go.opentelemetry.io/otel/trace"
)

type proposeRequest struct {
	ClaimID  string  `json:"claimId"`
	Amount   float64 `json:"amount"`
	Currency string  `json:"currency"`
}

// executeRequest is the confirmation of intent that accompanies an execution:
// the caller states the amount and currency it believes it is paying, and this
// service -- which holds the proposal -- checks that the instruction matches
// what was authorised. This is the thing real payment rails do. The executing
// party does not resolve an opaque reference and pay whatever it now resolves
// to; it verifies that the instruction in front of it is the one that was
// authorised, and refuses if the two disagree.
//
// Amount is a pointer so that "absent" is distinguishable from "0.00". A
// missing amount is a rejected request, not a zero-euro payment.
type executeRequest struct {
	Amount   *float64 `json:"amount"`
	Currency string   `json:"currency"`
}

// amountEpsilon is the tolerance for the confirmation check, in units of the
// currency: half a cent.
//
// The justification is a domain one, not a numerical one. Half a cent is below
// the smallest amount anyone can actually be paid, so this tolerance cannot
// mask a disagreement about money: any two amounts differing by something a
// payee could actually receive are still refused, and handlers_test.go pins
// that a one-cent difference is a 409.
//
// What it buys is that this stays a comparison of *quantities of money* rather
// than an identity test on a bit pattern. The amount is a float that has been
// through arithmetic somewhere upstream -- a sum of line items, a proportion
// of an assessed total, an excess subtracted -- and two routes to the same
// figure can land an ULP apart. Refusing a payment over the seventeenth
// decimal place would be a bug, not rigour.
//
// Note what this epsilon is deliberately *not* claimed to fix: the JSON hops
// themselves. Both Go's and Python's encoders emit the shortest
// round-tripping representation of a float64, so an amount survives
// encode/decode bit-exactly in both directions -- measured over this demo's
// own figures, including 6200.0 and 6200.01. An earlier version of this
// comment asserted a ~1e-12 round-trip error at EUR 6,200; that was false, and
// the tolerance does not rest on it (fix round 1, F2).
const amountEpsilon = 0.005

// confirmsPayment reports whether a stated amount and currency match the
// proposal that was recorded.
func confirmsPayment(payment Payment, statedAmount float64, statedCurrency string) bool {
	if math.Abs(payment.Amount-statedAmount) > amountEpsilon {
		return false
	}
	// Currency codes are ISO-4217 and case-insensitive in practice; the guard
	// is against paying the right number in the wrong unit, not against
	// spelling. An omitted currency defaults to EUR exactly as it does on
	// propose, so the two ends of the check agree on the same default.
	return strings.EqualFold(payment.Currency, statedCurrency)
}

// northstarRefusalCause names WHICH layer refused an execution.
//
// M2.2 rulings R23/R18. After M2.2 Task 4 there are two refusal mechanisms at
// two different layers on the same request path:
//
//  1. the Policy Decision Service, which denies `execute_payment` above the
//     agreed limit before the call ever reaches payments-mcp; and
//  2. this handler, which refuses an execution whose stated amount does not
//     confirm the recorded proposal, or one that has already happened.
//
// Both of this service's causes surface as `http.response.status_code=409` and
// nothing else. A 4xx leaves an OTel server span `Unset` (that is the HTTP
// convention: a 4xx is the client's problem, not the server's), so the span
// carries no status to distinguish them either. The consequence is a hole
// exactly where this demo's thesis lives: a trace could show a refusal and
// still not answer "which layer refused this, and why?" -- and answering that
// from a trace is the whole claim.
//
// So: ONE attribute, on the server span, naming the cause. It is
// `northstar.`-prefixed because it is ours to define (no OpenTelemetry
// semantic convention covers a payment refusal) and because that prefix is
// this repo's standing signal for "we invented this name", as opposed to the
// `http.*`, `gen_ai.*` and `mcp.*` attributes the frameworks emit. It sits
// beside `northstar.claim.id`, the only other attribute this service defines.
//
// What it deliberately is NOT: a rename or a supplement of anything
// agentgateway or the PDS emits. The PDS's own refusal is already legible in
// its `policy.decision` span (`policy.decision=deny` plus `policy.rule`), and
// the gateway's spans are recorded exactly as they appear. This attribute is
// only about the two causes that belong to this service, which is our own
// code, and it is set nowhere else.
//
// The value set is closed and low-cardinality -- two constants below -- so it
// is safe to group by. Absence is meaningful: a request that was not refused
// by this service carries no such attribute at all, rather than an "ok" or an
// empty string. That is asserted in handlers_test.go, because an attribute
// that is always present says nothing.
const northstarRefusalCause = "northstar.payment.refusal_cause"

const (
	// The stated amount or currency did not confirm the recorded proposal.
	// Nothing was mutated: the payment is still `proposed`.
	refusalAmountMismatch = "amount_mismatch"
	// The payment had already been executed. Also unmutated -- but for the
	// opposite reason: the money has already moved, once.
	refusalAlreadyExecuted = "already_executed"
)

// refuse records the cause on the current server span and writes the 409.
//
// The two are done together, in one function, deliberately: they are two
// halves of one fact, and every previous "fix one site, miss its sibling"
// defect in this project came from having two places to keep in step. A new
// 409 cause added here cannot forget the attribute, because there is no way
// to write the response without it.
func refuse(ctx context.Context, w http.ResponseWriter, cause, message string) {
	trace.SpanFromContext(ctx).SetAttributes(
		attribute.String(northstarRefusalCause, cause),
	)
	writeError(w, http.StatusConflict, message)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

// withClaimID annotates the current span with the claim id carried in W3C
// baggage. The Go SDK has no baggage span processor, so our own middleware
// does the same job explicitly. (N5, final-review.md: this doc comment used
// to name a function called claimIDFromBaggage, which this file has never
// had -- fixed to name the function it actually documents.)
func withClaimID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if member := baggage.FromContext(r.Context()).Member("northstar.claim.id"); member.Value() != "" {
			trace.SpanFromContext(r.Context()).SetAttributes(
				attribute.String("northstar.claim.id", member.Value()),
			)
		}
		next.ServeHTTP(w, r)
	})
}

// newRouter wires the HTTP surface. Each route is wrapped individually with
// otelhttp so the span name is the route template rather than the raw path,
// keeping span names low-cardinality per HTTP semantic conventions.
func newRouter(s *store) http.Handler {
	mux := http.NewServeMux()

	handle := func(pattern, spanName string, h http.HandlerFunc) {
		mux.Handle(pattern, otelhttp.NewHandler(withClaimID(h), spanName))
	}

	// N6 (final-review.md): the readiness probe added in deploy/k8s/*.yaml
	// (S6) polls this endpoint every few seconds, and it used to be wrapped
	// in otelhttp like every other route -- 162 GET /healthz traces against
	// 44 POST /payments/propose traces in one measured hour, ~79% probe
	// noise in Jaeger's own view of this service, plus every one of those
	// spans printed in full by the debug exporter's `verbosity: normal`.
	// This route carries no domain telemetry of its own (no claim id, no
	// business outcome), so it is wired directly, with no otelhttp span and
	// no withClaimID wrapper -- the fix for probe-noise pollution is not
	// tracing the probe, not filtering it out downstream after the fact.
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})

	handle("POST /payments/propose", "POST /payments/propose", func(w http.ResponseWriter, r *http.Request) {
		var req proposeRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeError(w, http.StatusBadRequest, "malformed JSON body")
			return
		}
		if req.ClaimID == "" {
			writeError(w, http.StatusBadRequest, "claimId is required")
			return
		}
		if req.Amount < 0 {
			writeError(w, http.StatusBadRequest, "amount must not be negative")
			return
		}
		if req.Currency == "" {
			req.Currency = "EUR"
		}
		writeJSON(w, http.StatusCreated, s.propose(req.ClaimID, req.Amount, req.Currency))
	})

	handle("POST /payments/{paymentId}/execute", "POST /payments/{paymentId}/execute",
		func(w http.ResponseWriter, r *http.Request) {
			// A body that is absent or unparseable is rejected, not defaulted.
			// A payment execution that cannot confirm what it is paying must
			// not proceed: falling back to "no body means whatever the
			// proposal says" would restore exactly the un-confirmable execute
			// call this check exists to remove. 400, matching how propose
			// already treats a malformed body -- this is a badly formed
			// request, not a conflict with recorded state.
			//
			// http.Request.Body is never nil server-side, so an empty body
			// reaches Decode and comes back as io.EOF -- the same rejection
			// path as malformed JSON, which is the intent.
			var req executeRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				writeError(w, http.StatusBadRequest,
					"execution must state the amount it is paying")
				return
			}
			if req.Amount == nil {
				writeError(w, http.StatusBadRequest, "amount is required")
				return
			}
			if req.Currency == "" {
				req.Currency = "EUR"
			}

			paymentID := r.PathValue("paymentId")

			// Read the proposal and confirm the instruction against it before
			// moving any money. Reading here and executing below is safe
			// without holding a lock across both: a record's amount and
			// currency are set once by propose and never mutated, and the
			// only state transition -- to executed -- is guarded inside
			// s.execute, so a concurrent duplicate still gets 409 there.
			proposal, err := s.get(paymentID)
			if errors.Is(err, ErrNotFound) {
				writeError(w, http.StatusNotFound, "payment not found")
				return
			}
			if !confirmsPayment(proposal, *req.Amount, req.Currency) {
				// 409, the status this handler already uses for "the recorded
				// state will not allow this execution", and one of the two
				// statuses settlement-agent's mcp_tools.py documents as
				// surfacing meaningfully through the MCP layer. Nothing is
				// mutated on this path: the payment stays proposed.
				//
				// `refuse` also records WHICH of this service's two 409
				// causes this is (rulings R23/R18). The status code alone
				// cannot tell this apart from the already-executed case
				// below, and a 4xx leaves the span Unset, so without the
				// attribute a trace shows a refusal with no cause.
				refuse(r.Context(), w, refusalAmountMismatch,
					"stated amount does not match the proposal")
				return
			}

			payment, err := s.execute(paymentID)
			switch {
			case errors.Is(err, ErrNotFound):
				writeError(w, http.StatusNotFound, "payment not found")
			case errors.Is(err, ErrExecuted):
				// The other 409, and the reason the attribute above needs a
				// second value rather than a boolean: this refusal means the
				// money HAS moved, once, which is the opposite fact from an
				// amount mismatch, where nothing moved at all. Reading a bare
				// 409 as "the payment did not happen" is right above and
				// wrong here, and nothing in the response distinguishes them.
				refuse(r.Context(), w, refusalAlreadyExecuted,
					"payment already executed")
			case err != nil:
				writeError(w, http.StatusInternalServerError, "unexpected error")
			default:
				writeJSON(w, http.StatusOK, payment)
			}
		})

	handle("GET /payments/{paymentId}", "GET /payments/{paymentId}",
		func(w http.ResponseWriter, r *http.Request) {
			payment, err := s.get(r.PathValue("paymentId"))
			if errors.Is(err, ErrNotFound) {
				writeError(w, http.StatusNotFound, "payment not found")
				return
			}
			writeJSON(w, http.StatusOK, payment)
		})

	return mux
}
