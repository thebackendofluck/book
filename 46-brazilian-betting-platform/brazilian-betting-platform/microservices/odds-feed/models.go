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

// Sport represents a supported sport category.
type Sport string

const (
	SportFutebol      Sport = "futebol"
	SportBrasileirão  Sport = "brasileirao"
	SportCopaBrasil   Sport = "copa-brasil"
	SportLibertadores Sport = "libertadores"
	SportSulAmericana Sport = "sul-americana"
	SportUFC          Sport = "ufc"
	SportNBA          Sport = "nba"
	SportNFL          Sport = "nfl"
	SportTenis        Sport = "tenis"
	SportVolei        Sport = "volei"
)

// EventStatus tracks whether an event is upcoming, live, or finished.
type EventStatus string

const (
	EventStatusScheduled EventStatus = "scheduled"
	EventStatusPreLive   EventStatus = "pre_live"
	EventStatusLive      EventStatus = "live"
	EventStatusSuspended EventStatus = "suspended"
	EventStatusFinished  EventStatus = "finished"
	EventStatusSettled   EventStatus = "settled"
	EventStatusVoided    EventStatus = "voided"
	EventStatusCancelled EventStatus = "cancelled"
	EventStatusPostponed EventStatus = "postponed"
)

// MarketType classifies betting markets.
type MarketType string

const (
	MarketTypeMatchWinner     MarketType = "match_winner"
	MarketTypeDoubleChance    MarketType = "double_chance"
	MarketTypeOverUnder       MarketType = "over_under"
	MarketTypeHandicap        MarketType = "handicap"
	MarketTypeBothTeamScore   MarketType = "both_teams_score"
	MarketTypeHalfTime        MarketType = "half_time_result"
	MarketTypeCorrectScore    MarketType = "correct_score"
	MarketTypeFirstGoal       MarketType = "first_goal_scorer"
	MarketTypeMethodOfVictory MarketType = "method_of_victory"
	MarketTypeRound           MarketType = "round_betting"
)

// Selection represents a single outcome within a market.
type Selection struct {
	ID       string  `json:"id"`
	Name     string  `json:"name"`
	Odds     float64 `json:"odds"`
	IsActive bool    `json:"is_active"`
	// Change in odds since last update (positive = drifted, negative = shortened).
	OddsMovement float64 `json:"odds_movement,omitempty"`
}

// Market represents a betting market with one or more selections.
type Market struct {
	ID         string      `json:"id"`
	Type       MarketType  `json:"type"`
	Name       string      `json:"name"`
	IsOpen     bool        `json:"is_open"`
	IsInPlay   bool        `json:"is_in_play"`
	Selections []Selection `json:"selections"`
	UpdatedAt  time.Time   `json:"updated_at"`
}

// Event represents a sporting event with available betting markets.
type Event struct {
	ID           string      `json:"id"`
	Sport        Sport       `json:"sport"`
	Competition  string      `json:"competition"` // e.g., "Brasileirão Série A"
	HomeTeam     string      `json:"home_team"`
	AwayTeam     string      `json:"away_team"`
	Status       EventStatus `json:"status"`
	StartTime    time.Time   `json:"start_time"`
	InPlayMinute int         `json:"in_play_minute,omitempty"`
	HomeScore    int         `json:"home_score,omitempty"`
	AwayScore    int         `json:"away_score,omitempty"`
	Markets      []Market    `json:"markets,omitempty"`
	IsFeatured   bool        `json:"is_featured"`
	// ManuallySuspended marks an event suspended by an operator (trader
	// action or kill switch) rather than by automatic incident handling.
	// Auto-reopen logic must never clear this on its own; only an explicit
	// unsuspend action may.
	ManuallySuspended bool      `json:"manually_suspended,omitempty"`
	UpdatedAt         time.Time `json:"updated_at"`
}

// OddsUpdate is the payload for an internal odds update from a feed provider.
type OddsUpdate struct {
	EventID     string    `json:"event_id"`
	MarketID    string    `json:"market_id"`
	SelectionID string    `json:"selection_id"`
	NewOdds     float64   `json:"new_odds"`
	Source      string    `json:"source"` // provider name
	UpdatedAt   time.Time `json:"updated_at"`
}

// LiveOddsMessage is streamed via SSE to subscribed clients.
type LiveOddsMessage struct {
	Type        string    `json:"type"` // "odds_update", "score_update", "market_closed"
	EventID     string    `json:"event_id"`
	MarketID    string    `json:"market_id,omitempty"`
	SelectionID string    `json:"selection_id,omitempty"`
	Odds        float64   `json:"odds,omitempty"`
	Score       string    `json:"score,omitempty"`
	Timestamp   time.Time `json:"timestamp"`
}
