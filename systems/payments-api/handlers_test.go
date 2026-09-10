package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func newTestServer() http.Handler {
	return newRouter(newStore())
}

func proposePayment(t *testing.T, srv http.Handler, claimID string, amount float64) Payment {
	t.Helper()
	body, _ := json.Marshal(map[string]any{
		"claimId":  claimID,
		"amount":   amount,
		"currency": "EUR",
	})
	req := httptest.NewRequest(http.MethodPost, "/payments/propose", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("propose: got status %d, want 201 (body: %s)", rec.Code, rec.Body.String())
	}
	var payment Payment
	if err := json.Unmarshal(rec.Body.Bytes(), &payment); err != nil {
		t.Fatalf("propose: decoding response: %v", err)
	}
	return payment
}

// executePayment posts a confirmation-of-intent body stating what the caller
// believes it is paying, which is what every real execute call now carries.
func executePayment(srv http.Handler, paymentID string, body any) *httptest.ResponseRecorder {
	var reader *bytes.Reader
	if body == nil {
		reader = bytes.NewReader(nil)
	} else {
		encoded, _ := json.Marshal(body)
		reader = bytes.NewReader(encoded)
	}
	req := httptest.NewRequest(http.MethodPost, "/payments/"+paymentID+"/execute", reader)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	return rec
}

func getPayment(t *testing.T, srv http.Handler, paymentID string) Payment {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/payments/"+paymentID, nil)
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("get: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	var payment Payment
	if err := json.Unmarshal(rec.Body.Bytes(), &payment); err != nil {
		t.Fatalf("get: decoding response: %v", err)
	}
	return payment
}

func TestProposeCreatesPaymentInProposedState(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	if payment.Status != StatusProposed {
		t.Errorf("got status %q, want %q", payment.Status, StatusProposed)
	}
	if payment.ClaimID != "CLM-1842" {
		t.Errorf("got claimId %q, want %q", payment.ClaimID, "CLM-1842")
	}
	if payment.Amount != 6200 {
		t.Errorf("got amount %v, want 6200", payment.Amount)
	}
	if payment.PaymentID == "" {
		t.Error("paymentId must not be empty")
	}
}

func TestProposeRejectsNegativeAmount(t *testing.T) {
	srv := newTestServer()
	body, _ := json.Marshal(map[string]any{"claimId": "CLM-1842", "amount": -1, "currency": "EUR"})
	req := httptest.NewRequest(http.MethodPost, "/payments/propose", bytes.NewReader(body))
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("got status %d, want 400", rec.Code)
	}
}

func TestExecuteMovesPaymentToExecuted(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 6200.0, "currency": "EUR"})

	if rec.Code != http.StatusOK {
		t.Fatalf("execute: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	var executed Payment
	if err := json.Unmarshal(rec.Body.Bytes(), &executed); err != nil {
		t.Fatalf("execute: decoding response: %v", err)
	}
	if executed.Status != StatusExecuted {
		t.Errorf("got status %q, want %q", executed.Status, StatusExecuted)
	}
}

func TestExecuteIsNotIdempotent(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)
	body := map[string]any{"amount": 6200.0, "currency": "EUR"}

	for i := 0; i < 2; i++ {
		rec := executePayment(srv, payment.PaymentID, body)
		if i == 0 && rec.Code != http.StatusOK {
			t.Fatalf("first execute: got %d, want 200", rec.Code)
		}
		if i == 1 && rec.Code != http.StatusConflict {
			t.Errorf("second execute: got %d, want 409", rec.Code)
		}
	}
}

func TestExecuteUnknownPaymentReturns404(t *testing.T) {
	srv := newTestServer()
	rec := executePayment(srv, "PAY-999999",
		map[string]any{"amount": 6200.0, "currency": "EUR"})

	if rec.Code != http.StatusNotFound {
		t.Errorf("got status %d, want 404", rec.Code)
	}
}

// ---------------------------------------------------------------------------
// Confirmation of intent.
//
// The execute call states the amount it believes it is paying, and this
// service -- the party holding the proposal -- refuses if the instruction and
// the authorisation disagree. That is what payment rails do, and it is also
// what makes the execute step judgeable at a choke point at all: an API that
// takes only an opaque id gives nobody anything to check.
// ---------------------------------------------------------------------------

