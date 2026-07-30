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

// SettlementStatus tracks the state of an event settlement run.
type SettlementStatus string

const (
	SettlementStatusPending    SettlementStatus = "pending"
	SettlementStatusProcessing SettlementStatus = "processing"
	SettlementStatusCompleted  SettlementStatus = "completed"
	SettlementStatusFailed     SettlementStatus = "failed"
)

// BetOutcome holds the final result for a single bet after settlement.
type BetOutcome string

const (
	BetOutcomeWon  BetOutcome = "won"
	BetOutcomeLost BetOutcome = "lost"
	BetOutcomeVoid BetOutcome = "void"
	BetOutcomePush BetOutcome = "push" // Stake returned, no profit/loss
)

// Settlement represents the settlement record for a single bet.
type Settlement struct {
	ID            string     `json:"id" db:"id"`
	BetID         string     `json:"bet_id" db:"bet_id"`
	EventID       string     `json:"event_id" db:"event_id"`
	CPF           string     `json:"cpf" db:"cpf"`
	Outcome       BetOutcome `json:"outcome" db:"outcome"`
	Stake         float64    `json:"stake" db:"stake"`
	GrossReturn   float64    `json:"gross_return" db:"gross_return"`
	TaxWithheld   float64    `json:"tax_withheld" db:"tax_withheld"`
	NetPayout     float64    `json:"net_payout" db:"net_payout"`
	GGRContrib    float64    `json:"ggr_contrib" db:"ggr_contrib"` // Contribution to GGR
	SIGAPReported bool       `json:"sigap_reported" db:"sigap_reported"`
	SettledAt     time.Time  `json:"settled_at" db:"settled_at"`
}

// EventSettlementRun records the batch settlement for an entire event.
type EventSettlementRun struct {
	ID               string           `json:"id" db:"id"`
	EventID          string           `json:"event_id" db:"event_id"`
	Status           SettlementStatus `json:"status" db:"status"`
	TotalBets        int              `json:"total_bets" db:"total_bets"`
	WinningBets      int              `json:"winning_bets" db:"winning_bets"`
	LosingBets       int              `json:"losing_bets" db:"losing_bets"`
	VoidBets         int              `json:"void_bets" db:"void_bets"`
	TotalStake       float64          `json:"total_stake" db:"total_stake"`
	TotalPrizesPaid  float64          `json:"total_prizes_paid" db:"total_prizes_paid"`
	TotalTaxWithheld float64          `json:"total_tax_withheld" db:"total_tax_withheld"`
	GGR              float64          `json:"ggr" db:"ggr"`
	StartedAt        time.Time        `json:"started_at" db:"started_at"`
	CompletedAt      *time.Time       `json:"completed_at,omitempty" db:"completed_at"`
}

// GGRReport is the daily Gross Gaming Revenue report for SIGAP.
// GGR = Total bets staked - Prizes paid out - Income tax withheld
type GGRReport struct {
	ID               string    `json:"id" db:"id"`
	ReportDate       string    `json:"report_date" db:"report_date"` // YYYY-MM-DD
	OperatorID       string    `json:"operator_id" db:"operator_id"`
	TotalBetsAmount  float64   `json:"total_bets_amount" db:"total_bets_amount"`
	TotalPrizesPaid  float64   `json:"total_prizes_paid" db:"total_prizes_paid"`
	TotalTaxWithheld float64   `json:"total_tax_withheld" db:"total_tax_withheld"`
	GGR              float64   `json:"ggr" db:"ggr"` // = TotalBetsAmount - TotalPrizesPaid - TotalTaxWithheld
	TotalBetCount    int       `json:"total_bet_count" db:"total_bet_count"`
	UniquePlayerCount int      `json:"unique_player_count" db:"unique_player_count"`
	SIGAPSubmittedAt *time.Time `json:"sigap_submitted_at,omitempty" db:"sigap_submitted_at"`
	GeneratedAt      time.Time `json:"generated_at" db:"generated_at"`
}

// TaxWithholding records a 15% income tax deduction per player per calendar
// month. Lei 14.790/2023, Art. 31: 15% on net winnings, aggregated monthly
// per CPF, exceeding the R$2,112 monthly exemption threshold.
type TaxWithholding struct {
	ID             string    `json:"id" db:"id"`
	CPF            string    `json:"cpf" db:"cpf"`
	Period         string    `json:"period" db:"period"` // calendar month, e.g. "2024-03"
	TotalWinnings  float64   `json:"total_winnings" db:"total_winnings"`
	TotalStake     float64   `json:"total_stake" db:"total_stake"`
	NetWinnings    float64   `json:"net_winnings" db:"net_winnings"`  // TotalWinnings - TotalStake
	TaxableAmount  float64   `json:"taxable_amount" db:"taxable_amount"` // NetWinnings above threshold
	TaxRate        float64   `json:"tax_rate" db:"tax_rate"`             // 0.15
	TaxAmount      float64   `json:"tax_amount" db:"tax_amount"`
	Withheld       bool      `json:"withheld" db:"withheld"`
	WithheldAt     *time.Time `json:"withheld_at,omitempty" db:"withheld_at"`
	CreatedAt      time.Time `json:"created_at" db:"created_at"`
}

// SettleEventRequest is the payload to trigger event settlement.
type SettleEventRequest struct {
	EventID string                 `json:"event_id"`
	Results map[string]string      `json:"results"` // selection_id -> "won"/"lost"/"void"
	Source  string                 `json:"source"`  // "feed", "manual", "official"
}

// BetRecord is a minimal projection of a bet needed for settlement.
type BetRecord struct {
	ID              string  `json:"id" db:"id"`
	CPF             string  `json:"cpf" db:"cpf"`
	Stake           float64 `json:"stake" db:"stake"`
	PotentialReturn float64 `json:"potential_return" db:"potential_return"`
	CombinedOdds    float64 `json:"combined_odds" db:"combined_odds"`
	Selections      []BetSelectionRecord `json:"selections"`
}

// BetSelectionRecord is a minimal projection of a bet selection for settlement.
type BetSelectionRecord struct {
	SelectionID string  `json:"selection_id" db:"selection_id"`
	OddsValue   float64 `json:"odds_value" db:"odds_value"`
}
