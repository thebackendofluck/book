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

import (
	"time"
)

// BetStatus represents the lifecycle state of a bet.
type BetStatus string

const (
	BetStatusPending   BetStatus = "pending"
	BetStatusAccepted  BetStatus = "accepted"
	BetStatusSettled   BetStatus = "settled"
	BetStatusCancelled BetStatus = "cancelled"
	BetStatusCashedOut BetStatus = "cashed_out"
)

// BetType represents the type of bet placed.
type BetType string

const (
	BetTypeSingle   BetType = "single"
	BetTypeMultiple BetType = "multiple"
	BetTypeSystem   BetType = "system"
)

// MarketType represents different betting markets.
type MarketType string

const (
	MarketTypeMatchWinner  MarketType = "match_winner"
	MarketTypeOverUnder    MarketType = "over_under"
	MarketTypeHandicap     MarketType = "handicap"
	MarketTypeBothTeamScore MarketType = "both_teams_score"
	MarketTypeCorrectScore MarketType = "correct_score"
	MarketTypeFirstGoal    MarketType = "first_goal_scorer"
)

// SelectionResult represents the outcome of a selection.
type SelectionResult string

const (
	SelectionResultWon      SelectionResult = "won"
	SelectionResultLost     SelectionResult = "lost"
	SelectionResultVoid     SelectionResult = "void"
	SelectionResultPending  SelectionResult = "pending"
	SelectionResultHalfWon  SelectionResult = "half_won"
	SelectionResultHalfLost SelectionResult = "half_lost"
)

// Odds represents pricing for a selection.
type Odds struct {
	ID         string     `json:"id" db:"id"`
	EventID    string     `json:"event_id" db:"event_id"`
	MarketID   string     `json:"market_id" db:"market_id"`
	SelectionID string    `json:"selection_id" db:"selection_id"`
	Value      float64    `json:"value" db:"value"`
	Probability float64   `json:"probability,omitempty" db:"probability"`
	IsActive   bool       `json:"is_active" db:"is_active"`
	UpdatedAt  time.Time  `json:"updated_at" db:"updated_at"`
}

// Market represents a betting market within an event.
type Market struct {
	ID          string     `json:"id" db:"id"`
	EventID     string     `json:"event_id" db:"event_id"`
	Type        MarketType `json:"type" db:"type"`
	Name        string     `json:"name" db:"name"`
	Description string     `json:"description,omitempty" db:"description"`
	IsOpen      bool       `json:"is_open" db:"is_open"`
	IsInPlay    bool       `json:"is_in_play" db:"is_in_play"`
	Selections  []Selection `json:"selections,omitempty" db:"-"`
	CreatedAt   time.Time  `json:"created_at" db:"created_at"`
	UpdatedAt   time.Time  `json:"updated_at" db:"updated_at"`
}

// Selection represents a possible outcome within a market.
type Selection struct {
	ID       string  `json:"id" db:"id"`
	MarketID string  `json:"market_id" db:"market_id"`
	Name     string  `json:"name" db:"name"`
	Odds     float64 `json:"odds" db:"odds"`
	IsActive bool    `json:"is_active" db:"is_active"`
}

// BetSelection represents one leg of a bet.
type BetSelection struct {
	ID          string          `json:"id" db:"id"`
	BetID       string          `json:"bet_id" db:"bet_id"`
	EventID     string          `json:"event_id" db:"event_id"`
	MarketID    string          `json:"market_id" db:"market_id"`
	SelectionID string          `json:"selection_id" db:"selection_id"`
	OddsValue   float64         `json:"odds_value" db:"odds_value"`
	EventName   string          `json:"event_name" db:"event_name"`
	MarketName  string          `json:"market_name" db:"market_name"`
	SelectionName string        `json:"selection_name" db:"selection_name"`
	Result      SelectionResult `json:"result" db:"result"`
	SettledAt   *time.Time      `json:"settled_at,omitempty" db:"settled_at"`
}

