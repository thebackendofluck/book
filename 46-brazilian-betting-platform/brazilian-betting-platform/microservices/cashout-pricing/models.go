// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package main

import "time"

// QuoteStatus represents the lifecycle state of a cashout quote.
type QuoteStatus string

const (
	QuoteStatusPending     QuoteStatus = "pending"
	QuoteStatusAccepted    QuoteStatus = "accepted"
	QuoteStatusExpired     QuoteStatus = "expired"
	QuoteStatusInvalidated QuoteStatus = "invalidated"
	QuoteStatusRejected    QuoteStatus = "rejected"
)

// CashoutType represents whether the cashout is full or partial.
type CashoutType string

const (
	CashoutTypeFull    CashoutType = "full"
	CashoutTypePartial CashoutType = "partial"
)

// CashoutQuote represents a time-limited offer to cash out a bet.
type CashoutQuote struct {
	ID              string      `json:"id" db:"id"`
	BetID           string      `json:"bet_id" db:"bet_id"`
	CPF             string      `json:"cpf" db:"cpf"`
	Status          QuoteStatus `json:"status" db:"status"`
	Type            CashoutType `json:"type" db:"type"`
	OriginalStake   float64     `json:"original_stake" db:"original_stake"`
	CashoutStake    float64     `json:"cashout_stake" db:"cashout_stake"`       // Portion being cashed out
	RemainingStake  float64     `json:"remaining_stake" db:"remaining_stake"`   // Stake left on bet (0 for full)
	OfferedValue    float64     `json:"offered_value" db:"offered_value"`       // Amount offered to player
	OperatorMargin  float64     `json:"operator_margin" db:"operator_margin"`   // Margin applied (e.g. 0.05)
	OriginalImplied float64     `json:"original_implied" db:"original_implied"` // Implied prob at placement
	CurrentImplied  float64     `json:"current_implied" db:"current_implied"`   // Implied prob at quote time
	CombinedOdds    float64     `json:"combined_odds" db:"combined_odds"`       // Original combined odds
	CurrentOdds     float64     `json:"current_odds" db:"current_odds"`         // Current combined odds
	TTLSeconds      int         `json:"ttl_seconds" db:"ttl_seconds"`
	ExpiresAt       time.Time   `json:"expires_at" db:"expires_at"`
	CreatedAt       time.Time   `json:"created_at" db:"created_at"`
	AcceptedAt      *time.Time  `json:"accepted_at,omitempty" db:"accepted_at"`
	InvalidatedAt   *time.Time  `json:"invalidated_at,omitempty" db:"invalidated_at"`
	InvalidReason   string      `json:"invalid_reason,omitempty" db:"invalid_reason"`
}

// CashoutJournalEntry records the financial impact of an accepted cashout.
type CashoutJournalEntry struct {
	ID            string    `json:"id" db:"id"`
	QuoteID       string    `json:"quote_id" db:"quote_id"`
	BetID         string    `json:"bet_id" db:"bet_id"`
	CPF           string    `json:"cpf" db:"cpf"`
	Type          string    `json:"type" db:"type"` // "cashout_credit", "stake_release", "partial_reduction"
	Amount        float64   `json:"amount" db:"amount"`
	BalanceBefore float64   `json:"balance_before" db:"balance_before"`
	BalanceAfter  float64   `json:"balance_after" db:"balance_after"`
	Reference     string    `json:"reference" db:"reference"`
	CreatedAt     time.Time `json:"created_at" db:"created_at"`
}

// CashoutEligibility holds the result of an eligibility check for a bet.
type CashoutEligibility struct {
	BetID    string `json:"bet_id"`
	Eligible bool   `json:"eligible"`
	Reason   string `json:"reason,omitempty"`
}

// BetSnapshot is a minimal projection of a bet needed for cashout pricing.
type BetSnapshot struct {
	ID              string              `json:"id"`
	CPF             string              `json:"cpf"`
	Status          string              `json:"status"`
	Stake           float64             `json:"stake"`
	CombinedOdds    float64             `json:"combined_odds"`
	PotentialReturn float64             `json:"potential_return"`
	RemainingStake  float64             `json:"remaining_stake"` // After partial cashouts
	Selections      []SelectionSnapshot `json:"selections"`
	PlacedAt        time.Time           `json:"placed_at"`
}

