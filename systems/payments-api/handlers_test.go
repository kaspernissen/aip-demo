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
// and asserts nothing about the tolerance. Values that arrive through a
// runtime decode cannot be folded, and `0.1 + 0.2` computed on them is
// 0x3fd3333333333334: one ULP away from 0.3, and genuinely `!=` it.
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
// The refusal cause on the span.
//
// There are two refusal mechanisms at two layers: the Policy
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

// ---------------------------------------------------------------------------
// The refusal record in the ledger.
//
// This is the ledger side of "a human reviewer can see, and later approve, a
// refused payout." `refused` is terminal-until-approved, not a new state
// machine: the execute path stays exactly as guarded as it always was, so a
// refused payment must still be executable through it. These tests pin the
// request/response shape as a contract for its consumers -- the settlement
// agent and the reviewer UI -- not a guess.
// ---------------------------------------------------------------------------

func refusePayment(srv http.Handler, paymentID string, body any) *httptest.ResponseRecorder {
	var reader *bytes.Reader
	if body == nil {
		reader = bytes.NewReader(nil)
	} else {
		encoded, _ := json.Marshal(body)
		reader = bytes.NewReader(encoded)
	}
	req := httptest.NewRequest(http.MethodPost, "/payments/"+paymentID+"/refuse", reader)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	return rec
}

// refusalBody is the request/response shape its consumers are written
// against: rule, limit and requested come from the PDS's own denial, and
// refusedTraceId is the id of the trace that recorded it. Values are literals
// throughout these tests -- no arithmetic on them -- so there is no constant-
// folding hazard to guard against here.
//
// refusedSpanId joins refusedTraceId. Measured against the
// installed opentelemetry-sdk (1.44.0): a Link built from a SpanContext with
// span_id=0 and no attributes is silently dropped by
// Span._new_links'/_is_valid_link's is_valid check before it ever reaches an
// exporter -- so linking the approved-execution trace back to the refusal
// needs a real span id, not a placeholder. This is the ledger's only carrier
// for it: settlement.py reads it off the current span when it records the
// refusal, and execute_approved reads it back to build a real link.
func refusalBody(rule string, limit, requested float64, narrative, traceID, spanID string) map[string]any {
	return map[string]any{
		"rule":           rule,
		"limit":          limit,
		"requested":      requested,
		"narrative":      narrative,
		"refusedTraceId": traceID,
		"refusedSpanId":  spanID,
	}
}