func TestExecuteWithMatchingAmountSucceeds(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	// 6200.0, not 6200: the amount reaches this handler as a JSON float, and
	// this is the shape a real settlement sends.
	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 6200.0, "currency": "EUR"})

	if rec.Code != http.StatusOK {
		t.Fatalf("got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusExecuted {
		t.Errorf("got status %q, want %q", got.Status, StatusExecuted)
	}
}

func TestExecuteWithMismatchedAmountIsRejectedAndDoesNotExecute(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 4000.0, "currency": "EUR"})

	if rec.Code != http.StatusConflict {
		t.Errorf("got status %d, want 409 (body: %s)", rec.Code, rec.Body.String())
	}
	// The important half: a refused execution must leave the money where it
	// is. A 409 that still marked the payment executed would be worse than
	// no check at all.
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusProposed {
		t.Errorf("payment status is %q after a rejected execute, want %q",
			got.Status, StatusProposed)
	}
}

func TestExecuteWithMismatchedCurrencyIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	// The right number in the wrong unit is not the authorised payment.
	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 6200.0, "currency": "USD"})

	if rec.Code != http.StatusConflict {
		t.Errorf("got status %d, want 409 (body: %s)", rec.Code, rec.Body.String())
	}
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusProposed {
		t.Errorf("payment status is %q after a rejected execute, want %q",
			got.Status, StatusProposed)
	}
}

func TestExecuteWithMissingBodyIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	// An execution that cannot confirm what it is paying does not proceed.
	// There is deliberately no "no body means whatever the proposal says"
	// fallback: that would restore the un-confirmable execute call.
	rec := executePayment(srv, payment.PaymentID, nil)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("got status %d, want 400 (body: %s)", rec.Code, rec.Body.String())
	}
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusProposed {
		t.Errorf("payment status is %q after a rejected execute, want %q",
			got.Status, StatusProposed)
	}
}

func TestExecuteWithBodyOmittingTheAmountIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	// A well-formed body with no amount must not be read as a zero-euro
	// payment that happens to mismatch -- it is a missing confirmation.
	rec := executePayment(srv, payment.PaymentID, map[string]any{"currency": "EUR"})

	if rec.Code != http.StatusBadRequest {
		t.Errorf("got status %d, want 400 (body: %s)", rec.Code, rec.Body.String())
	}
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusProposed {
		t.Errorf("payment status is %q after a rejected execute, want %q",
			got.Status, StatusProposed)
	}
}

// decodeFloat parses a float64 out of JSON at runtime.
//
// This exists to defeat constant folding, and that is not incidental -- it is
// what makes the test below able to fail at all. Go evaluates constant
// expressions in arbitrary precision and rounds once at the end, so the
// literal `0.1 + 0.2` is a constant expression whose float64 result is
// *bit-identical* to `0.3` (verified: both are 0x3fd3333333333333). Written
// that way, this test passes just as happily against an exact `==` comparison
// and asserts nothing about the tolerance -- which is exactly the defect fix
// round 1 F1 found here. Values that arrive through a runtime decode cannot be
// folded, and `0.1 + 0.2` computed on them is 0x3fd3333333333334: one ULP
// away from 0.3, and genuinely `!=` it.
func decodeFloat(t *testing.T, jsonNumber string) float64 {
	t.Helper()
	var v float64
	if err := json.Unmarshal([]byte(jsonNumber), &v); err != nil {
		t.Fatalf("decoding %s: %v", jsonNumber, err)
	}
	return v
}

func TestExecuteAmountIsComparedWithATolerance(t *testing.T) {
	srv := newTestServer()

	// Two values that are unequal in float64 but agree to far better than half
	// a cent. The confirmation check must accept them: it is a tolerance on a
	// quantity of money, not an identity test on a bit pattern.
	proposed := decodeFloat(t, "0.1") + decodeFloat(t, "0.2")
	const stated = 0.3

	// Guard, so this test can never quietly go vacuous again. If a future
	// compiler or a rewrite makes these two bit-equal, the test stops proving
	// anything about the epsilon -- and it must say so rather than pass.
	if proposed == stated {
		t.Fatalf("this test is vacuous: proposed %v is bit-identical to %v, so "+
			"an exact == comparison would satisfy it too", proposed, stated)
	}

	payment := proposePayment(t, srv, "CLM-1842", proposed)
	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": stated, "currency": "EUR"})

	if rec.Code != http.StatusOK {
		t.Fatalf("a sub-ULP difference was treated as a mismatch: got %d, want 200 "+
			"(proposed %v, stated %v, body: %s)",
			rec.Code, proposed, stated, rec.Body.String())
	}
}

