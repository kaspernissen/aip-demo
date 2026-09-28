package main

import (
	"errors"
	"fmt"
	"sort"
	"sync"
)

// Payment status values. Kept as strings so the JSON contract in
// contracts/openapi/payments-api.yaml is obvious from the code.
//
// StatusRefused is terminal-until-approved, not a new state machine: it is
// reached only from StatusProposed (see store.refuse), and StatusExecuted
// stays reachable only through store.execute -- the same execute path the
// policy engine already guards. Recording a refusal never touches that path;
// it only adds a fact beside the payment for a human reviewer to read. See
// TestARefusedPaymentCanStillBeExecutedAfterwards in handlers_test.go, which
// proves store.execute still accepts a refused payment.
const (
	StatusProposed = "proposed"
	StatusExecuted = "executed"
	StatusRefused  = "refused"
)

var (
	ErrNotFound = errors.New("payment not found")
	ErrExecuted = errors.New("payment already executed")
	// ErrAlreadyRefused: a payment already in StatusRefused may not be
	// refused a second time -- replacing the record on a second refuse call
	// would let a caller repoint the audit trail. See store.refuse's own doc
	// comment for the full reasoning.
	ErrAlreadyRefused = errors.New("payment already refused")
)

type Payment struct {
	PaymentID string   `json:"paymentId"`
	ClaimID   string   `json:"claimId"`
	Amount    float64  `json:"amount"`
	Currency  string   `json:"currency"`
	Status    string   `json:"status"`
	Refusal   *refusal `json:"refusal,omitempty"`
}

// refusal is what the platform decided and what the claimant was told.
// Stored beside the payment so the reviewer sees the decision, not a guess.
//
// Rule, Limit, Requested and RefusedTraceID are ledger facts: the Policy
// Decision Service's own denial, and the trace that recorded it -- this
// service never invents or recomputes any of them, it only carries what the
// settlement agent reports. RefusedAt is this ledger's own fact too, stamped
// by store.refuse at the moment it records the refusal, not supplied by the
// caller.
//
// RefusedSpanID names the refusal's own span, not just its trace: a span
// link needs a real span id to survive -- the OpenTelemetry SDK silently
// drops a Link built from an invalid SpanContext (span_id=0, no attributes)
// before it ever reaches an exporter. The trace id alone cannot carry the
// approved-execution trace's link back to a specific span, only to the
// trace as a whole. This field is what makes that link real rather than
// assumed.
//
// What this field actually carries is the settlement-agent span active in
// `decide_settlement` when the refusal was recorded -- settlement-agent's
// own A2A server span, by the time `settlement_agent.run()` has returned --
// not the Policy Decision Service's `policy.decision` span, and not the
// denied `execute_payment` client span. It is still the right span for
// `execute_approved` to link from (see settlement.py's `_refusal_link`).
//
// Narrative is the one field on this whole struct that is model output, not
// a ledger fact. It is stored verbatim as *what the model said* when it
// explained the refusal to the claimant -- never rewritten, summarised, or
// treated as a statement of what happened. The honesty rule this repo
// enforces everywhere applies here too: amounts, currencies, statuses and
// payment ids come from the ledger; only the narrative comes from the model,
// and it is presented as a quotation of the model, not as fact.
type refusal struct {
	Rule           string  `json:"rule"`
	Limit          float64 `json:"limit"`
	Requested      float64 `json:"requested"`
	Narrative      string  `json:"narrative"`
	RefusedTraceID string  `json:"refusedTraceId"`
	RefusedSpanID  string  `json:"refusedSpanId"`
	RefusedAt      string  `json:"refusedAt"`
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

// refuse records why a payout was refused, beside the payout. It is the only
// way a payment reaches StatusRefused.
//
// It rejects an already-executed payment with ErrExecuted -- the same error
// store.execute returns for its own already-executed case, and the handler
// gives it the same 409 shape -- because the money has already moved once,
// and a refusal record after that would misstate the ledger.
//
// It also rejects an already-refused payment with ErrAlreadyRefused: propose
// CREATES a record; refuse MUTATES the one a human reviewer reads before
// releasing money, so allowing an unconditional second refuse call would let
// any caller overwrite refusedTraceId/refusedSpanId, repointing the audit
// link at a trace of their own choosing. There is exactly one legitimate
// path to StatusRefused (a payment that is merely `proposed`), so a second
// refuse call is never the ordinary case.
func (s *store) refuse(paymentID string, r refusal) (Payment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	payment, ok := s.payments[paymentID]
	if !ok {
		return Payment{}, ErrNotFound
	}
	if payment.Status == StatusExecuted {
		return Payment{}, ErrExecuted
	}
	if payment.Status == StatusRefused {
		return Payment{}, ErrAlreadyRefused
	}
	payment.Status = StatusRefused
	payment.Refusal = &r
	s.payments[paymentID] = payment
	return payment, nil
}

// list returns payments matching status, or every payment if status is
// empty. Sorted by PaymentID -- which is sequential and fixed-width, so a
// string sort is also numeric order -- so the response is deterministic
// across runs and readable straight off the wire, the same care given to
// payment id assignment above.
func (s *store) list(status string) []Payment {
	s.mu.Lock()
	defer s.mu.Unlock()

	matches := make([]Payment, 0, len(s.payments))
	for _, payment := range s.payments {
		if status == "" || payment.Status == status {
			matches = append(matches, payment)
		}
	}
	sort.Slice(matches, func(i, j int) bool {
		return matches[i].PaymentID < matches[j].PaymentID
	})
	return matches
}
