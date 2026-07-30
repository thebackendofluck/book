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

// Sport identifies a supported sport for bet builder.
type Sport string

const (
	SportFootball   Sport = "football"
	SportTennis     Sport = "tennis"
	SportBasketball Sport = "basketball"
)

// MarketType identifies a betting market category.
type MarketType string

const (
	MarketMatch1X2      MarketType = "1x2"
	MarketTotalGoals    MarketType = "total_goals"
	MarketBTTS          MarketType = "btts"
	MarketDoubleChance  MarketType = "double_chance"
	MarketCorrectScore  MarketType = "correct_score"
	MarketFirstHalf1X2  MarketType = "first_half_1x2"
	MarketAsianHandicap MarketType = "asian_handicap"
	MarketDrawNoBet     MarketType = "draw_no_bet"
	MarketPlayerProps   MarketType = "player_props"
	MarketMatchWinner   MarketType = "match_winner"
	MarketTotalPoints   MarketType = "total_points"
	MarketSpread        MarketType = "spread"
	MarketSetWinner     MarketType = "set_winner"
	MarketTotalGames    MarketType = "total_games"
)

// BuilderSelection represents one leg within a bet builder bundle.
type BuilderSelection struct {
	ID            string     `json:"id"`
	EventID       string     `json:"event_id"`
	MarketID      string     `json:"market_id"`
	MarketType    MarketType `json:"market_type"`
	SelectionID   string     `json:"selection_id"`
	SelectionName string     `json:"selection_name"`
	OddsValue     float64    `json:"odds_value"`
	AddedAt       time.Time  `json:"added_at"`
}

// BuilderQuote holds the priced output of a bet builder request.
type BuilderQuote struct {
	EventID           string             `json:"event_id"`
	Selections        []BuilderSelection `json:"selections"`
	RawCombinedOdds   float64            `json:"raw_combined_odds"`
	AdjustedOdds      float64            `json:"adjusted_odds"`
	CorrelationFactor float64            `json:"correlation_factor"`
	PotentialReturn   float64            `json:"potential_return"`
	SelectionCount    int                `json:"selection_count"`
	Valid             bool               `json:"valid"`
	Errors            []string           `json:"errors,omitempty"`
}

// CorrelationFactor represents the adjustment factor between two correlated markets.
type CorrelationFactor struct {
	MarketA   MarketType `json:"market_a"`
	MarketB   MarketType `json:"market_b"`
	Factor    float64    `json:"factor"` // 0.0 = fully correlated, 1.0 = independent
	Sport     Sport      `json:"sport"`
	Symmetric bool       `json:"symmetric"` // true if factor applies in both directions
}

// CorrelationEdge represents a weighted edge in the correlation graph.
type CorrelationEdge struct {
	Target MarketType `json:"target"`
	Factor float64    `json:"factor"`
}

// CompatibilityRule defines whether two market types can be combined.
type CompatibilityRule struct {
	MarketA MarketType `json:"market_a"`
	MarketB MarketType `json:"market_b"`
	Allowed bool       `json:"allowed"`
	Sport   Sport      `json:"sport"`
	Reason  string     `json:"reason,omitempty"`
}

// BuilderTemplate is a predefined popular combination for the builder UX.
type BuilderTemplate struct {
	ID          string       `json:"id"`
	Name        string       `json:"name"`
	Description string       `json:"description"`
	Sport       Sport        `json:"sport"`
	Markets     []MarketType `json:"markets"`
	Popular     bool         `json:"popular"`
}

// BuilderBetslip tracks the in-progress bet builder state for one event.
type BuilderBetslip struct {
	ID         string             `json:"id"`
	EventID    string             `json:"event_id"`
	Sport      Sport              `json:"sport"`
	Selections []BuilderSelection `json:"selections"`
	Quote      *BuilderQuote      `json:"quote,omitempty"`
	CreatedAt  time.Time          `json:"created_at"`
	UpdatedAt  time.Time          `json:"updated_at"`
}

// AddSelectionRequest is the incoming payload to add a selection to the builder.
type AddSelectionRequest struct {
	EventID       string     `json:"event_id"`
	Sport         Sport      `json:"sport"`
	MarketID      string     `json:"market_id"`
	MarketType    MarketType `json:"market_type"`
	SelectionID   string     `json:"selection_id"`
	SelectionName string     `json:"selection_name"`
	OddsValue     float64    `json:"odds_value"`
}

// RemoveSelectionRequest is the payload to remove a selection from the builder.
type RemoveSelectionRequest struct {
	SelectionID string `json:"selection_id"`
}

// QuoteRequest is the payload to price a bet builder combination.
type QuoteRequest struct {
	EventID    string             `json:"event_id"`
	Sport      Sport              `json:"sport"`
	Stake      float64            `json:"stake"`
	Selections []BuilderSelection `json:"selections"`
}

// BuilderConfig holds limits for the bet builder engine.
type BuilderConfig struct {
	MinSelections      int     `json:"min_selections"`
	MaxSelections      int     `json:"max_selections"`
	MinOddsPerLeg      float64 `json:"min_odds_per_leg"`
	MaxPotentialReturn float64 `json:"max_potential_return"`
}

// DefaultBuilderConfig returns sensible defaults for Brazilian market.
func DefaultBuilderConfig() BuilderConfig {
	return BuilderConfig{
		MinSelections:      2,
		MaxSelections:      6,
		MinOddsPerLeg:      1.05,
		MaxPotentialReturn: 500000.00, // R$500k max return
	}
}