func TestExecuteToleranceIsSmallerThanTheSmallestPayableDifference(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	// One cent is a real disagreement about money and must be refused. This
	// pins the epsilon below the smallest amount anyone can be paid, so
	// widening it into "close enough is fine" fails here.
	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 6200.01, "currency": "EUR"})

	if rec.Code != http.StatusConflict {
		t.Errorf("a one-cent difference was accepted: got %d, want 409", rec.Code)
	}
}

func TestExecuteAcceptsAnOmittedCurrencyAsEUR(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	// Propose defaults an absent currency to EUR; the confirmation check uses
	// the same default, so the two ends agree.
	rec := executePayment(srv, payment.PaymentID, map[string]any{"amount": 6200.0})

	if rec.Code != http.StatusOK {
		t.Errorf("got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
}

func TestGetPaymentReturnsRecord(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1901", 800)

	req := httptest.NewRequest(http.MethodGet, "/payments/"+payment.PaymentID, nil)
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("got status %d, want 200", rec.Code)
	}
	var got Payment
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decoding response: %v", err)
	}
	if got.PaymentID != payment.PaymentID {
		t.Errorf("got paymentId %q, want %q", got.PaymentID, payment.PaymentID)
	}
}

func TestPaymentIDsAreDeterministicAndSequential(t *testing.T) {
	srv := newTestServer()
	first := proposePayment(t, srv, "CLM-1842", 6200)
	second := proposePayment(t, srv, "CLM-1901", 800)

	if first.PaymentID != "PAY-000001" {
		t.Errorf("got first paymentId %q, want PAY-000001", first.PaymentID)
	}
	if second.PaymentID != "PAY-000002" {
		t.Errorf("got second paymentId %q, want PAY-000002", second.PaymentID)
	}
}

// ---------------------------------------------------------------------------
// The refusal cause on the span (M2.2 rulings R23/R18).
//
// After M2.2 Task 4 there are two refusal mechanisms at two layers: the Policy
// Decision Service denies an over-limit `execute_payment` before it ever
// reaches this service, and this service refuses an execution that either
// fails to confirm the recorded amount or has already happened. All three
// refusals reach a reader of the trace as one thing -- a 409 -- and a 4xx
// leaves an OTel server span `Unset`, so the span status cannot separate them
// either. `northstar.payment.refusal_cause` is the one attribute that answers
// "which layer refused this, and why?" for the two causes that belong to this
// service.
//
// These tests read the attribute off a REAL recorded span rather than trusting
// the call site, because the two things that could break this are both
// invisible at the call site: an attribute set on a span that is not the one
// exported (the handler's own context vs. otelhttp's), and a handler wired
// without the otelhttp wrapper at all, where `trace.SpanFromContext` returns a
// no-op span that silently drops every attribute. Both would leave a
// source-reading assertion green.
// ---------------------------------------------------------------------------

// recordSpans installs a TracerProvider with an in-memory recorder as the
// global provider for the duration of one test, and returns the recorder.
//
// otelhttp.NewHandler resolves the global provider at request time, so this
// has to be the global one and not a local handle. The previous provider is
// restored on cleanup so the tests in this file stay order-independent.
func recordSpans(t *testing.T) *tracetest.SpanRecorder {
	t.Helper()
	recorder := tracetest.NewSpanRecorder()
	previous := otel.GetTracerProvider()
	otel.SetTracerProvider(sdktrace.NewTracerProvider(
		sdktrace.WithSpanProcessor(recorder),
	))
	t.Cleanup(func() { otel.SetTracerProvider(previous) })
	return recorder
}