// Bet represents a wager placed by a player.
type Bet struct {
	ID              string        `json:"id" db:"id"`
	CPF             string        `json:"cpf" db:"cpf"`
	Type            BetType       `json:"type" db:"type"`
	Status          BetStatus     `json:"status" db:"status"`
	Stake           float64       `json:"stake" db:"stake"`             // Amount wagered in BRL
	PotentialReturn float64       `json:"potential_return" db:"potential_return"`
	ActualReturn    float64       `json:"actual_return,omitempty" db:"actual_return"`
	CombinedOdds    float64       `json:"combined_odds" db:"combined_odds"`
	Selections      []BetSelection `json:"selections,omitempty" db:"-"`
	CashoutValue    float64       `json:"cashout_value,omitempty" db:"cashout_value"`
	CashedOutAt     *time.Time    `json:"cashed_out_at,omitempty" db:"cashed_out_at"`
	SIGAPReportID   string        `json:"sigap_report_id,omitempty" db:"sigap_report_id"`
	IntegrityFlags  []string      `json:"integrity_flags,omitempty" db:"-"`
	IPAddress       string        `json:"ip_address,omitempty" db:"ip_address"`
	DeviceID        string        `json:"device_id,omitempty" db:"device_id"`
	PlacedAt        time.Time     `json:"placed_at" db:"placed_at"`
	SettledAt       *time.Time    `json:"settled_at,omitempty" db:"settled_at"`
	UpdatedAt       time.Time     `json:"updated_at" db:"updated_at"`
}

// PlaceBetRequest is the incoming payload to place a bet.
type PlaceBetRequest struct {
	CPF        string         `json:"cpf"`
	SessionID  string         `json:"session_id"`
	Type       BetType        `json:"type"`
	Stake      float64        `json:"stake"`
	Selections []SelectionReq `json:"selections"`
	IPAddress  string         `json:"ip_address,omitempty"`
	DeviceID   string         `json:"device_id,omitempty"`
}

// SelectionReq is a single selection within a PlaceBetRequest.
type SelectionReq struct {
	EventID     string  `json:"event_id"`
	MarketID    string  `json:"market_id"`
	SelectionID string  `json:"selection_id"`
	OddsValue   float64 `json:"odds_value"`
}

// CashoutRequest is the payload to request early cashout.
type CashoutRequest struct {
	CPF   string  `json:"cpf"`
	Value float64 `json:"value"` // agreed cashout value
}

// SettleBetRequest triggers settlement of a bet based on event result.
type SettleBetRequest struct {
	EventID    string                     `json:"event_id"`
	Results    map[string]SelectionResult `json:"results"` // selection_id -> result
	SettledBy  string                     `json:"settled_by"`
}

// Settlement represents the final outcome record for a bet.
type Settlement struct {
	ID           string          `json:"id" db:"id"`
	BetID        string          `json:"bet_id" db:"bet_id"`
	CPF          string          `json:"cpf" db:"cpf"`
	Result       SelectionResult `json:"result" db:"result"`
	Stake        float64         `json:"stake" db:"stake"`
	Return       float64         `json:"return" db:"return"`
	Profit       float64         `json:"profit" db:"profit"`  // Return - Stake
	TaxWithheld  float64         `json:"tax_withheld" db:"tax_withheld"`
	NetPayout    float64         `json:"net_payout" db:"net_payout"`
	SettledBy    string          `json:"settled_by" db:"settled_by"`
	SettledAt    time.Time       `json:"settled_at" db:"settled_at"`
}

// PlayerLimits holds responsible gambling limits for a player.
type PlayerLimits struct {
	CPF             string    `json:"cpf" db:"cpf"`
	DailyStakeLimit float64   `json:"daily_stake_limit" db:"daily_stake_limit"`
	WeeklyStakeLimit float64  `json:"weekly_stake_limit" db:"weekly_stake_limit"`
	MonthlyStakeLimit float64 `json:"monthly_stake_limit" db:"monthly_stake_limit"`
	MaxSingleBet    float64   `json:"max_single_bet" db:"max_single_bet"`
	UpdatedAt       time.Time `json:"updated_at" db:"updated_at"`
}

// IntegrityAlert represents a Portaria 1207 integrity flag.
type IntegrityAlert struct {
	ID          string    `json:"id" db:"id"`
	BetID       string    `json:"bet_id" db:"bet_id"`
	CPF         string    `json:"cpf" db:"cpf"`
	AlertType   string    `json:"alert_type" db:"alert_type"`
	Description string    `json:"description" db:"description"`
	Severity    string    `json:"severity" db:"severity"` // low, medium, high, critical
	Reported    bool      `json:"reported" db:"reported"`
	CreatedAt   time.Time `json:"created_at" db:"created_at"`
}
