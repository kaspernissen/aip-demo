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

	req := httptest.NewRequest(http.MethodPost, "/payments/"+payment.PaymentID+"/execute", nil)
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)

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

	for i := 0; i < 2; i++ {
		req := httptest.NewRequest(http.MethodPost, "/payments/"+payment.PaymentID+"/execute", nil)
		rec := httptest.NewRecorder()
		srv.ServeHTTP(rec, req)
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
	req := httptest.NewRequest(http.MethodPost, "/payments/PAY-999999/execute", nil)
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Errorf("got status %d, want 404", rec.Code)
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