// executeSpanAttributes returns the attributes of the recorded server span for
// the execute route.
//
// Selected by span name rather than by position: `propose` records a span too,
// and every test below proposes before it executes, so taking the first or the
// last recorded span would be reading a different request's span half the time.
func executeSpanAttributes(t *testing.T, recorder *tracetest.SpanRecorder) map[string]string {
	t.Helper()
	const name = "POST /payments/{paymentId}/execute"
	var found []sdktrace.ReadOnlySpan
	for _, span := range recorder.Ended() {
		if span.Name() == name {
			found = append(found, span)
		}
	}
	if len(found) != 1 {
		var names []string
		for _, span := range recorder.Ended() {
			names = append(names, span.Name())
		}
		t.Fatalf("expected exactly one %q span; recorded %v", name, names)
	}
	attributes := map[string]string{}
	for _, kv := range found[0].Attributes() {
		attributes[string(kv.Key)] = kv.Value.Emit()
	}
	return attributes
}

func TestAMismatchedAmountRecordsItsRefusalCauseOnTheSpan(t *testing.T) {
	recorder := recordSpans(t)
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 4000.0, "currency": "EUR"})
	if rec.Code != http.StatusConflict {
		t.Fatalf("got status %d, want 409 (body: %s)", rec.Code, rec.Body.String())
	}

	got := executeSpanAttributes(t, recorder)[northstarRefusalCause]
	if got != refusalAmountMismatch {
		t.Errorf("span attribute %s is %q, want %q -- without it this 409 is "+
			"indistinguishable in a trace from an already-executed payment, "+
			"and from the PDS's own denial",
			northstarRefusalCause, got, refusalAmountMismatch)
	}
}

func TestAnAlreadyExecutedPaymentRecordsItsRefusalCauseOnTheSpan(t *testing.T) {
	recorder := recordSpans(t)
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	body := map[string]any{"amount": 6200.0, "currency": "EUR"}
	if rec := executePayment(srv, payment.PaymentID, body); rec.Code != http.StatusOK {
		t.Fatalf("first execute: got status %d, want 200 (body: %s)",
			rec.Code, rec.Body.String())
	}
	// Fresh recorder for the second call, so `executeSpanAttributes` reads the
	// refused execution's span and not the successful one's.
	recorder = recordSpans(t)
	if rec := executePayment(srv, payment.PaymentID, body); rec.Code != http.StatusConflict {
		t.Fatalf("second execute: got status %d, want 409 (body: %s)",
			rec.Code, rec.Body.String())
	}

	got := executeSpanAttributes(t, recorder)[northstarRefusalCause]
	if got != refusalAlreadyExecuted {
		t.Errorf("span attribute %s is %q, want %q -- this refusal means the "+
			"money HAS moved, which is the opposite fact from a mismatch, and "+
			"the 409 alone says neither",
			northstarRefusalCause, got, refusalAlreadyExecuted)
	}
}

func TestTheTwoRefusalCausesAreDistinguishable(t *testing.T) {
	// The property both tests above exist to establish, stated once directly:
	// a single value would leave the trace exactly as unable to answer "which
	// layer refused this" as no attribute at all.
	if refusalAmountMismatch == refusalAlreadyExecuted {
		t.Fatalf("both 409 causes report %q, so the attribute distinguishes "+
			"nothing", refusalAmountMismatch)
	}
}

func TestASuccessfulExecutionRecordsNoRefusalCause(t *testing.T) {
	// Absence is the signal for "not refused here". An attribute present on
	// every execution -- say an "ok" or an empty string -- would be indexed,
	// grouped and charted while distinguishing nothing, and would make
	// "refused by payments-api" unanswerable by the attribute's presence.
	recorder := recordSpans(t)
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 6200.0, "currency": "EUR"})
	if rec.Code != http.StatusOK {
		t.Fatalf("got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}

	if got, present := executeSpanAttributes(t, recorder)[northstarRefusalCause]; present {
		t.Errorf("a successful execution recorded %s=%q; the attribute must be "+
			"absent when this service did not refuse", northstarRefusalCause, got)
	}
}

func TestTheRefusalCauseAttributeIsNorthstarPrefixed(t *testing.T) {
	// House convention, and the reason it is a convention: a reader must be
	// able to tell at a glance which attributes this project invented and
	// which the frameworks emit. No OpenTelemetry semantic convention covers a
	// payment refusal, so this name is ours -- and naming it `payment.*` or
	// `http.*` would claim a shared vocabulary that does not exist.
	if !strings.HasPrefix(northstarRefusalCause, "northstar.") {
		t.Errorf("attribute %q must carry the northstar. prefix", northstarRefusalCause)
	}
}