// SelectionSnapshot is a minimal projection of a bet selection.
type SelectionSnapshot struct {
	EventID     string  `json:"event_id"`
	MarketID    string  `json:"market_id"`
	SelectionID string  `json:"selection_id"`
	OddsAtPlace float64 `json:"odds_at_place"`
	CurrentOdds float64 `json:"current_odds"`
	EventState  string  `json:"event_state"` // scheduled, live, suspended, settled
	MarketOpen  bool    `json:"market_open"`
}

// GenerateQuoteRequest is the incoming payload to request a cashout quote.
type GenerateQuoteRequest struct {
	BetID          string  `json:"bet_id"`
	CPF            string  `json:"cpf"`
	CashoutPercent float64 `json:"cashout_percent,omitempty"` // 0 < pct <= 1.0; default 1.0 (full)
}

// AcceptQuoteRequest is the payload to accept an outstanding cashout quote.
type AcceptQuoteRequest struct {
	QuoteID string `json:"quote_id"`
	BetID   string `json:"bet_id"`
	CPF     string `json:"cpf"`
}

// AcceptQuoteResponse confirms the cashout acceptance.
type AcceptQuoteResponse struct {
	QuoteID        string  `json:"quote_id"`
	BetID          string  `json:"bet_id"`
	CreditedAmount float64 `json:"credited_amount"`
	RemainingStake float64 `json:"remaining_stake"`
	JournalEntryID string  `json:"journal_entry_id"`
}

// PricingConfig holds operator-level cashout pricing parameters.
type PricingConfig struct {
	DefaultMargin    float64 `json:"default_margin"`    // e.g. 0.05 (5%)
	MinCashoutBRL    float64 `json:"min_cashout_brl"`   // Minimum cashout value
	MaxCashoutBRL    float64 `json:"max_cashout_brl"`   // Maximum cashout value (0 = no cap)
	DefaultTTL       int     `json:"default_ttl"`       // Seconds, default 30 (pre-match)
	LiveTTL          int     `json:"live_ttl"`          // Seconds, default 3 (live events move too fast for a 30s window)
	MinPartialPct    float64 `json:"min_partial_pct"`   // Minimum partial cashout percentage
	MaxPartialPct    float64 `json:"max_partial_pct"`   // Maximum partial cashout percentage
	SuspendedAllowed bool    `json:"suspended_allowed"` // Allow cashout on suspended markets
}

// DefaultPricingConfig returns production-safe defaults.
func DefaultPricingConfig() PricingConfig {
	return PricingConfig{
		DefaultMargin:    0.05,
		MinCashoutBRL:    1.00,
		MaxCashoutBRL:    0, // No cap
		DefaultTTL:       30,
		LiveTTL:          3,
		MinPartialPct:    0.10,
		MaxPartialPct:    0.90,
		SuspendedAllowed: false,
	}
}

// SIGAPCashoutReport is the SIGAP payload for cashout events (Lei 14.790/2023).
type SIGAPCashoutReport struct {
	Version    string              `json:"version"`
	OperatorID string              `json:"operator_id"`
	ReportedAt time.Time           `json:"reported_at"`
	Cashout    SIGAPCashoutPayload `json:"cashout"`
}

// SIGAPCashoutPayload contains the cashout data fields for SIGAP reporting.
type SIGAPCashoutPayload struct {
	QuoteID        string    `json:"quote_id"`
	BetID          string    `json:"bet_id"`
	CPF            string    `json:"cpf"` // Masked
	CashoutType    string    `json:"cashout_type"`
	OriginalStake  float64   `json:"original_stake_brl"`
	CashoutStake   float64   `json:"cashout_stake_brl"`
	CashoutValue   float64   `json:"cashout_value_brl"`
	RemainingStake float64   `json:"remaining_stake_brl"`
	OperatorMargin float64   `json:"operator_margin"`
	OriginalOdds   float64   `json:"original_odds"`
	CurrentOdds    float64   `json:"current_odds"`
	AcceptedAt     time.Time `json:"accepted_at"`
}