func listPayments(t *testing.T, srv http.Handler, status string) []Payment {
	t.Helper()
	path := "/payments"
	if status != "" {
		path += "?status=" + status
	}
	req := httptest.NewRequest(http.MethodGet, path, nil)
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("list: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	var payments []Payment
	if err := json.Unmarshal(rec.Body.Bytes(), &payments); err != nil {
		t.Fatalf("list: decoding response: %v", err)
	}
	return payments
}

func TestRefusingAProposedPaymentSetsRefusedAndStoresEveryField(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := refusePayment(srv, payment.PaymentID,
		refusalBody("execute_limit", 6000.0, 6200.0,
			"The requested amount exceeds the agreed settlement limit for this identity.",
			"331edba8cb21223e9de2f244360c42ef", "b7997877797c1b27"))

	if rec.Code != http.StatusOK {
		t.Fatalf("refuse: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	var refused Payment
	if err := json.Unmarshal(rec.Body.Bytes(), &refused); err != nil {
		t.Fatalf("refuse: decoding response: %v", err)
	}
	if refused.Status != StatusRefused {
		t.Fatalf("got status %q, want %q", refused.Status, StatusRefused)
	}
	if refused.Refusal == nil {
		t.Fatalf("refusal record is nil on the response")
	}
	if refused.Refusal.Rule != "execute_limit" {
		t.Errorf("got rule %q, want %q", refused.Refusal.Rule, "execute_limit")
	}
	if refused.Refusal.Limit != 6000.0 {
		t.Errorf("got limit %v, want 6000.0", refused.Refusal.Limit)
	}
	if refused.Refusal.Requested != 6200.0 {
		t.Errorf("got requested %v, want 6200.0", refused.Refusal.Requested)
	}
	if refused.Refusal.Narrative != "The requested amount exceeds the agreed settlement limit for this identity." {
		t.Errorf("narrative was not stored verbatim: %q", refused.Refusal.Narrative)
	}
	if refused.Refusal.RefusedTraceID != "331edba8cb21223e9de2f244360c42ef" {
		t.Errorf("got refusedTraceId %q, want %q", refused.Refusal.RefusedTraceID,
			"331edba8cb21223e9de2f244360c42ef")
	}
	if refused.Refusal.RefusedSpanID != "b7997877797c1b27" {
		t.Errorf("got refusedSpanId %q, want %q", refused.Refusal.RefusedSpanID,
			"b7997877797c1b27")
	}
	if refused.Refusal.RefusedAt == "" {
		t.Errorf("refusedAt must be stamped by the ledger, got empty string")
	}

	// And it is persisted, not just echoed in the response.
	got := getPayment(t, srv, payment.PaymentID)
	if got.Status != StatusRefused || got.Refusal == nil || got.Refusal.Rule != "execute_limit" {
		t.Errorf("refusal record did not persist: %+v", got)
	}
}

func TestRefusingAnExecutedPaymentIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)
	if rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 6200.0, "currency": "EUR"}); rec.Code != http.StatusOK {
		t.Fatalf("execute: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}

	rec := refusePayment(srv, payment.PaymentID,
		refusalBody("execute_limit", 6000.0, 6200.0, "narrative", "trace-id", "span-id"))

	if rec.Code != http.StatusConflict {
		t.Errorf("got status %d, want 409 (body: %s)", rec.Code, rec.Body.String())
	}
	// Same shape as the execute handler's own already-executed conflict: the
	// money has moved, so nothing about the record changes underneath it.
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusExecuted {
		t.Errorf("payment status is %q after a rejected refuse, want %q",
			got.Status, StatusExecuted)
	}
}

func TestRefusingAnUnknownPaymentReturns404(t *testing.T) {
	srv := newTestServer()
	rec := refusePayment(srv, "PAY-999999",
		refusalBody("execute_limit", 6000.0, 6200.0, "narrative", "trace-id", "span-id"))

	if rec.Code != http.StatusNotFound {
		t.Errorf("got status %d, want 404", rec.Code)
	}
}

func TestListPaymentsFiltersToRefusedStatus(t *testing.T) {
	srv := newTestServer()
	refusedPayment := proposePayment(t, srv, "CLM-1842", 6200)
	proposedPayment := proposePayment(t, srv, "CLM-1901", 800)
	executedPayment := proposePayment(t, srv, "CLM-2010", 500)

	if rec := refusePayment(srv, refusedPayment.PaymentID,
		refusalBody("execute_limit", 6000.0, 6200.0, "narrative", "trace-id", "span-id")); rec.Code != http.StatusOK {
		t.Fatalf("refuse: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	if rec := executePayment(srv, executedPayment.PaymentID,
		map[string]any{"amount": 500.0, "currency": "EUR"}); rec.Code != http.StatusOK {
		t.Fatalf("execute: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}

	got := listPayments(t, srv, "refused")
	if len(got) != 1 {
		t.Fatalf("got %d refused payments, want 1: %+v", len(got), got)
	}
	if got[0].PaymentID != refusedPayment.PaymentID {
		t.Errorf("got paymentId %q, want %q", got[0].PaymentID, refusedPayment.PaymentID)
	}

	// The other two statuses must not leak into the refused-only view.
	for _, p := range got {
		if p.PaymentID == proposedPayment.PaymentID || p.PaymentID == executedPayment.PaymentID {
			t.Errorf("status=refused list leaked a non-refused payment: %+v", p)
		}
	}
}

func TestARefusedPaymentCanStillBeExecutedAfterwards(t *testing.T) {
	// The design point this whole task turns on: `refused` is
	// terminal-until-approved, not a dead end. Nothing in this service closes
	// the execute path on a refusal -- the PDS is what guards it, and that
	// guard lives entirely outside payments-api. This test proves the ledger
	// side never forecloses the possibility of the same payment executing
	// later under a different, more senior, identity's approval.
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	if rec := refusePayment(srv, payment.PaymentID,
		refusalBody("execute_limit", 6000.0, 6200.0, "narrative", "trace-id", "span-id")); rec.Code != http.StatusOK {
		t.Fatalf("refuse: got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusRefused {
		t.Fatalf("payment did not reach refused: got %q", got.Status)
	}

	rec := executePayment(srv, payment.PaymentID,
		map[string]any{"amount": 6200.0, "currency": "EUR"})
	if rec.Code != http.StatusOK {
		t.Fatalf("execute after refuse: got status %d, want 200 (body: %s)",
			rec.Code, rec.Body.String())
	}
	var executed Payment
	if err := json.Unmarshal(rec.Body.Bytes(), &executed); err != nil {
		t.Fatalf("decoding response: %v", err)
	}
	if executed.Status != StatusExecuted {
		t.Errorf("got status %q, want %q", executed.Status, StatusExecuted)
	}
}

func TestRefuseWithMissingRuleIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := refusePayment(srv, payment.PaymentID,
		map[string]any{"limit": 6000.0, "requested": 6200.0, "narrative": "narrative"})

	if rec.Code != http.StatusBadRequest {
		t.Errorf("got status %d, want 400 (body: %s)", rec.Code, rec.Body.String())
	}
	if got := getPayment(t, srv, payment.PaymentID); got.Status != StatusProposed {
		t.Errorf("payment status is %q after a rejected refuse, want %q",
			got.Status, StatusProposed)
	}
}

func TestRefuseWithMalformedBodyIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	req := httptest.NewRequest(http.MethodPost, "/payments/"+payment.PaymentID+"/refuse",
		strings.NewReader("{not json"))
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Errorf("got status %d, want 400 (body: %s)", rec.Code, rec.Body.String())
	}
}

// ---------------------------------------------------------------------------
// POST /payments/{id}/refuse must guard against overwrite: propose CREATES a
// record; refuse MUTATES the one a human uses to release money. Validating
// only that `rule` is non-empty is not enough -- a second refuse call could
// then silently replace an existing refusal's record, including its
// refusedTraceId/refusedSpanId (the exact link a reviewer follows into a
// trace), and any caller could refuse for any `requested` amount, whether or
// not it matched what was actually proposed.
// ---------------------------------------------------------------------------

func TestRefusingAnAlreadyRefusedPaymentIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	first := refusePayment(srv, payment.PaymentID,
		refusalBody("execute_limit", 6000.0, 6200.0, "first narrative",
			"331edba8cb21223e9de2f244360c42ef", "b7997877797c1b27"))
	if first.Code != http.StatusOK {
		t.Fatalf("first refuse: got status %d, want 200 (body: %s)", first.Code, first.Body.String())
	}

	second := refusePayment(srv, payment.PaymentID,
		refusalBody("execute_limit", 6000.0, 6200.0, "attacker-authored narrative",
			"attacker-trace-id", "attacker-span-id"))
	if second.Code != http.StatusConflict {
		t.Errorf("second refuse: got status %d, want 409 (body: %s)", second.Code, second.Body.String())
	}

	// The original refusal record must survive untouched -- this is the
	// exact hole the review named: an unconditional refuse could overwrite
	// refusedTraceId/refusedSpanId, repointing the audit link at a trace of
	// the caller's choosing.
	got := getPayment(t, srv, payment.PaymentID)
	if got.Refusal == nil {
		t.Fatalf("refusal record vanished after a rejected second refuse")
	}
	if got.Refusal.Narrative != "first narrative" {
		t.Errorf("refusal record was overwritten: narrative is %q, want %q",
			got.Refusal.Narrative, "first narrative")
	}
	if got.Refusal.RefusedTraceID != "331edba8cb21223e9de2f244360c42ef" {
		t.Errorf("refusedTraceId was overwritten: got %q", got.Refusal.RefusedTraceID)
	}
	if got.Refusal.RefusedSpanID != "b7997877797c1b27" {
		t.Errorf("refusedSpanId was overwritten: got %q", got.Refusal.RefusedSpanID)
	}
}

func TestRefusingWithARequestedAmountThatDoesNotMatchTheProposalIsRejected(t *testing.T) {
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := refusePayment(srv, payment.PaymentID,
		refusalBody("execute_limit", 6000.0, 9999.0, "narrative", "trace-id", "span-id"))

	if rec.Code != http.StatusConflict {
		t.Errorf("got status %d, want 409 (body: %s)", rec.Code, rec.Body.String())
	}
	got := getPayment(t, srv, payment.PaymentID)
	if got.Status != StatusProposed {
		t.Errorf("payment status is %q after a rejected refuse, want %q",
			got.Status, StatusProposed)
	}
	if got.Refusal != nil {
		t.Errorf("a rejected refuse must not write a refusal record: %+v", got.Refusal)
	}
}

func TestRefusingWithARequestedAmountWithinEpsilonIsAccepted(t *testing.T) {
	// The same half-cent tolerance the execute route already applies
	// (confirmsPayment/amountEpsilon), applied here too: a requested amount
	// that has been through arithmetic upstream may land a fraction of a
	// cent off without being a real disagreement about money.
	srv := newTestServer()
	payment := proposePayment(t, srv, "CLM-1842", 6200)

	rec := refusePayment(srv, payment.PaymentID,
		refusalBody("execute_limit", 6000.0, 6200.001, "narrative", "trace-id", "span-id"))

	if rec.Code != http.StatusOK {
		t.Fatalf("got status %d, want 200 (body: %s)", rec.Code, rec.Body.String())
	}
}

func TestListPaymentsWithNoStatusReturnsEveryPayment(t *testing.T) {
	srv := newTestServer()
	proposePayment(t, srv, "CLM-1842", 6200)
	proposePayment(t, srv, "CLM-1901", 800)

	got := listPayments(t, srv, "")
	if len(got) != 2 {
		t.Fatalf("got %d payments, want 2: %+v", len(got), got)
	}
}
