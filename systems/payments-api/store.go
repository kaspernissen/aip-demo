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
// The state machine, maker-checker:
//
//	proposed -> executed            the agent, within its band (PDS-permitted)
//	proposed -> awaiting_approval   refuse: the PDS denied the agent's release
//	awaiting_approval -> executed   a human, through the PDS (execute)
//	awaiting_approval -> rejected   a human, /reject
//
// executed and rejected are terminal. WHAT IS ENFORCED, AND WHERE: this
// service does not decide who may execute, and store.execute accepts a
// `proposed` or an `awaiting_approval` payment from any caller; it refuses only
// one that can no longer be released (executed, rejected). The PDS decides, and
// it reads this status back (GET /payments/{id}) for EVERY execute_payment that
// passes the gateway: the settlement agent may execute only a `proposed`
// payment (and within its limit), the reviewer-ui only an `awaiting_approval`
// one, and an unreadable status denies both. A caller that reaches this service
// without the gateway is outside that guarantee, which is why the reviewer-ui's
// egress to this service is restricted at L7 to list, get and reject
// (deploy/platform/cilium-policies.yaml).
const (
	StatusProposed         = "proposed"
	StatusAwaitingApproval = "awaiting_approval"
	StatusExecuted         = "executed"
	StatusRejected         = "rejected"
)

var (
	ErrNotFound = errors.New("payment not found")
	ErrExecuted = errors.New("payment already executed")
	// ErrAlreadyRefused: a payment already awaiting approval may not be
	// refused a second time -- replacing the record on a second refuse call
	// would let a caller repoint the audit trail. See store.refuse's own doc
	// comment for the full reasoning.
	ErrAlreadyRefused = errors.New("payment already awaiting approval")
	// ErrRejected: a reviewer rejected this payment; it can be neither
	// executed nor refused again.
	ErrRejected = errors.New("payment was rejected by a reviewer")
	// ErrNotAwaiting: reject is only meaningful for a payment awaiting approval.
	ErrNotAwaiting = errors.New("payment is not awaiting approval")
)

type Payment struct {
	PaymentID string  `json:"paymentId"`
	ClaimID   string  `json:"claimId"`
	Amount    float64 `json:"amount"`
	Currency  string  `json:"currency"`
	Status    string  `json:"status"`
	// A2ATaskID is the customer's A2A task, recorded by refuse so the
	// reviewer-ui can resume it after a human decides.
	A2ATaskID string `json:"a2aTaskId,omitempty"`
	// AwaitingSince (RFC3339) is when refuse put the payment into
	// awaiting_approval: the one clock for "how long the human took".
	AwaitingSince string     `json:"awaitingSince,omitempty"`
	Refusal       *refusal   `json:"refusal,omitempty"`
	Rejection     *rejection `json:"rejection,omitempty"`
}

// rejection is a reviewer's decision not to release the payment. ReviewerID
// is what the reviewer-ui reports; RejectedAt is stamped by this ledger.
type rejection struct {
	ReviewerID string `json:"reviewerId"`
	RejectedAt string `json:"rejectedAt"`
}

// refusal is what the platform decided and what the claimant was told.
// Stored beside the payment so the reviewer sees the decision, not a guess.
//
// Rule, Limit, Requested and RefusedTraceID are ledger facts: the Policy
// Decision Service's own denial, and the trace that recorded it -- this
// service never invents or recomputes any of them, it only carries what the
// settlement agent reports. The moment the wait began is Payment.AwaitingSince,
// stamped by this ledger, not supplied by the caller.
//
// RefusedSpanID names the refusal's own span, not just its trace: a span
// link needs a real span id to survive -- the OpenTelemetry SDK silently
// drops a Link built from an invalid SpanContext (span_id=0, no attributes)
// before it ever reaches an exporter. The trace id alone cannot carry the
// approved-execution trace's link back to a specific span, only to the
// trace as a whole.
//
// What this field actually carries is the settlement-agent span active in
// `decide_settlement` when the refusal was recorded -- settlement-agent's
// own A2A server span, by the time `settlement_agent.run()` has returned --
// not the Policy Decision Service's `policy.decision` span, and not the
// denied `execute_payment` client span.
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
	if payment.Status == StatusRejected {
		return Payment{}, ErrRejected
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

// refuse records why a payout was refused, beside the payout, and leaves the
// payment awaiting a human. It is the only way a payment reaches
// StatusAwaitingApproval.
//
// It rejects an already-executed payment with ErrExecuted -- the same error
// store.execute returns for its own already-executed case, and the handler
// gives it the same 409 shape -- because the money has already moved once,
// and a refusal record after that would misstate the ledger.
//
// It rejects a rejected payment with ErrRejected: a reviewer's decision is
// final.
//
// It also rejects an already-awaiting payment with ErrAlreadyRefused: propose
// CREATES a record; refuse MUTATES the one a human reviewer reads before
// releasing money, so allowing an unconditional second refuse call would let
// any caller overwrite refusedTraceId/refusedSpanId, repointing the audit
// link at a trace of their own choosing. There is exactly one legitimate
// path to StatusAwaitingApproval (a payment that is merely `proposed`), so a second
// refuse call is never the ordinary case.
func (s *store) refuse(paymentID string, r refusal, a2aTaskID, now string) (Payment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	payment, ok := s.payments[paymentID]
	if !ok {
		return Payment{}, ErrNotFound
	}
	if payment.Status == StatusExecuted {
		return Payment{}, ErrExecuted
	}
	if payment.Status == StatusRejected {
		return Payment{}, ErrRejected
	}
	if payment.Status == StatusAwaitingApproval {
		return Payment{}, ErrAlreadyRefused
	}
	payment.Status = StatusAwaitingApproval
	payment.Refusal = &r
	payment.A2ATaskID = a2aTaskID
	payment.AwaitingSince = now
	s.payments[paymentID] = payment
	return payment, nil
}

// reject records a reviewer's decision not to release a payment awaiting
// approval. Only awaiting_approval payments can be rejected.
func (s *store) reject(paymentID, reviewerID, now string) (Payment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	payment, ok := s.payments[paymentID]
	if !ok {
		return Payment{}, ErrNotFound
	}
	if payment.Status != StatusAwaitingApproval {
		return Payment{}, ErrNotAwaiting
	}
	payment.Status = StatusRejected
	payment.Rejection = &rejection{ReviewerID: reviewerID, RejectedAt: now}
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
