package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
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
