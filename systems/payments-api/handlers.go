package main

import (
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
// The amount crosses two JSON hops as a float, so an exact `==` here is a
// latent failure rather than a strictness. Half a cent is below the smallest
// amount anyone can actually be paid, so it can never mask a real
// disagreement, while comfortably absorbing float representation noise (at
// €6,200 the float64 round-trip error is on the order of 1e-12).
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
				writeError(w, http.StatusConflict,
					"stated amount does not match the proposal")
				return
			}

			payment, err := s.execute(paymentID)
			switch {
			case errors.Is(err, ErrNotFound):
				writeError(w, http.StatusNotFound, "payment not found")
			case errors.Is(err, ErrExecuted):
				writeError(w, http.StatusConflict, "payment already executed")
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
