package main

import (
	"errors"
	"fmt"
	"sync"
)

// Payment status values. Kept as strings so the JSON contract in
// contracts/openapi/payments-api.yaml is obvious from the code.
const (
	StatusProposed = "proposed"
	StatusExecuted = "executed"
)

var (
	ErrNotFound = errors.New("payment not found")
	ErrExecuted = errors.New("payment already executed")
)

type Payment struct {
	PaymentID string  `json:"paymentId"`
	ClaimID   string  `json:"claimId"`
	Amount    float64 `json:"amount"`
	Currency  string  `json:"currency"`
	Status    string  `json:"status"`
}

// store holds payments in memory. Deterministic sequential IDs matter: the
// demo's numbers must be identical on every run.
type store struct {
	mu       sync.Mutex
	payments map[string]Payment
	nextID   int
}

func newStore() *store {
	return &store{payments: make(map[string]Payment), nextID: 1}
}

func (s *store) propose(claimID string, amount float64, currency string) Payment {
	s.mu.Lock()
	defer s.mu.Unlock()

	payment := Payment{
		PaymentID: fmt.Sprintf("PAY-%06d", s.nextID),
		ClaimID:   claimID,
		Amount:    amount,
		Currency:  currency,
		Status:    StatusProposed,
	}
	s.nextID++
	s.payments[payment.PaymentID] = payment
	return payment
}

func (s *store) execute(paymentID string) (Payment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	payment, ok := s.payments[paymentID]
	if !ok {
		return Payment{}, ErrNotFound
	}
	if payment.Status == StatusExecuted {
		return Payment{}, ErrExecuted
	}
	payment.Status = StatusExecuted
	s.payments[paymentID] = payment
	return payment, nil
}

func (s *store) get(paymentID string) (Payment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	payment, ok := s.payments[paymentID]
	if !ok {
		return Payment{}, ErrNotFound
	}
	return payment, nil
}
