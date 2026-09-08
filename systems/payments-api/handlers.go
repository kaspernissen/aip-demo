package main

import (
	"encoding/json"
	"errors"
	"net/http"

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
			payment, err := s.execute(r.PathValue("paymentId"))
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
